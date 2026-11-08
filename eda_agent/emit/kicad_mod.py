"""读 KiCad 封装文件（.kicad_mod）里的焊盘编号。只用标准库，G1 用它核对符号引脚和焊盘对得上。

封装几何（焊盘位置、courtyard）要经 pcbnew 读，
在 eda_agent/kicad_side/footprint_geometry.py（ADR-0005）。
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from eda_agent.kicad_side import sexpr


class FootprintNotFound(LookupError):
    pass


@cache
def _pad_numbers(path: str) -> frozenset[str]:
    node = sexpr.parse(Path(path).read_text(encoding="utf-8"))[0]
    # 没有编号的焊盘（安装孔、散热焊盘的辅助焊盘）编号是空字符串，不算
    return frozenset(str(p[1]) for p in sexpr.children(node, "pad") if len(p) > 1 and str(p[1]))


def pad_numbers(footprint: str, footprint_dir: Path) -> frozenset[str]:
    """ "库名:封装名" → 这个封装上有编号的焊盘的编号集合（同号的多个焊盘算一个）。"""
    lib, name = footprint.split(":", 1)
    path = footprint_dir / f"{lib}.pretty" / f"{name}.kicad_mod"
    if not path.exists():
        raise FootprintNotFound(f"找不到封装 {footprint}（{path}）")
    return _pad_numbers(str(path))
