"""线宽：按 IPC-2221 由电流算最小线宽（PLAN §7 G3、ADR-0018）。

    I = k · ΔT^0.44 · A^0.725         I：电流（A），ΔT：温升（°C），A：铜的截面积（mil²）
    k = 0.048（外层）、0.024（内层）
    线宽 = A / 铜厚

和公开的 IPC-2221 计算器（Advanced Circuits、Digi-Key、Cirexx、NextPCB 等）是同一个公式：
铜厚 1 oz/ft² = 1.378 mil = 35 µm。1 A、10 °C 温升、1 oz：外层 11.8 mil（0.30 mm），
内层 30.8 mil（0.78 mm）。

IPC-2221 的原始曲线只覆盖 35 A 以内、线宽 400 mil 以内、温升 10–100 °C、铜厚 0.5–3 oz；
超出范围公式照样外推，不报错，用的时候自己留意。内层 k 取外层的一半，所以同样的电流内层要更宽。
"""

from __future__ import annotations

from typing import Literal

Layer = Literal["external", "internal"]

K = {"external": 0.048, "internal": 0.024}
TEMP_EXPONENT = 0.44
AREA_EXPONENT = 0.725
UM_PER_MIL = 25.4
DEFAULT_COPPER_UM = 35.0  # 1 oz/ft²
DEFAULT_TEMP_RISE_C = 10.0


def layer_kind(layer_name: str) -> Layer:
    """KiCad 的层名 → 外层（F.Cu、B.Cu）还是内层（In1.Cu……）。"""
    return "internal" if layer_name.startswith("In") else "external"


def _check(copper_um: float, temp_rise_c: float) -> None:
    if copper_um <= 0:
        raise ValueError(f"铜厚必须为正：{copper_um} µm")
    if temp_rise_c <= 0:
        raise ValueError(f"温升必须为正：{temp_rise_c} °C")


def min_width_mm(
    current_a: float,
    *,
    copper_um: float = DEFAULT_COPPER_UM,
    temp_rise_c: float = DEFAULT_TEMP_RISE_C,
    layer: Layer = "external",
) -> float:
    """载 current_a 安培、温升不超过 temp_rise_c 度所需的最小线宽（mm）。电流为 0 返回 0。"""
    _check(copper_um, temp_rise_c)
    if current_a < 0:
        raise ValueError(f"电流不能是负数：{current_a} A")
    if current_a == 0:
        return 0.0
    area_mil2 = float((current_a / (K[layer] * temp_rise_c**TEMP_EXPONENT)) ** (1 / AREA_EXPONENT))
    thickness_mil = copper_um / UM_PER_MIL
    return area_mil2 / thickness_mil * UM_PER_MIL / 1000


def max_current_a(
    width_mm: float,
    *,
    copper_um: float = DEFAULT_COPPER_UM,
    temp_rise_c: float = DEFAULT_TEMP_RISE_C,
    layer: Layer = "external",
) -> float:
    """线宽 width_mm 的走线，温升不超过 temp_rise_c 度时能载多少安培（min_width_mm 的反函数）。"""
    _check(copper_um, temp_rise_c)
    if width_mm < 0:
        raise ValueError(f"线宽不能是负数：{width_mm} mm")
    width_mil = width_mm * 1000 / UM_PER_MIL
    area_mil2 = width_mil * copper_um / UM_PER_MIL
    return float(K[layer] * temp_rise_c**TEMP_EXPONENT * area_mil2**AREA_EXPONENT)
