"""CI 的后台安装脚本 .github/scripts/bg.sh（ADR-0012）：退出码原样传回，超时和没启动过都要报错。

只在 Linux 上跑（CI 就是 Linux）；Windows 上没有可靠的 bash，跳过。
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

import pytest

from eda_agent.config import REPO_ROOT
from eda_agent.tools.runner import ToolResult, run_tool

BG = REPO_ROOT / ".github" / "scripts" / "bg.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or BASH is None, reason="后台脚本只在 Linux 的 CI 上用"
)


def bg(cwd: Path, *args: str) -> ToolResult:
    assert BASH is not None
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    return run_tool("bg.sh", [BASH, BG, *args], cwd=cwd, env=env, timeout_s=60)


def test_start_returns_at_once_and_wait_passes_on_output_and_exit_code(tmp_path: Path) -> None:
    begin = time.monotonic()
    started = bg(tmp_path, "start", "job", "bash", "-c", "sleep 1; echo hello; exit 3")
    assert started.exit_code == 0
    assert time.monotonic() - begin < 0.9, "start 应该马上返回，不等后台跑完"

    waited = bg(tmp_path, "wait", "job", "30")

    assert waited.exit_code == 3
    assert "hello" in waited.stdout


def test_a_successful_job_gives_exit_code_zero(tmp_path: Path) -> None:
    bg(tmp_path, "start", "ok", "bash", "-c", "echo done")
    waited = bg(tmp_path, "wait", "ok", "30")
    assert waited.exit_code == 0
    assert "done" in waited.stdout


def test_waiting_for_a_job_that_was_never_started_fails(tmp_path: Path) -> None:
    assert bg(tmp_path, "wait", "never", "5").exit_code == 2


def test_waiting_too_long_fails_and_shows_the_output_so_far(tmp_path: Path) -> None:
    bg(tmp_path, "start", "slow", "bash", "-c", "echo begun; sleep 5")
    waited = bg(tmp_path, "wait", "slow", "1")
    assert waited.exit_code == 124
    assert "begun" in waited.stdout
