"""G3（KiCad DRC 加未布通网络数、板尺寸、电源网络线宽）在真实布线结果上的回归。
AGENTS.md：新增 Gate 要附 examples/ 里手写电路的结果。

流程和冒烟一样：IR → KiCad 工程 → 自动摆放 → fastroute 布线 → kicad-cli DRC → drc_checks。
电流只取 spec 接口的 i_max（G2 的电流上界在冒烟里一起用，这里不跑仿真）。

- led：过。VIN 按 0.02 A 估计，线宽远够。
- overcurrent_cutoff：不过，而且只有两条 trace_width：VBAT 和 LOAD_N 按 10 A 估计，IPC-2221
  （1 oz、温升 10 °C）要 7.19 mm，板上只有 1–1.5 mm。这是样例本身的问题
  （review-queue D12 说它只是示意板，D38），规则没有为了让它过去而放宽，
  所以这里把失败集合钉住：规则变了、样例变了，都要有意识地改这里。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from eda_agent.config import REPO_ROOT, load_config
from eda_agent.emit.kicad_project import emit_kicad_project
from eda_agent.flow import copy_project, route_board
from eda_agent.gates.drc import drc_checks, estimate_net_currents, pcb_net_names
from eda_agent.gates.report import GateReport
from eda_agent.gates.schematic_quality import exported_nets
from eda_agent.ir.models import load_design
from eda_agent.ir.spec import load_spec
from eda_agent.placement.run import place_board
from eda_agent.tools.kicad_cli import KicadCli

pytestmark = pytest.mark.tools

EXAMPLES = REPO_ROOT / "examples"


def g3_on(name: str, tmp_path: Path) -> GateReport:
    cfg = load_config()
    path = EXAMPLES / name / "design.json"
    design, spec = load_design(path), load_spec(path.with_name("spec.json"))
    logs = tmp_path / "logs"
    emitted = emit_kicad_project(
        path, tmp_path / "kicad", name, cfg.tools, log_dir=logs, date=f"{datetime.now():%Y-%m-%d}"
    )
    pcb = copy_project(emitted.pcb, tmp_path / "board", name)
    placed = place_board(
        cfg.tools, pcb, design, cfg.placement, work_dir=tmp_path / "board", log_dir=logs
    )
    assert placed.ok, placed.violations
    route_board(cfg.tools, pcb, "fastroute", log_dir=logs)
    drc = tmp_path / "drc.json"
    KicadCli(cfg.tools, log_dir=logs).pcb_drc(pcb, drc, severity="all")
    return drc_checks(
        drc_report=drc,
        pcb=pcb,
        spec=spec,
        net_names=pcb_net_names(design, exported_nets(emitted.netlist)),
        currents=estimate_net_currents(spec, None),
        copper_um=cfg.pcb.copper_thickness_um,
        temp_rise_c=cfg.pcb.temp_rise_c,
    )


def test_led_passes_g3_on_a_routed_board(tmp_path: Path) -> None:
    report = g3_on("led", tmp_path)
    assert report.passed, [f.message for f in report.findings]
    assert report.counts["unrouted_nets"] == 0 and report.counts["board_oversize"] == 0
    assert report.counts["width_checked"] == 1 and report.counts["width_failed"] == 0


def test_overcurrent_cutoff_fails_g3_only_on_the_two_10_a_trace_widths(tmp_path: Path) -> None:
    report = g3_on("overcurrent_cutoff", tmp_path)
    assert not report.passed
    errors = [f for f in report.findings if f.severity == "error"]
    assert [f.id for f in errors] == ["trace_width", "trace_width"]
    assert {f.detail["net"] for f in errors} == {"VBAT", "LOAD_N"}
    for found in errors:
        assert found.detail["current_a"] == 10.0
        assert found.threshold == pytest.approx(7.194, abs=0.01)  # IPC-2221，1 oz、10 °C
        assert found.measured is not None and found.measured < 2.0  # 板上的线宽
    # 摆放、布线、KiCad 自己的 DRC 都没问题：失败的只是线宽
    counts = report.counts
    assert counts["unrouted_nets"] == 0 and counts["board_oversize"] == 0
    assert counts["violations_errors"] == 0 and counts["parity_errors"] == 0
    assert counts["width_checked"] == 2 and counts["width_failed"] == 2
