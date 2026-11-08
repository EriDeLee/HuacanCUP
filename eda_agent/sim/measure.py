"""把 ngspice 的结果解释成每条验收标准的一个数（PLAN §7 G2）。

大部分测量直接读网表里 `.meas` 语句的输出（tools/ngspice.py 的 SimResult.measurements）：
- value_at、max、min、avg：就是 `.meas` 的值；
- time_until：`.meas` 的 TRIG/TARG 给出从 after 到条件首次成立的时间间隔（review-queue D17）。
  `.meas` 在条件一开始就成立时会失败（找不到穿越），这时回头看向量：
  after 那一刻已经满足条件，间隔就是 0；
- frequency：`.meas` 量 cycles 个周期的总时长，频率 = cycles / 总时长；
- gain_db_at：输出和参考信号各一条 `.meas`（vdb），相减。
op 没有 `.meas`，取工作点向量；
freq_at_db 要先做复数的比值再找交点，在向量上找（对数频率上线性插值）。

三种结局：
- ok：得到一个有限的数；
- unmet：测量对象没有出现（信号从没越过条件、没有足够的上升沿、增益没到过那个 dB 值）。
  电路没做到该做的事，是 fail，不是 sim_error；
- error：没得出结果（ngspice 没给出这条 .meas、数不是有限数、测量点在仿真范围外）。
  是 sim_error。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray

from eda_agent.ir.spec import parse_condition
from eda_agent.sim.testbench import Bench, Signal
from eda_agent.tools.ngspice import SimResult
from eda_agent.units import parse_si

Array = NDArray[Any]


@dataclass(frozen=True)
class Measurement:
    status: Literal["ok", "unmet", "error"]
    value: float | None = None
    message: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


def vector(sim: SimResult, name: str) -> Array:
    """向量名（小写）→ 数组。交流分析的是复数数组。"""
    key = name.lower()
    if key in sim.vectors:
        return np.asarray(sim.vectors[key], dtype=float)
    if key in sim.complex_vectors:
        re, im = sim.complex_vectors[key]
        return np.asarray(re, dtype=float) + 1j * np.asarray(im, dtype=float)
    raise KeyError(f"仿真结果里没有向量 {name}")


def scale_name(bench: Bench) -> str:
    return {"transient": "time", "ac": "frequency"}.get(bench.analysis, "")


def scale_vector(bench: Bench, sim: SimResult) -> Array:
    name = scale_name(bench)
    if name:
        return np.real(vector(sim, name))
    for key in ("v-sweep", "i-sweep", "sweep"):
        if key in sim.vectors:
            return vector(sim, key)
    return np.arange(len(next(iter(sim.vectors.values()), [])), dtype=float)


def node_values(sim: SimResult, node: str, like: Array) -> Array:
    return np.zeros_like(like) if node == "0" else vector(sim, node)


def signal_values(sim: SimResult, sig: Signal) -> Array:
    """被测信号随时间（频率、扫描值）的数组。"""
    if sig.kind == "i":
        return vector(sim, f"{sig.probe}#branch")
    first = (
        vector(sim, next(n for n in sig.nodes if n != "0"))
        if any(n != "0" for n in sig.nodes)
        else np.zeros(1)
    )
    values = [node_values(sim, n, first) for n in sig.nodes]
    return values[0] if len(values) == 1 else values[0] - values[1]


def _finite(value: float | None) -> bool:
    return value is not None and math.isfinite(value)


def _crossed(op: str, value: float, level: float) -> bool:
    return {"<": value < level, "<=": value <= level, ">": value > level, ">=": value >= level}[op]


def evaluate(bench: Bench, sim: SimResult) -> Measurement:
    plan = bench.plan
    kind = plan.measure.kind
    if plan.analysis == "op":
        return _op_value(bench, sim)
    if kind == "freq_at_db":
        return _freq_at_db(bench, sim)

    meas = sim.measurements
    missing = [n for n in plan.meas_names if n not in meas]
    if missing:
        return Measurement(
            "error", message=f"ngspice 没有给出测量 {', '.join(missing)}（.meas 语句没被执行）"
        )
    values = {n: meas[n] for n in plan.meas_names}
    if any(v is None for v in values.values()):
        return _failed_meas(bench, sim, values)
    nums = {n: float(v) for n, v in values.items() if v is not None}
    if not all(math.isfinite(v) for v in nums.values()):
        return Measurement("error", message=f"测量值不是有限数：{nums}")

    first = next(iter(nums.values()))
    if kind == "frequency":
        if first <= 0:
            return Measurement("error", message=f"周期总时长 {first:g} s 不是正数")
        return Measurement("ok", plan.measure.cycles / first, detail={"span_s": first})
    if kind == "gain_db_at":
        out = nums[f"m_{plan.acceptance.lower()}_out"]
        ref = nums.get(f"m_{plan.acceptance.lower()}_ref", 0.0)
        return Measurement("ok", out - ref, detail={"out_db": out, "ref_db": ref})
    return Measurement("ok", first)


def _op_value(bench: Bench, sim: SimResult) -> Measurement:
    try:
        arr = signal_values(sim, bench.plan.signal)
    except KeyError as exc:
        return Measurement("error", message=str(exc))
    if len(arr) < 1 or not _finite(float(arr[0])):
        return Measurement("error", message="工作点向量是空的或不是有限数")
    return Measurement("ok", float(arr[0]))


def _failed_meas(bench: Bench, sim: SimResult, values: dict[str, float | None]) -> Measurement:
    """ngspice 报了 failed。区分「电路没做到」（unmet）和「测量本身出了问题」（error）。"""
    plan = bench.plan
    kind = plan.measure.kind
    bad = ", ".join(n for n, v in values.items() if v is None)
    if kind == "time_until":
        try:
            time = np.real(vector(sim, "time"))
            arr = np.real(signal_values(sim, plan.signal))
        except KeyError as exc:
            return Measurement("error", message=str(exc))
        assert plan.condition is not None
        op, level = plan.condition
        if plan.after > time[-1]:
            return Measurement(
                "error",
                message=f"after = {plan.after:g} s 超出了仿真时间（{time[-1]:g} s）",
            )
        at_after = float(np.interp(plan.after, time, arr))
        if _crossed(op, at_after, level):
            return Measurement(
                "ok",
                0.0,
                message=f"{plan.measure.condition} 在 after 那一刻就已经成立",
                detail={"signal_at_after": at_after},
            )
        return Measurement(
            "unmet",
            message=f"{plan.signal.text} 在 {plan.after:g} s 之后到仿真结束都没有满足 "
            f"{plan.measure.condition}（最后一点 {arr[-1]:g}）",
        )
    if kind == "frequency":
        return Measurement(
            "unmet",
            message=f"{plan.signal.text} 在 {plan.measure.level:g} 处没有足够的上升沿"
            f"（要 {plan.measure.cycles + 1} 个）：电路没有振荡，或者振荡太慢、仿真时间不够",
        )
    return Measurement("error", message=f"ngspice 报告测量 {bad} 失败（测量点可能在仿真范围之外）")


def _freq_at_db(bench: Bench, sim: SimResult) -> Measurement:
    plan = bench.plan
    m = plan.measure
    assert m.db is not None
    try:
        freq = np.real(vector(sim, "frequency"))
        out = signal_values(sim, plan.signal)
        ref = signal_values(sim, plan.ref_signal) if plan.ref_signal is not None else None
    except KeyError as exc:
        return Measurement("error", message=str(exc))
    ratio = np.abs(out) if ref is None else np.abs(out) / np.maximum(np.abs(ref), 1e-300)
    gain = 20 * np.log10(np.maximum(ratio, 1e-300))
    target = m.db
    if m.ref_at is not None:
        at = parse_si(m.ref_at)
        if not freq[0] <= at <= freq[-1]:
            return Measurement("error", message=f"ref_at = {at:g} Hz 超出了扫描范围")
        target += float(np.interp(np.log10(at), np.log10(freq), gain))
    diff = gain - target
    hits = np.nonzero(np.signbit(diff[:-1]) != np.signbit(diff[1:]))[0]
    zero = np.nonzero(diff == 0)[0]
    if len(zero) and (not len(hits) or zero[0] <= hits[0]):
        return Measurement("ok", float(freq[zero[0]]), detail={"target_db": target})
    if not len(hits):
        return Measurement(
            "unmet",
            message=f"增益在 {freq[0]:g}–{freq[-1]:g} Hz 的范围内一直没有到过 {target:g} dB"
            f"（最高 {gain.max():g}，最低 {gain.min():g}）",
        )
    i = int(hits[0])
    frac = (target - gain[i]) / (gain[i + 1] - gain[i])
    f = float(10 ** (np.log10(freq[i]) + frac * (np.log10(freq[i + 1]) - np.log10(freq[i]))))
    return Measurement("ok", f, detail={"target_db": target})


def condition_of(text: str) -> tuple[str, float]:
    return parse_condition(text)
