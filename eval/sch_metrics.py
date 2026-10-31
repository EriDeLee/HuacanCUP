"""同一把尺子量原理图：导线条数、总长度、最长一根、横竖交叉数。

    python eval/sch_metrics.py a.kicad_sch b.kicad_sch
    python eval/sch_metrics.py --region=155,115,295,172 pic_programmer.kicad_sch

只数横平竖直的导线；交叉 = 一横一竖，各自的内部相交（端点相碰、T 形不算）。
KiCad 里交叉处没有结点圆点就是不相连，这里统一都数。
--region=x0,y0,x1,y1 只数两个端点都落在区域里的导线（mm，图纸坐标），用来量一张大图里的某一块；
之后的文件都用这个区域，要换区域或取消就另起一次命令。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

WIRE = re.compile(
    r"\(wire\s*\(pts\s*\(xy\s+([-\d.]+)\s+([-\d.]+)\)\s*\(xy\s+([-\d.]+)\s+([-\d.]+)\)", re.S
)

Wire = tuple[float, float, float, float]


def read_wires(path: Path, region: tuple[float, ...] | None = None) -> list[Wire]:
    text = path.read_text(encoding="utf-8")
    wires = [(float(a), float(b), float(c), float(d)) for a, b, c, d in WIRE.findall(text)]
    if region:
        x0, y0, x1, y1 = region
        wires = [
            w
            for w in wires
            if x0 <= w[0] <= x1 and x0 <= w[2] <= x1 and y0 <= w[1] <= y1 and y0 <= w[3] <= y1
        ]
    return wires


def measure(wires: list[Wire]) -> dict[str, int]:
    horizontal = [w for w in wires if abs(w[1] - w[3]) < 1e-6 and abs(w[0] - w[2]) > 1e-6]
    vertical = [w for w in wires if abs(w[0] - w[2]) < 1e-6 and abs(w[1] - w[3]) > 1e-6]
    lengths = [abs(w[0] - w[2]) + abs(w[1] - w[3]) for w in wires]
    crossings = sum(
        1
        for h in horizontal
        for v in vertical
        if min(h[0], h[2]) + 1e-6 < v[0] < max(h[0], h[2]) - 1e-6
        and min(v[1], v[3]) + 1e-6 < h[1] < max(v[1], v[3]) - 1e-6
    )
    return {
        "wires": len(wires),
        "diagonal": len(wires) - len(horizontal) - len(vertical),
        "length_mm": round(sum(lengths)),
        "longest_mm": round(max(lengths, default=0)),
        "crossings": crossings,
    }


def main(argv: list[str]) -> int:
    region: tuple[float, ...] | None = None
    for arg in argv:
        if arg.startswith("--region="):
            region = tuple(float(v) for v in arg.split("=", 1)[1].split(","))
            continue
        print(Path(arg).name, measure(read_wires(Path(arg), region)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
