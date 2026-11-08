"""每个 IC 的电源脚有去耦电容（PLAN §7 G1）：按网络拓扑判断，不按距离。

做法：对每个元件、它每个接在电源网络上的电源输入脚（地脚和接地的负电源脚不算），这个电源网络上必须
有一个电容，另一端接地网络。同一元件有几个脚接在同一电源网络上，算一处，只报一次。
一个电容接在两个电源网络之间（不接地）不算去耦。

拓扑看不出电容离芯片多远，离得远不远是摆放和版图的事（自动摆放会把同块元件聚在一起）。
"""

from __future__ import annotations

from eda_agent.gates.report import Finding, error_finding
from eda_agent.gates.rules.context import RuleContext
from eda_agent.ir.parts import passive_kind


def decoupled_nets(ctx: RuleContext) -> set[str]:
    """至少有一个电容把它接到地网络的电源网络。"""
    out: set[str] = set()
    for part in ctx.design.parts:
        if passive_kind(part) != "C":
            continue
        nets = {net for _, net in ctx.connected_pins(part)}
        if any(ctx.kinds[n] == "ground" for n in nets):
            out |= {n for n in nets if ctx.kinds[n] == "power"}
    return out


def missing_decoupling(ctx: RuleContext) -> list[Finding]:
    decoupled = decoupled_nets(ctx)
    findings: list[Finding] = []
    for part in ctx.design.parts:
        if ctx.connector(part):
            continue
        reported: set[str] = set()
        for pin, net in ctx.connected_pins(part):
            if pin.etype != "power_in" or ctx.is_ground_pin(pin) or ctx.kinds[net] != "power":
                continue
            if net in decoupled or net in reported:
                continue
            reported.add(net)
            pins = ", ".join(
                f"{p.number}（{p.name}）"
                for p, n in ctx.connected_pins(part)
                if n == net and p.etype == "power_in"
            )
            findings.append(
                error_finding(
                    "missing_decoupling",
                    f"{part.ref} 的电源脚 {pins} 所在的网络 {net} 上没有接到地的去耦电容",
                    detail={"ref": part.ref, "net": net},
                )
            )
    return findings
