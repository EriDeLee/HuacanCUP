"""spec.json 的模型（PLAN §5.1）：PLAN 里的例子能读进来，写错的地方都有明确的报错。"""

from __future__ import annotations

import copy
import json
from typing import Any

import jsonschema
import pytest
from pydantic import ValidationError

from eda_agent.ir.spec import Spec, parse_condition, spec_json_schema

PLAN_EXAMPLE: dict[str, Any] = {
    "id": "spec-20261031-001",
    "summary": "电池与ZVS之间的过流保护器",
    "interfaces": [
        {"name": "VIN", "kind": "power_in", "v_min": 10.0, "v_max": 14.8, "notes": "3S/4S 锂电"},
        {"name": "VOUT", "kind": "power_out", "i_max": 10.0},
    ],
    "acceptance": [
        {
            "id": "A1",
            "text": "负载电流超过 12A 时，在 1ms 内切断输出",
            "testbench": {
                "type": "transient",
                "stimulus": {"load": {"kind": "current_step", "from": 5, "to": 15, "at": "2ms"}},
                "sim_time": "10ms",
            },
            "measure": {
                "kind": "time_until",
                "signal": "I(VOUT)",
                "condition": "< 0.1",
                "after": "2ms",
            },
            "pass": {"op": "<=", "value": 0.001, "unit": "s"},
        }
    ],
    "constraints": {
        "board_max_mm": [60, 40],
        "layers_max": 2,
        "assembly": "hand_solder",
        "min_package": "0805",
        "prefer_inventory": True,
        "budget_cny": 80,
    },
    "assumptions": ["ZVS 装置启动峰值电流按 20A 估计（用户未提供，需确认）"],
    "frozen_sha256": None,
}


def _example() -> dict[str, Any]:
    return copy.deepcopy(PLAN_EXAMPLE)


def test_the_example_in_plan_5_1_loads() -> None:
    spec = Spec.model_validate(_example())
    assert [i.name for i in spec.interfaces] == ["VIN", "VOUT"]
    assert spec.acceptance[0].pass_.op == "<="
    assert spec.interface("VIN") is not None and spec.interface("NOPE") is None


def test_the_example_satisfies_the_json_schema() -> None:
    jsonschema.Draft202012Validator(spec_json_schema()).validate(_example())


def test_the_schema_uses_the_json_names_from_and_pass() -> None:
    text = json.dumps(spec_json_schema())
    assert '"pass"' in text and '"from"' in text
    assert "pass_" not in text and "from_" not in text


def test_nominal_voltage_is_the_midpoint_unless_given() -> None:
    spec = Spec.model_validate(_example())
    assert spec.interfaces[0].nominal_v == pytest.approx(12.4)
    data = _example()
    data["interfaces"][0]["v_nom"] = 12.0
    assert Spec.model_validate(data).interfaces[0].nominal_v == 12.0
    assert spec.interfaces[1].nominal_v is None


def _break(change: Any) -> dict[str, Any]:
    data = _example()
    change(data)
    return data


