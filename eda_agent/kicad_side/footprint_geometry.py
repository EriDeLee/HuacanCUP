"""读电路板上每个封装的几何，写成 JSON，给自动摆放用（ADR-0018）。在 KiCad 自带 Python 里运行：

    python footprint_geometry.py --pcb board.kicad_pcb --out geometry.json

只依赖标准库和 pcbnew（ADR-0005）。几何是 pcbnew 自己算的，不是我们去解析 .kicad_mod：
- 板框：Edge.Cuts 的外包框；
- 每个封装：当前位置和角度；0、90、180、270 度四个朝向下，courtyard 的外包框和每个焊盘的中心，
  都相对封装原点。把封装真的转过去再量，所以不用在这里猜 KiCad 的旋转正负号；
- 不在网表里的封装（安装孔，board_only）也列出来，摆放时当作不能动的障碍。
courtyard 没有画的封装，用焊盘和图形的外包框外扩 0.25 mm 代替，并标出 courtyard_missing。
"""

import argparse
import json
import sys
from pathlib import Path

import pcbnew

ANGLES = (0, 90, 180, 270)
FALLBACK_MARGIN_MM = 0.25


def mm(value):
    return round(pcbnew.ToMM(value), 4)


def box(rect):
    """pcbnew 的 BOX2I → [x0, y0, x1, y1]（mm）。"""
    return [mm(rect.GetLeft()), mm(rect.GetTop()), mm(rect.GetRight()), mm(rect.GetBottom())]


def courtyard_box(fp):
    """封装的 courtyard 外包框；没有 courtyard 返回 None。"""
    fp.BuildCourtyardCaches()
    shape = fp.GetCourtyard(pcbnew.F_CrtYd)
    if shape.OutlineCount() == 0:
        shape = fp.GetCourtyard(pcbnew.B_CrtYd)
    if shape.OutlineCount() == 0:
        return None
    return box(shape.BBox())


def at_angle(fp, angle):
    """封装转到 angle 度以后：courtyard 外包框、焊盘中心，都相对封装原点。"""
    fp.SetOrientationDegrees(angle)
    origin = fp.GetPosition()
    crt = courtyard_box(fp)
    missing = crt is None
    if missing:
        rect = fp.GetBoundingBox(False)
        crt = box(rect)
        crt = [
            crt[0] - FALLBACK_MARGIN_MM,
            crt[1] - FALLBACK_MARGIN_MM,
            crt[2] + FALLBACK_MARGIN_MM,
            crt[3] + FALLBACK_MARGIN_MM,
        ]
    crt = [
        round(crt[0] - mm(origin.x), 4),
        round(crt[1] - mm(origin.y), 4),
        round(crt[2] - mm(origin.x), 4),
        round(crt[3] - mm(origin.y), 4),
    ]
    pads = {}
    for pad in fp.Pads():
        pos = pad.GetPosition()
        pads.setdefault(pad.GetNumber(), []).append(
            [round(mm(pos.x) - mm(origin.x), 4), round(mm(pos.y) - mm(origin.y), 4)]
        )
    return {"courtyard": crt, "pads": pads}, missing


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pcb", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    board = pcbnew.LoadBoard(str(args.pcb))
    outline = box(board.GetBoardEdgesBoundingBox())
    parts = []
    # pcbnew 给封装的顺序跟着文件里的 UUID 走，每次生成的板子都不一样；按位号排，输出才可重复
    for fp in sorted(board.GetFootprints(), key=lambda item: item.GetReference()):
        origin = fp.GetPosition()
        original = fp.GetOrientationDegrees()
        shapes = {}
        missing = False
        for angle in ANGLES:
            shapes[str(angle)], miss = at_angle(fp, angle)
            missing = missing or miss
        fp.SetOrientationDegrees(original)
        parts.append(
            {
                "ref": fp.GetReference(),
                "x_mm": mm(origin.x),
                "y_mm": mm(origin.y),
                "rot_deg": round(original, 3) % 360,
                "board_only": bool(fp.IsBoardOnly()),
                "courtyard_missing": missing,
                "shapes": shapes,
            }
        )
    data = {"outline_mm": outline, "footprints": parts}
    args.out.write_text(json.dumps(data, indent=1), encoding="utf-8")
    print(json.dumps({"footprints": len(parts), "outline_mm": outline, "out": str(args.out)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
