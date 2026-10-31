from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from eda_agent.ir.models import Design, load_design


def _led(led_design_path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(led_design_path.read_text(encoding="utf-8"))
    return data


def test_led_example_loads(led_design_path: Path) -> None:
    design = load_design(led_design_path)
    assert [p.ref for p in design.parts] == ["J1", "R1", "D1"]
    assert design.board.size_mm == (30, 20)


def test_duplicate_reference_is_rejected(led_design_path: Path) -> None:
    data = _led(led_design_path)
    data["parts"][1]["ref"] = "J1"
    with pytest.raises(ValidationError, match="重复"):
        Design.model_validate(data)


def test_net_node_on_unknown_part_is_rejected(led_design_path: Path) -> None:
    data = _led(led_design_path)
    data["nets"][0]["nodes"].append(["U9", "1"])
    with pytest.raises(ValidationError, match="U9"):
        Design.model_validate(data)


def test_pin_in_two_nets_is_rejected(led_design_path: Path) -> None:
    data = _led(led_design_path)
    data["nets"][1]["nodes"].append(["J1", "1"])
    with pytest.raises(ValidationError, match=r"J1\.1"):
        Design.model_validate(data)


def test_unknown_field_is_rejected(led_design_path: Path) -> None:
    data = _led(led_design_path)
    data["parts"][0]["colour"] = "red"
    with pytest.raises(ValidationError):
        Design.model_validate(data)
