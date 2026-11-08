from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from pydantic import ValidationError

from eda_agent.config import REPO_ROOT
from eda_agent.ir.models import Design, load_design
from eda_agent.schemas import GENERATED, SCHEMA_DIR, render


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


def test_full_part_fields_of_plan_5_2_are_accepted(led_design_path: Path) -> None:
    data = _led(led_design_path)
    data["parts"][2] |= {
        "mpn": "LTL-307EE",
        "lcsc": "C2286",
        "source": "inventory",
        "spice": {
            "model": "LED_RED_GENERIC",
            "lib": "models/led_red_generic.lib",
            "pin_map": {"1": "K", "2": "A"},
        },
        "ratings": {"if_max": 0.02},
        "ratings_source": {"doc_id": "ltl-307ee", "page": 2},
        "role": "passive",
        "anchor": "R1",
    }
    part = Design.model_validate(data).parts[2]
    assert part.spice is not None and part.spice.pin_map["2"] == "A"
    assert part.ratings == {"if_max": 0.02}


def test_pins_can_be_marked_nc(led_design_path: Path) -> None:
    data = _led(led_design_path)
    data["parts"][0]["nc_pins"] = ["3", "4"]
    assert Design.model_validate(data).parts[0].nc_pins == ["3", "4"]


def test_net_classes_must_be_defined_on_the_board(led_design_path: Path) -> None:
    data = _led(led_design_path)
    data["nets"][0]["class"] = "power"
    with pytest.raises(ValidationError, match="power"):
        Design.model_validate(data)
    data["board"]["net_classes"] = {"power": {"track_mm": 1.0, "clearance_mm": 0.3}}
    design = Design.model_validate(data)
    assert design.nets[0].net_class == "power"
    assert design.board.net_classes["power"].track_mm == 1.0


def test_via_drill_must_be_smaller_than_the_via(led_design_path: Path) -> None:
    data = _led(led_design_path)
    data["board"]["net_classes"] = {
        "power": {"track_mm": 1.0, "clearance_mm": 0.3, "via_diameter_mm": 0.6, "via_drill_mm": 0.6}
    }
    with pytest.raises(ValidationError, match="钻孔"):
        Design.model_validate(data)


@pytest.mark.parametrize(
    "change, match",
    [
        (lambda d: d["parts"][0].update(block="nowhere"), "nowhere"),
        (lambda d: d["parts"][0].update(anchor="U9"), "anchor"),
        (lambda d: d["parts"][0].update(anchor="J1"), "anchor"),
        (lambda d: d["nets"].append({"name": "VIN", "nodes": [["R1", "9"]]}), "网络名重复"),
        (lambda d: d["nets"][1].update(name="LED/A"), "name"),
        (lambda d: d["blocks"].append({"id": "led", "purpose": "again"}), "功能块 id 重复"),
        (lambda d: d["board"].update(mount_holes="M5x3"), "mount_holes"),
        (lambda d: d["board"].update(layers=3), "layers"),
        (lambda d: d["parts"][0].update(role="chip"), "role"),
        (lambda d: d["parts"][0].update(nc_pins=["3", "3"]), "nc_pins 有重复"),
        (lambda d: d["parts"][0].update(nc_pins=["1"]), r"J1\.1 标了 NC"),
    ],
)
def test_bad_references_and_values_are_rejected(
    led_design_path: Path, change: Any, match: str
) -> None:
    data = _led(led_design_path)
    change(data)
    with pytest.raises(ValidationError, match=match):
        Design.model_validate(data)


@pytest.mark.parametrize("name", sorted(GENERATED))
def test_committed_schemas_match_their_models(name: str) -> None:
    """改了 ir/models.py、ir/spec.py 或 gates/report.py 后要运行 python tasks.py schemas。"""
    assert (SCHEMA_DIR / name).read_text(encoding="utf-8") == render(name)


def test_the_expected_schemas_are_exported() -> None:
    assert set(GENERATED) == {"design.schema.json", "spec.schema.json", "gate_report.schema.json"}


def test_the_examples_satisfy_the_json_schema() -> None:
    schema = json.loads((SCHEMA_DIR / "design.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    examples = sorted((REPO_ROOT / "examples").glob("*/design.json"))
    assert examples
    for path in examples:
        errors = [e.message for e in validator.iter_errors(json.loads(path.read_text("utf-8")))]
        assert not errors, (path, errors)
        load_design(path)  # pydantic 那边的交叉引用检查也要过
