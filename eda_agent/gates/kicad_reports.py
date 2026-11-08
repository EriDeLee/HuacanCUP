"""读 kicad-cli 的 ERC / DRC JSON 报告并给出判定。

只有 severity 为 error 的条目算不通过，写成 finding（id = KiCad 的违规类型，detail 里是原始条目）；
warning 只计数，不影响判定。GateReport 在 gates/report.py，G1、G2、G3 共用。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from eda_agent.gates.report import Finding, GateReport, make_report


def _errors(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in items if item.get("severity") == "error"]


def violation_finding(item: dict[str, Any], prefix: str = "") -> Finding:
    kind = str(item.get("type", "unknown"))
    return Finding(
        id=f"{prefix}{kind}",
        severity="error",
        status="fail",
        message=str(item.get("description") or kind),
        detail=item,
    )


def erc_gate(report: Path) -> GateReport:
    data: dict[str, Any] = json.loads(report.read_text(encoding="utf-8"))
    violations = [v for sheet in data.get("sheets", []) for v in sheet.get("violations", [])]
    errors = _errors(violations)
    return make_report(
        "erc",
        [violation_finding(v) for v in errors],
        {"errors": len(errors), "warnings": len(violations) - len(errors)},
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
    return make_report(
        "drc",
        [violation_finding(item) for items in errors.values() for item in items],
        counts,
    )
