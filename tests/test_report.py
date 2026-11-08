"""GateReport 和余量的定义（PLAN §5.3）：每个定义都有单测。"""

from __future__ import annotations

import json
import math
from typing import Any

import jsonschema
import pytest

from eda_agent.gates.report import (
    DeviceStress,
    Finding,
    GateReport,
    Op,
    error_finding,
    make_report,
    margin,
    report_json_schema,
    satisfies,
    warning_finding,
)

NON_STRICT: list[Op] = ["<=", ">="]


@pytest.mark.parametrize(
    "measured, op, threshold, high, expected",
    [
        # <=：(threshold - measured) / |threshold|
        (8.0, "<=", 10.0, None, 0.2),
        (12.0, "<=", 10.0, None, -0.2),
        (10.0, "<=", 10.0, None, 0.0),
        (-6.0, "<=", -5.0, None, 0.2),
        (-4.0, "<=", -5.0, None, -0.2),
        # >=：(measured - threshold) / |threshold|
        (12.0, ">=", 10.0, None, 0.2),
        (8.0, ">=", 10.0, None, -0.2),
        (-4.0, ">=", -5.0, None, 0.2),
        # 严格不等式的余量算法和非严格的一样
        (9.0, "<", 10.0, None, 0.1),
        (11.0, ">", 10.0, None, 0.1),
        # 超标时余量可以比 -1 更小（超了阈值的一倍以上）
        (30.0, "<=", 10.0, None, -2.0),
        # between：取两端里离失败更近的那一端
        (5.0, "between", 4.5, 5.5, 0.5 / 5.5),
        (5.4, "between", 4.5, 5.5, 0.1 / 5.5),
        (4.6, "between", 4.5, 5.5, 0.1 / 4.5),
        (6.0, "between", 4.5, 5.5, -0.5 / 5.5),
        (4.0, "between", 4.5, 5.5, -0.5 / 4.5),
        (4.5, "between", 4.5, 5.5, 0.0),
        (5.5, "between", 4.5, 5.5, 0.0),
    ],
)
def test_margin_follows_the_definition(
    measured: float, op: Op, threshold: float, high: float | None, expected: float
) -> None:
    assert margin(measured, op, threshold, high) == pytest.approx(expected, rel=1e-12, abs=1e-12)


@pytest.mark.parametrize(
    "measured, op, threshold, scale, expected",
    [
        # 阈值为 0 又没给 scale：只表示方向
        (-2.0, "<=", 0.0, None, 1.0),
        (0.0, "<=", 0.0, None, 0.0),
        (2.0, "<=", 0.0, None, -1.0),
        (3.0, ">=", 0.0, None, 1.0),
        (0.0, ">=", 0.0, None, 0.0),
        (-0.001, ">=", 0.0, None, -1.0),
        # 给了 scale 就拿它当分母
        (-2.0, "<=", 0.0, 5.0, 0.4),
        (2.0, "<=", 0.0, 5.0, -0.4),
        (3.0, ">=", 0.0, 5.0, 0.6),
        # scale 只在阈值为 0 时用，阈值不为 0 时不管
        (8.0, "<=", 10.0, 1000.0, 0.2),
    ],
)
def test_margin_when_the_threshold_is_zero(
    measured: float, op: Op, threshold: float, scale: float | None, expected: float
) -> None:
    assert margin(measured, op, threshold, scale=scale) == pytest.approx(expected)


def test_margin_of_a_between_with_a_zero_end() -> None:
    # [0, 5]：下端阈值为 0。没有 scale 时下端只贡献方向（+1），上端 2.5/5 = 0.5
    assert margin(2.5, "between", 0.0, 5.0) == pytest.approx(0.5)
    assert margin(-1.0, "between", 0.0, 5.0) == -1.0
    # 给了 scale = 5：下端 0.1/5 = 0.02，比上端的 0.98 更接近失败
    assert margin(0.1, "between", 0.0, 5.0, scale=5.0) == pytest.approx(0.02)


def test_margin_refuses_numbers_that_are_not_finite() -> None:
    for bad in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError, match="有限"):
            margin(bad, "<=", 1.0)


def test_between_needs_both_ends() -> None:
    with pytest.raises(ValueError, match="threshold_high"):
        margin(1.0, "between", 0.0)
    with pytest.raises(ValueError, match="threshold_high"):
        satisfies(1.0, "between", 0.0)


@pytest.mark.parametrize("op", ["<=", "<", ">=", ">", "between"])
@pytest.mark.parametrize("measured", [-30.0, -5.0, -0.5, 0.0, 0.5, 4.5, 5.0, 5.5, 7.0, 40.0])
@pytest.mark.parametrize("threshold", [-5.0, 0.0, 4.5, 10.0])
def test_status_and_margin_sign_agree(op: Op, measured: float, threshold: float) -> None:
    """通过 ⇔ 余量 ≥ 0（严格不等式：> 0）。between 的上限用 threshold + 2。"""
    high = threshold + 2 if op == "between" else None
    ok = satisfies(measured, op, threshold, high)
    m = margin(measured, op, threshold, high)
    strict = op in ("<", ">")
    assert ok == (m > 0 if strict else m >= 0), (op, measured, threshold, m)


@pytest.mark.parametrize(
    "measured, op, threshold, high, expected",
    [
        (10.0, "<=", 10.0, None, True),
        (10.0, "<", 10.0, None, False),
        (10.0, ">=", 10.0, None, True),
        (10.0, ">", 10.0, None, False),
        (4.5, "between", 4.5, 5.5, True),
        (5.5, "between", 4.5, 5.5, True),
        (5.6, "between", 4.5, 5.5, False),
        (4.4, "between", 4.5, 5.5, False),
    ],
)
def test_satisfies(
    measured: float, op: Op, threshold: float, high: float | None, expected: bool
) -> None:
    assert satisfies(measured, op, threshold, high) is expected


def test_make_report_passes_only_without_error_findings() -> None:
    ok = Finding(id="A1", severity="info", status="pass", message="好", measured=1.0)
    assert make_report("sim", [ok]).passed
    assert make_report("sim", [ok, warning_finding("w", "只是警告")]).passed
    bad = make_report("sim", [ok, error_finding("A2", "超标")])
    assert not bad.passed
    assert bad.findings[1].status == "fail"


def test_report_round_trips_through_json_and_matches_the_schema() -> None:
    report = make_report(
        "sim",
        [
            Finding(
                id="A1",
                severity="info",
                status="pass",
                message="切断时间 0.4 ms",
                measured=0.0004,
                threshold=0.001,
                op="<=",
                unit="s",
                margin=0.6,
                artifacts=["sim/A1.cir"],
            ),
            error_finding("A2", "没有模型", detail={"refs": ["U1"]}),
        ],
        {"pass": 1, "fail": 0, "sim_error": 1},
        device_stress={"R1": DeviceStress(v_peak=5.0, i_peak=0.01, p_peak=0.05)},
        net_current_peak={"VIN": 0.01},
    )
    data: dict[str, Any] = json.loads(json.dumps(report.model_dump(mode="json")))
    jsonschema.Draft202012Validator(report_json_schema()).validate(data)
    assert GateReport.model_validate(data) == report


def test_schema_rejects_a_finding_without_a_status() -> None:
    data = make_report("g1", [error_finding("x", "m")]).model_dump(mode="json")
    del data["findings"][0]["status"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(report_json_schema()).validate(data)
