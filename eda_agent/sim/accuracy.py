"""G2 额外测量的复核（review-queue D39）：峰值只在步长加密以后也不变，才采用。

峰值（最高电压、最大电流、最大功耗）是时间上的极值，对 ngspice 迈的步子很敏感。
开关一通一断的瞬间，默认的误差控制（trtol = 7）会放过太大的一步，量出来的极值就不准。
过流切断样例里 Q1 的最高电压：步子粗时是 13.8 V、22.8 V、55.4 V（看机器和收敛走到哪一档），
步子一路缩小，稳稳收敛到 12.40 V（电池 12 V 加 0.4 V 的过冲）。
原来只要 ngspice 没报错就信它给的峰值，同一个电路换一台机器就得到不同的数，G1 额定值检查跟着误报。

做法：
1. 收敛重试（四档）只负责出判定，不碰峰值；
2. 瞬态分析另跑一串严格设置（trtol = 1、gear 积分）：第 0 轮用 spec 给的最大步长，
   第 k 轮把误差系数和最大步长都缩小一半（一共最多 MAX_ROUNDS 轮）；
3. 相邻两轮里，一个条目（一个器件的 v、i、p，或一个网络的电流）每个数都差在 3 % 以内，
   这个条目就算收敛，采用较细那一轮的数；
4. 没收敛的条目不报：不交给 G1 的额定值检查，也就不会误报；detail 里写明是哪些。
op、dc、ac 没有时间步长的问题，不走这里。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from eda_agent.gates.report import DeviceStress
from eda_agent.sim.stress import device_stress, net_current_peaks
from eda_agent.sim.testbench import AttemptPlan, Bench
from eda_agent.tools.ngspice import SimResult

REL_TOL = 0.03
MAX_ROUNDS = 3  # 第 0 轮之外最多再加几轮
# 差在这个数以内就算一样（电压 V、电流 A、功耗 W）：接近零的数不去比相对误差
FLOOR_DEVICE = (1e-3, 1e-6, 1e-6)
FLOOR_NET = (1e-6,)

# 严格设置，按顺序试，第 0 轮跑得通的那一组用到底
STRICT_SETTINGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("gear", ("method=gear",)),
    ("trap_tight", ("reltol=0.0001",)),
    ("gear_tight", ("method=gear", "reltol=0.0001")),
)

Extremes = dict[str, tuple[float, ...]]  # "dev:Q1" → (v, i, p)；"net:VBAT" → (A,)
Run = Callable[[AttemptPlan, str], tuple[Bench, SimResult] | None]
Extract = Callable[[Bench, SimResult], Extremes]


def strict_plan(options: tuple[str, ...], round_number: int) -> AttemptPlan:
    """第 round_number 轮的设置：误差系数从 1 起每轮减半，最大步长每轮减半。"""
    return AttemptPlan("accuracy", options, False, 2**round_number, 1.0 / 2**round_number)


def extremes(bench: Bench, sim: SimResult) -> Extremes:
    """这一轮量到的所有极值。结果里缺探针的向量时抛 KeyError。"""
    out: Extremes = {}
    for ref, s in device_stress(bench, sim).items():
        out[f"dev:{ref}"] = (s.v_peak, s.i_peak, s.p_peak)
    for net, amps in net_current_peaks(bench, sim).items():
        out[f"net:{net}"] = (amps,)
    return out


def agree(key: str, a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    floors = FLOOR_NET if key.startswith("net:") else FLOOR_DEVICE
    return all(
        abs(x - y) <= max(REL_TOL * max(abs(x), abs(y)), floor)
        for x, y, floor in zip(a, b, floors, strict=True)
    )


@dataclass
class Confirmed:
    devices: dict[str, DeviceStress] = field(default_factory=dict)
    nets: dict[str, float] = field(default_factory=dict)
    settings: str = ""  # 用的是哪一组严格设置
    runs: list[dict[str, float]] = field(default_factory=list)  # 每轮的 trtol 和步长缩小的倍数
    unconfirmed: list[str] = field(default_factory=list)  # "dev:U1"、"net:GND"
    error: str = ""

    def summary(self) -> dict[str, Any]:
        """写进 finding.detail["stress_check"]。"""
        out: dict[str, Any] = {"settings": self.settings, "runs": self.runs}
        if self.unconfirmed:
            out["unconfirmed"] = self.unconfirmed
        if self.error:
            out["error"] = self.error
        return out


def confirm(run: Run, extract: Extract = extremes) -> Confirmed:
    """run(设置, 标签) 跑一次仿真，没跑通返回 None；返回 (测试台, 结果)。"""
    out = Confirmed()
    first: tuple[Bench, SimResult] | None = None
    options: tuple[str, ...] = ()
    for name, options in STRICT_SETTINGS:
        first = run(strict_plan(options, 0), f"{name}.0")
        if first is not None:
            out.settings = name
            break
    if first is None:
        out.error = "没有一组严格设置能跑完这个瞬态仿真，峰值不采用"
        return out
    try:
        previous = extract(*first)
    except KeyError as exc:
        out.error = f"结果里缺探针的向量 {exc.args[0] if exc.args else exc}，峰值不采用"
        return out
    out.runs.append({"trtol": 1.0, "step_divisor": 1})

    final: Extremes = {}
    pending = set(previous)
    for k in range(1, MAX_ROUNDS + 1):
        if not pending:
            break
        got = run(strict_plan(options, k), f"{out.settings}.{k}")
        if got is None:
            break
        out.runs.append({"trtol": 1.0 / 2**k, "step_divisor": 2**k})
        now = extract(*got)
        final |= {
            key: now[key]
            for key in sorted(pending)
            if key in now and agree(key, previous[key], now[key])
        }
        pending -= set(final)
        previous = now
    out.unconfirmed = sorted(pending)
    out.devices = {
        key[4:]: DeviceStress(v_peak=v[0], i_peak=v[1], p_peak=v[2])
        for key, v in sorted(final.items())
        if key.startswith("dev:")
    }
    out.nets = {key[4:]: v[0] for key, v in sorted(final.items()) if key.startswith("net:")}
    return out
