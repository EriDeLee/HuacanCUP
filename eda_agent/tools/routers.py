"""候选布线器（ADR-0004，M0 实测后选定）。

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


def _required(path: Path | None, name: str) -> Path:
    if path is None:
        raise RuntimeError(f"config.yaml 里没有配置 {name}")
    return path


def fastroute_route(
    tools: ToolsConfig,
    dsn: Path,
    ses: Path,
    *,
    log_dir: Path,
    trace: TraceWriter | None = None,
    max_passes: int = 20,
) -> ToolResult:
    """fastroute 0.1.14：Freerouting 的 Rust 移植，命令行与 Freerouting 相同（M0 对比候选）。

    --max-time 到点会自己写出目前最好的结果，比外层统一超时早 30 秒。
    """
    result = run_tool(
        "fastroute",
        [
            _required(tools.fastroute, "fastroute"),
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


# kct route 的退出码：0 全部布通且它自己的 DRC 干净；2 部分布通；3 仍有 DRC 违规；
# 4 部分布通且有间距违规；10 它自己的 DRC 没跑完。
# 我们另外用 KiCad DRC 判定，所以这些都算「跑完了」。
KCT_FINISHED = (0, 2, 3, 4, 10)


def kct_route(
    tools: ToolsConfig,
    pcb_in: Path,
    pcb_out: Path,
    *,
    log_dir: Path,
    trace: TraceWriter | None = None,
    backend: str = "cpp",
) -> ToolResult:
    """kicad-tools 0.26.0 的 kct route（M0 对比候选）。

    关掉所有会改动布线以外内容的默认行为：自动铺铜、自动加层、挪元件；也不自动编译 C++ 后端。
    backend：cpp 要求用编译好的 C++ 后端（没有就报错）；python 是纯 Python 后端，慢很多。
    """
    result = run_tool(
        "kct",
        [
            _required(tools.kct, "kct"),
            "route",
            pcb_in,
            "-o",
            pcb_out,
            "--no-auto-pour",
            "--no-auto-layers",
            "--no-placement-feedback",
            "--no-placement-delta-feedback",
            "--no-auto-build-native",
            "--backend",
            backend,
            "--timeout",
            str(int(tools.timeouts.route_s) - 30),
            "-q",
        ],
        cwd=pcb_in.parent,
        env=sandbox_env(tools),
        timeout_s=tools.timeouts.route_s,
        log_dir=log_dir,
        trace=trace,
    )
    return result.check(KCT_FINISHED)


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
