from __future__ import annotations

import importlib.util
import os
import time
from pathlib import Path
from types import ModuleType

import pytest

from eda_agent.config import REPO_ROOT


@pytest.fixture(scope="module")
def annotate() -> ModuleType:
    path = REPO_ROOT / ".github" / "scripts" / "annotate_failure.py"
    spec = importlib.util.spec_from_file_location("annotate_failure", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_escaping_follows_the_github_workflow_command_rules(annotate: ModuleType) -> None:
    assert annotate.escape_data("100%\nok\r") == "100%25%0Aok%0D"
    assert annotate.escape_prop("a:b,c") == "a%3Ab%2Cc"


def test_the_newest_step_log_and_tool_errors_become_annotations(
    annotate: ModuleType, tmp_path: Path
) -> None:
    old = tmp_path / "ci-logs" / "1-install.log"
    new = tmp_path / "ci-logs" / "2-lint.log"
    tool = tmp_path / "runs" / "smoke-x" / "logs" / "003_kicad-cli.stderr.txt"
    for path, text in (
        (old, "fine\n"),
        (new, "line1\nERROR: boom 50%\n"),
        (tool, "pcbnew failed\n"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    now = time.time()
    os.utime(old, (now - 100, now - 100))
    os.utime(new, (now - 10, now - 10))

    lines = annotate.annotations(tmp_path)

    assert any("2-lint.log" in line and "ERROR: boom 50%25" in line for line in lines)
    assert not any("1-install.log" in line for line in lines)
    assert any("pcbnew failed" in line for line in lines)
    assert all(line.startswith("::error ") for line in lines)
    assert all("\n" not in line for line in lines)


def test_long_logs_are_cut_and_the_count_is_capped(annotate: ModuleType, tmp_path: Path) -> None:
    for i in range(20):
        path = tmp_path / "runs" / f"smoke-{i}" / "logs" / "001_x.stderr.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x" * 10000, encoding="utf-8")

    lines = annotate.annotations(tmp_path)

    assert len(lines) == annotate.MAX_ANNOTATIONS
    assert all(len(line) < annotate.MAX_CHARS + 200 for line in lines)


def test_no_logs_still_gives_one_visible_message(annotate: ModuleType, tmp_path: Path) -> None:
    assert annotate.annotations(tmp_path) == ["::error::没有找到任何日志文件"]
