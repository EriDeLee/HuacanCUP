"""布线器对比（ADR-0004）：在 KiCad 自带的示例板上，删掉全部走线、过孔、铺铜，元件位置不动，
让每个候选布线器各布一次，再用 KiCad DRC（含原理图一致性）打分。原板的 DRC 结果作为参照。

    python eval/router_bakeoff.py --routers fastroute,krt ecc83/ecc83-pp ...
    python eval/router_bakeoff.py --routers tracemaker --out runs/bakeoff-tm ...   (只在 Linux 上)

位置参数是「示例目录/板名」（不带扩展名）。示例工程只复制到 runs/ 下使用，不进 git。
Freerouting 和 kicad-tools 已从项目里去掉（ADR-0004），它们的成绩是用提交 aeee649 里的这个脚本测的。
TraceMaker 只有 Linux 版，由 .github/workflows/bakeoff.yml 在 GitHub Actions 上跑。
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eda_agent.config import REPO_ROOT, ToolsConfig, load_config  # noqa: E402
from eda_agent.gates.kicad_reports import drc_gate  # noqa: E402
from eda_agent.gates.report import GateReport  # noqa: E402
from eda_agent.tools.kicad_cli import KicadCli  # noqa: E402
from eda_agent.tools.kicad_python import run_kicad_script  # noqa: E402
from eda_agent.tools.routers import fastroute_route, krt_route, tracemaker_route  # noqa: E402
from eda_agent.trace import TraceWriter  # noqa: E402

ROUTERS = ("fastroute", "krt", "tracemaker")
DSN_ROUTERS = {"fastroute": fastroute_route}
PCB_ROUTERS = {"krt": krt_route, "tracemaker": tracemaker_route}
_COPPER_TEXT = re.compile(r'\(gr_text\s+"[^"]*"\s+\(at[^)]*\)\s+\(layer\s+"(?:F|B|In\d+)\.Cu"')


def board_counts(pcb: Path) -> dict[str, int]:
    text = pcb.read_text(encoding="utf-8")
    return {
        "footprints": len(re.findall(r"^\s*\(footprint\s", text, flags=re.M)),
        "segments": len(re.findall(r"^\s*\(segment\s", text, flags=re.M)),
        "vias": len(re.findall(r"^\s*\(via\s", text, flags=re.M)),
        "copper_texts": len(_COPPER_TEXT.findall(text)),
    }


def error_types(gate: GateReport) -> dict[str, int]:
    return dict(collections.Counter(f.id for f in gate.findings))


def _router_summary(path: Path) -> dict[str, Any]:
    """布线器自己写的 JSON 摘要里，挑数值和短字符串（不同布线器的字段名不一样，原样保留）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"unreadable": str(path.name)}
    if not isinstance(data, dict):
        return {}
    return {
        k: v
        for k, v in data.items()
        if isinstance(v, (int, float, bool)) or (isinstance(v, str) and len(v) <= 80)
    }


def route_one(
    tools: ToolsConfig, kicad: KicadCli, pcb: Path, router: str, logs: Path, trace: TraceWriter
) -> dict[str, Any]:
    work = pcb.parent
    stripped = run_kicad_script(
        tools, "strip_routing.py", ["--pcb", pcb, "--out", pcb], cwd=work, log_dir=logs, trace=trace
    )
    entry: dict[str, Any] = {"strip": stripped.last_json()}
    start = time.monotonic()
    try:
        if router in DSN_ROUTERS:
            dsn, ses = pcb.with_suffix(".dsn"), pcb.with_suffix(".ses")
            args: list[str | Path] = ["export", "--pcb", pcb, "--dsn", dsn]
            run_kicad_script(tools, "specctra.py", args, cwd=work, log_dir=logs, trace=trace)
            DSN_ROUTERS[router](tools, dsn, ses, log_dir=logs, trace=trace)
            args = ["import", "--pcb", pcb, "--ses", ses]
            run_kicad_script(tools, "specctra.py", args, cwd=work, log_dir=logs, trace=trace)
        else:
            routed = pcb.with_name(f"{pcb.stem}_routed.kicad_pcb")
            summary = work / f"{router}_summary.json"
            route = PCB_ROUTERS[router]
            result = route(tools, pcb, routed, summary_json=summary, log_dir=logs, trace=trace)
            entry["exit_code"] = result.exit_code
            # 布线器自己的统计（布通几条、用了哪个变体……）只作参考，打分以 KiCad DRC 为准
            if summary.exists():
                entry["router_summary"] = _router_summary(summary)
            routed.replace(pcb)
            routed.with_suffix(".kicad_pro").unlink(missing_ok=True)
        entry["seconds"] = round(time.monotonic() - start, 1)
        report = work / f"drc-{router}.json"
        kicad.pcb_drc(pcb, report, severity="all")
        gate = drc_gate(report)
        entry.update(passed=gate.passed, **gate.counts, error_types=error_types(gate))
        entry.update(board_counts(pcb))
    except Exception as exc:
        entry.update(seconds=round(time.monotonic() - start, 1), error=str(exc)[-1500:])
    return entry


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--routers", default="fastroute,krt")
    parser.add_argument("--out", type=Path, help="运行目录（默认 runs/bakeoff-<时间>）")
    parser.add_argument("boards", nargs="+")
    args = parser.parse_args(argv)
    routers = [r for r in args.routers.split(",") if r]
    unknown = sorted(set(routers) - set(ROUTERS))
    if unknown:
        parser.error(f"不认识的布线器：{unknown}")

    tools = load_config().tools
    run_dir = args.out or REPO_ROOT / "runs" / f"bakeoff-{datetime.now():%Y%m%d-%H%M%S}"
    run_dir = run_dir if run_dir.is_absolute() else REPO_ROOT / run_dir
    trace = TraceWriter(run_dir / "trace.jsonl", run_dir.name)
    logs = run_dir / "logs"
    kicad = KicadCli(tools, log_dir=logs, trace=trace)
    results: dict[str, Any] = {"run_id": run_dir.name, "routers": routers, "boards": {}}
    for spec in args.boards:
        demo_dir, name = spec.split("/", 1)
        source = tools.kicad_share / "demos" / demo_dir
        board_result: dict[str, Any] = {}
        original = run_dir / demo_dir.replace(" ", "_") / "original"
        shutil.copytree(source, original)
        report = original / "drc-original.json"
        kicad.pcb_drc(original / f"{name}.kicad_pcb", report, severity="all")
        gate = drc_gate(report)
        board_result["original"] = {"passed": gate.passed, **gate.counts}
        board_result["original"]["error_types"] = error_types(gate)
        board_result["original"] |= board_counts(original / f"{name}.kicad_pcb")
        for router in routers:
            work = original.parent / router
            shutil.copytree(source, work)
            board_result[router] = route_one(
                tools, kicad, work / f"{name}.kicad_pcb", router, logs, trace
            )
        results["boards"][spec] = board_result
        (run_dir / "results.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print(run_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
