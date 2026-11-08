"""M1 完成标准（PLAN §12）：examples/ 下每个手写 IR 样例都生成可打开的 KiCad 工程。

    python eval/m1_examples.py [样例名 ...]       不写就跑全部

每个样例：发射工程（原理图、网表、PCB、网络类），再让 KiCad 自己检查——
ERC 没有错误、KiCad 读出的网表和 IR 逐网络一致、每页版面检查通过、符号都在图纸内、
DRC 的原理图一致性没有错误（这时还没布线，所以「没连上」不算）。另外导出原理图 PDF 方便看。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eda_agent.config import REPO_ROOT, load_config  # noqa: E402
from eda_agent.emit.kicad_project import emit_kicad_project  # noqa: E402
from eda_agent.gates.kicad_reports import drc_gate, erc_gate, violation_finding  # noqa: E402
from eda_agent.gates.report import Finding, GateReport  # noqa: E402
from eda_agent.gates.schematic_layout import layout_gate  # noqa: E402
from eda_agent.gates.schematic_quality import layout_quality, netlist_gate  # noqa: E402
from eda_agent.tools.kicad_cli import KicadCli  # noqa: E402
from eda_agent.trace import TraceWriter  # noqa: E402

EXAMPLES = REPO_ROOT / "examples"


def check_example(design_path: Path, out: Path, trace: TraceWriter) -> dict[str, Any]:
    tools = load_config().tools
    name = design_path.parent.name
    logs = out / "logs"
    emitted = emit_kicad_project(
        design_path, out, name, tools, log_dir=logs, date=f"{datetime.now():%Y-%m-%d}", trace=trace
    )
    kicad = KicadCli(tools, log_dir=logs, trace=trace)
    erc_json = out / "erc.json"
    kicad.sch_erc(emitted.root, erc_json, severity="all")
    drc_json = out / "drc.json"
    kicad.pcb_drc(emitted.pcb, drc_json, severity="all")
    kicad.sch_export_pdf(emitted.root, out / f"{name}.pdf")

    gates: list[GateReport] = [
        erc_gate(erc_json),
        netlist_gate(emitted.schematic.nets, emitted.netlist),
    ]
    drawings = [(p.key, p.drawing, out / p.file) for p in emitted.schematic.pages]
    if emitted.schematic.root is not None:  # 分页时根页的框图也查（ADR-0014）
        drawings.append(("root", emitted.schematic.root, emitted.root))
    for key, drawing, sheet in drawings:
        quality = layout_quality(drawing)
        gates.append(quality.model_copy(update={"gate": f"schematic_quality:{key}"}))
        if key == "root" and emitted.schematic.root is not None:
            continue  # 根页只有图纸框、没有符号，「符号在图纸内」的检查对它没有意义
        inside = layout_gate(sheet)
        gates.append(inside.model_copy(update={"gate": f"schematic_layout:{key}"}))
    drc = drc_gate(drc_json)
    parity = _parity_items(drc_json)
    gates.append(
        GateReport(
            gate="schematic_parity",
            passed=not parity,
            counts={"parity_errors": drc.counts.get("parity_errors", 0)},
            findings=parity,
        )
    )
    return {
        "passed": all(g.passed for g in gates),
        "pages": [p.key for p in emitted.schematic.pages],
        "papers": [p.drawing.paper for p in emitted.schematic.pages],
        # 同名标签连成导线：nets 个网络要连，wired 个全连上，partial 个连上一部分，failed 个没连
        "routing": {key: drawing.route_stats for key, drawing, _ in drawings},
        "drc_unrouted": drc.counts,
        "gates": {g.gate: _summary(g) for g in gates},
    }


def _summary(gate: GateReport) -> dict[str, Any]:
    data: dict[str, Any] = gate.model_dump(mode="json")
    data["findings"] = [f"{f.id}: {f.message}" for f in gate.findings[:8]]
    return data


def _parity_items(drc_json: Path) -> list[Finding]:
    data = json.loads(drc_json.read_text(encoding="utf-8"))
    return [
        violation_finding(i)
        for i in data.get("schematic_parity", [])
        if i.get("severity") == "error"
    ]


def main(names: list[str]) -> int:
    paths = sorted(EXAMPLES.glob("*/design.json"))
    if names:
        paths = [p for p in paths if p.parent.name in names]
    run_dir = REPO_ROOT / "runs" / f"m1-examples-{datetime.now():%Y%m%d-%H%M%S}"
    trace = TraceWriter(run_dir / "trace.jsonl", run_dir.name)
    results: dict[str, Any] = {"run_id": run_dir.name, "examples": {}}
    for path in paths:
        name = path.parent.name
        try:
            entry = check_example(path, run_dir / name, trace)
        except Exception as exc:  # 一个样例出错不影响其他样例
            entry = {"passed": False, "error": str(exc)[-2000:]}
        results["examples"][name] = entry
        (run_dir / "results.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        status = "通过" if entry["passed"] else "未通过"
        routing = entry.get("routing", {})
        wired = " ".join(
            f"{k}:{s.get('wired', 0)}/{s.get('nets', 0)}" for k, s in routing.items() if s
        )
        print(f"{name:20s} {status}  {entry.get('papers', '')}  连成导线 {wired or '-'}")
        if "error" in entry:
            print("    出错：", entry["error"].strip().splitlines()[-1][:300])
            continue
        for gate, data in entry["gates"].items():
            if not data["passed"]:
                print(f"    {gate} 未通过 {data['counts']} {data['findings'][:3]}")
    passed = sum(1 for e in results["examples"].values() if e["passed"])
    print(f"\n{passed}/{len(results['examples'])} 个样例通过 → {run_dir}")
    return 0 if passed == len(results["examples"]) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
