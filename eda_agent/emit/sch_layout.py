"""原理图版面引擎（M0 样张版）：一个功能块里的元件和网络 → 有坐标的图元。

做法叫「主芯片 + 引脚链」：
- 每个主芯片（引脚多的器件、连接器）是一个小团。它的每个引脚向外拉一条横线（链），
  链上依次是：串联的两脚元件、挂下来或挂上去的并联元件（接电源或地）、末端的电源符号或网络标签。
- 电源和地只画电源符号，不拉长线；跨团的信号只画同名标签。
- 没有接任何东西的引脚打 NC 叉。两脚元件如果不在任何链上，自成一团（旁路电容这类）。
- 摆放全由代码按固定规则完成，同样的输入每次画出一样的图。

坐标单位 mm，原点在图纸左上，y 向下，所有点落在 1.27 mm 网格上。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from eda_agent.emit.kicad_sym import Pin, SymbolDef, load_symbol

GRID = 1.27
STUB = 2.54
JOG = 2.54
GAP = 1.27
TEXT_H = 1.27
CHAR_W = 1.05
TAP_PITCH = 10.16

Point = tuple[float, float]
Box = tuple[float, float, float, float]

PAPERS = {"A4": (297.0, 210.0), "A3": (420.0, 297.0), "A2": (594.0, 420.0)}
PAGE_MARGIN = 20.32
TITLE_RESERVE = 50.8

_GND = re.compile(r"^/?(?:[ADPSE]?GND\w*|VSS\w*|0V?)$", re.IGNORECASE)
_PWR = re.compile(
    r"^/?(?:[+-]\d\S*|V(?:CC|DD|BUS|BAT|IN|PP|EE|REF|BB|CORE|IO)\w*|\d+V\d*)$", re.IGNORECASE
)


def snap(v: float) -> float:
    return round(round(v / GRID) * GRID, 4)


def ceil_grid(v: float) -> float:
    return round(math.ceil(v / GRID - 1e-9) * GRID, 4)


def r4(v: float) -> float:
    return round(v, 4)


def display_name(net: str) -> str:
    """网表里的网络名 → 画在图上的文字（去掉图页路径前缀，还原被转义的斜杠）。"""
    return net.split("/")[-1].replace("{slash}", "/")


def infer_kind(name: str) -> str:
    short = display_name(name)
    if _GND.match(short):
        return "ground"
    if _PWR.match(short):
        return "power"
    return "signal"


# ---------------------------------------------------------------- 输入


@dataclass(frozen=True)
class PartSpec:
    ref: str
    lib_id: str
    value: str
    footprint: str = ""
    role: str = ""  # "hub" 或 "connector" 强制当主芯片；空 = 按引脚数自动


@dataclass(frozen=True)
class NetSpec:
    name: str
    pins: tuple[tuple[str, str], ...]
    kind: str = ""  # ground / power / signal；空 = 按名字推断
    external: bool = False  # 还连到这一页以外
    supply: bool = False  # 电从这一页以外或板外送进来：放一个 PWR_FLAG 告诉 ERC 这里有电源


# ---------------------------------------------------------------- 图元


@dataclass
class Field:
    name: str
    text: str
    x: float
    y: float
    justify: str = ""
    hide: bool = False


@dataclass
class SymbolInst:
    ref: str
    lib_id: str
    value: str
    footprint: str
    x: float
    y: float
    rot: int
    mirror: bool
    fields: list[Field]
    box: Box  # 本体加引脚的外框
    core: Box | None  # 只有本体
    pins: dict[str, Point]
    prefix: str = ""  # 电源符号 "#PWR"、电源标记 "#FLG"；普通元件为空
    net: str = ""  # 电源符号所属的网络
    pin_dirs: dict[str, Point] = field(default_factory=dict)  # 每个引脚「往外」的单位方向

    @property
    def power(self) -> bool:
        return bool(self.prefix)

    def text_boxes(self) -> list[Box]:
        return [text_box(f.x, f.y, f.text, f.justify) for f in self.fields if not f.hide]

    def translated(self, dx: float, dy: float) -> SymbolInst:
        return replace(
            self,
            x=r4(self.x + dx),
            y=r4(self.y + dy),
            fields=[replace(f, x=r4(f.x + dx), y=r4(f.y + dy)) for f in self.fields],
            box=_shift(self.box, dx, dy),
            core=_shift(self.core, dx, dy) if self.core else None,
            pins={k: (r4(x + dx), r4(y + dy)) for k, (x, y) in self.pins.items()},
        )


@dataclass(frozen=True)
class Wire:
    a: Point
    b: Point


@dataclass(frozen=True)
class Label:
    text: str
    x: float
    y: float
    rot: int

    @property
    def box(self) -> Box:
        w = max(len(self.text), 1) * CHAR_W
        if self.rot == 180:
            return (self.x - w, self.y - TEXT_H - 0.2, self.x, self.y)
        return (self.x, self.y - TEXT_H - 0.2, self.x + w, self.y)


@dataclass
class Prims:
    symbols: list[SymbolInst] = field(default_factory=list)
    wires: list[Wire] = field(default_factory=list)
    junctions: list[Point] = field(default_factory=list)
    labels: list[Label] = field(default_factory=list)
    ncs: list[Point] = field(default_factory=list)

    def add_wire(self, a: Point, b: Point) -> None:
        a, b = (r4(a[0]), r4(a[1])), (r4(b[0]), r4(b[1]))
        if a != b:
            self.wires.append(Wire(a, b))

    def extend(self, other: Prims, dx: float = 0.0, dy: float = 0.0) -> None:
        self.symbols += [s.translated(dx, dy) for s in other.symbols]
        self.wires += [
            Wire((r4(w.a[0] + dx), r4(w.a[1] + dy)), (r4(w.b[0] + dx), r4(w.b[1] + dy)))
            for w in other.wires
        ]
        self.junctions += [(r4(x + dx), r4(y + dy)) for x, y in other.junctions]
        self.labels += [replace(lb, x=r4(lb.x + dx), y=r4(lb.y + dy)) for lb in other.labels]
        self.ncs += [(r4(x + dx), r4(y + dy)) for x, y in other.ncs]

    def boxes(self) -> list[Box]:
        out: list[Box] = []
        for s in self.symbols:
            out.append(s.box)
            out += s.text_boxes()
        out += [lb.box for lb in self.labels]
        out += [
            (min(w.a[0], w.b[0]), min(w.a[1], w.b[1]), max(w.a[0], w.b[0]), max(w.a[1], w.b[1]))
            for w in self.wires
        ]
        out += [(x - 0.9, y - 0.9, x + 0.9, y + 0.9) for x, y in self.ncs]
        return out

    def bbox(self) -> Box:
        boxes = self.boxes()
        if not boxes:
            return (0.0, 0.0, 0.0, 0.0)
        return (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )


def _shift(box: Box, dx: float, dy: float) -> Box:
    return (r4(box[0] + dx), r4(box[1] + dy), r4(box[2] + dx), r4(box[3] + dy))


def text_box(x: float, y: float, text: str, justify: str = "") -> Box:
    w = max(len(text), 1) * CHAR_W
    x0 = x if justify == "left" else x - w if justify == "right" else x - w / 2
    return (x0, y - TEXT_H / 2 - 0.1, x0 + w, y + TEXT_H / 2 + 0.1)


# ---------------------------------------------------------------- 几何


def rotate(px: float, py: float, rot: int) -> Point:
    """库坐标（y 向上）的偏移 → 原理图坐标（y 向下）的偏移；rot 是 KiCad 里逆时针的角度。"""
    if rot == 0:
        return px, -py
    if rot == 90:
        return -py, -px
    if rot == 180:
        return -px, py
    if rot == 270:
        return py, px
    raise ValueError(f"不支持的角度 {rot}")


def place_pin(pin: Pin, x: float, y: float, rot: int, mirror: bool) -> Point:
    dx, dy = rotate(pin.x, pin.y, rot)
    if mirror:
        dx = -dx
    return r4(x + dx), r4(y + dy)


_OUT: dict[str, Point] = {
    "left": (-1.0, 0.0),
    "right": (1.0, 0.0),
    "top": (0.0, -1.0),
    "bottom": (0.0, 1.0),
}


def pin_side(pin: Pin, rot: int, mirror: bool) -> str:
    a = math.radians(pin.angle)
    dx, dy = rotate(-math.cos(a), -math.sin(a), rot)
    if mirror:
        dx = -dx
    if abs(dx) >= abs(dy):
        return "right" if dx > 0 else "left"
    return "bottom" if dy > 0 else "top"


def _box_of(
    rect: tuple[float, float, float, float], x: float, y: float, rot: int, mirror: bool
) -> Box:
    pts = [rotate(cx, cy, rot) for cx in (rect[0], rect[2]) for cy in (rect[1], rect[3])]
    if mirror:
        pts = [(-a, b) for a, b in pts]
    xs, ys = [x + a for a, _ in pts], [y + b for _, b in pts]
    return (r4(min(xs)), r4(min(ys)), r4(max(xs)), r4(max(ys)))


def orient_pins(sdef: SymbolDef, entry: Pin, exit_: Pin, vec: Point) -> int:
    """找一个旋转角，让「入口脚 → 出口脚」的方向等于 vec（水平或竖直）。"""
    for rot in (0, 90, 180, 270):
        ex, ey = rotate(entry.x, entry.y, rot)
        xx, xy = rotate(exit_.x, exit_.y, rot)
        dx, dy = xx - ex, xy - ey
        if abs(dx * vec[1] - dy * vec[0]) < 1e-6 and dx * vec[0] + dy * vec[1] > 0:
            return rot
    raise ValueError(f"{sdef.lib_id} 的两个引脚转不成方向 {vec}")


def make_symbol(
    ref: str,
    sdef: SymbolDef,
    value: str,
    footprint: str,
    x: float,
    y: float,
    rot: int = 0,
    mirror: bool = False,
    fields: list[Field] | None = None,
    prefix: str = "",
    net: str = "",
) -> SymbolInst:
    return SymbolInst(
        ref=ref,
        lib_id=sdef.lib_id,
        value=value,
        footprint=footprint,
        x=r4(x),
        y=r4(y),
        rot=rot,
        mirror=mirror,
        fields=fields or [],
        box=_box_of(sdef.bbox, x, y, rot, mirror),
        core=_box_of(sdef.body, x, y, rot, mirror) if sdef.body else None,
        pins={p.number: place_pin(p, x, y, rot, mirror) for p in sdef.pins},
        prefix=prefix,
        net=net,
        pin_dirs={p.number: _OUT[pin_side(p, rot, mirror)] for p in sdef.pins},
    )


# ---------------------------------------------------------------- 链


@dataclass
class Shunt:
    ref: str
    entry: str
    exit: str
    rail: str


@dataclass
class Tap:
    shunts: list[Shunt]


@dataclass
class Series:
    ref: str
    entry: str
    exit: str


@dataclass
class End:
    kind: str  # power / label / none / nc
    net: str = ""


@dataclass
class Chain:
    hub: str
    pin: str
    steps: list[Tap | Series]
    end: End


@dataclass
class HubPlan:
    ref: str
    rot: int
    mirror: bool
    chains: list[Chain]


@dataclass
class Ctx:
    parts: dict[str, PartSpec]
    syms: dict[str, SymbolDef]
    net_of: dict[tuple[str, str], str]
    kinds: dict[str, str]
    members: dict[str, list[tuple[str, str]]]
    external: set[str]
    supply: set[str]
    power_defs: dict[str, SymbolDef]
    flag_def: SymbolDef
    passive: set[str] = field(default_factory=set)

    def kind(self, net: str) -> str:
        return self.kinds.get(net, "signal")

    def other_pin(self, ref: str, number: str) -> Pin:
        a, b = [p for p in self.syms[ref].pins if not p.hidden]
        return b if a.number == number else a


def build_ctx(parts: list[PartSpec], nets: list[NetSpec], lib_dirs: tuple[Path, ...]) -> Ctx:
    syms = {p.ref: load_symbol(p.lib_id, lib_dirs) for p in parts}
    net_of: dict[tuple[str, str], str] = {}
    members: dict[str, list[tuple[str, str]]] = {}
    kinds: dict[str, str] = {}
    external: set[str] = set()
    supply: set[str] = set()
    for net in nets:
        kinds[net.name] = net.kind or infer_kind(net.name)
        members[net.name] = list(net.pins)
        if net.external:
            external.add(net.name)
        if net.supply:
            supply.add(net.name)
        for ref, number in net.pins:
            net_of[(ref, number)] = net.name
    power_defs = {
        "ground": load_symbol("power:GND", lib_dirs),
        "power": load_symbol("power:VCC", lib_dirs),
    }
    flag_def = load_symbol("power:PWR_FLAG", lib_dirs)
    ctx = Ctx(
        {p.ref: p for p in parts},
        syms,
        net_of,
        kinds,
        members,
        external,
        supply,
        power_defs,
        flag_def,
    )
    for p in parts:
        sdef = syms[p.ref]
        visible = [q for q in sdef.pins if not q.hidden]
        if p.role not in ("hub", "connector") and len(visible) == 2 and sdef.two_pin_axis:
            ctx.passive.add(p.ref)
    return ctx


def build_chain(ctx: Ctx, hub: str, pin: Pin, claimed: set[str], *, steps_ok: bool = True) -> Chain:
    net = ctx.net_of.get((hub, pin.number))
    chain = Chain(hub, pin.number, [], End("nc"))
    if net is None:
        return chain
    if ctx.kind(net) != "signal":
        chain.end = End("power", net)
        return chain
    entry = (hub, pin.number)
    cur = net
    while True:
        shunts: list[Shunt] = []
        series: list[Series] = []
        rest = 0
        for ref, number in ctx.members[cur]:
            if (ref, number) == entry:
                continue
            if not steps_ok or ref not in ctx.passive or ref in claimed:
                rest += 1
                continue
            other = ctx.other_pin(ref, number)
            onet = ctx.net_of.get((ref, other.number))
            if onet is None or onet == cur:
                rest += 1
            elif ctx.kind(onet) != "signal":
                shunts.append(Shunt(ref, number, other.number, onet))
            else:
                series.append(Series(ref, number, other.number))
        if shunts:
            claimed.update(s.ref for s in shunts)
            chain.steps.append(Tap(shunts))
        outside = cur in ctx.external
        if len(series) == 1 and not rest and not outside:
            step = series[0]
            claimed.add(step.ref)
            chain.steps.append(step)
            entry = (step.ref, step.exit)
            nxt = ctx.net_of[(step.ref, step.exit)]
            if ctx.kind(nxt) != "signal":
                chain.end = End("power", nxt)
                return chain
            cur = nxt
            continue
        if not series and not rest and not outside:
            chain.end = End("none") if chain.steps else End("nc")
        else:
            chain.end = End("label", cur)
        return chain


# ---------------------------------------------------------------- 画一条链


def _power_symbol(ctx: Ctx, net: str, x: float, y: float, rot: int = 0) -> SymbolInst:
    kind = ctx.kind(net)
    sdef = ctx.power_defs[kind]
    vx, vy = sdef.prop_pos.get("Value", (0.0, -3.81 if kind == "ground" else 3.81))
    dx, dy = rotate(vx, vy, rot)
    text = display_name(net)
    fields = [
        Field("Reference", "#PWR", x, y, hide=True),
        Field("Value", text, r4(x + dx), r4(y + dy)),
    ]
    return make_symbol("#PWR", sdef, text, "", x, y, rot, False, fields, prefix="#PWR", net=net)


def _passive_fields(
    ref: str, value: str, cx: float, cy: float, core: Box | None, horizontal: bool, outward: int
) -> list[Field]:
    if horizontal:
        half = max(abs(core[1] - cy), abs(core[3] - cy)) if core else 1.0
        return [
            Field("Reference", ref, cx, r4(cy - half - 1.4)),
            Field("Value", value, cx, r4(cy + half + 1.4)),
        ]
    half = max(abs(core[0] - cx), abs(core[2] - cx)) if core else 1.0
    # 字都居中写：KiCad 会按符号朝向翻转靠左/靠右，居中最不会出意外。
    out = []
    for name, text, dy in (("Reference", ref, -1.27), ("Value", value, 1.27)):
        tx = r4(cx + outward * (half + 1.0 + len(text) * CHAR_W / 2))
        out.append(Field(name, text, tx, r4(cy + dy)))
    return out


def _place_two_pin(
    ctx: Ctx, p: Prims, ref: str, entry: str, exit_: str, at: Point, vec: Point, outward: int
) -> tuple[SymbolInst, float]:
    """摆一个两脚元件：入口脚在 at，出口脚在 at + vec * 脚距。返回符号和脚距。"""
    sdef, part = ctx.syms[ref], ctx.parts[ref]
    e, x = sdef.pin(entry), sdef.pin(exit_)
    rot = orient_pins(sdef, e, x, vec)
    ex, ey = rotate(e.x, e.y, rot)
    xx, xy = rotate(x.x, x.y, rot)
    span = abs(xx - ex) + abs(xy - ey)
    ox, oy = r4(at[0] - ex), r4(at[1] - ey)
    cx, cy = r4(at[0] + vec[0] * span / 2), r4(at[1] + vec[1] * span / 2)
    probe = make_symbol(ref, sdef, part.value, part.footprint, ox, oy, rot)
    fields = _passive_fields(ref, part.value, cx, cy, probe.core, vec[1] == 0, outward)
    sym = make_symbol(ref, sdef, part.value, part.footprint, ox, oy, rot, False, fields)
    p.symbols.append(sym)
    return sym, span


def _draw_shunt(ctx: Ctx, p: Prims, sh: Shunt, x: float, d: int) -> float:
    s = 1 if ctx.kind(sh.rail) == "ground" else -1
    y_entry = s * STUB
    p.add_wire((x, 0.0), (x, y_entry))
    sym, span = _place_two_pin(ctx, p, sh.ref, sh.entry, sh.exit, (x, y_entry), (0.0, float(s)), d)
    p.symbols.append(_power_symbol(ctx, sh.rail, x, y_entry + s * span))
    return max(abs(f.x - x) + len(f.text) * CHAR_W for f in sym.fields if not f.hide)


def render_lane(ctx: Ctx, chain: Chain, d: int) -> Prims:
    """把一条链画成横线，起点在 (0, 0)，d = +1 向右、-1 向左。"""
    p = Prims()
    u = 0.0
    last = 0.0

    def wire_to(nu: float) -> None:
        nonlocal last
        if nu > last + 1e-6:
            p.add_wire((d * last, 0.0), (d * nu, 0.0))
            last = nu

    for i, step in enumerate(chain.steps):
        more = i + 1 < len(chain.steps) or chain.end.kind in ("power", "label")
        if isinstance(step, Tap):
            width = 0.0
            for k, sh in enumerate(step.shunts):
                if k > 0:
                    u += max(TAP_PITCH, ceil_grid(width + STUB))
                else:
                    # 串联元件的数值文字在它下面，紧跟着挂下来的并联元件要留出一段导线
                    after_series = i > 0 and isinstance(chain.steps[i - 1], Series)
                    u = max(u + (STUB if after_series else 0.0), last, STUB)
                wire_to(u)
                if more or k < len(step.shunts) - 1:
                    p.junctions.append((r4(d * u), 0.0))
                width = _draw_shunt(ctx, p, sh, r4(d * u), d)
            if more:
                u += ceil_grid(max(width, STUB))
        else:
            u += STUB
            wire_to(u)
            _, span = _place_two_pin(
                ctx, p, step.ref, step.entry, step.exit, (r4(d * u), 0.0), (float(d), 0.0), d
            )
            u += span
            last = u
    if chain.end.kind == "power":
        u += STUB
        wire_to(u)
        s = 1 if ctx.kind(chain.end.net) == "ground" else -1
        p.add_wire((d * u, 0.0), (d * u, s * STUB))
        p.symbols.append(_power_symbol(ctx, chain.end.net, r4(d * u), s * STUB))
    elif chain.end.kind == "label":
        u += STUB
        wire_to(u)
        p.labels.append(Label(display_name(chain.end.net), r4(d * u), 0.0, 0 if d > 0 else 180))
    return p


# ---------------------------------------------------------------- 一个主芯片和它的链


def _hub_fields(sdef: SymbolDef, ref: str, value: str, rot: int, mirror: bool) -> list[Field]:
    out = []
    for name, text in (("Reference", ref), ("Value", value)):
        px, py = sdef.prop_pos.get(name, (0.0, 0.0))
        dx, dy = rotate(px, py, rot)
        out.append(Field(name, text, r4(-dx if mirror else dx), r4(dy)))
    return out


def layout_hub(ctx: Ctx, plan: HubPlan) -> Prims:
    sdef, part = ctx.syms[plan.ref], ctx.parts[plan.ref]
    if plan.ref in ctx.passive:
        core = make_symbol(plan.ref, sdef, part.value, "", 0.0, 0.0, plan.rot).core
        fields = _passive_fields(plan.ref, part.value, 0.0, 0.0, core, False, 1)
    else:
        fields = _hub_fields(sdef, plan.ref, part.value, plan.rot, plan.mirror)
    hub = make_symbol(
        plan.ref, sdef, part.value, part.footprint, 0.0, 0.0, plan.rot, plan.mirror, fields
    )
    p = Prims(symbols=[hub])
    groups: dict[str, list[tuple[Chain, float, float]]] = {
        "left": [],
        "right": [],
        "top": [],
        "bottom": [],
    }
    for ch in plan.chains:
        pin = sdef.pin(ch.pin)
        px, py = place_pin(pin, 0.0, 0.0, plan.rot, plan.mirror)
        if ch.end.kind == "nc":
            p.ncs.append((px, py))
        else:
            groups[pin_side(pin, plan.rot, plan.mirror)].append((ch, px, py))
    _lay_horizontal(ctx, p, groups["left"], -1)
    _lay_horizontal(ctx, p, groups["right"], 1)
    _lay_vertical(ctx, p, groups["top"], -1, hub.box)
    _lay_vertical(ctx, p, groups["bottom"], 1, hub.box)
    return p


@dataclass
class _Unit:
    """一侧引脚排版时的一个单位：一条链，或者一组合用同一个电源符号的相邻引脚（总线）。"""

    ys: list[float]  # 它包含的引脚 y，从上到下
    px: float
    anchor: float  # 竖直排版时的参考 y
    up: float  # 从参考 y 往上占多高
    down: float
    lane: Prims | None = None  # 链：画好的横线（起点在 (0,0)）
    chain: Chain | None = None
    bus_net: str = ""
    pinned: bool = False  # 总线不能挪，否则它的几个引脚就接不上了


def _isotonic(z: list[float], w: list[float]) -> list[float]:
    """加权保序回归（相邻违规者合并）：求最接近 z 的不降序列。"""
    blocks: list[list[float]] = []  # [权重和 * 均值, 权重和, 个数]
    for zi, wi in zip(z, w, strict=True):
        blocks.append([zi * wi, wi, 1.0])
        while (
            len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1] + 1e-9
        ):
            top = blocks.pop()
            blocks[-1] = [blocks[-1][0] + top[0], blocks[-1][1] + top[1], blocks[-1][2] + top[2]]
    out: list[float] = []
    for wz, wt, n in blocks:
        out += [wz / wt] * int(n)
    return out


def _bus_unit(ctx: Ctx, run: list[tuple[Chain, float, float]], d: int) -> _Unit:
    net = run[0][0].end.net
    s = 1 if ctx.kind(net) == "ground" else -1
    probe = Prims(symbols=[_power_symbol(ctx, net, 0.0, s * STUB)])
    box = probe.bbox()
    ys = [t[2] for t in run]
    span = ys[-1] - ys[0]
    if s > 0:  # 符号挂在最下面的引脚下面
        return _Unit(ys, run[0][1], ys[-1], span + 0.2, box[3], bus_net=net, pinned=True)
    return _Unit(ys, run[0][1], ys[0], -box[1], span + 0.2, bus_net=net, pinned=True)


def _units(ctx: Ctx, items: list[tuple[Chain, float, float]], d: int, merge: bool) -> list[_Unit]:
    units: list[_Unit] = []
    i = 0
    while i < len(items):
        ch, px, py = items[i]
        run = [items[i]]
        if merge and ch.end.kind == "power" and not ch.steps:
            for nxt in items[i + 1 :]:
                same = nxt[0].end.kind == "power" and not nxt[0].steps
                if (
                    same
                    and nxt[0].end.net == ch.end.net
                    and abs(nxt[1] - px) < 1e-6
                    and nxt[2] - run[-1][2] <= 2 * GRID * 2 + 1e-6
                ):
                    run.append(nxt)
                else:
                    break
        if len(run) > 1:
            units.append(_bus_unit(ctx, run, d))
        else:
            lane = render_lane(ctx, ch, d)
            b = lane.bbox()
            units.append(_Unit([py], px, py, -b[1], b[3], lane=lane, chain=ch))
        i += len(run)
    return units


def _stack(units: list[_Unit]) -> list[float] | None:
    """每个单位的竖直位置：互相不压住，并且离各自引脚尽量近（加权最小二乘）。"""
    n = len(units)
    sep = [0.0] + [ceil_grid(units[k - 1].down + units[k].up + GAP) for k in range(1, n)]
    cum = [sum(sep[: k + 1]) for k in range(n)]
    z = [units[k].anchor - cum[k] for k in range(n)]
    w = [1e6 if units[k].pinned else 1.0 for k in range(n)]
    base = _isotonic(z, w)
    v = [snap(base[k] + cum[k]) for k in range(n)]
    for k in range(1, n):
        v[k] = max(v[k], r4(v[k - 1] + sep[k]))
    if any(u.pinned and abs(v[k] - u.anchor) > 1e-3 for k, u in enumerate(units)):
        return None
    return v


def _draw_bus(ctx: Ctx, p: Prims, unit: _Unit, d: int) -> None:
    xb = r4(unit.px + d * STUB)
    for y in unit.ys:
        p.add_wire((unit.px, y), (xb, y))
    first, last = unit.ys[0], unit.ys[-1]
    p.add_wire((xb, first), (xb, last))
    s = 1 if ctx.kind(unit.bus_net) == "ground" else -1
    end_y = last if s > 0 else first
    p.add_wire((xb, end_y), (xb, r4(end_y + s * STUB)))
    p.junctions += [(xb, y) for y in unit.ys[1:-1]] + [(xb, end_y)]
    p.symbols.append(_power_symbol(ctx, unit.bus_net, xb, r4(end_y + s * STUB)))


def _lay_horizontal(ctx: Ctx, p: Prims, items: list[tuple[Chain, float, float]], d: int) -> None:
    if not items:
        return
    items = sorted(items, key=lambda t: t[2])
    units = _units(ctx, items, d, merge=True)
    v = _stack(units)
    if v is None:  # 合并后挪不开，退回一根一根地排
        units = _units(ctx, items, d, merge=False)
        v = _stack(units) or [u.anchor for u in units]
    lanes = [k for k, u in enumerate(units) if u.lane is not None]
    disp = {k: v[k] - units[k].anchor for k in lanes}
    # 拐弯的竖线放在哪一列，决定了会不会和别的线交叉。往下挪的线里，最下面的离芯片最近；
    # 往上挪的线里，最上面的离芯片最近。按位置排，而不是按挪动的距离排。
    col: dict[int, int] = {}
    ups = [k for k in lanes if disp[k] < -1e-6]
    downs = [k for k in lanes if disp[k] > 1e-6][::-1]
    for group in (ups, downs):
        for rank, k in enumerate(group):
            col[k] = rank + 1
    n_cols = max(col.values(), default=0)
    edge = max(u.px for u in units) if d > 0 else min(u.px for u in units)
    x0 = r4(edge + d * JOG * (n_cols + 1))
    for k, unit in enumerate(units):
        if unit.lane is None:
            _draw_bus(ctx, p, unit, d)
            continue
        pts = [(unit.px, unit.anchor)]
        if k in col:
            xc = r4(unit.px + d * JOG * col[k])
            pts += [(xc, unit.anchor), (xc, v[k])]
        pts += [(x0, v[k])]
        for a, b in zip(pts, pts[1:], strict=False):
            p.add_wire(a, b)
        p.extend(unit.lane, x0, v[k])


def _lay_vertical(
    ctx: Ctx, p: Prims, items: list[tuple[Chain, float, float]], s: int, hub_box: Box
) -> None:
    if not items:
        return
    straight = [t for t in items if t[0].end.kind == "power" and not t[0].steps]
    turning = [t for t in items if t not in straight]
    straight.sort(key=lambda t: t[1])
    # 紧挨着、接同一个电源的引脚合成一个符号
    groups: list[list[tuple[Chain, float, float]]] = []
    for t in straight:
        if (
            groups
            and groups[-1][-1][0].end.net == t[0].end.net
            and t[1] - groups[-1][-1][1] <= 5.08 + 1e-6
        ):
            groups[-1].append(t)
        else:
            groups.append([t])
    for g in groups:
        ends = [(px, r4(py + s * STUB)) for _, px, py in g]
        for (_, px, py), end in zip(g, ends, strict=True):
            p.add_wire((px, py), end)
        if len(g) > 1:
            p.add_wire(ends[0], ends[-1])
            p.junctions += ends[1:-1]
        mid = ends[
            min(
                range(len(ends)),
                key=lambda k: abs(ends[k][0] - sum(e[0] for e in ends) / len(ends)),
            )
        ]
        kind = ctx.kind(g[0][0].end.net)
        flip = (kind == "ground" and s < 0) or (kind != "ground" and s > 0)
        p.symbols.append(_power_symbol(ctx, g[0][0].end.net, mid[0], mid[1], 180 if flip else 0))
    if not turning:
        return
    cx = (hub_box[0] + hub_box[2]) / 2
    edge = p.bbox()[3] if s > 0 else p.bbox()[1]
    for d in (1, -1):
        side = [t for t in turning if (t[1] >= cx) == (d > 0)]
        side.sort(key=lambda t: -t[1] * d)  # 离出发方向最近的先画，深度最浅
        prev_y: float | None = None
        prev_ext = 0.0
        for ch, px, py in side:
            lane = render_lane(ctx, ch, d)
            b = lane.bbox()
            up, down = -b[1], b[3]
            if prev_y is None:
                y = snap(edge + s * (GRID * 4 + (up if s > 0 else down)))
            elif s > 0:
                y = max(prev_y + ceil_grid(prev_ext + up + GAP), snap(edge + GRID * 4 + up))
            else:
                y = min(prev_y - ceil_grid(prev_ext + down + GAP), snap(edge - GRID * 4 - down))
            p.add_wire((px, py), (px, y))
            p.extend(lane, px, y)
            prev_y, prev_ext = y, (down if s > 0 else up)


# ---------------------------------------------------------------- 计划、排版


def plan_hubs(ctx: Ctx) -> tuple[list[HubPlan], set[str]]:
    claimed: set[str] = set()
    plans: list[HubPlan] = []
    for ref, part in ctx.parts.items():
        sdef = ctx.syms[ref]
        visible = [q for q in sdef.pins if not q.hidden]
        if ref in ctx.passive or sdef.is_power or not visible:
            continue
        mirror = False
        if part.role == "connector":
            connected = [q for q in visible if (ref, q.number) in ctx.net_of]
            mirror = bool(connected) and all(pin_side(q, 0, False) == "left" for q in connected)
        chains = [build_chain(ctx, ref, q, claimed) for q in visible]
        plans.append(HubPlan(ref, 0, mirror, chains))
    return plans, claimed


def plan_free(ctx: Ctx, ref: str) -> HubPlan:
    """不在任何链上的两脚元件：竖着放，旁边的网络靠符号或标签。"""
    sdef = ctx.syms[ref]
    a, b = [q for q in sdef.pins if not q.hidden]
    rank = {"power": 0, "signal": 1, "ground": 2}

    def key(q: Pin) -> int:
        net = ctx.net_of.get((ref, q.number))
        return rank[ctx.kind(net)] if net else 1

    top, bottom = (a, b) if key(a) <= key(b) else (b, a)
    rot = orient_pins(sdef, top, bottom, (0.0, 1.0))
    dead: set[str] = set()
    chains = [build_chain(ctx, ref, q, dead, steps_ok=False) for q in (top, bottom)]
    return HubPlan(ref, rot, False, chains)


@dataclass
class Drawing:
    prims: Prims
    paper: str
    libs: dict[str, str]  # 用到的符号：库标识 → 库里的原文


def build_drawing(
    parts: list[PartSpec], nets: list[NetSpec], lib_dirs: tuple[Path, ...]
) -> Drawing:
    ctx = build_ctx(parts, nets, lib_dirs)
    plans, claimed = plan_hubs(ctx)
    clusters: list[Prims] = [layout_hub(ctx, plan) for plan in plans]
    for ref in ctx.parts:
        if ref in ctx.passive and ref not in claimed:
            clusters.append(layout_hub(ctx, plan_free(ctx, ref)))
    for paper, (w, h) in PAPERS.items():
        placed = _pack(clusters, w - 2 * PAGE_MARGIN, h - PAGE_MARGIN - TITLE_RESERVE)
        if placed is not None:
            prims = Prims()
            for cluster, ox, oy in placed:
                prims.extend(cluster, ox + PAGE_MARGIN, oy + PAGE_MARGIN)
            _add_supply_flags(ctx, prims)
            _center(prims, w - 2 * PAGE_MARGIN, h - PAGE_MARGIN - TITLE_RESERVE)
            used = {s.lib_id for s in prims.symbols}
            known = {
                d.lib_id: d for d in (*ctx.syms.values(), *ctx.power_defs.values(), ctx.flag_def)
            }
            return Drawing(prims, paper, {k: known[k].raw for k in sorted(used)})
    raise ValueError("A2 也放不下这一页，需要把功能块拆小")


def _overlaps(a: Box, b: Box) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _center(prims: Prims, usable_w: float, usable_h: float) -> None:
    """内容整体挪到可用区域正中（可用区域从页边距算起，不含标题栏）。"""
    x0, y0, x1, y1 = prims.bbox()
    dx = snap(PAGE_MARGIN + (usable_w - (x1 - x0)) / 2 - x0)
    dy = snap(PAGE_MARGIN + (usable_h - (y1 - y0)) / 2 - y0)
    moved = Prims()
    moved.extend(prims, dx, dy)
    prims.symbols, prims.wires = moved.symbols, moved.wires
    prims.junctions, prims.labels, prims.ncs = moved.junctions, moved.labels, moved.ncs


_FLAG_OFFSETS = tuple(
    sign * GRID * k for k in (4, 6, 8, 10, 12, 14, 16, 20, 24) for sign in (1, -1)
)


def _add_supply_flags(ctx: Ctx, prims: Prims) -> None:
    """每个由外面供电的电源网络放一个 PWR_FLAG。

    依次试这个网络的每个电源符号，在它旁边找一块空地，用一小段横线接上；
    空地要避开所有符号、文字、标签，也不能压到别的导线。
    """
    for net in sorted(ctx.supply):
        for anchor in (s for s in prims.symbols if s.prefix == "#PWR" and s.net == net):
            x, y = anchor.x, anchor.y
            boxes = [b for s in prims.symbols if s is not anchor for b in (s.box, *s.text_boxes())]
            boxes += [lb.box for lb in prims.labels]
            wires = [
                (min(w.a[0], w.b[0]), min(w.a[1], w.b[1]), max(w.a[0], w.b[0]), max(w.a[1], w.b[1]))
                for w in prims.wires
            ]
            for dx in _FLAG_OFFSETS:
                fx = r4(x + dx)
                fields = [
                    Field("Reference", "#FLG", fx, y, hide=True),
                    Field("Value", "PWR_FLAG", fx, y, hide=True),
                ]
                flag = make_symbol(
                    "#FLG", ctx.flag_def, "PWR_FLAG", "", fx, y, 0, False, fields,
                    prefix="#FLG", net=net,
                )  # fmt: skip
                wire_box = (min(x, fx), y - 0.05, max(x, fx), y + 0.05)
                if any(_overlaps(flag.box, b) or _overlaps(wire_box, b) for b in boxes):
                    continue
                if any(_overlaps(flag.box, w) or _overlaps(wire_box, w) for w in wires):
                    continue
                prims.add_wire((x, y), (fx, y))
                prims.symbols.append(flag)
                break
            else:
                continue
            break


def _pack(
    clusters: list[Prims], max_w: float, max_h: float
) -> list[tuple[Prims, float, float]] | None:
    x = y = row_h = 0.0
    out: list[tuple[Prims, float, float]] = []
    for c in clusters:
        x0, y0, x1, y1 = c.bbox()
        w, h = x1 - x0, y1 - y0
        if x > 0 and x + w > max_w:
            x, y, row_h = 0.0, y + row_h + 2 * GRID * 4, 0.0
        out.append((c, snap(x - x0), snap(y - y0)))
        x += w + GRID * 8
        row_h = max(row_h, h)
    if y + row_h > max_h or any(x1 - x0 > max_w for x0, _, x1, _ in (c.bbox() for c in clusters)):
        return None
    return out
