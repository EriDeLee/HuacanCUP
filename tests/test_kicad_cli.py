"""kicad-cli 封装的命令行（不真的运行 KiCad）。"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from eda_agent.config import REPO_ROOT, load_config
from eda_agent.tools import kicad_cli
from eda_agent.tools.runner import ToolResult


def test_relative_paths_are_made_absolute_before_the_working_directory_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: dict[str, Any] = {}

    def fake_run_tool(tool: str, argv: Sequence[str | Path], **kwargs: Any) -> ToolResult:
        seen["argv"] = list(argv)
        seen["cwd"] = kwargs["cwd"]
        return ToolResult(
            tool, tuple(map(str, argv)), kwargs["cwd"], 0, False, "none", 0.0, "", "", None, None
        )

    monkeypatch.setattr(kicad_cli, "run_tool", fake_run_tool)
    monkeypatch.chdir(tmp_path)
    cli = kicad_cli.KicadCli(load_config(REPO_ROOT / "config.example.yaml").tools, log_dir=tmp_path)

    cli.sch_export_netlist(Path("sub") / "board.kicad_sch", Path("sub") / "board.net")

    paths = [a for a in seen["argv"][1:] if isinstance(a, Path)]
    assert paths and all(p.is_absolute() for p in paths)
    assert (tmp_path / "sub" / "board.kicad_sch") in paths
    assert seen["cwd"] == Path("sub")
