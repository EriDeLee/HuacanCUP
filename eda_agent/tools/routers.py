"""两个候选布线器（ADR-0004，M0 实测后二选一）。

- Freerouting 2.5.0 Java 版（Temurin JRE 25）：读 Specctra .dsn，写 .ses。
  免 Java 的原生版遇到元件外框会崩溃并挂起，所以不用。
  参数按官方 docs/command_line_arguments.md 核对：-da 关闭匿名统计，--gui.enabled=false 无界面，
  --user_data_path 让设置和日志写进沙箱。
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


def freerouting_command(tools: ToolsConfig) -> list[str | Path]:
    """java 启动 Freerouting 的公共部分；输出强制 UTF-8，用户目录指向沙箱。"""
    return [
        tools.freerouting_java,
        f"-Duser.home={tools.sandbox_home}",
        "-Dstdout.encoding=UTF-8",
        "-Dstderr.encoding=UTF-8",
        "-jar",
        tools.freerouting_jar,
        "--gui.enabled=false",
        "-da",
        f"--user_data_path={tools.sandbox_home / 'freerouting'}",
    ]


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
        [
            *freerouting_command(tools),
            "-de",
            dsn,
            "-do",
            ses,
            f"--router.autorouter.max_passes={max_passes}",
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
