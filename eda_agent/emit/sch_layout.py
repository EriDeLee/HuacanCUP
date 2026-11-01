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
from collections.abc import Callable
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
ARROW_LEN = 1.8  # 虚线箭头的实心三角：长、半宽（mm）
ARROW_HALF = 0.7

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
    # hub / connector 强制当主芯片；passive 强制当两脚元件；空 = 按引脚数自动（ADR-0013）
    role: str = ""
    anchor: str = ""  # 不在引脚链上时贴在哪个元件旁边；空 = 自动找
    edge: str = ""  # 连接器在板子哪一边（left / right）：原理图上也放在那一边


@dataclass(frozen=True)
class NetSpec:
    name: str
    pins: tuple[tuple[str, str], ...]
    kind: str = ""  # ground / power / signal；空 = 按名字推断
    external: bool = False  # 还连到这一页以外
    supply: bool = False  # 电从这一页以外或板外送进来：放一个 PWR_FLAG 告诉 ERC 这里有电源
    # 这个信号网络通到别的功能块页：链的末端画层次标签（input / output / bidirectional / passive）
    port: str = ""


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
    unit: int = 1  # 多单元符号（如 LM358 的 A、B 两个运放和电源单元）画的是第几个单元

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
    shape: str = (
        ""  # 空 = 本页标签；input / output / bidirectional / passive = 通到上层页的层次标签
    )

    @property
    def box(self) -> Box:
        w = text_width(self.text)
        if self.shape:  # 层次标签的旗形框比文字长，且上下居中
            w += 2.6
            if self.rot == 180:
                return (self.x - w, self.y - TEXT_H / 2 - 0.4, self.x, self.y + TEXT_H / 2 + 0.4)
            return (self.x, self.y - TEXT_H / 2 - 0.4, self.x + w, self.y + TEXT_H / 2 + 0.4)
        if self.rot == 180:
            return (self.x - w, self.y - TEXT_H - 0.2, self.x, self.y)
        if self.rot == 90:  # 竖线上的网络名：字从锚点往上写，在线的左边
            return (self.x - TEXT_H - 0.2, self.y - w, self.x, self.y)
        return (self.x, self.y - TEXT_H - 0.2, self.x + w, self.y)


@dataclass(frozen=True)
class Note:
    """不带电气意义的文字（功能块的名字和用途、根页上的电源说明）。锚点在文字的左上角。"""

    text: str
    x: float
    y: float

    @property
    def box(self) -> Box:
        return text_box(self.x, self.y, self.text, "left top")


@dataclass(frozen=True)
class Frame:
    """圈住一个功能块的虚线方框，不带电气意义。"""

    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True)
class Line:
    """不带电气意义的虚线折线；arrow 为真时在最后一点画一个实心箭头。"""

    pts: tuple[Point, ...]
    arrow: bool = False

    def head(self) -> list[Point]:
        """最后一段末端的实心三角（尖在最后一点），首尾是同一点。"""
        (ax, ay), (bx, by) = self.pts[-2], self.pts[-1]
        length = math.hypot(bx - ax, by - ay) or 1.0
        ux, uy = (bx - ax) / length, (by - ay) / length
        base = (bx - ux * ARROW_LEN, by - uy * ARROW_LEN)
        left = (base[0] - uy * ARROW_HALF, base[1] + ux * ARROW_HALF)
        right = (base[0] + uy * ARROW_HALF, base[1] - ux * ARROW_HALF)
        return [(bx, by), left, right, (bx, by)]

    def boxes(self) -> list[Box]:
        """每一段的外框，有箭头时再加箭头的外框。"""
        out = [
            (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))
            for a, b in zip(self.pts, self.pts[1:], strict=False)
        ]
        if self.arrow and len(self.pts) >= 2:
            head = self.head()
            xs, ys = [p[0] for p in head], [p[1] for p in head]
            out.append((min(xs), min(ys), max(xs), max(ys)))
        return out


def _move(p: Point, dx: float, dy: float) -> Point:
    return r4(p[0] + dx), r4(p[1] + dy)


@dataclass
class Prims:
    symbols: list[SymbolInst] = field(default_factory=list)
    wires: list[Wire] = field(default_factory=list)
    junctions: list[Point] = field(default_factory=list)
    labels: list[Label] = field(default_factory=list)
    ncs: list[Point] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    frames: list[Frame] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    # KiCad 自己画、不由这里写出的字（根页图纸框上方的框名、下方的文件名）：(文字, 外框)。
    # 只给找路和检查关当文字看，免得线和箭头压上去（uno_like_board 的箭头曾压住框名）
    extra_texts: list[tuple[str, Box]] = field(default_factory=list)

    def add_wire(self, a: Point, b: Point) -> None:
        a, b = (r4(a[0]), r4(a[1])), (r4(b[0]), r4(b[1]))
        if a != b:
            self.wires.append(Wire(a, b))

    def extend(self, other: Prims, dx: float = 0.0, dy: float = 0.0) -> None:
        self.symbols += [s.translated(dx, dy) for s in other.symbols]
        self.wires += [Wire(_move(w.a, dx, dy), _move(w.b, dx, dy)) for w in other.wires]
        self.junctions += [_move(j, dx, dy) for j in other.junctions]
        self.labels += [replace(lb, x=r4(lb.x + dx), y=r4(lb.y + dy)) for lb in other.labels]
        self.ncs += [_move(n, dx, dy) for n in other.ncs]
        self.notes += [replace(n, x=r4(n.x + dx), y=r4(n.y + dy)) for n in other.notes]
        self.frames += [
            Frame(r4(f.x0 + dx), r4(f.y0 + dy), r4(f.x1 + dx), r4(f.y1 + dy)) for f in other.frames
        ]
        self.lines += [
            Line(tuple(_move(p, dx, dy) for p in ln.pts), ln.arrow) for ln in other.lines
        ]
        self.extra_texts += [(t, _shift(b, dx, dy)) for t, b in other.extra_texts]

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
        out += [n.box for n in self.notes]
        out += [(f.x0, f.y0, f.x1, f.y1) for f in self.frames]
        for ln in self.lines:
            xs, ys = [p[0] for p in ln.pts], [p[1] for p in ln.pts]
            out.append((min(xs) - 0.8, min(ys) - 0.8, max(xs) + 0.8, max(ys) + 0.8))
        out += [b for _, b in self.extra_texts]
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


def text_width(text: str) -> float:
    """估算一行文字的宽度（mm）：中文这类全角字大约是西文字母的 1.6 倍宽。"""
    return sum(CHAR_W * (1.6 if ord(ch) >= 0x2E80 else 1.0) for ch in text) or CHAR_W


