"""读 kicad-cli 的 ERC / DRC JSON 报告并给出判定（M0 版本；M2 按 PLAN §7 扩成完整 Gate）。

只有 severity 为 error 的条目算不通过；warning 计数照报，但不影响判定。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GateReport:
    gate: str
    passed: bool
    counts: dict[str, int]
    findings: list[dict[str, Any]] = field(default_factory=list)


def _errors(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in items if item.get("severity") == "error"]


def erc_gate(report: Path) -> GateReport:
    data: dict[str, Any] = json.loads(report.read_text(encoding="utf-8"))
    violations = [v for sheet in data.get("sheets", []) for v in sheet.get("violations", [])]
    errors = _errors(violations)
    return GateReport(
        gate="erc",
        passed=not errors,
        counts={"errors": len(errors), "warnings": len(violations) - len(errors)},
        findings=errors,
    )


def drc_gate(report: Path) -> GateReport:
    data: dict[str, Any] = json.loads(report.read_text(encoding="utf-8"))
    groups = {
        "violations": data.get("violations", []),
        "unconnected": data.get("unconnected_items", []),
        "parity": data.get("schematic_parity", []),
    }
    errors = {name: _errors(items) for name, items in groups.items()}
    counts = {f"{name}_errors": len(items) for name, items in errors.items()}
    counts["warnings"] = sum(len(items) for items in groups.values()) - sum(counts.values())
    return GateReport(
        gate="drc",
        passed=not any(errors.values()),
        counts=counts,
        findings=[item for items in errors.values() for item in items],
    )
