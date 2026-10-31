"""无界面导出 Specctra DSN、导回布线结果 SES（M0 待验证项①）。

KiCad 10 的 kicad-cli 没有这两个命令，只能走 pcbnew 的 Python 接口。在 KiCad 自带 Python 里运行：

    python specctra.py export --pcb led.kicad_pcb --dsn led.dsn
    python specctra.py import --pcb led.kicad_pcb --ses led.ses --out led_routed.kicad_pcb
"""

import argparse
import sys
from pathlib import Path

import pcbnew


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    exp = sub.add_parser("export")
    exp.add_argument("--pcb", type=Path, required=True)
    exp.add_argument("--dsn", type=Path, required=True)
    imp = sub.add_parser("import")
    imp.add_argument("--pcb", type=Path, required=True)
    imp.add_argument("--ses", type=Path, required=True)
    imp.add_argument("--out", type=Path)
    args = parser.parse_args()

    board = pcbnew.LoadBoard(str(args.pcb))
    if args.command == "export":
        ok = pcbnew.ExportSpecctraDSN(board, str(args.dsn))
        target = args.dsn
    else:
        ok = pcbnew.ImportSpecctraSES(board, str(args.ses))
        target = args.out or args.pcb
        ok = ok and pcbnew.SaveBoard(str(target), board)
    if not ok or not target.exists():
        print(f"{args.command} 失败：{target}", file=sys.stderr)
        return 1
    print(target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