def text_box(x: float, y: float, text: str, justify: str = "") -> Box:
    """KiCad 的对齐：left / right 是锚点在文字的左 / 右端；

    top / bottom 是锚点在文字的上 / 下沿；不写是居中。
    """
    words = justify.split()
    w = text_width(text)
    x0 = x if "left" in words else x - w if "right" in words else x - w / 2
    if "top" in words:
        y0 = y - 0.1
    elif "bottom" in words:
        y0 = y - TEXT_H - 0.1
    else:
        y0 = y - TEXT_H / 2 - 0.1
    return (x0, y0, x0 + w, y0 + TEXT_H + 0.2)


def flip_justify(justify: str) -> str:
    swap = {"left": "right", "right": "left"}
    return " ".join(swap.get(w, w) for w in justify.split())


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
    """sdef 要是已经选好单元的（SymbolDef.for_unit），pins / body 都只算这个单元的。"""
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
        unit=sdef.unit,
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
    unit: int = 1


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
    ports: dict[str, str] = field(default_factory=dict)  # 网络 → 层次标签的形状

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
    ports: dict[str, str] = {}
    for net in nets:
        kinds[net.name] = net.kind or infer_kind(net.name)
        members[net.name] = list(net.pins)
        if net.external:
            external.add(net.name)
        if net.supply:
            supply.add(net.name)
        if net.port and kinds[net.name] == "signal":
            ports[net.name] = net.port
        for ref, number in net.pins:
            if ref not in syms:
                raise ValueError(f"网络 {net.name} 用到的 {ref} 不在这一页的元件里")
            try:
                syms[ref].pin(number)
            except KeyError:
                raise ValueError(
                    f"网络 {net.name}：{ref}（{syms[ref].lib_id}）没有编号为 {number} 的引脚"
                ) from None
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
        ports=ports,
    )
    for p in parts:
        sdef = syms[p.ref]
        visible = [q for q in sdef.pins if not q.hidden]
        two_pin = sdef.unit_count == 1 and len(visible) == 2 and sdef.two_pin_axis is not None
        if p.role == "passive" and not two_pin:
            raise ValueError(
                f"{p.ref}（{p.lib_id}）标了 passive，但它不是只有两个共线引脚的单单元符号"
            )
        if p.role not in ("hub", "connector") and two_pin:
            ctx.passive.add(p.ref)
    return ctx


_OPPOSITE = {"left": "right", "right": "left", "top": "bottom", "bottom": "top"}


def _loops_back(ctx: Ctx, hub: str, sides: dict[str, str], from_side: str, net: str) -> bool:
    """这个网络是不是接回同一个芯片单元对面那一侧的引脚（比如运放的反馈电阻：从反相输入接到输出）。"""
    far = _OPPOSITE.get(from_side, "")
    return any(r == hub and sides.get(q) == far for r, q in ctx.members[net])


def build_chain(
    ctx: Ctx,
    hub: str,
    pin: Pin,
    claimed: set[str],
    *,
    steps_ok: bool = True,
    sides: dict[str, str] | None = None,
) -> Chain:
    """sides：这个单元每个引脚在哪一侧。给了的话，接回对面那一侧引脚的串联元件不放进链里：
    它单独成一团，导线绕过芯片把环接上（ADR-0014，lm358_amp 的反馈电阻）。"""
    net = ctx.net_of.get((hub, pin.number))
    chain = Chain(hub, pin.number, [], End("nc"))
    if net is None:
        return chain
    if ctx.kind(net) != "signal":
        chain.end = End("power", net)
        return chain
    entry = (hub, pin.number)
    from_side = sides.get(pin.number, "") if sides else ""
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
            elif sides and _loops_back(ctx, hub, sides, from_side, onet):
                rest += 1
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


@dataclass
class HeadPlan:
    """没有主芯片可挂的两脚元件串成的链，从一个网络起头。kind：

    port = 通到别页的信号（起头画层次标签）；power = 电源（起头画电源符号）；
    label = 本页信号（起头画标签）。
    """

    net: str
    kind: str
    chain: Chain


def _scan(
    ctx: Ctx, cur: str, entry: tuple[str, str] | None, claimed: set[str]
) -> tuple[list[Shunt], list[Series], int]:
    shunts: list[Shunt] = []
    series: list[Series] = []
    rest = 0
    for ref, number in ctx.members[cur]:
        if (ref, number) == entry:
            continue
        if ref not in ctx.passive or ref in claimed:
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
    return shunts, series, rest


def build_head_chain(ctx: Ctx, net: str, claimed: set[str]) -> Chain:
    """和 build_chain 一样往下走，只是起点是一个网络而不是芯片的引脚。

    起点是电源时只取一个通往信号网络的串联元件（电源和地之间的电容留给「贴在芯片旁边」）；
    起点是通到别页的信号时，起头的标签就代表了别页，所以不因为它通到页外而停下。
    """
    chain = Chain("", "", [], End("none"))
    shunts, series, rest = _scan(ctx, net, None, claimed)
    if ctx.kind(net) != "signal":
        shunts, rest = [], 0
        series = series[:1]
    if shunts:
        claimed.update(s.ref for s in shunts)
        chain.steps.append(Tap(shunts))
    if len(series) != 1 or rest:
        if chain.steps and (series or rest):
            chain.end = End("label", net)
        return chain
    step = series[0]
    claimed.add(step.ref)
    chain.steps.append(step)
    cur = ctx.net_of[(step.ref, step.exit)]
    entry: tuple[str, str] = (step.ref, step.exit)
    while True:
        if ctx.kind(cur) != "signal":
            chain.end = End("power", cur)
            return chain
        shunts, series, rest = _scan(ctx, cur, entry, claimed)
        if shunts:
            claimed.update(s.ref for s in shunts)
            chain.steps.append(Tap(shunts))
        outside = cur in ctx.external
        if len(series) == 1 and not rest and not outside:
            step = series[0]
            claimed.add(step.ref)
            chain.steps.append(step)
            entry = (step.ref, step.exit)
            cur = ctx.net_of[entry]
            continue
        chain.end = End("label", cur) if (series or rest or outside) else End("none")
        return chain


def plan_heads(ctx: Ctx, claimed: set[str]) -> list[HeadPlan]:
    """把还没挂上的两脚元件串起来：先从通到别页的信号起头，再从电源起头，最后从只连着两脚元件的信号起头。"""
    heads: list[HeadPlan] = []
    while True:
        loose = {r for r in ctx.parts if r in ctx.passive and r not in claimed}
        if not loose:
            return heads
        touching = [n for n in ctx.members if any(r in loose for r, _ in ctx.members[n])]
        ports = [n for n in touching if n in ctx.ports]
        powers = [n for n in touching if ctx.kind(n) == "power"]
        signals = [
            n
            for n in touching
            if ctx.kind(n) == "signal" and n not in ctx.ports
            and all(r in ctx.passive for r, _ in ctx.members[n])
        ]  # fmt: skip
        for kind, nets in (("port", ports), ("power", powers), ("label", signals)):
            found = None
            for net in nets:
                trial = set(claimed)
                chain = build_head_chain(ctx, net, trial)
                if chain.steps:
                    claimed |= trial
                    found = HeadPlan(net, kind, chain)
                    break
            if found:
                heads.append(found)
                break
        else:
            return heads


