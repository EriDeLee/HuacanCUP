"""电源脚没接电源网络、地脚没接地（PLAN §7 G1）。

引脚的电气类型从符号库读：power_in 的脚才查。是不是地脚看引脚名（GND、AGND、VSS……，和原理图
引擎认地网络用同一套规则）；网络是不是电源、地，用原理图引擎的 net_kind（地看名字；电源要有
板外送电标记、电源输出脚驱动，或者名字像电源且有电源输入脚接着），两边不会各说各的。

- 地脚（GND、VSS……）必须接地网络。
- 负电源脚（V−、VEE）接地网络或电源网络都行：单电源接地，双电源接负电源。
- 其余电源输入脚必须接电源网络：接了地网络是短路，接了信号网络是没有供电。
"""

from __future__ import annotations

from eda_agent.gates.report import Finding, error_finding
from eda_agent.gates.rules.context import RuleContext


def power_pin_nets(ctx: RuleContext) -> list[Finding]:
    findings: list[Finding] = []
    for part in ctx.design.parts:
        for pin, net in ctx.connected_pins(part):
            if pin.etype != "power_in":
                continue
            kind = ctx.kinds[net]
            detail = {"ref": part.ref, "pin": pin.number, "name": pin.name, "net": net}
            who = ctx.label(part.ref, pin)
            if ctx.is_ground_pin(pin):
                if kind != "ground":
                    findings.append(
                        error_finding(
                            "ground_pin_not_grounded",
                            f"{who} 是地脚，接的网络 {net} 不是地"
                            "（按网络名认地：GND、AGND、VSS……）",
                            detail=detail,
                        )
                    )
            elif ctx.is_negative_supply_pin(pin):
                if kind == "signal":
                    findings.append(
                        error_finding(
                            "power_pin_not_on_power_net",
                            f"{who} 是负电源脚，接的网络 {net} 既不是地也不是电源",
                            detail=detail,
                        )
                    )
            elif kind == "ground":
                findings.append(
                    error_finding(
                        "power_pin_on_ground",
                        f"{who} 是电源脚，却接在地网络 {net} 上",
                        detail=detail,
                    )
                )
            elif kind == "signal":
                findings.append(
                    error_finding(
                        "power_pin_not_on_power_net",
                        f"{who} 是电源脚，接的网络 {net} 不是电源网络："
                        "没有板外送电标记、没有电源输出脚驱动，名字也不像电源",
                        detail=detail,
                    )
                )
    return findings
