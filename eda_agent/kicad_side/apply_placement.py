"""把算好的摆放写回电路板：每个封装的位置和朝向。在 KiCad 自带 Python 里运行：

    python apply_placement.py --pcb in.kicad_pcb --placement placement.json --out out.kicad_pcb

placement.json：{"parts": {"R1": {"x_mm": 110.5, "y_mm": 105.0, "rot_deg": 90}, ...}}
x_mm、y_mm 是封装原点的位置，和 footprint_geometry.py 读出来的口径一样。
只依赖标准库和 pcbnew（ADR-0005）。板子上找不到的位号直接报错，不静默跳过。
"""

import argparse
import json
import sys
from pathlib import Path

import pcbnew


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pcb", type=Path, required=True)
    parser.add_argument("--placement", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    parts = json.loads(args.placement.read_text(encoding="utf-8"))["parts"]
    board = pcbnew.LoadBoard(str(args.pcb))
    missing = []
    for ref, spot in sorted(parts.items()):
        fp = board.FindFootprintByReference(ref)
        if fp is None:
            missing.append(ref)
            continue
        fp.SetOrientationDegrees(float(spot["rot_deg"]))
        fp.SetPosition(
            pcbnew.VECTOR2I(pcbnew.FromMM(float(spot["x_mm"])), pcbnew.FromMM(float(spot["y_mm"])))
        )
    if missing:
        print(f"板子上找不到这些位号：{missing}", file=sys.stderr)
        return 1
    if not pcbnew.SaveBoard(str(args.out), board):
        print(f"保存 {args.out} 失败", file=sys.stderr)
        return 1
    print(json.dumps({"moved": len(parts), "out": str(args.out)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
