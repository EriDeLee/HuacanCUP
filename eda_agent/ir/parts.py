"""元件分类：G1 的规则和测试台生成器按同一套说法认「连接器」和「阻容感」。"""

from __future__ import annotations

import re
from collections.abc import Collection
from typing import Literal

from eda_agent.ir.models import Design, Part

# 连接器、测试点、安装孔：仿真里没有意义（review-queue D17），不进网表。
NON_ELECTRICAL_LIBS = ("Connector", "TestPoint", "MountingHole", "Mechanical")

PassiveKind = Literal["R", "C", "L"]
_PASSIVE_SYMBOL = {
    "R": re.compile(r"^Device:R(_Small)?(_US)?$"),
    "C": re.compile(r"^Device:C(_Polarized)?(_Small)?(_US)?$"),
    "L": re.compile(r"^Device:L(_Small)?(_US)?$"),
}


def edge_refs(design: Design) -> frozenset[str]:
    return frozenset(c.ref for c in design.board.edge_connectors)


def is_connector(part: Part, edge: Collection[str] = ()) -> bool:
    """连接器、测试点这类：role 是 connector、在板边连接器列表里、或符号库是 Connector*。"""
    lib = part.symbol.split(":", 1)[0]
    return part.role == "connector" or part.ref in edge or lib.startswith(NON_ELECTRICAL_LIBS)


def passive_kind(part: Part) -> PassiveKind | None:
    """Device 库里的电阻、电容、电感（测试台直接用 ngspice 自己的 R、C、L，数值取自 value）。"""
    for kind, pattern in _PASSIVE_SYMBOL.items():
        if pattern.match(part.symbol):
            return kind  # type: ignore[return-value]
    return None
