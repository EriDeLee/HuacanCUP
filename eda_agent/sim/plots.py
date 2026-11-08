"""波形图（PLAN §1.2「仿真报告：波形图 + 每条验收标准的数值与结论」）。
每条验收标准一张 PNG，放进 sim/plots/。

用 matplotlib（requirements.txt 里写死版本，review-queue D25），Agg 后端，不开窗口。
matplotlib 的配置和字体缓存默认写在用户目录，这里改指到沙箱目录（ADR-0001：不写到项目以外）。
图上的文字只用英文和数字：Linux 的云端机器没有中文字体，会画成方框。
直流工作点（op）只有一个点，没有波形，不画。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np

from eda_agent.gates.report import Status
from eda_agent.sim.measure import Measurement, scale_vector, signal_values
from eda_agent.sim.testbench import Bench
from eda_agent.tools.ngspice import SimResult
from eda_agent.units import parse_si

_COLOR = {"pass": "#2e7d32", "fail": "#c62828", "sim_error": "#ef6c00"}
_VERDICT = {"pass": "PASS", "fail": "FAIL", "sim_error": "SIM ERROR"}
_TIME_UNITS = ((1.0, "s"), (1e-3, "ms"), (1e-6, "us"))


def _pyplot(config_dir: Path) -> Any:
    config_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(config_dir))
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot

    return pyplot


def _time_unit(span: float) -> tuple[float, str]:
    for scale, name in _TIME_UNITS:
        if span >= scale * 0.5:
            return scale, name
    return 1e-6, "us"


def plot_acceptance(
    bench: Bench,
    sim: SimResult,
    measurement: Measurement,
    status: Status,
    out: Path,
    *,
    config_dir: Path,
) -> Path | None:
    """画出这条验收标准的被测信号，标上测量结果。画不了（op、缺向量）返回 None。"""
    if bench.analysis == "op":
        return None
    plan = bench.plan
    try:
        x = scale_vector(bench, sim)
        y = signal_values(sim, plan.signal)
    except KeyError:
        return None
    plt = _pyplot(config_dir)
    value = "" if measurement.value is None else f", measured {measurement.value:.6g}"
    heading = f"{plan.acceptance}: {_VERDICT[status]}{value}"
    out.parent.mkdir(parents=True, exist_ok=True)

    if bench.analysis == "ac":
        ref = signal_values(sim, plan.ref_signal) if plan.ref_signal is not None else None
        denominator = 1.0 if ref is None else np.where(ref == 0, 1e-300, ref)
        ratio = y / denominator
        fig, (top, bottom) = plt.subplots(2, 1, sharex=True, figsize=(7, 5))
        top.semilogx(x, 20 * np.log10(np.maximum(np.abs(ratio), 1e-300)), color="#1565c0")
        top.set_ylabel("gain (dB)")
        top.grid(True, which="both", alpha=0.3)
        bottom.semilogx(x, np.degrees(np.unwrap(np.angle(ratio))), color="#6a1b9a")
        bottom.set_ylabel("phase (deg)")
        bottom.set_xlabel("frequency (Hz)")
        bottom.grid(True, which="both", alpha=0.3)
        marker = None
        if plan.measure.kind == "freq_at_db":
            marker = measurement.value
        elif plan.measure.kind == "gain_db_at" and plan.measure.at is not None:
            marker = parse_si(plan.measure.at)
        if marker is not None:
            top.axvline(marker, color=_COLOR[status], linestyle="--")
        fig.suptitle(heading, color=_COLOR[status])
    else:
        y = np.real(y)
        fig, ax = plt.subplots(figsize=(7, 3.6))
        if bench.analysis == "transient":
            scale, unit = _time_unit(float(x[-1] - x[0]) if len(x) > 1 else 1.0)
            ax.plot(np.asarray(x) / scale, y, color="#1565c0", linewidth=1.0)
            ax.set_xlabel(f"time ({unit})")
            if plan.measure.kind == "time_until" and measurement.value is not None:
                ax.axvline(plan.after / scale, color="#757575", linestyle=":")
                found = (plan.after + measurement.value) / scale
                ax.axvline(found, color=_COLOR[status], linestyle="--")
        else:
            ax.plot(x, y, color="#1565c0", linewidth=1.0)
            ax.set_xlabel("sweep value")
        ax.set_ylabel(plan.signal.text)
        ax.grid(True, alpha=0.3)
        ax.set_title(heading, color=_COLOR[status])
    fig.tight_layout()
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out
