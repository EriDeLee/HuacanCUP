from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from eda_agent.gates.kicad_reports import drc_gate, erc_gate


def _write(tmp_path: Path, data: dict[str, Any]) -> Path:
    path = tmp_path / "report.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_erc_passes_with_only_warnings(tmp_path: Path) -> None:
    report = _write(tmp_path, {"sheets": [{"violations": [{"severity": "warning"}]}]})
    gate = erc_gate(report)
    assert gate.passed
    assert gate.counts == {"errors": 0, "warnings": 1}


def test_erc_fails_on_error(tmp_path: Path) -> None:
    violation = {"severity": "error", "type": "power_pin_not_driven"}
    gate = erc_gate(_write(tmp_path, {"sheets": [{"violations": [violation]}]}))
    assert not gate.passed
    assert gate.findings == [violation]


def test_drc_passes_with_only_warnings(tmp_path: Path) -> None:
    data = {
        "violations": [{"severity": "warning"}],
        "unconnected_items": [],
        "schematic_parity": [{"severity": "warning"}],
    }
    gate = drc_gate(_write(tmp_path, data))
    assert gate.passed
    assert gate.counts["warnings"] == 2


def test_drc_fails_on_unconnected_item(tmp_path: Path) -> None:
    data = {"violations": [], "unconnected_items": [{"severity": "error"}], "schematic_parity": []}
    gate = drc_gate(_write(tmp_path, data))
    assert not gate.passed
    assert gate.counts["unconnected_errors"] == 1


def test_drc_fails_on_schematic_parity_error(tmp_path: Path) -> None:
    data = {"violations": [], "unconnected_items": [], "schematic_parity": [{"severity": "error"}]}
    assert not drc_gate(_write(tmp_path, data)).passed
