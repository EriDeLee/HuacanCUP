"""悬空引脚：输入脚和电源输入脚没接任何网络，也没标 NC（PLAN §7 G1）。

只查符号库里电气类型是 input 或 power_in 的引脚。输出、双向、无源脚不接东西是常见的（空闲的 IO、
空着的连接器脚），不报；符号库里本来就标成 no_connect 的脚也不报。
"""

from __future__ import annotations

from eda_agent.gates.report import Finding, error_finding
from eda_agent.gates.rules.context import RuleContext

MUST_BE_CONNECTED = frozenset({"input", "power_in"})


def dangling_pins(ctx: RuleContext) -> list[Finding]:
    findings: list[Finding] = []
    for part in ctx.design.parts:
        connected = {pin for (ref, pin) in ctx.net_of if ref == part.ref}
        for pin in ctx.part_pins(part):
            if pin.number in connected or pin.number in part.nc_pins:
                continue
            if pin.etype not in MUST_BE_CONNECTED:
                continue
            kind = "电源脚" if pin.etype == "power_in" else "输入脚"
            findings.append(
                error_finding(
                    "dangling_pin",
                    f"{ctx.label(part.ref, pin)} 是{kind}，没接任何网络，也没标 NC",
                    detail={
                        "ref": part.ref,
                        "pin": pin.number,
                        "name": pin.name,
                        "type": pin.etype,
                    },
                )
            )
    return findings