def layout_head(ctx: Ctx, head: HeadPlan) -> Prims:
    p = Prims()
    if head.kind == "power":
        p.symbols.append(_power_symbol(ctx, head.net, 0.0, 0.0))
    else:
        text = display_name(head.net)
        p.labels.append(Label(text, 0.0, 0.0, 180, ctx.ports.get(head.net, "")))
    p.extend(render_lane(ctx, head.chain, 1))
    return p


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


def render_lane(ctx: Ctx, chain: Chain, d: int, first_tap: float = STUB) -> Prims:
    """把一条链画成横线，起点在 (0, 0)，d = +1 向右、-1 向左。

    first_tap：第一个并联元件离起点多远。从芯片上下两边拐出来的链用得长一些，
    让挂下来的元件离开芯片顶上（或底下）别的引脚的电源符号（ne555_astable 的 C2，用户 2026-11-01）。
    """
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
                    u = max(
                        u + (STUB if after_series else 0.0), last, first_tap if i == 0 else STUB
                    )
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
        shape = ctx.ports.get(chain.end.net, "")
        text = display_name(chain.end.net)
        p.labels.append(Label(text, r4(d * u), 0.0, 0 if d > 0 else 180, shape))
    return p


# ---------------------------------------------------------------- 一个主芯片和它的链


def _hub_fields(sdef: SymbolDef, ref: str, value: str, rot: int, mirror: bool) -> list[Field]:
    """主芯片的位号和数值照库里的位置和对齐写（库作者排好的不会互相压住）。

    镜像的符号，KiCad 显示时会把左右对齐也镜像过来，所以这里算外框时也左右对调；写文件时再对调回去。
    """
    out = []
    for name, text in (("Reference", ref), ("Value", value)):
        px, py = sdef.prop_pos.get(name, (0.0, 0.0))
        dx, dy = rotate(px, py, rot)
        justify = sdef.prop_justify.get(name, "")
        out.append(
            Field(
                name,
                text,
                r4(-dx if mirror else dx),
                r4(dy),
                flip_justify(justify) if mirror else justify,
            )
        )
    return out


def layout_hub(ctx: Ctx, plan: HubPlan) -> Prims:
    sdef, part = ctx.syms[plan.ref].for_unit(plan.unit), ctx.parts[plan.ref]
    if plan.ref in ctx.passive:
        probe = make_symbol(plan.ref, sdef, part.value, "", 0.0, 0.0, plan.rot)
        a, b = list(probe.pins.values())[:2]
        horizontal = abs(a[1] - b[1]) < 1e-6
        fields = _passive_fields(plan.ref, part.value, 0.0, 0.0, probe.core, horizontal, 1)
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
    if plan.ref not in ctx.passive:
        _fix_hub_fields(p)
    return p


def _overlap(a: Box, b: Box, shrink: float = 0.05) -> bool:
    return (
        a[0] + shrink < b[2] - shrink
        and b[0] + shrink < a[2] - shrink
        and a[1] + shrink < b[3] - shrink
        and b[1] + shrink < a[3] - shrink
    )


def _pin_lines(s: SymbolInst) -> list[Box]:
    """符号自己的引脚线（从引脚端点到本体边上）。"""
    if s.core is None:
        return []
    out = []
    for number, (px, py) in s.pins.items():
        dx, dy = s.pin_dirs.get(number, (0.0, 0.0))
        if dx:
            ix = s.core[0] if dx < 0 else s.core[2]
            out.append((min(px, ix), py - 0.15, max(px, ix), py + 0.15))
        elif dy:
            iy = s.core[1] if dy < 0 else s.core[3]
            out.append((px - 0.15, min(py, iy), px + 0.15, max(py, iy)))
    return out


def _fix_hub_fields(p: Prims) -> None:
    """主芯片的位号和型号默认照库里的位置写；压到导线、别的符号或文字时，换到本体上方或下方的空处。

    ADR-0014：ldo_3v3 的 +3V3 导线从 AMS1117-3.3 的型号文字上穿过（用户 2026-11-01 指出）。
    依次试：库里的位置 → 叠放在本体左上 / 右上（往上挪几格）→ 叠放在本体左下 / 右下。
    """
    hub = p.symbols[0]
    shown = {f.name: f for f in hub.fields if not f.hide}
    if not shown or hub.core is None:
        return
    obstacles: list[Box] = [
        (min(w.a[0], w.b[0]), min(w.a[1], w.b[1]), max(w.a[0], w.b[0]), max(w.a[1], w.b[1]))
        for w in p.wires
    ]
    for s in p.symbols[1:]:
        obstacles.append(s.box)
        obstacles += s.text_boxes()
    obstacles += [lb.box for lb in p.labels]
    obstacles += [(x - 0.9, y - 0.9, x + 0.9, y + 0.9) for x, y in p.ncs]
    obstacles += _pin_lines(hub)
    obstacles.append(hub.core)

    def clear(fields: list[Field]) -> bool:
        boxes = [text_box(f.x, f.y, f.text, f.justify) for f in fields if not f.hide]
        if any(_overlap(a, b) for i, a in enumerate(boxes) for b in boxes[i + 1 :]):
            return False
        return not any(_overlap(tb, ob) for tb in boxes for ob in obstacles)

    if clear(hub.fields):
        return
    x0, y0, x1, y1 = hub.core
    order = [n for n in ("Reference", "Value") if n in shown]
    pitch = 1.8  # 两行字的行距

    def stacked(x: float, justify: str, near_y: float, step: float) -> list[Field]:
        """near_y 是贴着本体的那一行。

        往上叠（step < 0）时型号贴着本体、位号在上；往下叠时位号贴着本体。
        """
        names = order[::-1] if step < 0 else order
        out = [f for f in hub.fields if f.hide]
        for k, name in enumerate(names):
            out.append(replace(shown[name], x=r4(x), y=r4(near_y + step * k), justify=justify))
        return out

    candidates: list[list[Field]] = []
    for k in range(5):
        top = y0 - GRID - k * GRID
        candidates.append(stacked(x0, "left", top, -pitch))
        candidates.append(stacked(x1, "right", top, -pitch))
    for k in range(5):
        bottom = y1 + GRID + k * GRID
        candidates.append(stacked(x0, "left", bottom, pitch))
        candidates.append(stacked(x1, "right", bottom, pitch))
    for fields in candidates:
        if clear(fields):
            p.symbols[0] = replace(hub, fields=fields)
            return


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


