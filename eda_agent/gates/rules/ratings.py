"""额定值检查（PLAN §7 G1）：网络电压 ≤ 器件 ratings × 降额系数。
降额系数默认 0.8，在 config.yaml 的 gates.derating。

`parts[].ratings` 的键按这个约定认（单位是国际单位制，review-queue D18）：
    v…_max（vds_max、vcc_max……）电压 V
    i…_max（id_max、if_max……）电流 A
    p…_max（pd_max……）功耗 W
别的键不查。

两轮：
- 第一轮只有 spec：器件各引脚所在网络里，名字和 spec 接口同名的那些，取接口 v_min / v_nom / v_max
  里绝对值最大的当网络电压；器件的电压额定值要不小于其中最大的一个。不知道电压的网络不查。
- 第二轮有了 G2 的报告：器件的电压、电流、功耗用仿真量到的峰值（device_stress）；
  电压再和第一轮的数取大的，因为仿真用标称电压，spec 的上限可能更高。

「网络电压」是网络对地的电压，不是器件两端的压差。对单电源电路这是偏保守的做法；
双电源、浮地这类压差大于对地电压的电路会漏，到时候要换成按引脚对算压差（留到以后定）。
"""

from __future__ import annotations

import re

from eda_agent.gates.report import Finding, error_finding, margin_fields
from eda_agent.gates.rules.context import RuleContext
from eda_agent.ir.spec import Spec

_KEY = re.compile(r"^([vip])[a-z0-9]*_max$")
_UNIT = {"v": "V", "i": "A", "p": "W"}


def spec_net_voltages(spec: Spec | None) -> dict[str, float]:
    """spec 接口名（= 网络名）→ 这个网络可能出现的最大电压（绝对值）。"""
    out: dict[str, float] = {}
    for iface in spec.interfaces if spec is not None else []:
        volts = [abs(v) for v in (iface.v_min, iface.v_nom, iface.v_max) if v is not None]
        if volts:
            out[iface.name] = max(volts)
    return out


def rating_exceeded(ctx: RuleContext) -> list[Finding]:
    spec_v = spec_net_voltages(ctx.spec)
    stress = ctx.sim.device_stress if ctx.sim is not None else {}
    findings: list[Finding] = []
    for part in ctx.design.parts:
        measured: dict[str, list[tuple[float, str]]] = {"v": [], "i": [], "p": []}
        nets = {net for _, net in ctx.connected_pins(part)}
        known = [(spec_v[n], f"spec 接口 {n}") for n in sorted(nets) if n in spec_v]
        if known:
            measured["v"].append(max(known))
        if part.ref in stress:
            s = stress[part.ref]
            measured["v"].append((s.v_peak, "G2 仿真峰值"))
            measured["i"].append((s.i_peak, "G2 仿真峰值"))
            measured["p"].append((s.p_peak, "G2 仿真峰值"))
        for key, rating in sorted(part.ratings.items()):
            m = _KEY.match(key)
            if m is None or not measured[m.group(1)]:
                continue
            kind = m.group(1)
            actual, source = max(measured[kind])
            limit = rating * ctx.derating
            if actual <= limit:
                continue
            findings.append(
                error_finding(
                    "rating_exceeded",
                    f"{part.ref} 的 {key}：{actual:g} {_UNIT[kind]}（{source}）超过额定 "
                    f"{rating:g} × 降额 {ctx.derating:g} = {limit:g} {_UNIT[kind]}",
                    **margin_fields(actual, "<=", limit, unit=_UNIT[kind]),
                    detail={
                        "ref": part.ref,
                        "key": key,
                        "rating": rating,
                        "derating": ctx.derating,
                        "source": source,
                    },
                )
            )
    return findings
