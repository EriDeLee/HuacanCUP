"""两个输出驱动同一个网络（PLAN §7 G1）。

只算会硬推的输出：电气类型 output 和 power_out。开集、开射、三态、双向的脚本来就可以并在一起
（线与、总线），不算冲突。同一个元件的两个输出脚接在同一网络上也算（通常是接错了）。
"""

from __future__ import annotations

from eda_agent.gates.report import Finding, error_finding
from eda_agent.gates.rules.context import RuleContext, natural_key

HARD_DRIVERS = frozenset({"output", "power_out"})


def output_conflicts(ctx: RuleContext) -> list[Finding]:
    findings: list[Finding] = []
    for net in ctx.design.nets:
        drivers = sorted(
            ((ref, pin) for ref, pin in net.nodes if ctx.pins[ref][pin].etype in HARD_DRIVERS),
            key=lambda rp: (natural_key(rp[0]), natural_key(rp[1])),
        )
        if len(drivers) < 2:
            continue
        names = "、".join(f"{r}.{p}（{ctx.pins[r][p].name}）" for r, p in drivers)
        findings.append(
            error_finding(
                "output_conflict",
                f"网络 {net.name} 上有 {len(drivers)} 个输出同时驱动：{names}",
                detail={"net": net.name, "drivers": [f"{r}.{p}" for r, p in drivers]},
            )
        )
    return findings
