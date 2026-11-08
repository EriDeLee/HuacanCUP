"""M2 的摆放验收（PLAN §12、ADR-0018）：examples/ 的每个样例，
按行排（rows）和自动摆放（auto）各做一遍，
用本机的 fastroute 布线，比总线长 HPWL、未布通网络数、DRC 错误数。

    python eval/m2_placement.py [样例名 ...]       不写就跑全部
    python eval/m2_placement.py --seed 3 ...       换随机种子

输出 runs/m2-placement-<时间>/results.json 和 table.md。
auto 不能比 rows 差（未布通网络数、DRC 错误数）；
变差的样例如实列在表后面，不为了好看去调参数。
li4s_protect：examples/li4s_protect/design.json 在仓库里才比，
没有就跳过并提醒，不自己编这个文件。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eda_agent.config import REPO_ROOT, load_config  # noqa: E402
from eda_agent.emit.kicad_project import emit_kicad_project  # noqa: E402
from eda_agent.flow import copy_project, route_board  # noqa: E402
from eda_agent.gates.drc import unrouted_nets  # noqa: E402
from eda_agent.gates.kicad_reports import drc_gate  # noqa: E402
from eda_agent.ir.models import load_design  # noqa: E402
from eda_agent.placement.run import place_board  # noqa: E402
from eda_agent.tools.kicad_cli import KicadCli  # noqa: E402
from eda_agent.trace import TraceWriter  # noqa: E402

EXAMPLES = REPO_ROOT / "examples"
LI4S = EXAMPLES / "li4s_protect" / "design.json"


def run_variant(
    name: str,
    mode: str,
    project: Path,
    out: Path,
    trace: TraceWriter,
    seed: int | None,
) -> dict[str, Any]:
    cfg = load_config()
    design = load_design(EXAMPLES / name / "design.json")
    logs = out / "logs"
    pcb = copy_project(project, out, name)
    placed = place_board(
        cfg.tools,
        pcb,
        design,
        cfg.placement,
        mode=mode,
        seed=seed,
        work_dir=out / "placement",
        log_dir=logs,
        trace=trace,
    )
    entry: dict[str, Any] = {
        "hpwl_mm": placed.hpwl_mm,
        "placement_violations": placed.violations,
        "placement_seconds": placed.seconds,
        "placement": {
            k: v for k, v in asdict(placed).items() if k in ("seed", "method", "iterations")
        },
    }
    try:
        entry["route_seconds"] = round(
            route_board(cfg.tools, pcb, "fastroute", log_dir=logs, trace=trace), 1
        )
        report = out / "drc.json"
        KicadCli(cfg.tools, log_dir=logs, trace=trace).pcb_drc(pcb, report, severity="all")
        gate = drc_gate(report)
        nets = unrouted_nets(report)
        entry |= {
            "unrouted_nets": len(nets),
            "unrouted_net_names": list(nets),
            "drc_errors": gate.counts["violations_errors"] + gate.counts["parity_errors"],
            "drc_error_types": sorted({f.id for f in gate.findings if f.id != "unconnected_items"}),
        }
    except Exception as exc:  # 一个布线器出错不影响别的样例
        entry["error"] = str(exc)[-800:]
    return entry


def compare(rows: dict[str, Any], auto: dict[str, Any]) -> list[str]:
    """auto 比 rows 差在哪（空 = 没有变差）。"""
    worse = []
    for key, label in (("unrouted_nets", "未布通网络数"), ("drc_errors", "DRC 错误数")):
        if key in rows and key in auto and auto[key] > rows[key]:
            worse.append(f"{label} {rows[key]} → {auto[key]}")
    if "error" in auto and "error" not in rows:
        worse.append("auto 布线出错")
    return worse


def table(results: dict[str, Any]) -> str:
    head = (
        "| 样例 | HPWL rows | HPWL auto | 变化 | 未布通网络 rows / auto | DRC 错误 rows / auto "
        "| auto 摆放违规 | 摆放用时 |\n|---|---|---|---|---|---|---|---|\n"
    )
    lines = []
    for name, entry in results.items():
        rows, auto = entry["rows"], entry["auto"]
        change = auto["hpwl_mm"] / rows["hpwl_mm"] - 1 if rows["hpwl_mm"] else 0.0
        lines.append(
            f"| {name} | {rows['hpwl_mm']:.1f} | {auto['hpwl_mm']:.1f} | {change:+.0%} "
            f"| {rows.get('unrouted_nets', '出错')} / {auto.get('unrouted_nets', '出错')} "
            f"| {rows.get('drc_errors', '出错')} / {auto.get('drc_errors', '出错')} "
            f"| {len(auto['placement_violations'])} | {auto['placement_seconds']:.0f} s |"
        )
    return head + "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("names", nargs="*")
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args(argv)
    paths = sorted(EXAMPLES.glob("*/design.json"))
    if args.names:
        paths = [p for p in paths if p.parent.name in args.names]
    run_dir = REPO_ROOT / "runs" / f"m2-placement-{datetime.now():%Y%m%d-%H%M%S}"
    trace = TraceWriter(run_dir / "trace.jsonl", run_dir.name)
    cfg = load_config()
    results: dict[str, Any] = {}
    for path in paths:
        name = path.parent.name
        out = run_dir / name
        emitted = emit_kicad_project(
            path,
            out / "kicad",
            name,
            cfg.tools,
            log_dir=out / "logs",
            date=f"{datetime.now():%Y-%m-%d}",
            trace=trace,
        )
        entry = {
            mode: run_variant(name, mode, emitted.pcb, out / mode, trace, args.seed)
            for mode in ("rows", "auto")
        }
        entry["worse"] = compare(entry["rows"], entry["auto"])
        results[name] = entry
        (run_dir / "results.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        flag = "变差：" + "；".join(entry["worse"]) if entry["worse"] else "没有变差"
        print(
            f"{name:20s} HPWL {entry['rows']['hpwl_mm']:8.1f} -> {entry['auto']['hpwl_mm']:8.1f}  "
            f"未布通 {entry['rows'].get('unrouted_nets')} -> {entry['auto'].get('unrouted_nets')}  "
            f"DRC {entry['rows'].get('drc_errors')} -> {entry['auto'].get('drc_errors')}  {flag}",
            flush=True,
        )
    text = table(results)
    worse = {n: e["worse"] for n, e in results.items() if e["worse"]}
    text += "\n变差的样例：" + (json.dumps(worse, ensure_ascii=False) if worse else "无") + "\n"
    if LI4S.exists():
        text += (
            "\nli4s_protect 的 design.json 在仓库里，"
            "但这个脚本还没写对比它的手工摆放（等你定怎么比）。\n"
        )
    else:
        text += (
            "\nli4s_protect：examples/li4s_protect/design.json 不在仓库里，"
            "跳过（需要你提供，我不自己编）。\n"
        )
    (run_dir / "table.md").write_text(text, encoding="utf-8")
    print("\n" + text)
    print(run_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