def _facing_pair(ctx: Ctx, upper: Chain, lower: Chain, gap: float) -> bool:
    """相邻两个直接接电源的引脚，上面的接地、下面的接电源：照常画的话地符号往下、电源符号往上，
    两个符号会面对面挤在一起（overcurrent_cutoff 的 U1B，用户 2026-11-01 指出）。"""
    direct = all(c.end.kind == "power" and not c.steps for c in (upper, lower))
    return (
        direct
        and gap <= 2 * STUB + 1e-6
        and ctx.kind(upper.end.net) == "ground"
        and ctx.kind(lower.end.net) != "ground"
    )


def _facing_lanes(ctx: Ctx, upper: Chain, lower: Chain, d: int) -> tuple[Prims, Prims]:
    """面对面的那一对换个画法，两条线都不用挪：上面那根拉长，地符号挂在最外头；
    下面那根先往下拐，再往外，电源符号放在它下面、箭头朝上。"""
    up = Prims()
    far = 5 * STUB
    up.add_wire((0.0, 0.0), (d * far, 0.0))
    up.add_wire((d * far, 0.0), (d * far, STUB))
    up.symbols.append(_power_symbol(ctx, upper.end.net, d * far, STUB))
    low = Prims()
    low.add_wire((0.0, 0.0), (d * STUB, 0.0))
    low.add_wire((d * STUB, 0.0), (d * STUB, 2 * STUB))
    low.add_wire((d * STUB, 2 * STUB), (d * 3 * STUB, 2 * STUB))
    low.symbols.append(_power_symbol(ctx, lower.end.net, d * 3 * STUB, 2 * STUB))
    return up, low


def _units(ctx: Ctx, items: list[tuple[Chain, float, float]], d: int, merge: bool) -> list[_Unit]:
    units: list[_Unit] = []
    special: dict[int, Prims] = {}
    uppers: set[int] = set()
    for k in range(len(items) - 1):
        (upper, ux, uy), (lower, lx, ly) = items[k], items[k + 1]
        if k not in special and abs(ux - lx) < 1e-6 and _facing_pair(ctx, upper, lower, ly - uy):
            special[k], special[k + 1] = _facing_lanes(ctx, upper, lower, d)
            uppers.add(k)
    i = 0
    while i < len(items):
        ch, px, py = items[i]
        if i in special:
            lane = special[i]
            b = lane.bbox()
            # 上面那根的地符号挂在下面那根够不着的最外头，排上下位置时只算它那根横线
            down = 0.2 if i in uppers else b[3]
            units.append(_Unit([py], px, py, -b[1], down, lane=lane, chain=ch))
            i += 1
            continue
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


def _direct_power(u: _Unit) -> bool:
    return u.chain is not None and u.chain.end.kind == "power" and not u.chain.steps


def _stack(units: list[_Unit]) -> list[float] | None:
    """每个单位的竖直位置：互相不压住，并且离各自引脚尽量近（加权最小二乘）。"""
    n = len(units)
    sep = [0.0] + [ceil_grid(units[k - 1].down + units[k].up + GAP) for k in range(1, n)]
    cum = [sum(sep[: k + 1]) for k in range(n)]
    z = [units[k].anchor - cum[k] for k in range(n)]
    # 直接接电源的引脚尽量不挪（电源符号就贴在引脚线头上），让带元件的链去拐弯（用户 2026-11-01）
    w = [1e6 if u.pinned else 20.0 if _direct_power(u) else 1.0 for u in units]
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
    all_x = [t[1] for t in items]
    cx = (hub_box[0] + hub_box[2]) / 2
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
    placed: list[Box] = []  # 这一侧已经放好的电源符号和它们的字
    for g in groups:
        net = g[0][0].end.net
        # 电源箭头一律朝上、地一律朝下（用户 2026-11-01）。引脚朝向和符号方向相反时（比如电源接在
        # 下面的引脚上），线先多走一段，再拐到外侧放符号，而不是把符号倒过来画。
        natural = (ctx.kind(net) == "ground") == (s > 0)
        reach = STUB if natural else 2 * STUB
        if natural:
            # 相邻两个引脚接不同的电源时，两个符号的名字可能叠在一起（uno_like_board 的 CH340C：
            # +5V 和 CH340_V3）：后一个的线拉长几格，名字错开高度
            mid_x = min(
                (px for _, px, _ in g), key=lambda x: abs(x - sum(t[1] for t in g) / len(g))
            )
            for extra in (0.0, 2 * GRID, 4 * GRID, 6 * GRID, 8 * GRID):
                probe = _power_symbol(ctx, net, mid_x, r4(g[0][2] + s * (reach + extra)))
                boxes = [probe.box, *probe.text_boxes()]
                if not any(_overlap(a, b) for a in boxes for b in placed):
                    break
            reach += extra
        ends = [(px, r4(py + s * reach)) for _, px, py in g]
        for (_, px, py), end in zip(g, ends, strict=True):
            p.add_wire((px, py), end)
        if len(g) > 1:
            p.add_wire(ends[0], ends[-1])
            p.junctions += ends[1:-1]
        if natural:
            mid = ends[
                min(
                    range(len(ends)),
                    key=lambda k: abs(ends[k][0] - sum(e[0] for e in ends) / len(ends)),
                )
            ]
            sym = _power_symbol(ctx, net, mid[0], mid[1])
            p.symbols.append(sym)
            placed += [sym.box, *sym.text_boxes()]
            continue
        xs = [e[0] for e in ends]
        outward = 1 if sum(xs) / len(xs) >= cx else -1
        for o in (outward, -outward):
            edge_x = max(xs) if o > 0 else min(xs)
            jx = r4(edge_x + o * 2 * STUB)
            lo, hi = sorted((edge_x, jx))
            if any(lo - 1e-6 <= x <= hi + 2 + 1e-6 and x not in xs for x in all_x):
                continue
            ey = ends[0][1]
            p.add_wire((edge_x, ey), (jx, ey))
            if len(g) > 1:
                p.junctions.append((edge_x, ey))
            p.symbols.append(_power_symbol(ctx, net, jx, ey))
            break
        else:  # 两边都有别的引脚挡着：只好照旧把符号倒过来
            p.symbols.append(_power_symbol(ctx, net, ends[0][0], ends[0][1], 180))
    if not turning:
        return
    edge = p.bbox()[3] if s > 0 else p.bbox()[1]

    def direction(t: tuple[Chain, float, float]) -> int:
        """拐出去的链往人少的一边走：右边还有别的引脚就往左，反之往右；两边一样多再看在中线哪一侧。"""
        px = t[1]
        left_n = sum(1 for x in all_x if x < px - 1e-6)
        right_n = sum(1 for x in all_x if x > px + 1e-6)
        if right_n != left_n:
            return -1 if right_n > left_n else 1
        return 1 if px >= cx else -1

    for d in (1, -1):
        side = [t for t in turning if direction(t) == d]
        side.sort(key=lambda t: -t[1] * d)  # 离出发方向最近的先画，深度最浅
        prev_y: float | None = None
        prev_ext = 0.0
        for ch, px, py in side:
            lane = render_lane(ctx, ch, d, first_tap=3 * STUB)
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


