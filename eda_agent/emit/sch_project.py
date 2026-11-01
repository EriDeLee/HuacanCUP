"""design.json → KiCad 原理图工程（一页或多页），ADR-0013。

- 只有一个功能块（或都没写功能块）：一页，所有元件都在根页上。
- 多个功能块：每个功能块一页。根页是框图：每块一个图纸框，框上的引脚是这一块和别的块共用的信号，
  引脚之间用短线加同名标签连起来，框下面写这一块做什么；框按信号流向从左往右排。
  子页里，跨页的信号在链的末端画层次标签，箭头方向看驱动这个信号的引脚在哪一页：
  在本页 = output，在别的页 = input，都有 = bidirectional，都没有 = passive。
- 电源和地在各页都只画电源符号（KiCad 的电源符号本来就全工程相连），不占图纸框的引脚。
  板外送电进来的网络（external_supply）只在第一张用到它的页上放一个 PWR_FLAG（ADR-0008）。
"""

from __future__ import annotations

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
    Drawing,
    Label,
    NetSpec,
    PartSpec,
    Prims,
    build_drawing,
    ceil_grid,
    display_name,
    flow_columns,
    infer_kind,
    snap,
)
from eda_agent.emit.sch_writer import Counter, RootExtras, SheetPin, SheetSym, Text, render_page
from eda_agent.ir.models import Design, Net

UNASSIGNED = "unassigned"
DRIVERS = {"output", "power_out", "tri_state", "open_collector", "open_emitter", "bidirectional"}
SHEET_GAP_Y = GRID * 12
SHEET_GAP_X = GRID * 8


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


def build_project(design: Design, name: str, lib_dirs: tuple[Path, ...]) -> SchematicProject:
    specs = part_specs(design)
    syms = {p.ref: load_symbol(p.symbol, lib_dirs) for p in design.parts}
    all_nets = [
        NetSpec(n.name, tuple(n.nodes), kind=net_kind(n, syms), supply=n.external_supply)
        for n in design.nets
    ]
    page_of = _page_of(design)
    order = _page_order(design, page_of)
    purposes = {b.id: b.purpose for b in design.blocks}
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

    pages: list[Page] = []
    ports: dict[str, dict[str, str]] = {key: {} for key in order}  # 页 → {网络: 形状}
    for key in order:
        parts = [specs[p.ref] for p in design.parts if page_of[p.ref] == key]
        nets: list[NetSpec] = []
        for net in design.nets:
            here = [(r, p) for r, p in net.nodes if page_of[r] == key]
            if not here:
                continue
            there = [(r, p) for r, p in net.nodes if page_of[r] != key]
            kind = net_kind(net, syms)
            first_page = next(k for k in order if any(page_of[r] == k for r, _ in net.nodes))
            port = ""
            if there and kind == "signal":
                port = _shape(here, there, syms)
                ports[key][net.name] = port
            nets.append(
                NetSpec(
                    net.name,
                    tuple(here),
                    kind=kind,
                    external=bool(there),
                    supply=net.external_supply and first_page == key,
                    port=port,
                )
            )
        drawing = build_drawing(parts, nets, lib_dirs)
        pages.append(Page(key, f"{key}.kicad_sch", purposes.get(key, ""), parts, nets, drawing))
    root, extras = _root_sheet(design, order, ports, purposes)
    return SchematicProject(name, pages, root, extras, all_nets)


# ---------------------------------------------------------------- 根页：框图


def _block_columns(
    design: Design, order: list[str], ports: dict[str, dict[str, str]]
) -> list[list[str]]:
    """和页内一样按信号流向分列：从有左边板边连接器的块出发（没有就从第一块），沿共用的信号一列列往右。"""
    page_of = _page_of(design)
    left = {page_of[c.ref] for c in design.board.edge_connectors if c.side == "left"}
    right = {page_of[c.ref] for c in design.board.edge_connectors if c.side == "right"} - left
    sources = [k for k in order if k in left]
    return flow_columns(order, sources, right, lambda a, b: bool(set(ports[a]) & set(ports[b])))


def _pin_sides(
    key: str, ports: dict[str, dict[str, str]], column_of: dict[str, int]
) -> dict[str, str]:
    sides = {}
    for net, shape in ports[key].items():
        if shape == "input":
            sides[net] = "left"
        elif shape == "output":
            sides[net] = "right"
        else:
            others = [column_of[k] for k in ports if k != key and net in ports[k]]
            earlier = sum(1 for c in others if c < column_of[key])
            sides[net] = "left" if earlier * 2 > len(others) else "right"
    return sides


def name_w(nets: list[str]) -> float:
    """一组网络名里最长那个的文字宽度。"""
    return max((len(display_name(n)) for n in nets), default=0) * CHAR_W


def _root_sheet(
    design: Design, order: list[str], ports: dict[str, dict[str, str]], purposes: dict[str, str]
) -> tuple[Drawing, RootExtras]:
    columns = _block_columns(design, order, ports)
    column_of = {k: i for i, col in enumerate(columns) for k in col}
    pitch = GRID * 2
    geometry: dict[str, tuple[float, float, float, float, dict[str, str]]] = {}
    for key in order:
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

    prims = Prims()
    extras = RootExtras()
    x = PAGE_MARGIN
    page_no = {key: str(i + 2) for i, key in enumerate(order)}
    for col in columns:
        left_ext = max(geometry[k][2] for k in col)
        width = max(geometry[k][0] for k in col)
        right_ext = max(geometry[k][3] for k in col)
        sx = snap(x + left_ext)
        y = PAGE_MARGIN + GRID * 4
        for key in col:
            w, h, _, _, sides = geometry[key]
            sy = snap(y)
            pins = []
            for side in ("left", "right"):
                nets = sorted((n for n, s in sides.items() if s == side), key=display_name)
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
            if purposes.get(key):
                extras.texts.append(Text(purposes[key], sx, round(sy + h + GRID * 3, 4)))
            y = sy + h + SHEET_GAP_Y
        x = sx + width + right_ext + SHEET_GAP_X
    right_edge = x - SHEET_GAP_X
    bottom = max(s.y + s.h + GRID * 6 for s in extras.sheets)
    for paper, (pw, ph) in PAPERS.items():
        if right_edge <= pw - PAGE_MARGIN and bottom <= ph - TITLE_RESERVE:
            return Drawing(prims, paper, {}), extras
    raise ValueError("根页的框图在 A2 上也放不下，功能块太多了")


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
