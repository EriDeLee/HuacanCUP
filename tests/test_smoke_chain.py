"""冒烟里 G1 → G2 → G1（带仿真峰值）的接法：前一关没过就停在那里，不往下做板子；
设计旁边没有 spec.json 时 G2 跳过并写明，不是悄悄略过。

关本身的判定在各自的测试里；这里只看接法，所以三个关换成假的，不需要 KiCad 和 ngspice。
整条链（含摆放、布线、G3）由 `python tasks.py smoke` 在真工具上走。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from eda_agent.config import REPO_ROOT
from eda_agent.gates.report import GateReport, error_finding, make_report
from eda_agent.smoke import Smoke

LED = REPO_ROOT / "examples" / "led" / "design.json"
NO_SPEC = REPO_ROOT / "examples" / "ldo_3v3" / "design.json"


def verdict(gate: str, ok: bool) -> GateReport:
    return make_report(gate, [] if ok else [error_finding("x", "不过")])


def smoke_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, design: Path, results: dict[str, bool]
) -> tuple[Smoke, list[str]]:
    """results：关名 → 通过与否。返回冒烟对象和被调用的关（按顺序）。"""
    smoke = Smoke(design, tmp_path)
    calls: list[str] = []

    def static(erc: GateReport, sim: GateReport | None = None) -> GateReport:
        name = "static" if sim is None else "static_with_sim"
        calls.append(name)
        return verdict("static", results.get(name, True))

    def simulate(spec: Any) -> GateReport:
        calls.append("sim")
        return verdict("sim", results.get("sim", True))

    monkeypatch.setattr(smoke, "static", static)
    monkeypatch.setattr(smoke, "simulate", simulate)
    return smoke, calls


def run_chain(smoke: Smoke) -> tuple[list[GateReport], GateReport | None, dict[str, Any]]:
    summary: dict[str, Any] = {}
    reports, sim = smoke.gate_chain(verdict("erc", True), summary)
    return reports, sim, summary


def test_all_three_gates_run_in_order_when_everything_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    smoke, calls = smoke_with(tmp_path, monkeypatch, LED, {})
    reports, sim, summary = run_chain(smoke)
    assert calls == ["static", "sim", "static_with_sim"]
    assert len(reports) == 3 and all(r.passed for r in reports)
    assert sim is reports[1]
    assert summary == {}


def test_a_failing_static_gate_stops_the_chain_before_the_simulation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    smoke, calls = smoke_with(tmp_path, monkeypatch, LED, {"static": False})
    reports, sim, summary = run_chain(smoke)
    assert calls == ["static"]
    assert [r.passed for r in reports] == [False] and sim is None
    assert summary["stopped_at"] == "static"


def test_a_failing_simulation_stops_the_chain_before_the_second_static_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    smoke, calls = smoke_with(tmp_path, monkeypatch, LED, {"sim": False})
    reports, sim, summary = run_chain(smoke)
    assert calls == ["static", "sim"]
    assert [r.passed for r in reports] == [True, False]
    assert sim is reports[1]
    assert summary["stopped_at"] == "sim"


def test_the_second_static_pass_can_stop_the_chain_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    smoke, calls = smoke_with(tmp_path, monkeypatch, LED, {"static_with_sim": False})
    reports, _, summary = run_chain(smoke)
    assert calls == ["static", "sim", "static_with_sim"]
    assert [r.passed for r in reports] == [True, True, False]
    assert summary["stopped_at"] == "static_with_sim"


def test_without_a_spec_the_simulation_is_skipped_and_the_summary_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    smoke, calls = smoke_with(tmp_path, monkeypatch, NO_SPEC, {})
    assert smoke.spec is None
    reports, sim, summary = run_chain(smoke)
    assert calls == ["static"] and sim is None and len(reports) == 1
    assert "spec.json" in summary["skipped"]["sim"]
    assert "stopped_at" not in summary


def test_the_smoke_circuit_has_a_spec_so_g2_and_g3_have_something_to_check(
    tmp_path: Path,
) -> None:
    smoke = Smoke(LED, tmp_path)
    assert smoke.spec is not None
    assert [a.id for a in smoke.spec.acceptance] == ["A1"]
    assert smoke.spec.constraints.board_max_mm is not None
