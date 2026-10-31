from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from eda_agent.tools.runner import ToolError, run_tool
from eda_agent.trace import TraceWriter, read_trace


def _run(tmp_path: Path, code: str, timeout_s: float = 30, trace: TraceWriter | None = None):
    return run_tool(
        "py",
        [sys.executable, "-c", code],
        cwd=tmp_path,
        # 和 sandbox_env 一样让子进程 Python 用 UTF-8 输出。
        env={**os.environ, "PYTHONUTF8": "1"},
        timeout_s=timeout_s,
        log_dir=tmp_path / "logs",
        trace=trace,
    )


def test_captures_output_and_writes_full_command_to_trace(
    tmp_path: Path, trace_validator: Draft202012Validator
) -> None:
    trace = TraceWriter(tmp_path / "trace.jsonl", "run-1")
    result = _run(tmp_path, "import sys; print('你好'); print('err', file=sys.stderr)", trace=trace)

    assert result.check().exit_code == 0
    assert result.stdout.strip() == "你好"
    assert result.stderr.strip() == "err"
    assert result.stdout_path is not None and result.stdout_path.read_text(encoding="utf-8")

    (event,) = read_trace(tmp_path / "trace.jsonl")
    trace_validator.validate(event)
    assert event["type"] == "tool_call"
    assert event["argv"][0] == sys.executable
    assert event["exit_code"] == 0
    assert event["termination"] == "none"


def test_check_raises_on_unexpected_exit_code(tmp_path: Path) -> None:
    result = _run(tmp_path, "raise SystemExit(3)")
    assert result.exit_code == 3
    with pytest.raises(ToolError):
        result.check()
    assert result.check(ok_codes=(0, 3)) is result


def test_timeout_stops_the_process(tmp_path: Path) -> None:
    result = _run(tmp_path, "import time; time.sleep(60)", timeout_s=1)
    assert result.timed_out
    assert result.termination in ("graceful", "forced")
    assert result.duration_s < 30
    with pytest.raises(ToolError):
        result.check()
