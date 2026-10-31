"""布线器（ADR-0004，用户 2026-10-31 决定）：fastroute 主用，KiCadRoutingTools 备用。

- fastroute 0.1.14：读 Specctra .dsn，写 .ses。单个 exe，不需要 Java。
  --max-time 到点会自己写出目前最好的结果，比外层统一超时早 30 秒。
- KiCadRoutingTools v0.23.7：直接读写 .kicad_pcb。
  必须带 --no-fix-drc-settings：默认它会把工程里的 DRC 下限放宽到布出来的尺寸，
  那样 DRC 关就被布线器自己降低了门槛。

Freerouting 和 kicad-tools 的实测结果在 ADR-0004；它们的适配代码在 git 提交 aeee649 里。
"""

from __future__ import annotations

from pathlib import Path

from eda_agent.config import ToolsConfig
from eda_agent.tools.runner import ToolResult, run_tool
from eda_agent.tools.sandbox import sandbox_env
from eda_agent.trace import TraceWriter


def fastroute_route(
    tools: ToolsConfig,
    dsn: Path,
    ses: Path,
    *,
    log_dir: Path,
    trace: TraceWriter | None = None,
    max_passes: int = 20,
) -> ToolResult:
    result = run_tool(
        "fastroute",
        [
            tools.fastroute,
            "-de",
            dsn,
            "-do",
            ses,
            "-mp",
            str(max_passes),
            f"--max-time={int(tools.timeouts.route_s) - 30}",
            f"--report={ses.with_suffix('.fastroute.json')}",
        ],
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
