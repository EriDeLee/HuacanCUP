from __future__ import annotations

from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from eda_agent.trace import TraceWriter, read_trace


def test_events_are_appended_in_order_and_match_schema(
    tmp_path: Path, trace_validator: Draft202012Validator
) -> None:
    trace = TraceWriter(tmp_path / "trace.jsonl", "run-1")
    trace.emit("step", name="erc", status="start")
    trace.emit("step", name="erc", status="ok", detail={"errors": 0})

    events = read_trace(tmp_path / "trace.jsonl")
    assert [e["seq"] for e in events] == [0, 1]
    for event in events:
        trace_validator.validate(event)


def test_schema_rejects_tool_call_without_command_line(
    trace_validator: Draft202012Validator,
) -> None:
    event = {"ts": "2026-10-31T00:00:00+00:00", "run_id": "r", "seq": 0, "type": "tool_call"}
    with pytest.raises(ValidationError):
        trace_validator.validate(event)
