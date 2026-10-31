"""M0 实验：用 KiCad 示例工程 pic_programmer（63 个元件、两层板）做两件事。

1. 原理图可读性（PLAN M0 待验证项③）：示例原理图 → kicad-cli 网表 → design.json →
   我们的发射器（SKiDL + schematizer）重画 → PDF，和示例原图的 PDF 对照着看。
2. 布线器对比（ADR-0004）：删掉示例板上的全部走线、过孔、铺铜，元件位置不动，
   Freerouting 和 KiCadRoutingTools 各布一次，都过 KiCad DRC（含原理图一致性），
   记录没连上的数量、违规数、用时、走线段数和过孔数。原板的 DRC 结果作为参照。

    python eval/m0_pic_programmer.py

示例工程只复制到 runs/ 下使用，不进 git。
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eda_agent.config import REPO_ROOT, load_config  # noqa: E402
from eda_agent.gates.kicad_reports import drc_gate, erc_gate  # noqa: E402
from eda_agent.kicad_side import sexpr  # noqa: E402
from eda_agent.tools.kicad_cli import KicadCli  # noqa: E402
from eda_agent.tools.kicad_python import run_kicad_script  # noqa: E402
from eda_agent.tools.routers import freerouting_route, krt_route  # noqa: E402
from eda_agent.tools.runner import run_tool  # noqa: E402
from eda_agent.tools.sandbox import sandbox_env  # noqa: E402
from eda_agent.trace import TraceWriter  # noqa: E402

NAME = "pic_programmer"


def netlist_to_design(netlist: Path) -> dict[str, Any]:
    export = sexpr.parse(netlist.read_text(encoding="utf-8"))[0]
    parts = []
    for comp in sexpr.children(sexpr.child(export, "components"), "comp"):
        src = sexpr.child(comp, "libsource")
        parts.append(
            {
                "ref": sexpr.value(comp, "ref"),
                "value": sexpr.value(comp, "value"),
                "symbol": f"{sexpr.value(src, 'lib')}:{sexpr.value(src, 'part')}",
                "footprint": sexpr.value(comp, "footprint"),
            }
        )
    nets = []
    for net in sexpr.children(sexpr.child(export, "nets"), "net"):
        name = sexpr.value(net, "name")
        nodes = [
            [sexpr.value(n, "ref"), sexpr.value(n, "pin")] for n in sexpr.children(net, "node")
        ]
        # 只有一个引脚、本来就不连的网络不进 IR。
        if not name.startswith("unconnected-") and len(nodes) > 1:
            nets.append({"name": name, "nodes": nodes})
    return {"parts": parts, "nets": nets, "board": {"size_mm": [120, 80], "layers": 2}}


def board_counts(pcb: Path) -> dict[str, int]:
    text = pcb.read_text(encoding="utf-8")
    return {
        "segments": len(re.findall(r"^\s*\(segment\s", text, flags=re.M)),
        "vias": len(re.findall(r"^\s*\(via\s", text, flags=re.M)),
    }


def main() -> int:
    tools = load_config().tools
    run_dir = REPO_ROOT / "runs" / f"m0-pic-{datetime.now():%Y%m%d-%H%M%S}"
    trace = TraceWriter(run_dir / "trace.jsonl", run_dir.name)
    logs = run_dir / "logs"
    kicad = KicadCli(tools, log_dir=logs, trace=trace)
    demo = run_dir / "demo"
    shutil.copytree(tools.kicad_share / "demos" / NAME, demo)
    results: dict[str, Any] = {"run_id": run_dir.name}

    # 1. 原理图可读性
    netlist = run_dir / f"{NAME}.net"
    kicad.sch_export_netlist(demo / f"{NAME}.kicad_sch", netlist)
    full = netlist_to_design(netlist)
    connected_refs = {ref for net in full["nets"] for ref, _ in net["nodes"]}
    variants = {
        # 全部 63 个元件，正常模式（摆放 + 连线）
        "all-normal": (full, "normal"),
        # 去掉没有任何连接的元件（安装孔等），正常模式
        "connected-normal": (
            full | {"parts": [p for p in full["parts"] if p["ref"] in connected_refs]},
            "normal",
        ),
        # 全部元件，只挂网络标签、不连线
        "all-labels": (full, "labels"),
    }
    results["schematic"] = {}
    for variant, (design, mode) in variants.items():
        design_path = run_dir / f"design-{variant}.json"
        design_path.write_text(json.dumps(design, ensure_ascii=False, indent=1), encoding="utf-8")
        sch_dir = run_dir / f"sch-{variant}"
        emit = run_tool(
            "skidl",
            [
                sys.executable,
                "-m",
                "eda_agent.emit.skidl_emit",
                "--design",
                design_path,
                "--out-dir",
                sch_dir,
                "--name",
                NAME,
                "--lib-dir",
                demo,
                "--mode",
                mode,
            ],
            cwd=sch_dir,
            env=sandbox_env(tools),
            timeout_s=tools.timeouts.route_s,
            log_dir=logs,
            trace=trace,
        )
        entry: dict[str, Any] = {
            "parts": len(design["parts"]),
            "nets": len(design["nets"]),
            "exit_code": emit.exit_code,
            "seconds": round(emit.duration_s, 1),
        }
        if emit.exit_code != 0:
            entry["error"] = emit.stderr.strip().splitlines()[-1][-300:]
        regenerated = sch_dir / f"{NAME}.kicad_sch"
        if emit.exit_code == 0 and regenerated.exists():
            kicad.sch_export_pdf(regenerated, run_dir / f"{variant}.pdf")
            report = run_dir / f"erc-{variant}.json"
            kicad.sch_erc(regenerated, report, severity="all")
            entry["erc"] = erc_gate(report).counts
        results["schematic"][variant] = entry
    kicad.sch_export_pdf(demo / f"{NAME}.kicad_sch", run_dir / "original.pdf")

    # 2. 布线器对比
    baseline = run_dir / "drc-original.json"
    kicad.pcb_drc(demo / f"{NAME}.kicad_pcb", baseline, severity="all")
    results["original"] = drc_gate(baseline).counts | board_counts(demo / f"{NAME}.kicad_pcb")
    for router in ("freerouting", "krt"):
        work = run_dir / f"route-{router}"
        shutil.copytree(demo, work)
        pcb = work / f"{NAME}.kicad_pcb"
        stripped = run_kicad_script(
            tools,
            "strip_routing.py",
            ["--pcb", pcb, "--out", pcb],
            cwd=work,
            log_dir=logs,
            trace=trace,
        )
        entry: dict[str, Any] = {"strip": stripped.last_json()}
        start = time.monotonic()
        try:
            if router == "freerouting":
                dsn, ses = pcb.with_suffix(".dsn"), pcb.with_suffix(".ses")
                run_kicad_script(
                    tools,
                    "specctra.py",
                    ["export", "--pcb", pcb, "--dsn", dsn],
                    cwd=work,
                    log_dir=logs,
                    trace=trace,
                )
                freerouting_route(tools, dsn, ses, log_dir=logs, trace=trace)
                run_kicad_script(
                    tools,
                    "specctra.py",
                    ["import", "--pcb", pcb, "--ses", ses],
                    cwd=work,
                    log_dir=logs,
                    trace=trace,
                )
            else:
                routed = pcb.with_name(f"{NAME}_routed.kicad_pcb")
                krt_route(
                    tools,
                    pcb,
                    routed,
                    summary_json=work / "krt_summary.json",
                    log_dir=logs,
                    trace=trace,
                )
                routed.replace(pcb)
                routed.with_suffix(".kicad_pro").unlink(missing_ok=True)
            entry["seconds"] = round(time.monotonic() - start, 1)
            report = run_dir / f"drc-{router}.json"
            kicad.pcb_drc(pcb, report, severity="all")
            gate = drc_gate(report)
            entry.update(passed=gate.passed, **gate.counts, **board_counts(pcb))
        except Exception as exc:
            entry.update(seconds=round(time.monotonic() - start, 1), error=str(exc)[-1500:])
        results[router] = entry

    (run_dir / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print(run_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