def _connector_mirror(ctx: Ctx, ref: str, visible: list[Pin]) -> bool:
    """连接器的引脚朝向电路：板子左边的连接器引脚朝右，右边的朝左；没说在哪边就让引脚朝右。"""
    connected = [q for q in visible if (ref, q.number) in ctx.net_of] or visible
    sides = {pin_side(q, 0, False) for q in connected}
    if ctx.parts[ref].edge == "right":
        return sides == {"right"}
    return sides == {"left"}


def plan_hubs(ctx: Ctx) -> tuple[list[HubPlan], set[str]]:
    """每个主芯片的每个单元一个计划；单元之间各画各的，引脚链照常从各自的引脚拉出去。"""
    claimed: set[str] = set()
    plans: list[HubPlan] = []
    for ref, part in ctx.parts.items():
        full = ctx.syms[ref]
        if ref in ctx.passive or full.is_power:
            continue
        for unit in sorted(full.units):
            sdef = full.for_unit(unit)
            visible = [q for q in sdef.pins if not q.hidden]
            if not visible:
                continue
            mirror = part.role == "connector" and _connector_mirror(ctx, ref, visible)
            sides = {q.number: pin_side(q, 0, False) for q in visible}
            chains = [build_chain(ctx, ref, q, claimed, sides=sides) for q in visible]
            plans.append(HubPlan(ref, 0, mirror, chains, unit))
    return plans, claimed


def plan_free(ctx: Ctx, ref: str) -> HubPlan:
    """不在任何链上的两脚元件：一般竖着放，电源在上、地在下，旁边的网络靠符号或标签。

    两头都是电源（比如串在 VBUS 和 +5V 之间的保险丝）或都是地时横着放，两个电源符号都朝上
    （或都朝下）：竖着放的话下面那个符号只能倒过来画。
    """
    sdef = ctx.syms[ref]
    a, b = [q for q in sdef.pins if not q.hidden]
    rank = {"power": 0, "signal": 1, "ground": 2}

    def key(q: Pin) -> int:
        net = ctx.net_of.get((ref, q.number))
        return rank[ctx.kind(net)] if net else 1

    dead: set[str] = set()
    if key(a) == key(b) != 1:
        rot = orient_pins(sdef, a, b, (1.0, 0.0))
        chains = [build_chain(ctx, ref, q, dead, steps_ok=False) for q in (a, b)]
        return HubPlan(ref, rot, False, chains)
    top, bottom = (a, b) if key(a) <= key(b) else (b, a)
    rot = orient_pins(sdef, top, bottom, (0.0, 1.0))
    chains = [build_chain(ctx, ref, q, dead, steps_ok=False) for q in (top, bottom)]
    return HubPlan(ref, rot, False, chains)


@dataclass
class Drawing:
    prims: Prims
    paper: str
    libs: dict[str, str]  # 用到的符号：库标识 → 库里的原文
    # 同名标签连成导线的结果（sch_route.RouteStats 的各项计数）；没连过线是空的
    route_stats: dict[str, int] = field(default_factory=dict)


@dataclass
class _Cluster:
    """页面上的一团：一个主芯片（的一个单元）连同它的引脚链和贴在旁边的元件；或一个落单的两脚元件。"""

    prims: Prims
    refs: set[str]
    nets: set[str]  # 这一团碰到的信号网络（用来排信号流向）
    side: str = ""  # left / right：板边连接器，原理图上也放在那一边
    source: bool = False  # 连接器：信号流向从它开始


@dataclass
class _Proto:
    """还没画的一团。先算出它碰到哪些信号网络、排在哪一列，再决定主芯片要不要左右翻转，最后才画。"""

    kind: str  # hub / head / free
    plan: HubPlan | None = None
    head: HeadPlan | None = None
    sats: list[str] = field(default_factory=list)
    # 横跨在芯片单元上方或下方的两脚元件：(位号, 接左侧网络的脚, 接右侧网络的脚, 放在下方)，
    # 左右按芯片不翻转时算
    bridges: list[tuple[str, Pin, Pin, bool]] = field(default_factory=list)
    refs: set[str] = field(default_factory=set)
    nets: set[str] = field(default_factory=set)
    side: str = ""
    source: bool = False


def _part_nets(ctx: Ctx, ref: str) -> set[str]:
    full = ctx.syms[ref]
    pins = {p.number for geom in full.units.values() for p in geom.pins} or {
        p.number for p in full.pins
    }
    return {net for number in pins if (net := ctx.net_of.get((ref, number))) is not None}


def _anchor_index(ctx: Ctx, ref: str, plans: list[HubPlan]) -> int | None:
    """落单的两脚元件贴在哪个主芯片（的哪个单元）旁边：先看 anchor；
    再按共用网络的方式排：经电源输入脚共用的最优先，只共用地的排在最后
    （几乎所有芯片都接地，按地来找会全挤到第一个芯片旁边）。"""
    nets = _part_nets(ctx, ref)

    def rank(plan: HubPlan) -> int | None:
        """0 = 经电源输入脚共用（去耦电容该贴的地方）；1 = 经别的脚共用电源或信号；2 = 只共用地。"""
        sdef = ctx.syms[plan.ref]
        best: int | None = None
        for ch in plan.chains:
            net = ctx.net_of.get((plan.ref, ch.pin))
            if net not in nets:
                continue
            if ctx.kind(net) == "ground":
                r = 2
            elif sdef.pin(ch.pin).etype == "power_in":
                r = 0
            else:
                r = 1
            best = r if best is None else min(best, r)
        return best

    def best_of(candidates: list[int]) -> int | None:
        found: tuple[int, int] | None = None
        for i in candidates:
            r = rank(plans[i])
            if r is not None and (found is None or r < found[0]):
                found = (r, i)
        return found[1] if found else None

    anchor = ctx.parts[ref].anchor
    if anchor:
        # 多单元芯片（如运放）：贴在和它共用电源的那个单元旁边，而不是第一个接地的单元
        same = [i for i, plan in enumerate(plans) if plan.ref == anchor]
        if not same:
            return None
        found = best_of(same)
        return same[0] if found is None else found
    if any(ctx.kind(n) == "signal" for n in nets):
        # 两头接信号的（比如 RS-485 的终端电阻）不贴在芯片下面：单独成一团，按信号流向排，
        # 导线从两边接进来（ADR-0014）。贴在芯片下面只适合去耦电容这种只接电源的。
        return None
    return best_of(list(range(len(plans))))


