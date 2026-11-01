"""读取 KiCad 符号库（.kicad_sym）：每个单元的引脚位置和外框、原文。只用标准库。

- 原理图里嵌入符号时直接用库里的原文，所以这里只读几何，不重新生成符号。
- 多单元符号（如 LM358 的两个运放 + 电源单元）：units 里每个单元一份引脚和外框；
  单元 0 里的引脚是所有单元共用的，算进每个单元。只读普通画法（style 1），不读 De Morgan 画法。
- 派生符号（extends）：在同一个库文件里找到父符号，用父符号的图形和引脚、子符号自己的属性，
  拼成一个「展平」的符号原文（KiCad 自己把派生符号存进原理图时也是展平的）。
- 库坐标 y 向上；原理图坐标 y 向下，转换见 sch_layout.rotate。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from functools import cache
from pathlib import Path

from eda_agent.kicad_side import sexpr

_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|[()]')
_SYMBOL_HEAD = re.compile(r'\s*symbol\s+"((?:[^"\\]|\\.)*)"')
_HEAD = re.compile(r"\s*([A-Za-z_]+)")
_TOP_NAME = re.compile(r'\(symbol\s+"[^"]*"')
_UNIT_SUFFIX = re.compile(r"_(\d+)_(\d+)$")
# 引脚的 angle 是「从连接点指向符号本体」的方向，所以连接点在本体的反方向一侧。
_SIDE = {0: "left", 90: "bottom", 180: "right", 270: "top"}

Rect = tuple[float, float, float, float]


@dataclass(frozen=True)
class Pin:
    number: str
    name: str
    x: float
    y: float
    angle: int
    length: float
    etype: str
    hidden: bool

    @property
    def side(self) -> str:
        return _SIDE[self.angle % 360]


@dataclass(frozen=True)
class UnitGeom:
    pins: tuple[Pin, ...]
    body: Rect | None


@dataclass(frozen=True)
class SymbolDef:
    """一个符号；pins / body 是当前单元（unit）的，units 是全部单元的。"""

    lib_id: str
    pins: tuple[Pin, ...]
    body: Rect | None
    raw: str
    is_power: bool
    properties: dict[str, str]
    prop_pos: dict[str, tuple[float, float]]
    unit: int = 1
    units: dict[int, UnitGeom] = field(default_factory=dict)
    prop_justify: dict[str, str] = field(default_factory=dict)  # 库里属性文字的对齐，如 "left"

    @property
    def unit_count(self) -> int:
        return max(len(self.units), 1)

    def for_unit(self, n: int) -> SymbolDef:
        geom = self.units[n]
        return replace(self, pins=geom.pins, body=geom.body, unit=n)

    def units_with_pin(self, number: str) -> list[int]:
        return [
            n
            for n, geom in sorted(self.units.items())
            if any(p.number == number for p in geom.pins)
        ]

    def pin(self, number: str) -> Pin:
        for p in self.pins:
            if p.number == number:
                return p
        for geom in self.units.values():
            for p in geom.pins:
                if p.number == number:
                    return p
        raise KeyError(f"{self.lib_id} 没有引脚 {number}")

    @property
    def bbox(self) -> Rect:
        """本体加引脚连接点的外框（库坐标，y 向上）。"""
        xs = [p.x for p in self.pins]
        ys = [p.y for p in self.pins]
        if self.body is not None:
            xs += [self.body[0], self.body[2]]
            ys += [self.body[1], self.body[3]]
        if not xs:
            return (0.0, 0.0, 0.0, 0.0)
        return (min(xs), min(ys), max(xs), max(ys))

    @property
    def two_pin_axis(self) -> str | None:
        """两脚元件的两个引脚连线方向：v 竖直、h 水平；不共线返回 None。"""
        if len(self.pins) != 2:
            return None
        a, b = self.pins
        if abs(a.x - b.x) < 1e-6:
            return "v"
        if abs(a.y - b.y) < 1e-6:
            return "h"
        return None


def _scan(text: str, start: int, end: int, depth_wanted: int) -> list[tuple[str, str, int, int]]:
    """在 text[start:end] 里找给定深度的 S 表达式：(标签, 名字, 起点, 终点)。

    名字只对 symbol 有意义。
    """
    out: list[tuple[str, str, int, int]] = []
    depth = 0
    open_at = -1
    tag = name = ""
    for m in _TOKEN.finditer(text, start, end):
        token = m.group()
        if token == "(":
            depth += 1
            if depth == depth_wanted:
                open_at = m.start()
                head = _HEAD.match(text, m.end())
                tag = head.group(1) if head else ""
                sym = _SYMBOL_HEAD.match(text, m.end()) if tag == "symbol" else None
                name = sym.group(1) if sym else ""
        elif token == ")":
            if depth == depth_wanted and open_at >= 0:
                out.append((tag, name, open_at, m.end()))
                open_at = -1
            depth -= 1
    return out


@cache
def _index(path: str) -> tuple[str, dict[str, tuple[int, int]]]:
    """扫一遍库文件，记下每个顶层符号的起止位置。"""
    text = Path(path).read_text(encoding="utf-8")
    spans = {name: (s, e) for tag, name, s, e in _scan(text, 0, len(text), 2) if tag == "symbol"}
    return text, spans


def _num(value: str) -> float:
    return float(value)


def _xy(node: sexpr.Node) -> tuple[float, float]:
    return _num(node[1]), _num(node[2])


def _graphic_points(item: sexpr.Node) -> list[tuple[float, float]]:
    tag = item[0]
    if tag == "rectangle":
        start, end = sexpr.child(item, "start"), sexpr.child(item, "end")
        return [_xy(start), _xy(end)] if start and end else []
    if tag in ("polyline", "bezier"):
        pts = sexpr.child(item, "pts")
        return [_xy(p) for p in sexpr.children(pts, "xy")]
    if tag == "circle":
        center, radius = sexpr.child(item, "center"), sexpr.child(item, "radius")
        if not (center and radius):
            return []
        cx, cy = _xy(center)
        r = _num(radius[1])
        return [(cx - r, cy - r), (cx + r, cy + r)]
    if tag == "arc":
        return [_xy(n) for n in (sexpr.child(item, k) for k in ("start", "mid", "end")) if n]
    return []


def _pin(item: sexpr.Node) -> Pin | None:
    at, length = sexpr.child(item, "at"), sexpr.child(item, "length")
    if at is None or length is None:
        return None
    return Pin(
        number=sexpr.value(item, "number"),
        name=sexpr.value(item, "name"),
        x=_num(at[1]),
        y=_num(at[2]),
        angle=int(round(_num(at[3]))) % 360 if len(at) > 3 else 0,
        length=_num(length[1]),
        etype=str(item[1]),
        hidden="hide" in item or sexpr.value(item, "hide") == "yes",
    )


_MIN_BODY = 2.54
_UNIT = {0: (1.0, 0.0), 90: (0.0, 1.0), 180: (-1.0, 0.0), 270: (0.0, -1.0)}


def _inner_end(pin: Pin) -> tuple[float, float]:
    """引脚靠本体那一端（连接点沿 angle 方向走 length）。"""
    ux, uy = _UNIT.get(pin.angle % 360, (0.0, 0.0))
    return pin.x + ux * pin.length, pin.y + uy * pin.length


def _body(points: list[tuple[float, float]], pins: list[Pin]) -> Rect | None:
    """本体外框：图形加上引脚靠本体的一端。

    像 LM358 的电源单元那样只有两根引脚、没有图形的单元，外框会是一条线，
    这里撑到至少 2.54 mm 宽，免得别的东西被摆到它的引脚名上。
    """
    points = points + [_inner_end(p) for p in pins]
    if not points:
        return None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    if x1 - x0 < _MIN_BODY:
        cx = (x0 + x1) / 2
        x0, x1 = cx - _MIN_BODY / 2, cx + _MIN_BODY / 2
    if y1 - y0 < _MIN_BODY:
        cy = (y0 + y1) / 2
        y0, y1 = cy - _MIN_BODY / 2, cy + _MIN_BODY / 2
    return (x0, y0, x1, y1)


def parse_symbol(lib_id: str, raw_text: str) -> SymbolDef:
    node = sexpr.parse(raw_text)[0]
    if sexpr.child(node, "extends") is not None:
        raise ValueError(f"{lib_id} 是派生符号（extends），要先用 load_symbol 展平")
    per_unit_pins: dict[int, list[Pin]] = {}
    per_unit_points: dict[int, list[tuple[float, float]]] = {}
    for sub in sexpr.children(node, "symbol"):
        m = _UNIT_SUFFIX.search(str(sub[1]))
        unit, style = (int(m.group(1)), int(m.group(2))) if m else (1, 1)
        if style not in (0, 1):
            continue  # De Morgan 的另一种画法
        pins = per_unit_pins.setdefault(unit, [])
        points = per_unit_points.setdefault(unit, [])
        for item in sub[2:]:
            if not isinstance(item, list) or not item:
                continue
            if item[0] == "pin":
                pin = _pin(item)
                if pin is not None and all(p.number != pin.number for p in pins):
                    pins.append(pin)
            else:
                points += _graphic_points(item)
    numbers = sorted(u for u in set(per_unit_pins) | set(per_unit_points) if u > 0) or [1]
    common_pins = per_unit_pins.get(0, [])
    common_points = per_unit_points.get(0, [])
    units = {}
    for n in numbers:
        pins = common_pins + per_unit_pins.get(n, [])
        units[n] = UnitGeom(
            pins=tuple(pins), body=_body(common_points + per_unit_points.get(n, []), pins)
        )
    props: dict[str, str] = {}
    prop_pos: dict[str, tuple[float, float]] = {}
    prop_justify: dict[str, str] = {}
    for prop in sexpr.children(node, "property"):
        if len(prop) < 3:
            continue
        props[str(prop[1])] = str(prop[2])
        at = sexpr.child(prop, "at")
        if at is not None:
            prop_pos[str(prop[1])] = (_num(at[1]), _num(at[2]))
        justify = sexpr.child(sexpr.child(prop, "effects"), "justify")
        if justify is not None:
            prop_justify[str(prop[1])] = " ".join(str(t) for t in justify[1:])
    first = units[numbers[0]]
    return SymbolDef(
        lib_id=lib_id,
        pins=first.pins,
        body=first.body,
        raw=raw_text,
        is_power=sexpr.child(node, "power") is not None,
        properties=props,
        prop_pos=prop_pos,
        unit=numbers[0],
        units=units,
        prop_justify=prop_justify,
    )


_PROP_NAME = re.compile(r'\(property\s+"((?:[^"\\]|\\.)*)"')


def _prop_name(text: str) -> str:
    m = _PROP_NAME.match(text)
    return m.group(1) if m else ""


def _props(raw: str) -> list[str]:
    return [raw[s:e] for tag, _, s, e in _scan(raw, 0, len(raw), 2) if tag == "property"]


def merge_properties(levels: list[str]) -> list[str]:
    """从最上面的父符号往下逐层合并属性：上一层的按原顺序保留，同名的换成下一层的，
    下一层多出来的接在后面（KiCad 自己展平派生符号时也是这样）。"""
    merged = _props(levels[0])
    for raw in levels[1:]:
        own = _props(raw)
        by_name = {_prop_name(p): p for p in own}
        names = {_prop_name(p) for p in merged}
        merged = [by_name.get(_prop_name(p), p) for p in merged]
        merged += [p for p in own if _prop_name(p) not in names]
    return merged


def _flatten(lib_id: str, levels: list[str], parent_name: str, child_name: str) -> str:
    """派生符号 → 展平的符号原文。levels 从最上面的父符号（有图形和引脚）排到要的这个子符号。

    图形、引脚取最上面的父符号，单元名换成子符号的名字；属性逐层合并（merge_properties）。
    只用子符号自己的属性会丢掉父符号独有的字段（LM358 的父符号 LM2904 有 ki_locked
    = 单元不能互换），KiCad 的 ERC 会报「符号与库里的不一致」（M1 实测）。
    """
    parent_raw = levels[0]
    child_props = merge_properties(levels)
    parent_spans = [
        (s, e) for tag, _, s, e in _scan(parent_raw, 0, len(parent_raw), 2) if tag == "property"
    ]
    if not parent_spans:
        raise ValueError(f"{lib_id} 的父符号 {parent_name} 没有属性，没法展平")
    first, last = parent_spans[0][0], parent_spans[-1][1]
    middle = parent_raw[first:last]
    # 两个属性之间如果夹着别的东西（不应该出现），就照原样留在最后一个属性后面
    leftovers = "".join(
        middle[e - first : s2 - first]
        for (_, e), (s2, _) in zip(parent_spans, parent_spans[1:], strict=False)
        if middle[e - first : s2 - first].strip()
    )
    text = parent_raw[:first] + "\n\t\t".join(child_props) + leftovers + parent_raw[last:]
    text = _TOP_NAME.sub(f'(symbol "{lib_id}"', text, count=1)
    escaped = re.escape(parent_name)
    return re.sub(rf'\(symbol\s+"{escaped}_(\d+)_(\d+)"', rf'(symbol "{child_name}_\1_\2"', text)


def load_symbol(lib_id: str, lib_dirs: tuple[Path, ...]) -> SymbolDef:
    lib, name = lib_id.split(":", 1)
    for directory in lib_dirs:
        path = directory / f"{lib}.kicad_sym"
        if not path.exists():
            continue
        text, spans = _index(str(path))
        if name not in spans:
            continue
        s, e = spans[name]
        raw = text[s:e]
        node = sexpr.parse(raw)[0]
        if sexpr.child(node, "extends") is None:
            return parse_symbol(lib_id, _TOP_NAME.sub(f'(symbol "{lib_id}"', raw, count=1))
        # 派生符号可以一层层派生（KiCad 10 的 MAX485E → MAX481E → …）：图形和引脚取最上面那个
        # 不再派生的符号，属性从上往下逐层合并。
        base_name, base_raw = name, raw
        levels = [raw]
        seen = {name}
        while (parent := sexpr.child(sexpr.parse(base_raw)[0], "extends")) is not None:
            base_name = str(parent[1])
            if base_name in seen or base_name not in spans:
                raise KeyError(
                    f"{lib_id} 的父符号 {base_name} 不在 {path.name} 里，或者派生关系绕成了圈"
                )
            seen.add(base_name)
            ps, pe = spans[base_name]
            base_raw = text[ps:pe]
            levels.insert(0, base_raw)
        return parse_symbol(lib_id, _flatten(lib_id, levels, base_name, name))
    raise KeyError(f"在 {[str(d) for d in lib_dirs]} 里找不到符号 {lib_id}")
