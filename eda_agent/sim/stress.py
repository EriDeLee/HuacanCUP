"""G2 的额外测量（PLAN §7 G2）：每个器件的峰值电压、电流、功耗，以及每个网络的电流上界。

数据来自测试台里每个引脚上串的 0 V 电压源（电流探针）和网络的节点电压：
- 器件的 v_peak：各引脚所在网络对地电压的最大绝对值（和 G1 额定值检查的「网络电压」同一个口径）；
- 器件的 i_peak：各引脚电流绝对值的最大值；
- 器件的 p_peak：各引脚电压乘流进该引脚的电流，加起来就是瞬时功耗，取时间上的最大值（负的不计）；
- 网络电流上界：这个网络上所有端点电流绝对值之和的一半，取时间上的最大值。证明：把网络的端点分成
  两边，穿过切口的电流等于一边的净流出，不超过两边绝对值之和中较小的那个，也就不超过总和的一半。
  所以任何一段走线上的电流都不会超过它（G3 线宽检查用，review-queue D21）。端点包括元件的引脚、
  接口电源的两头（电源端和地）、负载的两头。
op、dc、transient 才有这些量；ac 是小信号，没有「峰值」，不算。

用了 uic 的那次尝试（收敛重试的最后一档）一开始所有节点从 0 V 起步，电源对电容的冲击电流是假的
（几十上百安），所以头 0.5 % 的仿真时间不参加。
"""

from __future__ import annotations

import numpy as np

from eda_agent.gates.report import DeviceStress
from eda_agent.sim.measure import Array, vector
from eda_agent.sim.testbench import Bench
from eda_agent.tools.ngspice import SimResult

STRESS_ANALYSES = ("op", "dc", "transient")
UIC_SKIP = 0.005


def _keep(bench: Bench, sim: SimResult) -> Array | None:
    """要保留的采样点（布尔数组）；全部保留返回 None。"""
    if bench.attempt.uic and bench.analysis == "transient":
        time = np.real(vector(sim, "time"))
        return np.asarray(time > UIC_SKIP * time[-1])
    return None


def _current(sim: SimResult, name: str, keep: Array | None) -> Array:
    values = np.real(vector(sim, f"{name}#branch"))
    return values if keep is None else values[keep]


def _voltage(bench: Bench, sim: SimResult, net: str, like: Array, keep: Array | None) -> Array:
    node = bench.nodes[net]
    if node == "0":
        return np.zeros_like(like)
    values = np.real(vector(sim, node))
    return values if keep is None else values[keep]


def device_stress(bench: Bench, sim: SimResult) -> dict[str, DeviceStress]:
    if bench.analysis not in STRESS_ANALYSES:
        return {}
    keep = _keep(bench, sim)
    pin_net = {p.name: p.net for p in bench.probes if p.kind == "pin"}
    out: dict[str, DeviceStress] = {}
    for ref, pins in bench.part_probes.items():
        currents = [_current(sim, probe, keep) for probe in pins.values()]
        if not currents or not len(currents[0]):
            continue
        volts = [_voltage(bench, sim, pin_net[p], currents[0], keep) for p in pins.values()]
        power = sum(v * i for v, i in zip(volts, currents, strict=True))
        out[ref] = DeviceStress(
            v_peak=float(max(np.abs(v).max() for v in volts)),
            i_peak=float(max(np.abs(i).max() for i in currents)),
            p_peak=float(max(np.max(power), 0.0)),
        )
    return out


def net_current_peaks(bench: Bench, sim: SimResult) -> dict[str, float]:
    if bench.analysis not in STRESS_ANALYSES:
        return {}
    keep = _keep(bench, sim)
    terminals: dict[str, list[Array]] = {}
    for probe in bench.probes:
        current = _current(sim, probe.name, keep)
        terminals.setdefault(probe.net, []).append(current)
        # 电源和负载还有另一头：电源的负极接在地上，负载的另一头接在乙网络上
        if probe.kind == "load":
            terminals.setdefault(probe.other_net, []).append(current)
        elif probe.kind == "source":
            terminals.setdefault(bench.ground, []).append(current)
    return {
        net: float(np.max(sum(np.abs(c) for c in currents)) / 2)
        for net, currents in terminals.items()
        if len(currents[0])
    }