def _with_satellites(hub: Prims, satellites: list[Prims]) -> Prims:
    """落单的元件在主芯片这一团的下面排成一行，以主芯片为中心。

    放在整团的最下沿以下，所以左右怎么摆都不会压到这一团里已有的东西。
    """
    if not satellites:
        return hub
    _, _, _, y1 = hub.bbox()
    chip = hub.symbols[0].box  # layout_hub 把主芯片放在第一个
    widths = [sat.bbox()[2] - sat.bbox()[0] for sat in satellites]
    total = sum(widths) + GRID * 4 * (len(satellites) - 1)
    x = snap((chip[0] + chip[2]) / 2 - total / 2)
    y = y1 + GRID * 4
    merged = Prims()
    merged.extend(hub)
    for sat in satellites:
        sx0, sy0, sx1, _ = sat.bbox()
        merged.extend(sat, snap(x - sx0), snap(y - sy0))
        x += (sx1 - sx0) + GRID * 4
    return merged


def _plan_nets(ctx: Ctx, plan: HubPlan, sats: list[str]) -> set[str]:
    """一个主芯片单元这一团真正碰到的信号网络：它这个单元的引脚、链上的元件、贴在旁边的元件。"""
    nets: set[str] = set()
    for ch in plan.chains:
        if (n := ctx.net_of.get((plan.ref, ch.pin))) is not None:
            nets.add(n)
        for step in ch.steps:
            for s in _step_refs(step):
                nets |= _part_nets(ctx, s.ref)
    for r in sats:
        nets |= _part_nets(ctx, r)
    return {n for n in nets if ctx.kind(n) == "signal"}


def _bridge_for(ctx: Ctx, ref: str, plans: list[HubPlan]) -> tuple[int, Pin, Pin, bool] | None:
    """两头都接信号、分别接到同一个芯片单元左右两侧引脚的两脚元件（运放的反馈电阻、比较器的
    迟滞电阻这类），返回 (单元序号, 接左侧的脚, 接右侧的脚, 放在下方)。这样的元件横跨在芯片
    上方或下方，两头的导线接到左右两侧，环一眼就看得出来（ADR-0014）。

    放上方还是下方，看它接的那两个引脚在本体中线的哪一边：lm358_amp 的反馈接的是下面的反相
    输入，放上方的话导线会把上面的同相输入圈在环里，输入信号就接不进去了。
    """
    pins = [q for q in ctx.syms[ref].pins if not q.hidden]
    if len(pins) != 2:
        return None
    nets = [ctx.net_of.get((ref, q.number)) for q in pins]
    if any(n is None or ctx.kind(n) != "signal" for n in nets):
        return None
    for i, plan in enumerate(plans):
        if ctx.parts[plan.ref].role == "connector":
            continue
        usdef = ctx.syms[plan.ref].for_unit(plan.unit)
        sides: dict[str, set[str]] = {}
        ys: dict[str, list[float]] = {}
        for ch in plan.chains:
            n = ctx.net_of.get((plan.ref, ch.pin))
            if n is None:
                continue
            q = usdef.pin(ch.pin)
            side = pin_side(q, plan.rot, False)
            sides.setdefault(n, set()).add(side)
            if side in ("left", "right"):
                ys.setdefault(n, []).append(place_pin(q, 0.0, 0.0, plan.rot, False)[1])
        s0, s1 = sides.get(nets[0] or "", set()), sides.get(nets[1] or "", set())
        if not (("left" in s0 and "right" in s1) or ("right" in s0 and "left" in s1)):
            continue
        body = usdef.body or usdef.bbox
        center = -(body[1] + body[3]) / 2  # 库坐标 y 向上，原理图 y 向下
        loop = ys.get(nets[0] or "", []) + ys.get(nets[1] or "", [])
        below = bool(loop) and sum(loop) / len(loop) > center + 1e-6
        if "left" in s0:
            return i, pins[0], pins[1], below
        return i, pins[1], pins[0], below
    return None


def plan_bridge(ctx: Ctx, ref: str, left: Pin, right: Pin) -> HubPlan:
    """横着放的两脚元件：left 脚在左、right 脚在右，两头各一小段线和网络标签（之后连成导线）。"""
    sdef = ctx.syms[ref]
    rot = orient_pins(sdef, left, right, (1.0, 0.0))
    dead: set[str] = set()
    chains = [build_chain(ctx, ref, q, dead, steps_ok=False) for q in (left, right)]
    return HubPlan(ref, rot, False, chains)


def _with_bridges(hub: Prims, bridges: list[tuple[Prims, bool]]) -> Prims:
    """横跨元件放在这一团的最上沿以上（或最下沿以下），水平方向以芯片为中心；
    同一边有好几个时一个摞一个往外放。"""
    if not bridges:
        return hub
    chip = hub.symbols[0].core or hub.symbols[0].box
    cx = (chip[0] + chip[2]) / 2
    merged = Prims()
    merged.extend(hub)
    _, top, _, bottom = hub.bbox()
    up_y, down_y = top - GRID * 3, bottom + GRID * 3
    for br, below in bridges:
        bx0, by0, bx1, by1 = br.bbox()
        dx = snap(cx - (bx0 + bx1) / 2)
        if below:
            dy = snap(down_y - by0)
            down_y = by1 + dy + GRID * 2
        else:
            dy = snap(up_y - by1)
            up_y = by0 + dy - GRID * 2
        merged.extend(br, dx, dy)
    return merged


def _clusters(ctx: Ctx) -> tuple[list[_Cluster], list[list[int]]]:
    """排好的各团和它们的分列（每列从上到下的团序号）。"""
    plans, claimed = plan_hubs(ctx)
    heads = plan_heads(ctx, claimed)
    free = [ref for ref in ctx.parts if ref in ctx.passive and ref not in claimed]
    attached: dict[int, list[str]] = {}
    bridged: dict[int, list[tuple[str, Pin, Pin, bool]]] = {}
    alone: list[str] = []
    for ref in free:
        bridge = _bridge_for(ctx, ref, plans)
        if bridge is not None:
            bridged.setdefault(bridge[0], []).append((ref, bridge[1], bridge[2], bridge[3]))
            continue
        index = _anchor_index(ctx, ref, plans)
        if index is None:
            alone.append(ref)
        else:
            attached.setdefault(index, []).append(ref)
    protos: list[_Proto] = []
    for i, plan in enumerate(plans):
        sats = attached.get(i, [])
        bridges = bridged.get(i, [])
        refs = {plan.ref, *sats, *(b[0] for b in bridges)}
        refs |= {s.ref for ch in plan.chains for step in ch.steps for s in _step_refs(step)}
        part = ctx.parts[plan.ref]
        protos.append(
            _Proto(
                "hub",
                plan=plan,
                sats=sats,
                bridges=bridges,
                refs=refs,
                nets=_plan_nets(ctx, plan, [*sats, *(b[0] for b in bridges)]),
                side=part.edge,
                source=part.role == "connector",
            )
        )
    for head in heads:
        refs = {s.ref for step in head.chain.steps for s in _step_refs(step)}
        nets = {n for r in refs for n in _part_nets(ctx, r) if ctx.kind(n) == "signal"}
        source = head.kind == "port" and ctx.ports.get(head.net) == "input"
        protos.append(_Proto("head", head=head, refs=refs, nets=nets, source=source))
    for ref in alone:
        nets = {n for n in _part_nets(ctx, ref) if ctx.kind(n) == "signal"}
        protos.append(_Proto("free", plan=plan_free(ctx, ref), refs={ref}, nets=nets))
    columns = _order_columns(protos, _flow_columns(protos))
    _choose_mirrors(ctx, protos, columns)
    out: list[_Cluster] = []
    for pr in protos:
        if pr.kind == "head" and pr.head is not None:
            prims = layout_head(ctx, pr.head)
        elif pr.plan is not None and pr.kind == "hub":
            # 芯片翻转后左右对调，横跨元件的两只脚也跟着对调
            spans = [
                (
                    layout_hub(ctx, plan_bridge(ctx, r, *((b, a) if pr.plan.mirror else (a, b)))),
                    below,
                )
                for r, a, b, below in pr.bridges
            ]
            prims = _with_satellites(
                _with_bridges(layout_hub(ctx, pr.plan), spans),
                [layout_hub(ctx, plan_free(ctx, r)) for r in pr.sats],
            )
        elif pr.plan is not None:
            prims = layout_hub(ctx, pr.plan)
        else:
            raise AssertionError(pr.kind)
        out.append(_Cluster(prims, pr.refs, pr.nets, pr.side, pr.source))
    return out, columns


