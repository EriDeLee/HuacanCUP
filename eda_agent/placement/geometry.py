"""电路板上的封装几何（kicad_side/footprint_geometry.py 读出来的 JSON）。

坐标单位 mm，和 KiCad 的板坐标一致（y 向下）。角度 0、90、180、270 度，正方向是 pcbnew 的
SetOrientationDegrees 的正方向；每个朝向下的 courtyard 外包框和焊盘中心都是 pcbnew 把封装转过去
以后量的，相对封装原点，所以这里不需要知道旋转的正负号。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

Rect = tuple[float, float, float, float]  # x0, y0, x1, y1
ANGLES = (0, 90, 180, 270)


@dataclass(frozen=True)
class Shape:
    """一个封装在某个朝向下的几何，都相对封装原点。"""

    courtyard: Rect
    # 焊盘编号 → 这个编号的所有焊盘中心（同号的可能有好几个）
    pads: dict[str, list[tuple[float, float]]]


@dataclass(frozen=True)
class FootprintGeom:
    ref: str
    x: float
    y: float
    rot: float
    board_only: bool  # 只在板子上、不在原理图里（安装孔）：摆放时是不能动的障碍
    courtyard_missing: bool
    shapes: dict[int, Shape]

    def rect(self, angle: int, x: float | None = None, y: float | None = None) -> Rect:
        """放在 (x, y)、转 angle 度（0、90、180、270）时 courtyard 的外包框（板坐标）。
        缺省用当前位置。"""
        cx = self.x if x is None else x
        cy = self.y if y is None else y
        x0, y0, x1, y1 = self.shapes[angle].courtyard
        return (cx + x0, cy + y0, cx + x1, cy + y1)

    def size(self, angle: int) -> tuple[float, float]:
        x0, y0, x1, y1 = self.shapes[angle].courtyard
        return (x1 - x0, y1 - y0)


@dataclass(frozen=True)
class BoardGeom:
    outline: Rect
    footprints: dict[str, FootprintGeom] = field(default_factory=dict)

    @property
    def width(self) -> float:
        return self.outline[2] - self.outline[0]

    @property
    def height(self) -> float:
        return self.outline[3] - self.outline[1]

    def fixed(self) -> list[FootprintGeom]:
        return [f for f in self.footprints.values() if f.board_only]


def parse_geometry(data: dict[str, Any]) -> BoardGeom:
    footprints: dict[str, FootprintGeom] = {}
    for item in data["footprints"]:
        shapes = {
            int(angle): Shape(
                courtyard=tuple(raw["courtyard"]),
                pads={
                    number: [(p[0], p[1]) for p in centers]
                    for number, centers in raw["pads"].items()
                },
            )
            for angle, raw in item["shapes"].items()
        }
        footprints[item["ref"]] = FootprintGeom(
            ref=item["ref"],
            x=item["x_mm"],
            y=item["y_mm"],
            rot=float(item["rot_deg"]),
            board_only=bool(item["board_only"]),
            courtyard_missing=bool(item["courtyard_missing"]),
            shapes=shapes,
        )
    return BoardGeom(outline=tuple(data["outline_mm"]), footprints=footprints)


def load_geometry(path: Path) -> BoardGeom:
    return parse_geometry(json.loads(path.read_text(encoding="utf-8")))


def overlap_depth(a: Rect, b: Rect) -> float:
    """两个矩形重叠的深度（较小的那个方向上的穿透量）；不重叠返回 0。"""
    dx = min(a[2], b[2]) - max(a[0], b[0])
    dy = min(a[3], b[3]) - max(a[1], b[1])
    return min(dx, dy) if dx > 0 and dy > 0 else 0.0
