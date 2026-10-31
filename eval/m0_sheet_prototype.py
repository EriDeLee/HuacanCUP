"""M0 样张：用自写版面引擎重画 pic_programmer 的两个功能块，和示例原图对照。

    python eval/m0_sheet_prototype.py [sockets] [vpp]

输入是 kicad-cli 从示例原理图导出的网表（不是手写的），元件符号用示例工程自带的库。
每个功能块写出 .kicad_sch 和 PDF，并做四项检查：
网表一致（KiCad 重新导出的网表与输入逐网络相同）、ERC、图元页内检查、版面质量。
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eda_agent.config import REPO_ROOT, load_config  # noqa: E402
from eda_agent.emit.sch_layout import (  # noqa: E402
    NetSpec,
    PartSpec,
    build_drawing,
    infer_kind,
)
from eda_agent.emit.sch_writer import render_schematic  # noqa: E402
from eda_agent.gates.kicad_reports import erc_gate  # noqa: E402
from eda_agent.gates.schematic_layout import layout_gate  # noqa: E402
from eda_agent.gates.schematic_quality import layout_quality, netlist_gate  # noqa: E402
from eda_agent.kicad_side import sexpr  # noqa: E402
from eda_agent.tools.kicad_cli import KicadCli  # noqa: E402
from eda_agent.trace import TraceWriter  # noqa: E402

BLOCKS: dict[str, list[str]] = {
    # 示例原图第 2 页：五个插座和两个旁路电容
    "sockets": ["P2", "P3", "U1", "U5", "U6", "C6", "C7"],
    # 示例原图第 1 页里的「VPP (13V) power」框：LT1373 升压电路
    "vpp": ["U4", "L1", "D10", "C3", "C4", "C5", "R10", "R15", "R16", "RV1"],
}


def read_netlist(path: Path) -> tuple[dict[str, PartSpec], dict[str, list[tuple[str, str]]]]:
    export = sexpr.parse(path.read_text(encoding="utf-8"))[0]
    parts: dict[str, PartSpec] = {}
    for comp in sexpr.children(sexpr.child(export, "components"), "comp"):
        src = sexpr.child(comp, "libsource")
        ref = sexpr.value(comp, "ref")
        parts[ref] = PartSpec(
            ref=ref,
            lib_id=f"{sexpr.value(src, 'lib')}:{sexpr.value(src, 'part')}",
            value=sexpr.value(comp, "value"),
            footprint=sexpr.value(comp, "footprint"),
        )
    nets: dict[str, list[tuple[str, str]]] = {}
    for net in sexpr.children(sexpr.child(export, "nets"), "net"):
        nets[sexpr.value(net, "name")] = [
            (sexpr.value(n, "ref"), sexpr.value(n, "pin")) for n in sexpr.children(net, "node")
        ]
    return parts, nets


def block_inputs(
    refs: list[str], all_parts: dict[str, PartSpec], all_nets: dict[str, list[tuple[str, str]]]
) -> tuple[list[PartSpec], list[NetSpec]]:
    inside = set(refs)
    nets: list[NetSpec] = []
    for name, nodes in all_nets.items():
        if name.startswith("unconnected-"):
            continue
        here = tuple(n for n in nodes if n[0] in inside)
        if here:
            outside = len(here) < len(nodes)
            # 电源网络在这一页以外还有别的引脚：电从别处来，放 PWR_FLAG 让 ERC 知道
            supply = outside and infer_kind(name) != "signal"
            nets.append(NetSpec(name, here, external=outside, supply=supply))
    return [all_parts[r] for r in refs], nets


def main(names: list[str]) -> int:
    tools = load_config().tools
    run_dir = REPO_ROOT / "runs" / f"m0-sheets-{datetime.now():%Y%m%d-%H%M%S}"
    trace = TraceWriter(run_dir / "trace.jsonl", run_dir.name)
    kicad = KicadCli(tools, log_dir=run_dir / "logs", trace=trace)
    demo = tools.kicad_share / "demos" / "pic_programmer"
    lib_dirs = (demo, tools.kicad_share / "symbols")
    netlist = run_dir / "pic_programmer.net"
    kicad.sch_export_netlist(demo / "pic_programmer.kicad_sch", netlist)
    all_parts, all_nets = read_netlist(netlist)
    results: dict[str, Any] = {"run_id": run_dir.name}
    for name in names or list(BLOCKS):
        parts, nets = block_inputs(BLOCKS[name], all_parts, all_nets)
        drawing = build_drawing(parts, nets, lib_dirs)
        out = run_dir / name
        out.mkdir(parents=True, exist_ok=True)
        sch = out / f"{name}.kicad_sch"
        text = render_schematic(
            drawing, project=name, title=f"{name} (M0 sample)", date="2026-10-31"
        )
        sch.write_text(text, encoding="utf-8")
        kicad.sch_export_pdf(sch, out / f"{name}.pdf")
        erc_json = out / "erc.json"
        kicad.sch_erc(sch, erc_json, severity="all")
        net_out = out / f"{name}.net"
        kicad.sch_export_netlist(sch, net_out)
        gates = [
            layout_quality(drawing),
            netlist_gate(nets, net_out),
            erc_gate(erc_json),
            layout_gate(sch),
        ]
        results[name] = {
            "paper": drawing.paper,
            "passed": all(g.passed for g in gates),
            "gates": {g.gate: asdict(g) | {"findings": g.findings[:6]} for g in gates},
        }
    (run_dir / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for name in names or list(BLOCKS):
        entry = results[name]
        print(name, entry["paper"], "通过" if entry["passed"] else "未通过")
        for gate, data in entry["gates"].items():
            print("   ", gate, "通过" if data["passed"] else "未通过", data["counts"])
    print(run_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
