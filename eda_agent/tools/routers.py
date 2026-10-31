"""两个候选布线器（ADR-0004，M0 实测后二选一）。

- Freerouting 2.5.0 原生命令行版：读 Specctra .dsn，写 .ses；不需要 Java。
  匿名统计在 .tools/home 的 freerouting.json 里关掉（disable_analytics: true）。
- KiCadRoutingTools v0.23.7：直接读写 .kicad_pcb。
  必须带 --no-fix-drc-settings：默认它会把工程里的 DRC 下限放宽到布出来的尺寸，
  那样 DRC 关就被布线器自己降低了门槛。
"""

from __future__ import annotations

from pathlib import Path

from eda_agent.config import ToolsConfig
from eda_agent.tools.runner import ToolResult, run_tool
from eda_agent.tools.sandbox import sandbox_env
from eda_agent.trace import TraceWriter


def freerouting_route(
    tools: ToolsConfig,
    dsn: Path,
    ses: Path,
    *,
    log_dir: Path,
    trace: TraceWriter | None = None,
    max_passes: int = 20,
) -> ToolResult:
    result = run_tool(
        "freerouting",
        [tools.freerouting, "-de", dsn, "-do", ses, "-mp", str(max_passes)],
        cwd=dsn.parent,
        env=sandbox_env(tools),
        timeout_s=tools.timeouts.route_s,
        log_dir=log_dir,
        trace=trace,
    )
    return result.check()


def krt_route(
    tools: ToolsConfig,
    pcb_in: Path,
    pcb_out: Path,
    *,
    summary_json: Path,
    log_dir: Path,
    trace: TraceWriter | None = None,
) -> ToolResult:
    result = run_tool(
        "kicad-routing-tools",
        [
            tools.krt_python,
            tools.krt_route,
            pcb_in,
            pcb_out,
            "--no-fix-drc-settings",
            "--escalation",
            "board",
            "--json-out",
            summary_json,
        ],
        cwd=pcb_in.parent,
        env=sandbox_env(tools),
        timeout_s=tools.timeouts.route_s,
        log_dir=log_dir,
        trace=trace,
    )
    return result.check()
