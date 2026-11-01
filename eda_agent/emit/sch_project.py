"""design.json → KiCad 原理图工程（一页或多页），ADR-0013、ADR-0014。

- 只有一个功能块（或都没写功能块）：一页，所有元件都在根页上。
- 多个功能块，整个设计在一张 A4（不够用 A3）上放得下：还是一页
  （用户 2026-11-01 选「放得下就画一页」）。每个功能块的元件各自排好，用虚线方框圈起来，
  框里左上角写块名和用途；方框按信号流向从左往右排。块和块之间的信号和块里的一样，
  能连的都连成导线（sch_route）。
- 一张 A3 也放不下：每个功能块一页。根页是框图：每块一个图纸框，框上的引脚是这一块和别的块
  共用的信号，框和框之间用导线连起来（连不上的才用同名标签），框下面写这一块做什么、用到哪些电源；
  电源从哪块供给哪块，用不带电气意义的虚线箭头画出来（用户 2026-11-01 选「全部都改」）。
  子页里，跨页的信号在链的末端画层次标签，箭头方向看驱动这个信号的引脚在哪一页：
  在本页 = output，在别的页 = input，都有 = bidirectional，都没有 = passive。
- 电源和地在各页都只画电源符号（KiCad 的电源符号本来就全工程相连），不占图纸框的引脚。
  板外送电进来的网络（external_supply）只放一个 PWR_FLAG（ADR-0008）：单页时放在这一页，
  分页时放在第一张用到它的页上。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from eda_agent.emit.kicad_sym import SymbolDef, load_symbol
from eda_agent.emit.sch_layout import (
    CHAR_W,
    GRID,
    PAGE_MARGIN,
    PAPERS,
    STUB,
    TITLE_RESERVE,
    Ctx,
    Drawing,
    Frame,
    Label,
    Line,
    NetSpec,
    Note,
    PartSpec,
    Prims,
    add_supply_flags,
    arrange,
    build_drawing,
    ceil_grid,
    center,
    column_gaps,
    display_name,
    fits,
    flow_columns,
    infer_kind,
    layout_content,
    r4,
    snap,
    text_width,
    usable_area,
    used_libs,
)
from eda_agent.emit.sch_route import box_nodes, connect_labels, corners, route_free
from eda_agent.emit.sch_writer import Counter, RootExtras, SheetPin, SheetSym, render_page
from eda_agent.ir.models import Design, Net

UNASSIGNED = "unassigned"
DRIVERS = {"output", "power_out", "tri_state", "open_collector", "open_emitter", "bidirectional"}
SHEET_GAP_Y = GRID * 14
SHEET_GAP_X = GRID * 8
FRAME_PAD = GRID * 3  # 方框和里面的元件之间留的空
TITLE_GAP = GRID * 3  # 方框顶上写块名的那一行
BLOCK_GAP_Y = GRID * 6
SINGLE_PAGE_PAPERS = ("A4", "A3")


@dataclass
class Page:
    key: str  # 功能块 id；单页工程是 "root"
    file: str
    purpose: str
    parts: list[PartSpec]
    nets: list[NetSpec]
    drawing: Drawing


@dataclass
class SchematicProject:
    name: str
    pages: list[Page]
    root: Drawing | None  # 多页工程的根页（框图）；单页工程为 None
    extras: RootExtras | None
    nets: list[NetSpec]  # 整个设计的网络，网表一致检查用

    @property
    def root_file(self) -> str:
        return f"{self.name}.kicad_sch"


def net_kind(net: Net, syms: dict[str, SymbolDef]) -> str:
    """地看名字（GND、AGND、VSS……）；电源要有凭据才画成电源符号：

    板外送电进来（external_supply）、有电源输出脚驱动它、或者名字像电源（VCC、+5V……）且有电源输入脚接在上面。
    只看名字会把 VREF 这种基准信号也画成电源符号，ERC 就会报「电源输入脚没有电源驱动」（M1 实测）。
    """
    kind = infer_kind(net.name)
    if kind == "ground":
        return kind
    types = {syms[r].pin(p).etype for r, p in net.nodes}
    if net.external_supply or "power_out" in types:
        return "power"
    if kind == "power" and "power_in" in types:
        return "power"
    return "signal"


def part_specs(design: Design) -> dict[str, PartSpec]:
    edges = {c.ref: c.side for c in design.board.edge_connectors}
    out = {}
    for p in design.parts:
        role = p.role or ""
        if not role and (p.ref in edges or p.symbol.split(":", 1)[0].startswith("Connector")):
            role = "connector"
        out[p.ref] = PartSpec(
            p.ref,
            p.symbol,
            p.value,
            p.footprint,
            role=role,
            anchor=p.anchor or "",
            edge=edges.get(p.ref, ""),
        )
    return out


def _page_of(design: Design) -> dict[str, str]:
    return {p.ref: p.block or UNASSIGNED for p in design.parts}


def _page_order(design: Design, page_of: dict[str, str]) -> list[str]:
    used = set(page_of.values())
    order = [b.id for b in design.blocks if b.id in used]
    if UNASSIGNED in used and UNASSIGNED not in order:
        order.append(UNASSIGNED)
    return order


def _etype(syms: dict[str, SymbolDef], ref: str, pin: str) -> str:
    return syms[ref].pin(pin).etype


def _shape(
    here: list[tuple[str, str]], there: list[tuple[str, str]], syms: dict[str, SymbolDef]
) -> str:
    drive_here = any(_etype(syms, r, p) in DRIVERS for r, p in here)
    drive_there = any(_etype(syms, r, p) in DRIVERS for r, p in there)
    if drive_here and not drive_there:
        return "output"
    if drive_there and not drive_here:
        return "input"
    return "bidirectional" if drive_here else "passive"


@dataclass
class _Ctx:
    """一次 build_project 里各处都要用的东西。"""

    design: Design
    name: str
    lib_dirs: tuple[Path, ...]
    specs: dict[str, PartSpec]
    syms: dict[str, SymbolDef]
    page_of: dict[str, str]
    order: list[str]
    purposes: dict[str, str]


def _block_nets(c: _Ctx, key: str, *, single: bool) -> tuple[list[PartSpec], list[NetSpec]]:
    """一个功能块里的元件，和它碰到的网络（只含它自己的引脚）。

    single：整个设计画在一页上（跨块的信号只是本页的标签，不画层次标签；PWR_FLAG 在整页上统一放）。
    """
    parts = [c.specs[p.ref] for p in c.design.parts if c.page_of[p.ref] == key]
    nets: list[NetSpec] = []
    for net in c.design.nets:
        here = [(r, p) for r, p in net.nodes if c.page_of[r] == key]
        if not here:
            continue
        there = [(r, p) for r, p in net.nodes if c.page_of[r] != key]
        kind = net_kind(net, c.syms)
        first = next(k for k in c.order if any(c.page_of[r] == k for r, _ in net.nodes))
        port = _shape(here, there, c.syms) if there and kind == "signal" and not single else ""
        nets.append(
            NetSpec(
                net.name,
                tuple(here),
                kind=kind,
                external=bool(there),
                supply=net.external_supply and first == key and not single,
                port=port,
            )
        )
    return parts, nets


def build_project(
    design: Design, name: str, lib_dirs: tuple[Path, ...], *, single_page: bool = True
) -> SchematicProject:
    """single_page=False：多个功能块时直接每块一页（测试和对照用；平时放得下就画一页）。"""
    specs = part_specs(design)
    syms = {p.ref: load_symbol(p.symbol, lib_dirs) for p in design.parts}
    all_nets = [
        NetSpec(n.name, tuple(n.nodes), kind=net_kind(n, syms), supply=n.external_supply)
        for n in design.nets
    ]
    page_of = _page_of(design)
    order = _page_order(design, page_of)
    purposes = {b.id: b.purpose for b in design.blocks}
    c = _Ctx(design, name, lib_dirs, specs, syms, page_of, order, purposes)
    if len(order) <= 1:
        drawing = build_drawing(list(specs.values()), all_nets, lib_dirs)
        page = Page(
            "root",
            f"{name}.kicad_sch",
            purposes.get(order[0], "") if order else "",
            list(specs.values()),
            all_nets,
            drawing,
        )
        return SchematicProject(name, [page], None, None, all_nets)

    single = _single_page(c) if single_page else None
    if single is not None:
        page = Page("root", f"{name}.kicad_sch", "", list(specs.values()), all_nets, single)
        return SchematicProject(name, [page], None, None, all_nets)

    pages: list[Page] = []
    ports: dict[str, dict[str, str]] = {key: {} for key in order}  # 页 → {网络: 形状}
    for key in order:
        parts, nets = _block_nets(c, key, single=False)
        for n in nets:
            if n.port:
                ports[key][n.name] = n.port
        drawing = build_drawing(parts, nets, lib_dirs)
        pages.append(Page(key, f"{key}.kicad_sch", purposes.get(key, ""), parts, nets, drawing))
    root, extras = _root_sheet(c, ports)
    return SchematicProject(name, pages, root, extras, all_nets)


# ---------------------------------------------------------------- 单页：每块一个方框


def _edge_blocks(c: _Ctx) -> tuple[list[str], set[str]]:
    left = {c.page_of[e.ref] for e in c.design.board.edge_connectors if e.side == "left"}
    right = {c.page_of[e.ref] for e in c.design.board.edge_connectors if e.side == "right"} - left
    return [k for k in c.order if k in left], right


def _order_blocks(columns: list[list[str]], linked: dict[str, set[str]]) -> list[list[str]]:
    """每列里的块按它连着的上一列各块的平均位置排（重心法），线少交叉。"""
    out = [list(columns[0])] if columns else []
    for col in columns[1:]:
        prev = {k: i for i, k in enumerate(out[-1])}

        def bary(key: str, prev: dict[str, int] = prev) -> float:
            ks = [i for k, i in prev.items() if linked[key] & linked[k]]
            return sum(ks) / len(ks) if ks else math.inf

        out.append(sorted(col, key=lambda k: (bary(k), col.index(k))))
    return out


def block_title(key: str, purpose: str) -> str:
    return f"{key}：{purpose}" if purpose else key


def _single_page(c: _Ctx) -> Drawing | None:
    """整个设计画在一页上；A3 也放不下时返回 None（改成每块一页）。"""
    max_w, max_h = usable_area(SINGLE_PAGE_PAPERS[-1])
    contents: dict[str, Prims] = {}
    ctxs: list[Ctx] = []
    signals: dict[str, set[str]] = {}
    for key in c.order:
        parts, nets = _block_nets(c, key, single=True)
        ctx, clusters, columns = layout_content(parts, nets, c.lib_dirs)
        content = arrange(clusters, columns, max_w, max_h)
        if content is None:
            return None
        contents[key] = content
        ctxs.append(ctx)
        signals[key] = {n.name for n in nets if n.kind == "signal"}

    sources, right = _edge_blocks(c)
    block_cols = flow_columns(c.order, sources, right, lambda a, b: bool(signals[a] & signals[b]))
    block_cols = _order_blocks(block_cols, signals)
    gaps = column_gaps([set().union(*(signals[k] for k in col)) for col in block_cols])

    sizes: dict[str, tuple[float, float]] = {}
    for key, content in contents.items():
        x0, y0, x1, y1 = content.bbox()
        title_w = text_width(block_title(key, c.purposes.get(key, "")))
        w = ceil_grid(max(x1 - x0, title_w) + 2 * FRAME_PAD)
        h = ceil_grid(y1 - y0 + 2 * FRAME_PAD + TITLE_GAP)
        sizes[key] = (w, h)
    col_h = [sum(sizes[k][1] for k in col) + BLOCK_GAP_Y * (len(col) - 1) for col in block_cols]
    tallest = max(col_h)
    page = Prims()
    x = 0.0
    for ci, col in enumerate(block_cols):
        y = snap((tallest - col_h[ci]) / 2)
        width = max(sizes[k][0] for k in col)
        for key in col:
            w, h = sizes[key]
            content = contents[key]
            x0, y0, _, _ = content.bbox()
            dx = snap(x + FRAME_PAD - x0)
            dy = snap(y + FRAME_PAD + TITLE_GAP - y0)
            page.extend(content, dx, dy)
            moved = (x0 + dx, y0 + dy, content.bbox()[2] + dx, content.bbox()[3] + dy)
            fx0 = r4(min(x, moved[0] - FRAME_PAD))
            fy0 = r4(y)
            fx1 = r4(max(x + w, moved[2] + FRAME_PAD))
            fy1 = r4(max(y + h, moved[3] + FRAME_PAD))
            page.frames.append(Frame(fx0, fy0, fx1, fy1))
            page.notes.append(
                Note(block_title(key, c.purposes.get(key, "")), r4(fx0 + GRID), r4(fy0 + GRID))
            )
            y = fy1 + BLOCK_GAP_Y
        x = x + width + (gaps[ci] if ci < len(gaps) else 0.0)

    stats = vars(connect_labels(page))
    supply = {n.name for n in c.design.nets if n.external_supply}
    add_supply_flags(page, supply, ctxs[0].flag_def)
    for paper in SINGLE_PAGE_PAPERS:
        usable_w, usable_h = usable_area(paper)
        if fits(page, usable_w, usable_h):
            center(page, usable_w, usable_h)
            return Drawing(page, paper, used_libs(page, ctxs), stats)
    return None


# ---------------------------------------------------------------- 分页：根页上的框图


def _block_columns(c: _Ctx, ports: dict[str, dict[str, str]]) -> list[list[str]]:
    """和页内一样按信号流向分列：从有左边板边连接器的块出发（没有就从第一块），沿共用的信号一列列往右。"""
    sources, right = _edge_blocks(c)
    columns = flow_columns(
        c.order, sources, right, lambda a, b: bool(set(ports[a]) & set(ports[b]))
    )
    return _order_blocks(columns, {k: set(v) for k, v in ports.items()})


def _pin_sides(
    key: str, ports: dict[str, dict[str, str]], column_of: dict[str, int]
) -> dict[str, str]:
    sides = {}
    for net, shape in ports[key].items():
        others = [column_of[k] for k in ports if k != key and net in ports[k]]
        earlier = sum(1 for col in others if col < column_of[key])
        later = sum(1 for col in others if col > column_of[key])
        if earlier != later:
            sides[net] = "left" if earlier > later else "right"
        else:
            sides[net] = "left" if shape == "input" else "right"
    return sides


def name_w(nets: list[str]) -> float:
    """一组网络名里最长那个的文字宽度。"""
    return max((len(display_name(n)) for n in nets), default=0) * CHAR_W


def _providers(c: _Ctx, net: Net) -> set[str]:
    """哪几块把这个电源供给别的块。依次看：有电源输出脚接在上面的块；板外送电进来时，
    板边连接器所在的块；再没有，任何有连接器接在上面的块。只在别的块也用到这个电源时才算。"""
    users = {c.page_of[r] for r, _ in net.nodes}
    if len(users) < 2 or infer_kind(net.name) == "ground":
        return set()
    out = {c.page_of[r] for r, p in net.nodes if _etype(c.syms, r, p) == "power_out"}
    if not out and net.external_supply:
        # 板左边的连接器算电进来的地方（信号和电都按从左往右画）；overcurrent_cutoff 的 VBAT
        # 从左边的电池端子进、从右边的负载端子出，只有电池那一块算供出
        for side in ("left", "right"):
            edges = {e.ref for e in c.design.board.edge_connectors if e.side == side}
            out = {c.page_of[r] for r, _ in net.nodes if r in edges}
            if out:
                break
        if not out:
            out = {c.page_of[r] for r, _ in net.nodes if c.specs[r].role == "connector"}
    return out


def _rails(c: _Ctx) -> dict[str, list[tuple[str, bool]]]:
    """每个功能块用到的电源：(网络名, 是不是由这一块供给别的块)，见 _providers。"""
    out: dict[str, list[tuple[str, bool]]] = {k: [] for k in c.order}
    for net in c.design.nets:
        if net_kind(net, c.syms) == "signal":
            continue
        providers = _providers(c, net)
        for key in c.order:
            if any(c.page_of[r] == key for r, _ in net.nodes):
                out[key].append((net.name, key in providers))
    for key in out:
        out[key].sort(key=lambda t: (not t[1], infer_kind(t[0]) == "ground", t[0]))
    return out


def rails_text(rails: list[tuple[str, bool]]) -> str:
    if not rails:
        return ""
    return "电源：" + "、".join(display_name(n) + ("（出）" if out else "") for n, out in rails)


def _root_sheet(c: _Ctx, ports: dict[str, dict[str, str]]) -> tuple[Drawing, RootExtras]:
    columns = _block_columns(c, ports)
    column_of = {k: i for i, col in enumerate(columns) for k in col}
    rails = _rails(c)
    pitch = GRID * 2
    geometry: dict[str, tuple[float, float, float, float, dict[str, str]]] = {}
    for key in c.order:
        sides = _pin_sides(key, ports, column_of)
        left = [n for n, s in sides.items() if s == "left"]
        right = [n for n, s in sides.items() if s == "right"]
        width = max(
            GRID * 28,
            ceil_grid(name_w(left) + name_w(right) + GRID * 8),
            ceil_grid(len(key) * CHAR_W + GRID * 4),
        )
        height = max(GRID * 12, pitch * (max(len(left), len(right)) + 1))
        left_ext = name_w(left) + STUB + GRID * 2 if left else 0.0
        right_ext = name_w(right) + STUB + GRID * 2 if right else 0.0
        geometry[key] = (width, height, left_ext, right_ext, sides)

    row_of = {k: i for col in columns for i, k in enumerate(col)}

    def pin_order(key: str, side: str, net: str) -> tuple[int, int, str]:
        """框上一侧的引脚按对面那一块的位置排：连到同一块的排在一起，块靠上的排前面；
        同一块里按名字排。两块都这么排，相连的引脚就在同一高度，导线能拉直。"""
        here = column_of[key]
        partners = [
            (abs(column_of[k] - here), row_of[k])
            for k in c.order
            if k != key
            and net in ports[k]
            and ((column_of[k] > here) if side == "right" else (column_of[k] < here))
        ]
        near = min(partners, default=(99, 99))
        return near[0], near[1], display_name(net)

    prims = Prims()
    extras = RootExtras()
    x = PAGE_MARGIN
    page_no = {key: str(i + 2) for i, key in enumerate(c.order)}
    for col in columns:
        left_ext = max(geometry[k][2] for k in col)
        right_ext = max(geometry[k][3] for k in col)
        # 框下面两行字（这一块做什么、用到哪些电源）可能比框还宽，这一列要留够
        width = max(
            max(
                geometry[k][0] + right_ext,
                text_width(c.purposes.get(k, "")),
                text_width(rails_text(rails[k])),
            )
            for k in col
        )
        sx = snap(x + left_ext)
        y = PAGE_MARGIN + GRID * 4
        for key in col:
            w, h, _, _, sides = geometry[key]
            sy = snap(y)
            pins = []
            for side in ("left", "right"):
                ordered = [(pin_order(key, side, n), n) for n, s in sides.items() if s == side]
                nets = [n for _, n in sorted(ordered)]
                for i, net in enumerate(nets):
                    py = round(sy + pitch * (i + 1), 4)
                    text = display_name(net)
                    pins.append(SheetPin(text, ports[key][net], side, py))
                    if side == "left":
                        prims.add_wire((sx, py), (sx - STUB, py))
                        prims.labels.append(Label(text, round(sx - STUB, 4), py, 180))
                    else:
                        prims.add_wire((sx + w, py), (sx + w + STUB, py))
                        prims.labels.append(Label(text, round(sx + w + STUB, 4), py, 0))
            extras.sheets.append(
                SheetSym(key, f"{key}.kicad_sch", sx, sy, w, h, page_no[key], tuple(pins))
            )
            line_y = sy + h + GRID * 3
            for text in (c.purposes.get(key, ""), rails_text(rails[key])):
                if text:
                    prims.notes.append(Note(text, sx, round(line_y, 4)))
                    line_y += GRID * 2
            y = sy + h + SHEET_GAP_Y
        x = sx + width + SHEET_GAP_X
    boxes = [(s.x, s.y, s.x + s.w, s.y + s.h) for s in extras.sheets]
    stats = vars(connect_labels(prims, obstacles=boxes, keep_name=False))
    _power_arrows(c, prims, extras, rails)
    right_edge = max(b[2] for b in prims.boxes() + boxes)
    bottom = max(b[3] for b in prims.boxes() + boxes) + GRID * 2
    for paper, (pw, ph) in PAPERS.items():
        if right_edge <= pw - PAGE_MARGIN and bottom <= ph - TITLE_RESERVE:
            return Drawing(prims, paper, {}, stats), extras
    raise ValueError("根页的框图在 A2 上也放不下，功能块太多了")


def _ring(box: tuple[float, float, float, float], gap: float) -> set[tuple[int, int]]:
    """框外一圈（离框 gap 毫米）上的网格点：箭头从这里出发、在这里结束。"""
    x0, y0 = math.floor((box[0] - gap) / GRID), math.floor((box[1] - gap) / GRID)
    x1, y1 = math.ceil((box[2] + gap) / GRID), math.ceil((box[3] + gap) / GRID)
    out = {(x, y0) for x in range(x0, x1 + 1)} | {(x, y1) for x in range(x0, x1 + 1)}
    out |= {(x0, y) for y in range(y0, y1 + 1)} | {(x1, y) for y in range(y0, y1 + 1)}
    return out


def _power_arrows(
    c: _Ctx, prims: Prims, extras: RootExtras, rails: dict[str, list[tuple[str, bool]]]
) -> None:
    """电源从哪块供给哪块：每对（供出的块 → 用它的块）画一根虚线箭头，旁边写电源名。

    只是说明，不是电气连接（KiCad 的电源符号本来就全工程相通）。找不到不压东西的路就不画这一根，
    框下面那行「电源：……」照样写着。地不画。
    """
    boxes = {s.name: (s.x, s.y, s.x + s.w, s.y + s.h) for s in extras.sheets}
    pairs: dict[tuple[str, str], list[str]] = {}
    for src in c.order:
        for net, provides in rails[src]:
            if not provides or infer_kind(net) == "ground":
                continue
            for dst in c.order:
                if dst != src and any(n == net and not out for n, out in rails[dst]):
                    pairs.setdefault((src, dst), []).append(display_name(net))

    def gap(pair: tuple[str, str]) -> tuple[float, tuple[str, str]]:
        a, b = boxes[pair[0]], boxes[pair[1]]
        dx = max(0.0, b[0] - a[2], a[0] - b[2])
        dy = max(0.0, b[1] - a[3], a[1] - b[3])
        return dx + dy, pair

    used: set[tuple[int, int]] = set()
    # 先画离得近的：远的那根能绕开近的，反过来近的常被远的那根挡死（attiny85 实测）
    for (src, dst), names in sorted(pairs.items(), key=lambda kv: gap(kv[0])):
        end_ring = _ring(boxes[dst], GRID * 2)
        tx = (boxes[dst][0] + boxes[dst][2]) / 2 / GRID
        ty = (boxes[dst][1] + boxes[dst][3]) / 2 / GRID
        avoid = used | _note_nodes(prims)
        # 起点从源框外一圈上离目标最近的几个点里挑：最近的那个可能正好被别的线或字挡住
        starts = sorted(
            (n for n in _ring(boxes[src], GRID * 2) if n not in avoid),
            key=lambda n: (abs(n[0] - tx) + abs(n[1] - ty), n),
        )
        path = None
        for start in starts[:: max(1, len(starts) // 12)][:12]:
            path = route_free(prims, start, end_ring, obstacles=list(boxes.values()), avoid=avoid)
            if path is not None:
                break
        if path is None:
            continue
        pts = corners(path)
        prims.lines.append(Line(tuple((r4(n[0] * GRID), r4(n[1] * GRID)) for n in pts), True))
        used |= set(path) | {(n[0] + dx, n[1] + dy) for n in path for dx, dy in _AROUND}
        _label_arrow(prims, pts, "、".join(names))


_AROUND = ((1, 0), (-1, 0), (0, 1), (0, -1))


def _note_nodes(prims: Prims) -> set[tuple[int, int]]:
    out: set[tuple[int, int]] = set()
    for n in prims.notes:
        out.update(box_nodes(n.box, 0.3))
    for ln in prims.lines:
        for (ax, ay), (bx, by) in zip(ln.pts, ln.pts[1:], strict=False):
            out.update(box_nodes((min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)), 0.7))
    return out


def _label_arrow(prims: Prims, pts: list[tuple[int, int]], text: str) -> None:
    """箭头最长的一段横线上方写电源名；没有够长的横线就写在最长那段竖线的右边。"""
    segs = list(zip(pts, pts[1:], strict=False))
    if not segs:
        return
    (a, b) = max(segs, key=lambda s: abs(s[0][0] - s[1][0]) + abs(s[0][1] - s[1][1]))
    if a[1] == b[1]:
        x = min(a[0], b[0]) * GRID + GRID
        y = a[1] * GRID - GRID * 2
    else:
        x = a[0] * GRID + GRID
        y = (a[1] + b[1]) / 2 * GRID - GRID / 2
    prims.notes.append(Note(text, r4(x), r4(y)))


# ---------------------------------------------------------------- 写文件


def write_project(project: SchematicProject, out_dir: Path, *, title: str, date: str) -> Path:
    """写出全部 .kicad_sch，返回根页的路径。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    counter = Counter()
    root_path = out_dir / project.root_file
    if project.root is None:
        text = render_page(
            project.pages[0].drawing, project=project.name, title=title, date=date, counter=counter
        )
        root_path.write_text(text, encoding="utf-8")
        return root_path
    root_text = render_page(
        project.root,
        project=project.name,
        title=title,
        date=date,
        counter=counter,
        extras=project.extras,
    )
    root_path.write_text(root_text, encoding="utf-8")
    for page in project.pages:
        text = render_page(
            page.drawing,
            project=project.name,
            title=f"{title} / {page.key}",
            date=date,
            page=page.key,
            counter=counter,
            comment=page.purpose,
        )
        (out_dir / page.file).write_text(text, encoding="utf-8")
    return root_path
