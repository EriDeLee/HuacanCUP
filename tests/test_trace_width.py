"""IPC-2221 线宽（eda_agent/pcb/trace_width.py）：用公开计算器给出的数值核对。"""

from __future__ import annotations

import pytest

from eda_agent.pcb.trace_width import (
    UM_PER_MIL,
    layer_kind,
    max_current_a,
    min_width_mm,
)

# 公开的 IPC-2221 计算器数值，1 oz 铜、温升 10 °C：
#   NextPCB「PCB Trace Width Calculator」指南的对照表（2026-07，宽度单位 mil，保留一位小数）
#   ProtoFlow 计算器页：1 A → 外层 0.3 mm（11.8 mil）、内层 0.781 mm（30.8 mil）
# 公式和 Advanced Circuits、Digi-Key、Cirexx 计算器页上写的一样（k = 0.048 / 0.024，b = 0.44，
# c = 0.725，1 oz = 1.378 mil）。
EXTERNAL_MIL = {0.5: 4.5, 1.0: 11.8, 2.0: 30.8, 3.0: 53.8, 5.0: 108.9, 7.0: 173.2}
INTERNAL_MIL = {0.5: 11.8, 1.0: 30.8, 2.0: 80.0, 3.0: 140.0, 5.0: 283.2, 7.0: 450.5}


def _mil(mm: float) -> float:
    return mm * 1000 / UM_PER_MIL


@pytest.mark.parametrize("amps, mil", sorted(EXTERNAL_MIL.items()))
def test_external_layer_matches_published_calculators(amps: float, mil: float) -> None:
    assert _mil(min_width_mm(amps, layer="external")) == pytest.approx(mil, abs=0.06)


@pytest.mark.parametrize("amps, mil", sorted(INTERNAL_MIL.items()))
def test_internal_layer_matches_published_calculators(amps: float, mil: float) -> None:
    assert _mil(min_width_mm(amps, layer="internal")) == pytest.approx(mil, abs=0.06)


def test_one_amp_in_millimetres() -> None:
    assert min_width_mm(1.0) == pytest.approx(0.30, abs=0.005)
    assert min_width_mm(1.0, layer="internal") == pytest.approx(0.781, abs=0.005)


def test_defaults_are_one_ounce_and_ten_degrees() -> None:
    assert min_width_mm(2.0) == min_width_mm(2.0, copper_um=35.0, temp_rise_c=10.0)


def test_width_is_inversely_proportional_to_copper_thickness() -> None:
    one_oz = min_width_mm(3.0, copper_um=35.0)
    assert min_width_mm(3.0, copper_um=70.0) == pytest.approx(one_oz / 2)
    assert min_width_mm(3.0, copper_um=17.5) == pytest.approx(one_oz * 2)


def test_a_bigger_temperature_rise_allows_a_narrower_trace() -> None:
    # 面积 ∝ ΔT^(-0.44/0.725)
    ratio = min_width_mm(2.0, temp_rise_c=20.0) / min_width_mm(2.0, temp_rise_c=10.0)
    assert ratio == pytest.approx(2 ** (-0.44 / 0.725))
    assert min_width_mm(2.0, temp_rise_c=40.0) < min_width_mm(2.0, temp_rise_c=20.0)


def test_internal_layer_at_i_needs_what_external_needs_at_2i() -> None:
    """内层的 k 是外层的一半，所以内层 I 安培的宽度 = 外层 2I 安培的宽度。"""
    for amps in (0.3, 1.0, 2.5):
        assert min_width_mm(amps, layer="internal") == pytest.approx(
            min_width_mm(2 * amps, layer="external")
        )


def test_width_grows_with_current_but_less_than_linearly_at_first_then_faster() -> None:
    widths = [min_width_mm(a) for a in (1.0, 2.0, 4.0, 8.0)]
    assert widths == sorted(widths)
    # 指数 1/0.725 ≈ 1.38：电流翻倍，宽度涨 2^1.38 ≈ 2.6 倍
    assert widths[1] / widths[0] == pytest.approx(2 ** (1 / 0.725))


def test_zero_current_needs_no_copper() -> None:
    assert min_width_mm(0.0) == 0.0


@pytest.mark.parametrize("amps", [0.2, 1.0, 6.0])
@pytest.mark.parametrize("layer", ["external", "internal"])
@pytest.mark.parametrize("copper", [17.5, 35.0, 70.0])
def test_max_current_is_the_inverse(amps: float, layer: str, copper: float) -> None:
    width = min_width_mm(amps, copper_um=copper, temp_rise_c=15.0, layer=layer)  # type: ignore[arg-type]
    back = max_current_a(width, copper_um=copper, temp_rise_c=15.0, layer=layer)  # type: ignore[arg-type]
    assert back == pytest.approx(amps)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"current_a": -1.0},
        {"current_a": 1.0, "copper_um": 0.0},
        {"current_a": 1.0, "temp_rise_c": 0},
    ],
)
def test_nonsense_inputs_are_errors(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        min_width_mm(**kwargs)
    with pytest.raises(ValueError):
        max_current_a(-0.1)


def test_layer_kind_from_kicad_layer_names() -> None:
    assert layer_kind("F.Cu") == "external"
    assert layer_kind("B.Cu") == "external"
    assert layer_kind("In1.Cu") == "internal"
    assert layer_kind("In2.Cu") == "internal"
