"""自写规则共用的输入：设计、spec、符号库里每个引脚的电气类型，以及由此算出的网络归类。"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from eda_agent.emit.kicad_sym import Pin, SymbolDef
from eda_agent.emit.sch_layout import infer_kind
from eda_agent.emit.sch_project import net_kind
from eda_agent.gates.report import Finding, GateReport
from eda_agent.ir.models import Design, Part
from eda_agent.ir.parts import edge_refs, is_connector
from eda_agent.ir.spec import Spec

_NATURAL = re.compile(r"(\d+)")

# 负电源脚（运放的 V−、VEE）：单电源用法接地，双电源用法接负电源，两种都对
_NEGATIVE_SUPPLY = re.compile(r"^(?:V-|VEE|VS-|-V|-VS|VNEG)$", re.IGNORECASE)


def natural_key(text: str) -> list[object]:
    """ "2" 排在 "10" 前面，"A9" 排在 "A12" 前面。"""
    return [int(t) if t.isdigit() else t for t in _NATURAL.split(text)]


@dataclass(frozen=True)
class RuleContext:
    design: Design
    spec: Spec | None
    symbols: Mapping[str, SymbolDef]  # 位号 → 符号
    derating: float
    sim: GateReport | None = None  # G2 的报告：有了它，额定值检查走第二轮
    pins: Mapping[str, Mapping[str, Pin]] = field(init=False)
    net_of: Mapping[tuple[str, str], str] = field(init=False)
    kinds: Mapping[str, str] = field(init=False)  # 网络名 → ground / power / signal
    edge: frozenset[str] = field(init=False)

    def __post_init__(self) -> None:
        pins: dict[str, dict[str, Pin]] = {}
        for ref, sym in self.symbols.items():
            table: dict[str, Pin] = {}
            for unit in sym.units.values():
                for pin in unit.pins:
                    table.setdefault(pin.number, pin)
            for pin in sym.pins:
                table.setdefault(pin.number, pin)
            pins[ref] = table
        object.__setattr__(self, "pins", pins)
        object.__setattr__(
            self,
            "net_of",
            {(r, p): n.name for n in self.design.nets for r, p in n.nodes},
        )
        object.__setattr__(
            self, "kinds", {n.name: net_kind(n, dict(self.symbols)) for n in self.design.nets}
        )
        object.__setattr__(self, "edge", edge_refs(self.design))

    # ---- 查询
    def part_pins(self, part: Part) -> list[Pin]:
        return sorted(self.pins[part.ref].values(), key=lambda p: natural_key(p.number))

    def connected_pins(self, part: Part) -> list[tuple[Pin, str]]:
        """这个元件接了网络的引脚和它所在的网络。"""
        return [
            (pin, self.net_of[(part.ref, pin.number)])
            for pin in self.part_pins(part)
            if (part.ref, pin.number) in self.net_of
        ]

    def label(self, ref: str, pin: Pin) -> str:
        return f"{ref}.{pin.number}（{pin.name}）"

    def connector(self, part: Part) -> bool:
        return is_connector(part, self.edge)

    def is_ground_pin(self, pin: Pin) -> bool:
        return infer_kind(pin.name) == "ground"

    def is_negative_supply_pin(self, pin: Pin) -> bool:
        return bool(_NEGATIVE_SUPPLY.match(pin.name))


Rule = Callable[[RuleContext], list[Finding]]
