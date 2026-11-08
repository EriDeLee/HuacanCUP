"""运行目录（PLAN §5.4）：快照编号、补丁成败、检查报告、断点续跑；事件都符合 trace.schema.json。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from eda_agent.gates.report import GateReport
from eda_agent.ir.models import load_design
from eda_agent.orchestrator.rundir import RunDir
from eda_agent.trace import read_trace

NOW = datetime(2026, 11, 1, 8, 0, 0)


def _run(tmp_path: Path) -> RunDir:
    return RunDir.create(tmp_path, "在电池和负载之间接一个过流保护器", prefix="demo", now=NOW)


def test_create_lays_out_the_plan_5_4_directories(tmp_path: Path) -> None:
    run = _run(tmp_path)
    assert run.run_id == "demo-20261101-080000"
    for sub in ("iterations", "kicad", "final", "branches/0/sim"):
        assert (run.path / sub).is_dir()
    assert run.sim_dir == run.path / "branches" / "0" / "sim"
    assert run.sim_dir_of(2) == run.path / "branches" / "2" / "sim"
    assert (run.path / "request.md").read_text(encoding="utf-8").startswith("在电池")
    assert run.state().iteration == -1
    assert run.latest_design() is None


def test_two_runs_in_the_same_second_get_different_folders(tmp_path: Path) -> None:
    assert _run(tmp_path).path != _run(tmp_path).path


def test_snapshots_are_numbered_and_reload_identically(
    tmp_path: Path, led_design_path: Path
) -> None:
    run = _run(tmp_path)
    design = load_design(led_design_path)
    assert run.snapshot(design, by="example", note="手写样例") == 0
    assert run.snapshot(design, by="example") == 1
    assert (run.path / "iterations" / "00" / "notes.md").exists()
    assert not (run.path / "iterations" / "01" / "notes.md").exists()
    assert run.latest_design() == design
    assert run.state().iteration == 1


def test_a_good_patch_becomes_a_new_snapshot(tmp_path: Path, led_design_path: Path) -> None:
    run = _run(tmp_path)
    run.snapshot(load_design(led_design_path), by="example")

    result = run.apply_patch(
        [{"op": "replace", "path": "/parts/1/value", "value": "470"}], by="SchematicAgent"
    )

    assert result.ok
    assert run.state().iteration == 1
    latest = run.latest_design()
    assert latest is not None and latest.parts[1].value == "470"


def test_a_bad_patch_is_recorded_but_changes_nothing(tmp_path: Path, led_design_path: Path) -> None:
    run = _run(tmp_path)
    run.snapshot(load_design(led_design_path), by="example")

    result = run.apply_patch([{"op": "remove", "path": "/parts/9"}], by="SchematicAgent")

    assert not result.ok
    assert run.state().iteration == 0
    patch_events = [e for e in read_trace(run.path / "trace.jsonl") if e["type"] == "patch"]
    assert patch_events[-1]["ok"] is False
    assert patch_events[-1]["errors"][0]["op_index"] == 0


def test_patching_before_any_snapshot_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        _run(tmp_path).apply_patch([], by="x")


def test_gate_reports_go_next_to_their_design(tmp_path: Path, led_design_path: Path) -> None:
    run = _run(tmp_path)
    run.snapshot(load_design(led_design_path), by="example")
    report = GateReport(gate="erc", passed=True, counts={"errors": 0, "warnings": 2})

    path = run.record_gate(report)

    assert path == run.path / "iterations" / "00" / "gates" / "erc.json"
    assert json.loads(path.read_text(encoding="utf-8"))["passed"] is True


def test_state_survives_reopening(tmp_path: Path, led_design_path: Path) -> None:
    run = _run(tmp_path)
    run.snapshot(load_design(led_design_path), by="example")
    run.save_state(run.state().model_copy(update={"state": "G1", "retries": {"S4-G1": 2}}))

    again = RunDir.open(run.path)

    assert again.state().state == "G1"
    assert again.state().retries == {"S4-G1": 2}
    assert again.latest_design() == run.latest_design()
    with pytest.raises(FileNotFoundError):
        RunDir.open(tmp_path / "nowhere")


def test_every_event_matches_the_trace_schema(
    tmp_path: Path, led_design_path: Path, trace_validator: Draft202012Validator
) -> None:
    run = _run(tmp_path)
    run.snapshot(load_design(led_design_path), by="example")
    run.apply_patch([{"op": "replace", "path": "/parts/1/value", "value": "1k"}], by="a")
    run.apply_patch([{"op": "add", "path": "/parts/0/lcsc", "value": "bad"}], by="a")
    run.record_gate(GateReport(gate="erc", passed=False, counts={"errors": 1}))

    events = read_trace(run.path / "trace.jsonl")

    assert [e["type"] for e in events] == ["snapshot", "patch", "snapshot", "patch", "gate"]
    for event in events:
        trace_validator.validate(event)
