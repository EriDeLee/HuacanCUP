"""M0 实验（ADR-0009）：把 pic_programmer 按连接关系切成每块不超过 N 个元件的功能块，
每块画一张子页（SKiDL Group + schematizer auto_stub），看每页的符号是否都在图纸内。

PLAN §15 对「原理图可读性差」的对策是「按功能块分层（每块一页）」；
schematizer 文档也建议每块 5–15 个元件。

    python eval/m0_schematic_blocks.py 12

切块规则（通用，不针对这块板）：忽略电源网络后按连接关系做广度优先，攒满 N 个就开新块；
完全不连线的元件（安装孔等）不进原理图（schematizer 会因它们崩溃，见 ADR-0009）。
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eda_agent.config import REPO_ROOT, load_config  # noqa: E402
from eda_agent.gates.kicad_reports import erc_gate  # noqa: E402
from eda_agent.gates.schematic_layout import layout_gate  # noqa: E402
from eda_agent.kicad_side import sexpr  # noqa: E402
from eda_agent.tools.kicad_cli import KicadCli  # noqa: E402
from eda_agent.tools.runner import run_tool  # noqa: E402
from eda_agent.tools.sandbox import sandbox_env  # noqa: E402
from eda_agent.trace import TraceWriter  # noqa: E402

NAME = "pic_programmer"
POWER_NET = re.compile(r"^(\+.*|GND|VCC|VDD|VSS|VEE|.*GND)$", re.IGNORECASE)


def load_netlist(netlist: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
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
        if not name.startswith("unconnected-") and len(nodes) > 1:
            nets.append({"name": name, "nodes": nodes})
    return parts, nets


def partition(parts: list[dict[str, Any]], nets: list[dict[str, Any]], size: int) -> dict[str, str]:
    neighbours: dict[str, set[str]] = {p["ref"]: set() for p in parts}
    for net in nets:
        if POWER_NET.match(net["name"].split("/")[-1]):
            continue
        refs = {ref for ref, _ in net["nodes"]}
        for ref in refs:
            neighbours[ref] |= refs - {ref}
    block_of: dict[str, str] = {}
    for seed in sorted(neighbours):
        if seed in block_of:
            continue
        block = f"B{len(set(block_of.values())) + 1:02d}"
        queue = deque([seed])
        members = 0
        while queue and members < size:
            ref = queue.popleft()
            if ref in block_of:
                continue
            block_of[ref] = block
            members += 1
            queue.extend(sorted(neighbours[ref] - block_of.keys()))
    return block_of


def main(size: int) -> int:
    tools = load_config().tools
    run_dir = REPO_ROOT / "runs" / f"m0-blocks{size}-{datetime.now():%Y%m%d-%H%M%S}"
    trace = TraceWriter(run_dir / "trace.jsonl", run_dir.name)
    logs = run_dir / "logs"
    kicad = KicadCli(tools, log_dir=logs, trace=trace)
    demo = run_dir / "demo"
    shutil.copytree(tools.kicad_share / "demos" / NAME, demo)
    netlist = run_dir / f"{NAME}.net"
    kicad.sch_export_netlist(demo / f"{NAME}.kicad_sch", netlist)
    parts, nets = load_netlist(netlist)
    connected = {ref for net in nets for ref, _ in net["nodes"]}
    parts = [p for p in parts if p["ref"] in connected]
    block_of = partition(parts, nets, size)
    for part in parts:
        part["block"] = block_of[part["ref"]]
    design = {"parts": parts, "nets": nets, "board": {"size_mm": [120, 80], "layers": 2}}
    design_path = run_dir / "design.json"
    design_path.write_text(json.dumps(design, ensure_ascii=False, indent=1), encoding="utf-8")

    sch_dir = run_dir / "sch"
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
            "--auto-stub",
            "--blocks-as-sheets",
        ],
        cwd=sch_dir,
        env=sandbox_env(tools),
        timeout_s=tools.timeouts.route_s,
        log_dir=logs,
        trace=trace,
    )
    blocks = sorted(set(block_of.values()))
    results: dict[str, Any] = {
        "run_id": run_dir.name,
        "block_size_max": size,
        "parts": len(parts),
        "blocks": {b: sorted(r for r, x in block_of.items() if x == b) for b in blocks},
        "exit_code": emit.exit_code,
        "seconds": round(emit.duration_s, 1),
    }
    if emit.exit_code != 0:
        results["error"] = emit.stderr.strip().splitlines()[-1][-300:]
    else:
        results["sheets"] = {
            f.name: layout_gate(f).counts for f in sorted(sch_dir.glob("*.kicad_sch"))
        }
        top = sch_dir / f"{NAME}.kicad_sch"
        kicad.sch_export_pdf(top, run_dir / "blocks.pdf")
        report = run_dir / "erc.json"
        kicad.sch_erc(top, report, severity="all")
        results["erc"] = erc_gate(report).counts
    (run_dir / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print(run_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 12))
