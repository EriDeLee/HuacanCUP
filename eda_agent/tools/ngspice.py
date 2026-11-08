"""运行 SPICE 网表：在子进程里用 KiCad 自带的 ngspice 共享库仿真（ADR-0003）。"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eda_agent.config import ToolsConfig
from eda_agent.tools.runner import ToolResult, run_tool
from eda_agent.tools.sandbox import sandbox_env
from eda_agent.trace import TraceWriter

# ngspice 把 .meas 的结果印在 "Measurements for … Analysis" 这一行后面，一条测量一行：
#   stdout vmax                =  4.99938e+00 at=  1.00000e-02
# 测不出来的印 failed。
_MEAS_HEADER = re.compile(r"Measurements for .* Analysis", re.IGNORECASE)
_MEAS_LINE = re.compile(r"^(?:std(?:out|err)\s+)?([A-Za-z_][\w.]*)\s*=\s*(\S+)")
# 测不出来的另外报在 stderr：".meas tran never when v(out)=10 rise=1 failed!"
_MEAS_FAILED = re.compile(
    r"^(?:std(?:out|err)\s+)?\.meas\s+\w+\s+([A-Za-z_][\w.]*)\s.*failed", re.I
)


@dataclass(frozen=True)
class SimResult:
    ok: bool
    vectors: dict[str, list[float]]
    errors: list[str]
    output: list[str]
    result_json: Path
    tool: ToolResult
    # 交流分析的向量是复数：名字 → (实部, 虚部)
    complex_vectors: dict[str, tuple[list[float], list[float]]] = field(default_factory=dict)
    plot: str = ""

    def scalar(self, name: str) -> float:
        values = self.vectors[name]
        if len(values) != 1:
            raise ValueError(f"{name} 有 {len(values)} 个点，不是单值")
        return values[0]

    @property
    def measurements(self) -> dict[str, float | None]:
        return parse_measurements(self.output)


def parse_measurements(output: Sequence[str]) -> dict[str, float | None]:
    """.meas 的结果：名字（小写）→ 数值；ngspice 报 failed 的是 None。"""
    found: dict[str, float | None] = {}
    inside = False
    for line in output:
        text = line.strip()
        failed = _MEAS_FAILED.match(text)
        if failed is not None:
            found[failed.group(1).lower()] = None
            continue
        if _MEAS_HEADER.search(text):
            inside = True
            continue
        # 测量失败的提示（out of interval 等）会夹在结果中间，所以不能遇到别的行就结束
        m = _MEAS_LINE.match(text) if inside else None
        if m is None:
            continue
        name, raw = m.group(1).lower(), m.group(2)
        try:
            found[name] = float(raw)
        except ValueError:
            found[name] = None
    return found


def run_netlist(
    tools: ToolsConfig,
    netlist: Path,
    result_json: Path,
    *,
    log_dir: Path,
    trace: TraceWriter | None = None,
    settings: Sequence[str] = (),
    raw: Path | None = None,
    timeout_s: float | None = None,
) -> SimResult:
    """settings：加载电路前执行的 `set …`，如 ngbehavior=ltpsa（厂商模型的兼容模式）。

    raw：不为空就把仿真后的全部向量写成 .raw 文件（PLAN §5.4 的 sim/*.raw）。
    相对路径的 .include 以网表所在目录为准（子进程的工作目录就是它）。
    """
    argv: list[str | Path] = [
        sys.executable,
        "-m",
        "eda_agent.tools.ngspice_worker",
        "--lib",
        tools.ngspice_lib,
        "--netlist",
        netlist,
        "--out",
        result_json,
    ]
    for setting in settings:
        argv += ["--set", setting]
    if raw is not None:
        argv += ["--raw", raw.absolute()]
    result_json.unlink(missing_ok=True)  # 子进程崩溃时不能读到上一次留下的结果
    tool = run_tool(
        "ngspice",
        argv,
        cwd=netlist.parent,
        env=sandbox_env(tools),
        timeout_s=timeout_s or tools.timeouts.sim_s,
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
        complex_vectors={
            k.lower(): (list(v["re"]), list(v["im"]))
            for k, v in data.get("complex_vectors", {}).items()
        },
        plot=str(data.get("plot", "")),
    )