def _order_columns(protos: list[_Proto], columns: list[list[int]]) -> list[list[int]]:
    """每一列里的团按它连着的上一列各团的平均位置排（重心法），这样团和团之间的线少交叉。"""
    out = [list(columns[0])] if columns else []
    for col in columns[1:]:
        prev = {j: k for k, j in enumerate(out[-1])}

        def bary(i: int, prev: dict[int, int] = prev) -> float:
            ks = [k for j, k in prev.items() if protos[i].nets & protos[j].nets]
            return sum(ks) / len(ks) if ks else math.inf

        out.append(sorted(col, key=lambda i: (bary(i), col.index(i))))
    return out


def _choose_mirrors(ctx: Ctx, protos: list[_Proto], columns: list[list[int]]) -> None:
    """主芯片左右翻转，让要和别的团相连的引脚朝向那一团所在的一边（用户 2026-11-01：能连的都连上）。

    连接器不翻（它朝向板边，见 _connector_mirror）。数一数左右两侧的引脚里，有多少个连着的团在
    它朝的那一边、有多少个在反方向；反方向的多才翻。
    """
    col_of = {i: c for c, col in enumerate(columns) for i in col}
    for i, pr in enumerate(protos):
        plan = pr.plan
        if pr.kind != "hub" or plan is None or ctx.parts[plan.ref].role == "connector":
            continue
        sdef = ctx.syms[plan.ref].for_unit(plan.unit)
        score = 0
        for ch in plan.chains:
            if ch.end.kind != "label":
                continue
            side = pin_side(sdef.pin(ch.pin), plan.rot, False)
            if side not in ("left", "right"):
                continue
            partners = [col_of[j] for j, q in enumerate(protos) if j != i and ch.end.net in q.nets]
            left = sum(1 for c in partners if c < col_of[i])
            right = sum(1 for c in partners if c > col_of[i])
            if left != right:
                score += 1 if (side == "left") == (left > right) else -1
        if score < 0:
            plan.mirror = True


def _step_refs(step: Tap | Series) -> list[Shunt | Series]:
    return list(step.shunts) if isinstance(step, Tap) else [step]


def flow_layers[K](
    items: list[K], sources: list[K], skip: set[K], linked: Callable[[K, K], bool]
) -> dict[K, int]:
    """分层：从 sources 出发沿 linked 一层层往外；没走到的，从第一个没走到的接着走（从第 1 层起）。
    skip 里的不参与（留给调用方放到最后一层）。"""
    depth: dict[K, int] = {}

    def walk(start: list[K], level: int) -> None:
        for s in start:
            depth[s] = level
        frontier = list(start)
        while frontier:
            nxt = []
            for a in frontier:
                for b in items:
                    if b not in depth and b not in skip and linked(a, b):
                        depth[b] = depth[a] + 1
                        nxt.append(b)
            frontier = nxt

    walk([s for s in sources if s not in skip], 0)
    while rest := [k for k in items if k not in depth and k not in skip]:
        walk([rest[0]], 1)
    return depth


def flow_columns[K](
    items: list[K], sources: list[K], last: set[K], linked: Callable[[K, K], bool]
) -> list[list[K]]:
    """按信号流向分列；last 里的（右边的板边连接器）放在最后一列。同一列里保持输入顺序。"""
    depth = flow_layers(items, sources or items[:1], last, linked)
    end = max(depth.values(), default=-1) + 1
    for k in last:
        depth[k] = end
    columns: dict[int, list[K]] = {}
    for k in items:
        columns.setdefault(depth[k], []).append(k)
    return [columns[i] for i in sorted(columns)]


def _flow_columns(clusters: list[_Proto]) -> list[list[int]]:
    """从左边（没说哪边就从任一个）连接器出发，沿共用的信号网络一层层往右；右边的连接器放在最后一列。

    一个信号网络都不碰的团（只接电源的连接器、运放的电源单元这类）不参与排流向：
    连接器放进第一列，芯片的电源单元跟同一个芯片的其他单元放一列，其余放最后一列，
    免得它们都挤进第二列、把那一列撑得太高（lm358_amp 实测）。
    """
    items = [i for i in range(len(clusters)) if clusters[i].nets]
    right = {i for i in items if clusters[i].side == "right"}
    sources = [i for i in items if clusters[i].side == "left"] or [
        i for i in items if clusters[i].source and i not in right
    ]
    columns: list[list[int]] = []
    if items:
        columns = flow_columns(
            items, sources, right, lambda a, b: bool(clusters[a].nets & clusters[b].nets)
        )
    refs = [pr.plan.ref if pr.plan is not None else "" for pr in clusters]

    def only_connectors(col: list[int]) -> bool:
        return all(clusters[j].source for j in col)

    for i, pr in enumerate(clusters):
        if pr.nets:
            continue
        same_chip = [c for c in columns if refs[i] and any(refs[j] == refs[i] for j in c)]
        if not columns:
            columns.append([i])
        elif pr.source and pr.side == "right":
            # 只接电源的连接器单独放最外一列（ne555_astable 的电源插座曾被叠到芯片下面）
            if only_connectors(columns[-1]):
                columns[-1].append(i)
            else:
                columns.append([i])
        elif pr.source:
            if only_connectors(columns[0]):
                columns[0].append(i)
            else:
                columns.insert(0, [i])
        elif same_chip:
            same_chip[0].append(i)
        else:
            columns[-1].append(i)
    return columns


