"""G2（eda_agent/gates/sim.py）：用假的 ngspice 结果检查三态判定、余量、收敛重试、
额外测量、文件和 trace。

真的 ngspice 在 tests/tools/test_sim_circuits.py 里跑。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from eda_agent.config import REPO_ROOT, load_config
from eda_agent.gates.report import GateReport
from eda_agent.gates.sim import SimEnv, sim_gate
from eda_agent.ir.models import Design, load_design
from eda_agent.ir.spec import Spec, load_spec
from eda_agent.tools.ngspice import SimResult
from eda_agent.trace import TraceWriter, read_trace
from tests.helpers import fake_sim

DIVIDER = REPO_ROOT / "tests" / "circuits" / "voltage_divider"
OVERCURRENT = REPO_ROOT / "examples" / "overcurrent_cutoff"


class Scripted:
    """按顺序交出准备好的结果；用完以后一直交最后一个。"""

    def __init__(self, *results: SimResult) -> None:
        self.results = list(results)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, tools: Any, netlist: Path, result_json: Path, **kw: Any) -> SimResult:
        self.calls.append({"netlist": netlist, "json": result_json, **kw})
        i = min(len(self.calls), len(self.results)) - 1
        return self.results[i]


def env(tmp_path: Path, sim: Scripted, **kw: Any) -> SimEnv:
    return SimEnv(
        tools=load_config().tools,
        sim_dir=tmp_path / "branches" / "0" / "sim",
        run_dir=tmp_path,
        simulate=sim,
        plot=False,
        **kw,
    )


def divider(**spec_patch: Any) -> tuple[Design, Spec]:
    design = load_design(DIVIDER / "design.json")
    spec_doc = load_spec(DIVIDER / "spec.json").model_dump(by_alias=True)
    spec_doc.update(spec_patch)
    return design, Spec.model_validate(spec_doc)


def divider_vectors(mid: float = 3.0, current: float = 0.0003) -> dict[str, list[float]]:
    return {
        "vin": [12.0],
        "mid": [mid],
        "vi_r1_1#branch": [current],
        "vi_r1_2#branch": [-current],
        "vi_r2_1#branch": [current],
        "vi_r2_2#branch": [-current],
        "vx_vin#branch": [current],
    }


def by_id(report: GateReport) -> dict[str, Any]:
    return {f.id: f for f in report.findings}


# ---------------------------------------------------------------- 通过和不通过


def test_every_criterion_gets_a_finding_and_passing_ones_carry_numbers(tmp_path: Path) -> None:
    design, spec = divider()
    sim = Scripted(fake_sim(divider_vectors()))
    report = sim_gate(design, spec, env(tmp_path, sim))
    assert report.gate == "sim" and report.passed
    assert report.counts == {"pass": 2, "fail": 0, "sim_error": 0, "total": 2, "attempts": 2}
    a1 = by_id(report)["A1"]
    assert (a1.status, a1.severity) == ("pass", "info")
    assert a1.measured == pytest.approx(3.0) and (a1.threshold, a1.threshold_high) == (2.97, 3.03)
    assert (a1.op, a1.unit) == ("between", "V")
    assert a1.margin == pytest.approx(0.03 / 3.03, rel=0.05)
    assert a1.margin is not None and a1.margin > 0
    assert "测得 3 V" in a1.message and "余量" in a1.message
    a2 = by_id(report)["A2"]
    assert a2.measured == pytest.approx(0.0003) and a2.unit == "A"


def test_a_value_outside_the_window_fails_with_a_negative_margin(tmp_path: Path) -> None:
    design, spec = divider()
    report = sim_gate(design, spec, env(tmp_path, Scripted(fake_sim(divider_vectors(mid=3.43)))))
    a1 = by_id(report)["A1"]
    assert (a1.status, a1.severity) == ("fail", "error")
    assert a1.margin is not None and a1.margin < 0
    assert not report.passed
    assert (
        report.counts["fail"] == 1
        and report.counts["pass"] == 1
        and report.counts["sim_error"] == 0
    )


def test_the_report_validates_against_the_json_schema(tmp_path: Path) -> None:
    import json

    design, spec = divider()
    report = sim_gate(design, spec, env(tmp_path, Scripted(fake_sim(divider_vectors()))))
    schema = json.loads((REPO_ROOT / "schemas" / "gate_report.schema.json").read_text("utf-8"))
    Draft202012Validator(schema).validate(report.model_dump(mode="json"))


def test_files_go_into_the_branch_sim_directory_and_artifacts_are_relative(tmp_path: Path) -> None:
    design, spec = divider()
    sim = Scripted(fake_sim(divider_vectors()))
    report = sim_gate(design, spec, env(tmp_path, sim))
    sim_dir = tmp_path / "branches" / "0" / "sim"
    assert (sim_dir / "A1.cir").read_text(encoding="utf-8").startswith("* eda_agent testbench A1")
    assert (sim_dir / "A2.cir").exists()
    assert by_id(report)["A1"].artifacts == ["branches/0/sim/A1.cir"]
    assert sim.calls[0]["raw"] == sim_dir / "A1.raw"
    assert sim.calls[0]["settings"] == ()


def test_the_netlist_is_written_exactly_as_generated(tmp_path: Path) -> None:
    from eda_agent.sim.testbench import build_bench

    design, spec = divider()
    sim = Scripted(fake_sim(divider_vectors()))
    sim_gate(design, spec, env(tmp_path, sim))
    expected = build_bench(design, spec, spec.acceptance[0], models_root=REPO_ROOT).text
    assert (tmp_path / "branches" / "0" / "sim" / "A1.cir").read_text(encoding="utf-8") == expected


# ---------------------------------------------------------------- sim_error：模型缺失


def test_a_part_without_a_model_makes_every_criterion_a_sim_error(tmp_path: Path) -> None:
    design, spec = divider()
    doc = design.model_dump(by_alias=True)
    doc["parts"].append(
        {"ref": "U9", "symbol": "Sensor_Temperature:LM75B", "footprint": "a:b", "value": "LM75B"}
    )
    doc["nets"][1]["nodes"].append(["U9", "1"])
    sim = Scripted(fake_sim(divider_vectors()))
    report = sim_gate(Design.model_validate(doc), spec, env(tmp_path, sim))
    assert sim.calls == [], "没有模型就不仿真，更不能悄悄跳过"
    assert not report.passed
    assert report.counts["sim_error"] == 2 and report.counts["pass"] == 0 == report.counts["fail"]
    f = by_id(report)["A1"]
    assert (f.status, f.severity) == ("sim_error", "error")
    assert f.measured is None and f.margin is None
    assert f.detail == {"reason": "model_missing", "refs": ["U9"]}
    assert "模型缺失" in f.message and "U9" in f.message


def test_a_spec_that_does_not_fit_the_design_is_a_sim_error_too(tmp_path: Path) -> None:
    design, spec = divider(interfaces=[{"name": "NOPE", "kind": "signal_out"}])
    report = sim_gate(design, spec, env(tmp_path, Scripted(fake_sim())))
    assert report.counts["sim_error"] == 2
    assert by_id(report)["A1"].detail["reason"] == "spec"


# ---------------------------------------------------------------- 收敛重试


def aborted() -> SimResult:
    return fake_sim(
        ok=False,
        output=[
            "stderr doAnalyses: TRAN:  Timestep too small; time = 0.0026845, timestep = 6.25e-18",
            "stderr run simulation(s) aborted",
        ],
        exit_code=1,
    )


def overcurrent_ok() -> SimResult:
    return fake_sim(
        {"time": [0.0, 0.001, 0.002, 0.003, 0.004, 0.005, 0.006], "vl_load#branch": [1.0] * 7},
        m_a1=7.2e-5,
    )


def overcurrent_a1() -> tuple[Design, Spec]:
    spec = load_spec(OVERCURRENT / "spec.json")
    only_a1 = spec.model_copy(update={"acceptance": spec.acceptance[:1]})
    return load_design(OVERCURRENT / "design.json"), only_a1


def test_convergence_attempts_go_in_order_and_each_one_is_recorded(tmp_path: Path) -> None:
    design, spec = overcurrent_a1()
    sim = Scripted(aborted(), aborted(), aborted(), overcurrent_ok())
    report = sim_gate(design, spec, env(tmp_path, sim))
    f = by_id(report)["A1"]
    assert f.status == "pass" and f.measured == pytest.approx(7.2e-5)
    assert [(a.name, a.outcome) for a in f.attempts] == [
        ("default", "error"),
        ("relaxed", "error"),
        ("gear", "error"),
        ("short_step", "ok"),
    ]
    assert "Timestep too small" in f.attempts[0].error
    assert f.attempts[1].options[:2] == ["gmin=1e-9", "reltol=0.01"]
    assert "method=gear" in f.attempts[2].options and "itl1=1000" in f.attempts[3].options
    assert report.counts["attempts"] == 4
    sim_dir = tmp_path / "branches" / "0" / "sim"
    names = sorted(p.name for p in sim_dir.glob("A1*.cir"))
    assert names == ["A1.cir", "A1.gear.cir", "A1.relaxed.cir", "A1.short_step.cir"]
    assert [a.netlist for a in f.attempts] == [
        f"branches/0/sim/{n}"
        for n in ("A1.cir", "A1.relaxed.cir", "A1.gear.cir", "A1.short_step.cir")
    ]
    # 每一次用的网表不同，最后一次带 uic；成功那次的网表是留在 artifacts 里的那份
    assert " uic" in (sim_dir / "A1.short_step.cir").read_text(encoding="utf-8")
    assert f.artifacts[0] == "branches/0/sim/A1.short_step.cir"


def test_ngspice_settings_and_models_follow_every_attempt(tmp_path: Path) -> None:
    design, spec = overcurrent_a1()
    sim = Scripted(aborted(), overcurrent_ok())
    sim_gate(design, spec, env(tmp_path, sim))
    assert [c["settings"] for c in sim.calls] == [("ngbehavior=ltpsa",)] * 2
    models = tmp_path / "branches" / "0" / "sim" / "models"
    assert sorted(p.name for p in models.iterdir()) == ["irlz44n.lib", "lm393.lib"]
    assert (models / "lm393.lib").read_bytes() == (REPO_ROOT / "models" / "lm393.lib").read_bytes()


def test_four_failed_attempts_are_a_sim_error_not_a_fail(tmp_path: Path) -> None:
    design, spec = overcurrent_a1()
    sim = Scripted(aborted())
    report = sim_gate(design, spec, env(tmp_path, sim))
    f = by_id(report)["A1"]
    assert len(sim.calls) == 4
    assert (f.status, f.severity) == ("sim_error", "error")
    assert f.detail["reason"] == "no_convergence" and "4 组设置都不收敛" in f.message
    assert [a.outcome for a in f.attempts] == ["error"] * 4
    assert not report.passed and report.counts["sim_error"] == 1 and report.counts["fail"] == 0


def test_an_aborted_run_is_a_convergence_failure_even_if_the_ok_flag_is_set(tmp_path: Path) -> None:
    design, spec = overcurrent_a1()
    half_done = fake_sim(
        {"time": [0.0, 0.001], "vl_load#branch": [1.0, 1.0]},
        ok=True,
        output=["stderr run simulation(s) aborted"],
    )
    sim = Scripted(half_done, overcurrent_ok())
    f = by_id(sim_gate(design, spec, env(tmp_path, sim)))["A1"]
    assert [a.outcome for a in f.attempts] == ["error", "ok"] and f.status == "pass"


def test_errors_that_a_different_setting_cannot_fix_do_not_retry(tmp_path: Path) -> None:
    design, spec = overcurrent_a1()
    bad = fake_sim(
        ok=False,
        errors=["stderr Error: Could not find include file models/irlz44n.lib"],
        output=["stderr Error: Could not find include file models/irlz44n.lib"],
        exit_code=1,
    )
    sim = Scripted(bad)
    f = by_id(sim_gate(design, spec, env(tmp_path, sim)))["A1"]
    assert len(sim.calls) == 1 and len(f.attempts) == 1
    assert f.status == "sim_error" and "ngspice 报错" in f.message and "include" in f.message
    assert f.detail["reason"] == "ngspice_error"


def test_a_timeout_stops_the_attempts(tmp_path: Path) -> None:
    design, spec = overcurrent_a1()
    sim = Scripted(fake_sim(ok=False, timed_out=True, exit_code=None))
    f = by_id(sim_gate(design, spec, env(tmp_path, sim)))["A1"]
    assert len(sim.calls) == 1
    assert f.status == "sim_error" and f.attempts[0].outcome == "timeout"
    assert f.detail["reason"] == "timeout" and "超时" in f.message


# ---------------------------------------------------------------- unmet 和测量出错


def test_a_circuit_that_never_does_the_thing_is_a_fail_with_no_measured_value(
    tmp_path: Path,
) -> None:
    design, spec = overcurrent_a1()
    never = fake_sim({"time": [0.0, 0.003, 0.006], "vl_load#branch": [10.0, 10.0, 10.0]}, m_a1=None)
    f = by_id(sim_gate(design, spec, env(tmp_path, Scripted(never))))["A1"]
    assert (f.status, f.severity) == ("fail", "error")
    assert f.measured is None and f.margin is None
    assert "没有满足" in f.message


def test_a_measurement_that_did_not_come_out_is_a_sim_error(tmp_path: Path) -> None:
    design, spec = overcurrent_a1()
    f = by_id(sim_gate(design, spec, env(tmp_path, Scripted(fake_sim({"time": [0.0]})))))["A1"]
    assert f.status == "sim_error" and "m_a1" in f.message


# ---------------------------------------------------------------- 额外测量、trace、空 spec


def test_peaks_are_the_maximum_over_all_runs(tmp_path: Path) -> None:
    design, spec = divider()
    low, high = divider_vectors(), divider_vectors(mid=3.0, current=0.0006)
    high["vin"] = [24.0]
    sim = Scripted(fake_sim(low), fake_sim(high))
    report = sim_gate(design, spec, env(tmp_path, sim))
    assert report.device_stress["R1"].v_peak == pytest.approx(24.0)
    assert report.device_stress["R1"].i_peak == pytest.approx(0.0006)
    assert report.net_current_peak["VIN"] == pytest.approx(0.0006)
    assert list(report.device_stress) == ["R1", "R2"], "按位号排序，输出稳定"


def test_a_failed_criterion_still_contributes_its_peaks(tmp_path: Path) -> None:
    design, spec = divider()
    report = sim_gate(design, spec, env(tmp_path, Scripted(fake_sim(divider_vectors(mid=9.0)))))
    assert by_id(report)["A1"].status == "fail" and report.device_stress["R2"].v_peak == 9.0


def test_a_spec_with_no_acceptance_criteria_does_not_pass(tmp_path: Path) -> None:
    design, spec = divider(acceptance=[])
    report = sim_gate(design, spec, env(tmp_path, Scripted(fake_sim())))
    assert not report.passed
    assert [f.id for f in report.findings] == ["no_acceptance"]
    assert report.counts["total"] == 0


def test_trace_events_say_which_branch_and_validate_against_the_schema(
    tmp_path: Path, trace_validator: Draft202012Validator
) -> None:
    design, spec = overcurrent_a1()
    trace = TraceWriter(tmp_path / "trace.jsonl", "run-1")
    sim = Scripted(aborted(), overcurrent_ok())
    sim_gate(design, spec, env(tmp_path, sim, trace=trace, branch=0))
    events = read_trace(tmp_path / "trace.jsonl")
    assert [(e["name"], e["status"]) for e in events] == [
        ("sim:A1", "start"),
        ("sim-attempt", "fail"),
        ("sim:A1", "ok"),
    ]
    assert all(e["branch"] == 0 for e in events)
    for event in events:
        trace_validator.validate(event)
    assert events[1]["detail"]["attempt"] == "default"


def test_without_a_trace_nothing_is_written(tmp_path: Path) -> None:
    design, spec = divider()
    sim_gate(design, spec, env(tmp_path, Scripted(fake_sim(divider_vectors()))))
    assert not (tmp_path / "trace.jsonl").exists()
