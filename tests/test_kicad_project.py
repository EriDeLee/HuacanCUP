"""网络类写进 .kicad_pro（不需要 KiCad：直接拿一份 KiCad 写的 net_settings 来改）。"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from eda_agent.emit.kicad_project import net_class_settings
from eda_agent.ir.models import Design

KICAD_DEFAULT = {
    "classes": [
        {
            "name": "Default",
            "clearance": 0.2,
            "track_width": 0.2,
            "via_diameter": 0.6,
            "via_drill": 0.3,
            "priority": 2147483647,
            "wire_width": 6,
        }
    ],
    "meta": {"version": 5},
    "netclass_assignments": {},
    "netclass_patterns": [],
}


def _design(classes: dict[str, Any], nets: list[dict[str, Any]]) -> Design:
    return Design.model_validate(
        {
            "parts": [
                {
                    "ref": "J1",
                    "symbol": "Connector_Generic:Conn_01x02",
                    "footprint": "a:b",
                    "value": "J",
                },
                {"ref": "R1", "symbol": "Device:R", "footprint": "a:b", "value": "1k"},
            ],
            "nets": nets,
            "board": {"size_mm": [20, 20], "net_classes": classes},
        }
    )


NETS = [
    {"name": "VIN", "class": "power", "nodes": [["J1", "1"], ["R1", "1"]]},
    {"name": "SIG", "nodes": [["R1", "2"], ["J1", "2"]]},
]
ACTUAL = {
    ("J1", "1"): "/power/VIN",
    ("R1", "1"): "/power/VIN",
    ("R1", "2"): "SIG",
    ("J1", "2"): "SIG",
}


def test_custom_classes_are_added_and_nets_assigned_by_their_netlist_names() -> None:
    design = _design(
        {"power": {"track_mm": 1.0, "clearance_mm": 0.3, "via_diameter_mm": 0.8}}, NETS
    )
    before = copy.deepcopy(KICAD_DEFAULT)

    settings = net_class_settings(KICAD_DEFAULT, design, ACTUAL)

    assert before == KICAD_DEFAULT, "传入的设置不能被改"
    power = next(c for c in settings["classes"] if c["name"] == "power")
    assert (
        power["track_width"] == 1.0 and power["clearance"] == 0.3 and power["via_diameter"] == 0.8
    )
    assert power["via_drill"] == 0.3, "没写的项沿用 Default"
    assert settings["netclass_patterns"] == [{"netclass": "power", "pattern": "/power/VIN"}]


def test_the_default_class_can_be_overridden() -> None:
    design = _design({"default": {"track_mm": 0.25, "clearance_mm": 0.15}}, NETS[1:])
    settings = net_class_settings(KICAD_DEFAULT, design, ACTUAL)
    default = next(c for c in settings["classes"] if c["name"] == "Default")
    assert default["track_width"] == 0.25 and default["clearance"] == 0.15
    assert settings["netclass_patterns"] == []


def test_running_twice_does_not_duplicate_classes() -> None:
    design = _design({"power": {"track_mm": 1.0, "clearance_mm": 0.3}}, NETS)
    once = net_class_settings(KICAD_DEFAULT, design, ACTUAL)
    twice = net_class_settings(once, design, ACTUAL)
    assert [c["name"] for c in twice["classes"]] == ["Default", "power"]


def test_a_net_split_in_the_netlist_is_an_error() -> None:
    design = _design({"power": {"track_mm": 1.0, "clearance_mm": 0.3}}, NETS)
    split = ACTUAL | {("R1", "1"): "Net-(R1-Pad1)"}
    with pytest.raises(ValueError, match="VIN"):
        net_class_settings(KICAD_DEFAULT, design, split)