@pytest.mark.parametrize(
    "change, match",
    [
        (lambda d: d["interfaces"].append(dict(d["interfaces"][0])), "接口名重复"),
        (lambda d: d["acceptance"].append(dict(d["acceptance"][0])), "编号重复"),
        (lambda d: d["interfaces"][0].update(v_min=15.0), "v_min"),
        (lambda d: d["interfaces"][0].update(kind="power"), "kind"),
        (lambda d: d["interfaces"][1].update(i_max=-1), "i_max"),
        (lambda d: d["interfaces"][0].update(colour="red"), "colour"),
        (lambda d: d["acceptance"][0]["testbench"].pop("sim_time"), "sim_time"),
        (lambda d: d["acceptance"][0]["testbench"].update(sim_time="soon"), "sim_time"),
        (lambda d: d["acceptance"][0]["testbench"].update(type="op"), "sim_time"),
        (lambda d: d["acceptance"][0]["measure"].update(kind="gain_db_at", at="1k"), "测不了"),
        (lambda d: d["acceptance"][0]["measure"].pop("condition"), "condition"),
        (lambda d: d["acceptance"][0]["measure"].update(condition="about 0"), "condition"),
        (lambda d: d["acceptance"][0]["measure"].update(signal="VOUT"), "signal"),
        (lambda d: d["acceptance"][0]["measure"].update(at="1ms"), "不用 at"),
        (lambda d: d["acceptance"][0]["pass"].update(op="~="), "op"),
        (lambda d: d["acceptance"][0]["pass"].pop("value"), "要写 value"),
        (lambda d: d["acceptance"][0]["pass"].update(low=1, high=2), "不写 low"),
        (
            lambda d: d["acceptance"][0]["testbench"]["stimulus"].update(
                drive={"X": {"kind": "dc", "value": 1}}
            ),
            "不是接口",
        ),
        (lambda d: d["interfaces"].pop(), "power_out 接口有 0 个"),
        (lambda d: d["constraints"].update(min_package="0805mm"), "min_package"),
        (lambda d: d["constraints"].update(assembly="by_robot"), "assembly"),
        (lambda d: d["constraints"].update(layers_max=3), "layers_max"),
    ],
)
def test_bad_specs_are_rejected_with_a_reason(change: Any, match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        Spec.model_validate(_break(change))


def test_between_needs_low_below_high() -> None:
    data = _example()
    data["acceptance"][0]["pass"] = {"op": "between", "low": 1, "high": 2, "unit": "s"}
    assert Spec.model_validate(data).acceptance[0].pass_.low == 1
    data["acceptance"][0]["pass"] = {"op": "between", "low": 2, "high": 1}
    with pytest.raises(ValidationError, match="low < high"):
        Spec.model_validate(data)
    data["acceptance"][0]["pass"] = {"op": "between", "value": 1}
    with pytest.raises(ValidationError, match="low 和 high"):
        Spec.model_validate(data)


def test_a_load_between_two_nets_is_needed_when_there_is_no_single_output() -> None:
    data = _example()
    data["interfaces"].append({"name": "VOUT2", "kind": "power_out"})
    with pytest.raises(ValidationError, match="没写 between"):
        Spec.model_validate(data)
    data["acceptance"][0]["testbench"]["stimulus"]["load"]["between"] = ["VIN", "VOUT"]
    Spec.model_validate(data)


def test_each_analysis_accepts_only_measurements_it_can_make() -> None:
    op = {
        "id": "A2",
        "text": "分压点",
        "testbench": {"type": "op"},
        "measure": {"kind": "value_at", "signal": "V(MID)"},
        "pass": {"op": "between", "low": 3.2, "high": 3.4, "unit": "V"},
    }
    data = _example()
    data["acceptance"].append(op)
    Spec.model_validate(data)
    op["measure"]["at"] = "1ms"
    with pytest.raises(ValidationError, match="op 分析的 value_at 不写 at"):
        Spec.model_validate(data)
    op["measure"].update(kind="max")
    del op["measure"]["at"]
    with pytest.raises(ValidationError, match="op 分析测不了 max"):
        Spec.model_validate(data)


def test_ac_needs_a_sweep_and_an_ac_source() -> None:
    ac = {
        "id": "A3",
        "text": "增益",
        "testbench": {
            "type": "ac",
            "stimulus": {"drive": {"VIN": {"kind": "ac", "dc": 0.2, "magnitude": 1}}},
            "sweep": {"start": 10, "stop": "1MEG", "points_per_decade": 20},
        },
        "measure": {"kind": "gain_db_at", "signal": "V(VOUT)", "at": "1k"},
        "pass": {"op": "between", "low": 20, "high": 21, "unit": "dB"},
    }
    data = _example()
    data["acceptance"].append(ac)
    Spec.model_validate(data)
    del ac["testbench"]["sweep"]
    with pytest.raises(ValidationError, match="sweep"):
        Spec.model_validate(data)
    ac["testbench"]["sweep"] = {"start": 10, "stop": "1MEG"}
    ac["testbench"]["stimulus"] = {}
    with pytest.raises(ValidationError, match='kind="ac"'):
        Spec.model_validate(data)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("< 0.1", ("<", 0.1)),
        (">=4.5", (">=", 4.5)),
        ("<= 2m", ("<=", 0.002)),
        ("> -1", (">", -1.0)),
    ],
)
def test_parse_condition(text: str, expected: tuple[str, float]) -> None:
    assert parse_condition(text) == expected


@pytest.mark.parametrize("text", ["0.1", "== 1", "< ", "< abc"])
def test_parse_condition_rejects_garbage(text: str) -> None:
    with pytest.raises(ValueError):
        parse_condition(text)
