"""布线器封装的命令行与退出码处理（不真的运行布线器）。"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from eda_agent.config import REPO_ROOT, load_config
from eda_agent.tools import routers
from eda_agent.tools.runner import ToolError, ToolResult


def _fake_run(exit_code: int | None, seen: dict[str, Any]) -> Any:
    def run_tool(tool: str, argv: Sequence[str | Path], **kwargs: Any) -> ToolResult:
        seen["tool"] = tool
        seen["argv"] = [str(a) for a in argv]
        return ToolResult(
            tool=tool,
            argv=tuple(seen["argv"]),
            cwd=kwargs["cwd"],
            exit_code=exit_code,
            timed_out=False,
            termination="none",
            duration_s=0.0,
            stdout="",
            stderr="",
            stdout_path=None,
            stderr_path=None,
        )

    return run_tool


def _paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    return tmp_path / "in.kicad_pcb", tmp_path / "out.kicad_pcb", tmp_path / "route.json"


def test_tracemaker_must_be_configured(tmp_path: Path) -> None:
    tools = load_config(REPO_ROOT / "config.example.yaml").tools
    assert tools.tracemaker is None
    pcb_in, pcb_out, summary = _paths(tmp_path)
    with pytest.raises(ValueError, match="tracemaker"):
        routers.tracemaker_route(tools, pcb_in, pcb_out, summary_json=summary, log_dir=tmp_path)


@pytest.mark.parametrize("exit_code", [0, 3])
def test_tracemaker_command_line_and_partial_results_are_kept(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, exit_code: int
) -> None:
    tools = load_config(REPO_ROOT / "config.linux.yaml").tools
    seen: dict[str, Any] = {}
    monkeypatch.setattr(routers, "run_tool", _fake_run(exit_code, seen))
    pcb_in, pcb_out, summary = _paths(tmp_path)

    result = routers.tracemaker_route(
        tools, pcb_in, pcb_out, summary_json=summary, log_dir=tmp_path
    )

    assert result.exit_code == exit_code
    argv = seen["argv"]
    assert Path(argv[0]).name == "tracemaker"
    assert argv[1:5] == ["route", str(pcb_in), "-o", str(pcb_out)]
    assert argv[argv.index("--json") + 1] == str(summary)
    assert {"--no-gpu", "--no-kb"} <= set(argv)
    # 时间限制用 TraceMaker 自己的默认值（每个变体 120 秒），不在命令行里改
    assert "--time" not in argv and "--work" not in argv


def test_board_min_track_width_is_read_from_the_project_file(tmp_path: Path) -> None:
    pro = tmp_path / "x.kicad_pro"
    pro.write_text('{"board": {"design_settings": {"rules": {"min_track_width": 0.2}}}}', "utf-8")
    assert routers.board_min_track_um(pro) == 200
    pro.write_text("{}", "utf-8")
    assert routers.board_min_track_um(pro) is None
    assert routers.board_min_track_um(tmp_path / "missing.kicad_pro") is None


def test_board_copper_edge_clearance_is_read_from_the_project_file(tmp_path: Path) -> None:
    pro = tmp_path / "x.kicad_pro"
    rules = '{"min_copper_edge_clearance": 0.5, "min_track_width": 0.2}'
    pro.write_text('{"board": {"design_settings": {"rules": ' + rules + "}}}", "utf-8")
    assert routers.board_copper_edge_um(pro) == 500
    zero = '{"board": {"design_settings": {"rules": {"min_copper_edge_clearance": 0}}}}'
    pro.write_text(zero, "utf-8")
    assert routers.board_copper_edge_um(pro) is None
    assert routers.board_copper_edge_um(tmp_path / "missing.kicad_pro") is None


def test_fastroute_is_told_the_minimum_track_width(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tools = load_config(REPO_ROOT / "config.example.yaml").tools
    seen: dict[str, Any] = {}
    monkeypatch.setattr(routers, "run_tool", _fake_run(0, seen))
    routers.fastroute_route(
        tools, tmp_path / "b.dsn", tmp_path / "b.ses", log_dir=tmp_path, min_track_um=200
    )
    assert "--router.min_trace_width_um=200" in seen["argv"]
    routers.fastroute_route(tools, tmp_path / "b.dsn", tmp_path / "b.ses", log_dir=tmp_path)
    assert not any("min_trace_width" in a for a in seen["argv"])


def test_fastroute_is_told_the_board_edge_clearance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """KiCad 导出的 .dsn 里没有板边间距，要单独告诉 fastroute（ADR-0004，uno_like_board 实测）。"""
    tools = load_config(REPO_ROOT / "config.example.yaml").tools
    seen: dict[str, Any] = {}
    monkeypatch.setattr(routers, "run_tool", _fake_run(0, seen))
    routers.fastroute_route(
        tools, tmp_path / "b.dsn", tmp_path / "b.ses", log_dir=tmp_path, edge_clearance_um=500
    )
    assert "--router.copper_to_edge_clearance_um=500" in seen["argv"]
    routers.fastroute_route(tools, tmp_path / "b.dsn", tmp_path / "b.ses", log_dir=tmp_path)
    assert not any("copper_to_edge" in a for a in seen["argv"])


@pytest.mark.parametrize("exit_code", [1, 2, None])
def test_tracemaker_failures_raise(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, exit_code: int | None
) -> None:
    tools = load_config(REPO_ROOT / "config.linux.yaml").tools
    monkeypatch.setattr(routers, "run_tool", _fake_run(exit_code, {}))
    pcb_in, pcb_out, summary = _paths(tmp_path)
    with pytest.raises(ToolError):
        routers.tracemaker_route(tools, pcb_in, pcb_out, summary_json=summary, log_dir=tmp_path)
