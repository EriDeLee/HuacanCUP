"""M2 的摆放验收（PLAN §12、ADR-0018）：examples/ 的每个样例，
按行排（rows）和自动摆放（auto）各做一遍，
用本机的 fastroute 布线，比总线长 HPWL、未布通网络数、DRC 错误数。

    python eval/m2_placement.py [样例名 ...]       不写就跑全部
    python eval/m2_placement.py --seed 3 ...       换随机种子

输出 runs/m2-placement-<时间>/results.json 和 table.md。
auto 不能比 rows 差（未布通网络数、DRC 错误数）；
变差的样例如实列在表后面，不为了好看去调参数。
li4s_protect：另有 examples/li4s_protect/manual/plan.json（手摆坐标）时，再加一路 manual：
只取元件位置和角度，J1、J2 仍由建板程序贴边，用同一个布线器重新布线，不含铺铜和丝印。
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
from eda_agent.gates.drc import read_pcb, unrouted_nets  # noqa: E402
from eda_agent.gates.kicad_reports import drc_gate  # noqa: E402
from eda_agent.ir.models import load_design  # noqa: E402
from eda_agent.placement import PlacementResult, board_hpwl, check_board  # noqa: E402
from eda_agent.placement.run import apply_positions, place_board, read_geometry  # noqa: E402
from eda_agent.tools.kicad_cli import KicadCli  # noqa: E402
from eda_agent.trace import TraceWriter  # noqa: E402

EXAMPLES = REPO_ROOT / "examples"
ROUTE_RUNS = 3
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
    route_and_check(cfg, pcb, name, out, trace, entry)
    return entry


def route_once(cfg: Any, pcb: Path, out: Path, trace: TraceWriter) -> dict[str, Any]:
    logs = out / "logs"
    seconds = route_board(cfg.tools, pcb, "fastroute", log_dir=logs, trace=trace)
    report = out / "drc.json"
    KicadCli(cfg.tools, log_dir=logs, trace=trace).pcb_drc(pcb, report, severity="all")
    gate = drc_gate(report)
    nets = unrouted_nets(report)
    return {
        "route_seconds": round(seconds, 1),
        "unrouted_nets": len(nets),
        "unrouted_net_names": list(nets),
        "drc_errors": gate.counts["violations_errors"] + gate.counts["parity_errors"],
        "drc_error_types": sorted({f.id for f in gate.findings if f.id != "unconnected_items"}),
        "drc_warnings": gate.counts["warnings"],
        "track_length_mm": round(sum(t.length_mm for t in read_pcb(pcb).tracks), 1),
    }


def route_and_check(
    cfg: Any, pcb: Path, name: str, out: Path, trace: TraceWriter, entry: dict[str, Any]
) -> None:
    """fastroute 没有种子选项，每次布线的结果不完全一样（ADR-0018）：
    同一块摆好的板布 ROUTE_RUNS 次，各项取最差值；验收是每次都 0 未布通、0 DRC 错误。"""
    runs: list[dict[str, Any]] = []
    try:
        for k in range(ROUTE_RUNS):
            attempt = out / f"route{k}"
            runs.append(route_once(cfg, copy_project(pcb, attempt, name), attempt, trace))
    except Exception as exc:  # 一个布线器出错不影响别的样例
        entry["error"] = str(exc)[-800:]
        return
    entry["route_runs"] = runs
    entry |= {
        "route_seconds": max(r["route_seconds"] for r in runs),
        "unrouted_nets": max(r["unrouted_nets"] for r in runs),
        "unrouted_net_names": sorted({n for r in runs for n in r["unrouted_net_names"]}),
        "drc_errors": max(r["drc_errors"] for r in runs),
        "drc_error_types": sorted({t for r in runs for t in r["drc_error_types"]}),
        "drc_warnings": max(r["drc_warnings"] for r in runs),
        "track_length_mm": max(r["track_length_mm"] for r in runs),
    }


def run_manual(
    name: str, project: Path, plan_path: Path, out: Path, trace: TraceWriter
) -> dict[str, Any]:
    """手摆：只取 plan.json 里元件的位置和角度，套到按行排好的板上；其余和 rows、auto 一样。"""
    cfg = load_config()
    design = load_design(EXAMPLES / name / "design.json")
    logs = out / "logs"
    pcb = copy_project(project, out, name)
    work = out / "placement"
    work.mkdir(parents=True, exist_ok=True)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    base = read_geometry(cfg.tools, pcb, work / "geometry_rows.json", log_dir=logs, trace=trace)
    result = PlacementResult(
        positions={ref: (float(x), float(y), int(rot)) for ref, (x, y, rot) in plan.items()},
        seed=0,
        restarts=0,
        iterations=0,
        method="manual",
        hpwl_mm=0.0,
    )
    placed = pcb.with_name(pcb.stem + "_placed.kicad_pcb")
    apply_positions(cfg.tools, pcb, placed, result, work_dir=work, log_dir=logs, trace=trace)
    placed.replace(pcb)
    for suffix in (".kicad_pro", ".kicad_prl"):
        placed.with_suffix(suffix).unlink(missing_ok=True)
    final = read_geometry(cfg.tools, pcb, work / "geometry_manual.json", log_dir=logs, trace=trace)
    entry: dict[str, Any] = {
        "hpwl_mm": board_hpwl(design, final, cfg.placement),
        "hpwl_rows_mm": board_hpwl(design, base, cfg.placement),
        "placement_violations": check_board(design, final, cfg.placement),
        "outline_mm": list(final.outline),
        "edge_connectors": {
            ref: [final.footprints[ref].x, final.footprints[ref].y, final.footprints[ref].rot]
            for ref in ("J1", "J2")
            if ref in final.footprints
        },
        "plan_parts": len(plan),
    }
    route_and_check(cfg, pcb, name, out, trace, entry)
    return entry


def compare(rows: dict[str, Any], auto: dict[str, Any]) -> list[str]:
    """auto 比 rows 差在哪（空 = 没有变差）；数字是 3 次布线里的最差值。
    另外 auto 自己必须 3 次均为 0 未布通、0 DRC 错误。"""
    worse = []
    for key, label in (("unrouted_nets", "未布通网络数"), ("drc_errors", "DRC 错误数")):
        if auto.get(key, 0) > 0:
            worse.append(f"auto 的{label}不是 3 次均为 0（最差 {auto[key]}）")
    for key, label in (("unrouted_nets", "未布通网络数"), ("drc_errors", "DRC 错误数")):
        if key in rows and key in auto and auto[key] > rows[key]:
            worse.append(f"{label} {rows[key]} → {auto[key]}")
    if "error" in auto and "error" not in rows:
        worse.append("auto 布线出错")
    return worse


def pair(rows: dict[str, Any], auto: dict[str, Any], key: str) -> str:
    return f"{rows.get(key, '出错')} / {auto.get(key, '出错')}"


def table(results: dict[str, Any]) -> str:
    head = (
        "布线每种摆法 3 次，表里的未布通、DRC、走线总长都是 3 次里的最差值。\n\n"
        "| 样例 | HPWL rows | HPWL auto | 变化 | 未布通网络 rows / auto | DRC 错误 rows / auto "
        "| DRC 警告 rows / auto | 走线总长 mm rows / auto | auto 摆放违规 | 摆放用时 |\n"
        "|---|---|---|---|---|---|---|---|---|---|\n"
    )
    lines = []
    for name, entry in results.items():
        rows, auto = entry["rows"], entry["auto"]
        change = auto["hpwl_mm"] / rows["hpwl_mm"] - 1 if rows["hpwl_mm"] else 0.0
        lines.append(
            f"| {name} | {rows['hpwl_mm']:.1f} | {auto['hpwl_mm']:.1f} | {change:+.0%} "
            f"| {pair(rows, auto, 'unrouted_nets')} | {pair(rows, auto, 'drc_errors')} "
            f"| {pair(rows, auto, 'drc_warnings')} | {pair(rows, auto, 'track_length_mm')} "
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
        plan = EXAMPLES / name / "manual" / "plan.json"
        if plan.exists():
            entry["manual"] = run_manual(name, emitted.pcb, plan, out / "manual", trace)
            (run_dir / "results.json").write_text(
                json.dumps(results | {name: entry}, ensure_ascii=False, indent=2),
                encoding="utf-8",
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
    manual = {n: e["manual"] for n, e in results.items() if "manual" in e}
    for name, m in manual.items():
        rows, auto = results[name]["rows"], results[name]["auto"]
        text += (
            f"\n{name} 三种摆法（同一个布线器 fastroute，各布 {ROUTE_RUNS} 次取最差值）：\n\n"
            "| 摆法 | HPWL | 未布通网络 | DRC 错误 | DRC 警告 | 走线总长 mm |\n"
            "|---|---|---|---|---|---|\n"
        )
        for label, e in (("手摆", m), ("auto", auto), ("rows", rows)):
            text += (
                f"| {label} | {e['hpwl_mm']:.1f} | {e.get('unrouted_nets', '出错')} "
                f"| {e.get('drc_errors', '出错')} | {e.get('drc_warnings', '出错')} "
                f"| {e.get('track_length_mm', '出错')} |\n"
            )
        text += (
            f"\n手摆板框 {m['outline_mm']}，J1/J2 {m['edge_connectors']}，"
            f"plan 里 {m['plan_parts']} 个元件，摆放违规：{m['placement_violations'] or '无'}\n"
        )
    if not LI4S.exists():
        text += "\nli4s_protect：examples/li4s_protect/design.json 不在仓库里，跳过。\n"
    (run_dir / "table.md").write_text(text, encoding="utf-8")
    print("\n" + text)
    print(run_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
