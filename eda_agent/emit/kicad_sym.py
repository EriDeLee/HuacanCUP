"""读取 KiCad 符号库（.kicad_sym）：引脚位置、外框、原文。只用标准库。

原理图里嵌入符号时直接用库里的原文，所以这里只读几何，不重新生成符号。
库坐标 y 向上；原理图坐标 y 向下，转换见 sch_layout.xf。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from eda_agent.kicad_side import sexpr

_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|[()]')
_SYMBOL_HEAD = re.compile(r'\s*symbol\s+"((?:[^"\\]|\\.)*)"')
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
class SymbolDef:
    lib_id: str
    pins: tuple[Pin, ...]
    body: Rect | None
    raw: str
    is_power: bool
    properties: dict[str, str]
    prop_pos: dict[str, tuple[float, float]]

    def pin(self, number: str) -> Pin:
        for p in self.pins:
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


@cache
def _index(path: str) -> tuple[str, dict[str, tuple[int, int]]]:
    """扫一遍库文件，记下每个顶层符号的起止位置。"""
    text = Path(path).read_text(encoding="utf-8")
    spans: dict[str, tuple[int, int]] = {}
    depth = 0
    start = -1
    name = ""
    for m in _TOKEN.finditer(text):
        token = m.group()
        if token == "(":
            depth += 1
            if depth == 2:
                head = _SYMBOL_HEAD.match(text, m.end())
                if head:
                    start, name = m.start(), head.group(1)
                else:
                    start = -1
        elif token == ")":
            if depth == 2 and start >= 0:
                spans[name] = (start, m.end())
                start = -1
            depth -= 1
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


def parse_symbol(lib_id: str, raw_text: str) -> SymbolDef:
    node = sexpr.parse(raw_text)[0]
    if sexpr.child(node, "extends") is not None:
        raise ValueError(f"{lib_id} 是派生符号（extends），暂不支持")
    pins: list[Pin] = []
    seen: set[str] = set()
    points: list[tuple[float, float]] = []
    for sub in sexpr.children(node, "symbol"):
        m = _UNIT_SUFFIX.search(str(sub[1]))
        unit, style = (int(m.group(1)), int(m.group(2))) if m else (1, 1)
        if unit not in (0, 1) or style not in (0, 1):
            continue
        for item in sub[2:]:
            if not isinstance(item, list) or not item:
                continue
            if item[0] == "pin":
                pin = _pin(item)
                if pin is not None and pin.number not in seen:
                    seen.add(pin.number)
                    pins.append(pin)
            else:
                points += _graphic_points(item)
    body: Rect | None = None
    if points:
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        body = (min(xs), min(ys), max(xs), max(ys))
    props: dict[str, str] = {}
    prop_pos: dict[str, tuple[float, float]] = {}
    for prop in sexpr.children(node, "property"):
        if len(prop) < 3:
            continue
        props[str(prop[1])] = str(prop[2])
        at = sexpr.child(prop, "at")
        if at is not None:
            prop_pos[str(prop[1])] = (_num(at[1]), _num(at[2]))
    return SymbolDef(
        lib_id=lib_id,
        pins=tuple(pins),
        body=body,
        raw=raw_text,
        is_power=sexpr.child(node, "power") is not None,
        properties=props,
        prop_pos=prop_pos,
    )


def load_symbol(lib_id: str, lib_dirs: tuple[Path, ...]) -> SymbolDef:
    lib, name = lib_id.split(":", 1)
    for directory in lib_dirs:
        path = directory / f"{lib}.kicad_sym"
        if not path.exists():
            continue
        text, spans = _index(str(path))
        if name in spans:
            s, e = spans[name]
            raw = _TOP_NAME.sub(f'(symbol "{lib_id}"', text[s:e], count=1)
            return parse_symbol(lib_id, raw)
    raise KeyError(f"在 {[str(d) for d in lib_dirs]} 里找不到符号 {lib_id}")
