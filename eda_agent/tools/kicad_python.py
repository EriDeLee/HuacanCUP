"""在 KiCad 自带的 Python 3.11（能 import pcbnew）里运行 eda_agent/kicad_side 下的脚本。

主程序跑在 Python 3.14，pcbnew 只能在 KiCad 自带的 Python 里用，所以单独起进程（ADR-0005）。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from eda_agent.config import REPO_ROOT, ToolsConfig
from eda_agent.tools.runner import ToolResult, run_tool
from eda_agent.tools.sandbox import sandbox_env
from eda_agent.trace import TraceWriter

KICAD_SIDE_DIR = REPO_ROOT / "eda_agent" / "kicad_side"


def run_kicad_script(
    tools: ToolsConfig,
    script: str,
    args: Sequence[str | Path],
    *,
    cwd: Path,
    log_dir: Path,
    trace: TraceWriter | None = None,
    timeout_s: float | None = None,
) -> ToolResult:
    result = run_tool(
        f"kicad-python:{script}",
        [tools.kicad_python, KICAD_SIDE_DIR / script, *args],
        cwd=cwd,
        env=sandbox_env(tools),
        timeout_s=timeout_s or tools.timeouts.default_s,
        log_dir=log_dir,
        trace=trace,
    )
    return result.check()