def column_gaps(nets_by_column: list[set[str]]) -> list[float]:
    """相邻两列之间留多宽：基本 8 格。

    每有一个信号网络跨过这条缝，多留 2 格给导线走（最多多留 20 格）。
    """
    gaps = []
    for c in range(len(nets_by_column) - 1):
        left = set().union(*nets_by_column[: c + 1])
        right = set().union(*nets_by_column[c + 1 :])
        gaps.append(GRID * (8 + 2 * min(len(left & right), 10)))
    return gaps


def _place_columns(
    clusters: list[_Cluster],
    columns: list[list[int]],
    max_w: float,
    max_h: float,
    vgap: float = GRID * 8,
) -> list[tuple[Prims, float, float]] | None:
    """按列摆：每列从上到下叠，列和列竖直居中对齐；列间距按要穿过的线数留。"""
    gaps = column_gaps([set().union(*(clusters[i].nets for i in col)) for col in columns])
    boxes_by_col = [[clusters[i].prims.bbox() for i in col] for col in columns]
    heights = [sum(b[3] - b[1] for b in bs) + vgap * (len(bs) - 1) for bs in boxes_by_col]
    if any(h > max_h for h in heights):
        return None
    tallest = max(heights, default=0.0)
    out: list[tuple[Prims, float, float]] = []
    x = 0.0
    for c, (col, boxes) in enumerate(zip(columns, boxes_by_col, strict=True)):
        width = max(b[2] - b[0] for b in boxes)
        y = snap((tallest - heights[c]) / 2)
        for i, b in zip(col, boxes, strict=True):
            out.append((clusters[i].prims, snap(x - b[0]), snap(y - b[1])))
            y += (b[3] - b[1]) + vgap
        x += width + (gaps[c] if c < len(gaps) else 0.0)
    if x > max_w:
        return None
    return out


def arrange(
    clusters: list[_Cluster],
    columns: list[list[int]],
    max_w: float,
    max_h: float,
    *,
    allow_pack: bool = True,
) -> Prims | None:
    """把各团摆进 max_w × max_h：先按列（列里的团上下间距从 8 格试到 4 格），
    还放不下才按行折行（allow_pack）；都放不下返回 None。左上角在 (0, 0) 附近。
    """
    placed = None
    for vgap in (GRID * 8, GRID * 6, GRID * 4):
        placed = _place_columns(clusters, columns, max_w, max_h, vgap)
        if placed is not None:
            break
    if placed is None and allow_pack:
        placed = _pack([c.prims for c in clusters], max_w, max_h)
    if placed is None:
        return None
    prims = Prims()
    for cluster, ox, oy in placed:
        prims.extend(cluster, ox, oy)
    return prims


def layout_content(
    parts: list[PartSpec], nets: list[NetSpec], lib_dirs: tuple[Path, ...]
) -> tuple[Ctx, list[_Cluster], list[list[int]]]:
    """一组元件画成一团团、分好列，还没摆到纸上（单页多功能块时每块各自调用一次）。"""
    ctx = build_ctx(parts, nets, lib_dirs)
    clusters, columns = _clusters(ctx)
    return ctx, clusters, columns


def used_libs(prims: Prims, ctxs: list[Ctx]) -> dict[str, str]:
    known: dict[str, str] = {}
    for c in ctxs:
        for d in (*c.syms.values(), *c.power_defs.values(), c.flag_def):
            known[d.lib_id] = d.raw
    return {k: known[k] for k in sorted({s.lib_id for s in prims.symbols})}


def fits(prims: Prims, usable_w: float, usable_h: float) -> bool:
    x0, y0, x1, y1 = prims.bbox()
    return x1 - x0 <= usable_w + 1e-6 and y1 - y0 <= usable_h + 1e-6


def usable_area(paper: str) -> tuple[float, float]:
    w, h = PAPERS[paper]
    return w - 2 * PAGE_MARGIN, h - PAGE_MARGIN - TITLE_RESERVE


def build_drawing(
    parts: list[PartSpec], nets: list[NetSpec], lib_dirs: tuple[Path, ...], *, route: bool = True
) -> Drawing:
    """一页：各团摆好 → 同名标签连成导线（route）→ PWR_FLAG → 选最小放得下的纸 → 居中。"""
    from eda_agent.emit.sch_route import connect_labels  # sch_route 依赖本模块，只能在这里引入

    ctx, clusters, columns = layout_content(parts, nets, lib_dirs)
    # 按信号流向分列比按行折行好读得多：宁可用大一号的纸，也先试按列摆（lm358_amp 实测）
    for allow_pack in (False, True):
        for paper in PAPERS:
            usable_w, usable_h = usable_area(paper)
            prims = arrange(clusters, columns, usable_w, usable_h, allow_pack=allow_pack)
            if prims is None:
                continue
            stats = vars(connect_labels(prims)) if route else {}
            add_supply_flags(prims, ctx.supply, ctx.flag_def)
            if not fits(prims, usable_w, usable_h):
                continue
            center(prims, usable_w, usable_h)
            return Drawing(prims, paper, used_libs(prims, [ctx]), stats)
    raise ValueError("A2 也放不下这一页，需要把功能块拆小")


def _overlaps(a: Box, b: Box) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def center(prims: Prims, usable_w: float, usable_h: float) -> None:
    """内容整体挪到可用区域正中（可用区域从页边距算起，不含标题栏）。"""
    x0, y0, x1, y1 = prims.bbox()
    dx = snap(PAGE_MARGIN + (usable_w - (x1 - x0)) / 2 - x0)
    dy = snap(PAGE_MARGIN + (usable_h - (y1 - y0)) / 2 - y0)
    moved = Prims()
    moved.extend(prims, dx, dy)
    vars(prims).update(vars(moved))


_FLAG_OFFSETS = tuple(
    sign * GRID * k for k in (4, 6, 8, 10, 12, 14, 16, 20, 24) for sign in (1, -1)
)


def add_supply_flags(prims: Prims, supply: set[str], flag_def: SymbolDef) -> None:
    """每个由外面供电的电源网络放一个 PWR_FLAG。

    依次试这个网络的每个电源符号，在它旁边找一块空地，用一小段横线接上；
    空地要避开所有符号、文字、标签，也不能压到别的导线。
    """
    for net in sorted(supply):
        for anchor in (s for s in prims.symbols if s.prefix == "#PWR" and s.net == net):
            x, y = anchor.x, anchor.y
            boxes = [b for s in prims.symbols if s is not anchor for b in (s.box, *s.text_boxes())]
            boxes += [lb.box for lb in prims.labels]
            boxes += [n.box for n in prims.notes]
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
                    "#FLG", flag_def, "PWR_FLAG", "", fx, y, 0, False, fields,
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
