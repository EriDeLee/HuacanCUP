"""design.json → SKiDL → KiCad 10 原理图（schematizer 自动摆放、连线）。

作为子进程在 sandbox 环境里运行（KICAD10_SYMBOL_DIR 指向 KiCad 自带符号库）：

    python -m eda_agent.emit.skidl_emit --design design.json --out-dir kicad --name led

SKiDL 会在当前目录写日志文件，所以调用方把 cwd 设成输出目录。
M0 只处理单页原理图；按功能块分页在 M1 做。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from eda_agent.ir.models import Design, load_design


def emit_schematic(design: Design, out_dir: Path, name: str) -> Path:
    import skidl

    skidl.set_default_tool(skidl.KICAD10)
    parts = {}
    for part in design.parts:
        lib, symbol = part.symbol.split(":", 1)
        parts[part.ref] = skidl.Part(
            lib, symbol, ref=part.ref, value=part.value, footprint=part.footprint
        )
    for net in design.nets:
        skidl_net = skidl.Net(net.name)
        for ref, pin in net.nodes:
            # .p[] 只按引脚编号查找，避免 "1" 误匹配到名字里带 1 的引脚。
            skidl_net += parts[ref].p[pin]
        if net.external_supply:
            # 电从板外进来：放 PWR_FLAG 告诉 ERC 这个网络有电源驱动（ADR-0008）。
            skidl_net += skidl.Part("power", "PWR_FLAG").p["1"]

    out_dir.mkdir(parents=True, exist_ok=True)
    skidl.generate_schematic(filepath=str(out_dir), top_name=name, title=name, tool=skidl.KICAD10)
    sch = out_dir / f"{name}.kicad_sch"
    if not sch.exists():
        raise RuntimeError(f"SKiDL 没有生成 {sch}")
    return sch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--design", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args(argv)
    print(emit_schematic(load_design(args.design), args.out_dir, args.name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
