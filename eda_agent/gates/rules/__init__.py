"""G1 的自写电气规则（PLAN §7）：每条规则一个函数，输入 RuleContext，输出 finding 列表。

都是通用的电气规则，不针对某一类电路（AGENTS.md 原则 6）。每条规则在 tests/test_rules.py 里
至少有一个「应报错」和一个「不应报错」的用例。
"""

from __future__ import annotations

from eda_agent.gates.rules.assembly import package_assemblable
from eda_agent.gates.rules.context import Rule, RuleContext
from eda_agent.gates.rules.dangling import dangling_pins
from eda_agent.gates.rules.decoupling import missing_decoupling
from eda_agent.gates.rules.drivers import output_conflicts
from eda_agent.gates.rules.power_pins import power_pin_nets
from eda_agent.gates.rules.ratings import rating_exceeded

RULES: tuple[tuple[str, Rule], ...] = (
    ("dangling_pins", dangling_pins),
    ("power_pin_nets", power_pin_nets),
    ("output_conflicts", output_conflicts),
    ("missing_decoupling", missing_decoupling),
    ("rating_exceeded", rating_exceeded),
    ("package_assemblable", package_assemblable),
)

__all__ = ["RULES", "Rule", "RuleContext"]
