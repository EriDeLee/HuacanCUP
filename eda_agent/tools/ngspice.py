"""运行 SPICE 网表：在子进程里用 KiCad 自带的 ngspice 共享库仿真（ADR-0003）。"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eda_agent.config import ToolsConfig
from eda_agent.tools.runner import ToolResult, run_tool
from eda_agent.tools.sandbox import sandbox_env
from eda_agent.trace import TraceWriter


@dataclass(frozen=True)
class SimResult:
    ok: bool
    vectors: dict[str, list[float]]
    errors: list[str]
    output: list[str]
    result_json: Path
    tool: ToolResult

    def scalar(self, name: str) -> float:
        values = self.vectors[name]
        if len(values) != 1:
            raise ValueError(f"{name} 有 {len(values)} 个点，不是单值")
        return values[0]


def run_netlist(
    tools: ToolsConfig,
    netlist: Path,
    result_json: Path,
    *,
    log_dir: Path,
    trace: TraceWriter | None = None,
) -> SimResult:
    tool = run_tool(
        "ngspice",
        [
            sys.executable,
            "-m",
            "eda_agent.tools.ngspice_worker",
            "--lib",
            tools.ngspice_lib,
            "--netlist",
            netlist,
            "--out",
            result_json,
        ],
        cwd=netlist.parent,
        env=sandbox_env(tools),
        timeout_s=tools.timeouts.sim_s,
        log_dir=log_dir,
        trace=trace,
    )
    data: dict[str, Any] = {}
    if result_json.exists():
        data = json.loads(result_json.read_text(encoding="utf-8"))
    return SimResult(
        ok=tool.exit_code == 0 and not tool.timed_out and bool(data.get("ok")),
        vectors={k.lower(): v for k, v in data.get("vectors", {}).items()},
        errors=list(data.get("errors", [])),
        output=list(data.get("output", [])),
        result_json=result_json,
        tool=tool,
    )
