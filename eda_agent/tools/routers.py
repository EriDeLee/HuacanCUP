"""布线器（ADR-0004，用户 2026-10-31 决定）：fastroute 主用，KiCadRoutingTools 备用。

- fastroute 0.1.14：读 Specctra .dsn，写 .ses。单个 exe，不需要 Java。
  --max-time 到点会自己写出目前最好的结果，比外层统一超时早 30 秒。
- KiCadRoutingTools v0.23.7：直接读写 .kicad_pcb。
  必须带 --no-fix-drc-settings：默认它会把工程里的 DRC 下限放宽到布出来的尺寸，
  那样 DRC 关就被布线器自己降低了门槛。

Freerouting 和 kicad-tools 的实测结果在 ADR-0004；它们的适配代码在 git 提交 aeee649 里。

- TraceMaker 0.12.1：只在布线器对比里用（ADR-0004），只有 Linux 版，在 GitHub Actions 上跑。
  直接读写 .kicad_pcb。参数按官方 docs/reference/cli.md 核对：--no-gpu 不找显卡（结果相同），
  --no-kb 不读写它跨运行的「失败记忆」，每次对比都从零开始；
  时间限制用它的默认值（每个变体 120 秒）。
"""

from __future__ import annotations

import json
from pathlib import Path

from eda_agent.config import ToolsConfig
from eda_agent.tools.runner import ToolResult, run_tool
from eda_agent.tools.sandbox import sandbox_env
from eda_agent.trace import TraceWriter


def board_min_track_um(project_file: Path) -> int | None:
    """KiCad 工程文件里板子规定的最小线宽（µm）；读不到返回 None。"""
    try:
        data = json.loads(project_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = data.get("board", {}).get("design_settings", {}).get("rules", {}).get("min_track_width")
    return round(value * 1000) if isinstance(value, (int, float)) and value > 0 else None


def fastroute_route(
    tools: ToolsConfig,
    dsn: Path,
    ses: Path,
    *,
    log_dir: Path,
    trace: TraceWriter | None = None,
    max_passes: int = 20,
    min_track_um: int | None = None,
) -> ToolResult:
    """min_track_um：板子规定的最小线宽。fastroute 默认会在焊盘附近把线收窄（neck-down），
    不告诉它下限就会收到 0.15 mm 以下，DRC 报线宽错误（M1 多页样例实测）。"""
    args: list[str | Path] = [
        tools.fastroute,
        "-de",
        dsn,
        "-do",
        ses,
        "-mp",
        str(max_passes),
        f"--max-time={int(tools.timeouts.route_s) - 30}",
        f"--report={ses.with_suffix('.fastroute.json')}",
    ]
    if min_track_um is not None:
        args.append(f"--router.min_trace_width_um={min_track_um}")
    result = run_tool(
        "fastroute",
        args,
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


# TraceMaker 的退出码：0 = 计划的连接全部布通；
# 3 = 有没布通的，但结果照样写出来（由 KiCad DRC 打分）。
TRACEMAKER_OK_CODES = (0, 3)


def tracemaker_route(
    tools: ToolsConfig,
    pcb_in: Path,
    pcb_out: Path,
    *,
    summary_json: Path,
    log_dir: Path,
    trace: TraceWriter | None = None,
) -> ToolResult:
    if tools.tracemaker is None:
        raise ValueError("配置里没有 tracemaker：它只有 Linux 版，只在布线器对比里用（ADR-0004）")
    result = run_tool(
        "tracemaker",
        [
            tools.tracemaker,
            "route",
            pcb_in,
            "-o",
            pcb_out,
            "--json",
            summary_json,
            "--no-gpu",
            "--no-kb",
        ],
        cwd=pcb_in.parent,
        env=sandbox_env(tools),
        timeout_s=tools.timeouts.route_s,
        log_dir=log_dir,
        trace=trace,
    )
    return result.check(ok_codes=TRACEMAKER_OK_CODES)
