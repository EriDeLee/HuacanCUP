"""封装可装配性：和 spec.constraints.assembly / min_package 冲突就报错（PLAN §7 G1）。

- min_package（如 "0805"）：封装名里带英制尺寸代码的（KiCad 的命名 R_0805_2012Metric、
  LED_0603_1608Metric……），比它小的不允许。尺寸比较按「长×宽」的面积：
  0201 < 0402 < 0603 < 0805 < 1206 < 1210……
- assembly = "hand_solder"（手焊）：焊盘在封装底下的封装
  （QFN、DFN、SON、LGA、BGA、WLCSP……）不允许。reflow、smt_service 不限制封装类型。
只看封装名，不看尺寸表；封装名里没有这些记号的，规则不会误报，也不会替它做判断。
"""

from __future__ import annotations

import re

from eda_agent.gates.report import Finding, error_finding
from eda_agent.gates.rules.context import RuleContext

# 带英制代码的封装名：…_0805_2012Metric
_CHIP_SIZE = re.compile(r"(?:^|_)(\d{2})(\d{2})_\d{4}Metric(?:_|$)")
# 焊盘在底下的封装：名字里以这些记号开头，或者被 _ - 隔开（QFN-16、DFN8、WQFN-24、BGA-64……）
_BOTTOM_PADS = re.compile(
    r"(?:^|[_-])(?:[A-Z]{0,2}QFN|[A-Z]{0,2}DFN|[A-Z]{0,2}SON|LGA|[A-Z]?BGA|WLCSP|CSP)(?=$|[-_\d])",
    re.IGNORECASE,
)


def chip_size_area(footprint: str) -> int | None:
    """ "R_0805_2012Metric" → 8 × 5 = 40；封装名里没有尺寸代码返回 None。"""
    m = _CHIP_SIZE.search(footprint.split(":", 1)[-1])
    return int(m.group(1)) * int(m.group(2)) if m else None


def bottom_pad_package(footprint: str) -> bool:
    return _BOTTOM_PADS.search(footprint.split(":", 1)[-1]) is not None


def package_assemblable(ctx: RuleContext) -> list[Finding]:
    if ctx.spec is None:
        return []
    cons = ctx.spec.constraints
    findings: list[Finding] = []
    for part in ctx.design.parts:
        if cons.min_package is not None:
            limit = int(cons.min_package[:2]) * int(cons.min_package[2:])
            area = chip_size_area(part.footprint)
            if area is not None and area < limit:
                findings.append(
                    error_finding(
                        "package_too_small",
                        f"{part.ref} 的封装 {part.footprint} 比 spec 允许的最小封装 "
                        f"{cons.min_package} 还小",
                        detail={"ref": part.ref, "footprint": part.footprint},
                    )
                )
        if cons.assembly == "hand_solder" and bottom_pad_package(part.footprint):
            findings.append(
                error_finding(
                    "package_not_hand_solderable",
                    f"{part.ref} 的封装 {part.footprint} 焊盘在封装底下，spec 要求手焊",
                    detail={"ref": part.ref, "footprint": part.footprint},
                )
            )
    return findings
