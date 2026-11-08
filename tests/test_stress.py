"""G2 的额外测量：器件的峰值电压、电流、功耗和网络的电流上界（eda_agent/sim/stress.py）。"""

from __future__ import annotations

import dataclasses

import pytest

from eda_agent.config import REPO_ROOT
from eda_agent.ir.models import load_design
from eda_agent.ir.spec import load_spec
from eda_agent.sim.stress import device_stress, net_current_peaks
from eda_agent.sim.testbench import ATTEMPTS, Bench, build_bench
from tests.helpers import fake_sim

FOLDER = REPO_ROOT / "tests" / "circuits" / "voltage_divider"


def divider_bench() -> Bench:
    design, spec = load_design(FOLDER / "design.json"), load_spec(FOLDER / "spec.json")
    return build_bench(design, spec, spec.acceptance[0], models_root=REPO_ROOT)


def op_vectors(scale: float = 1.0) -> dict[str, list[float]]:
    """12 V 经 30 k / 10 k 分压：0.3 mA。探针的电流方向是网络 → 器件。"""
    i = 0.0003 * scale
    return {
        "vin": [12.0 * scale],
        "mid": [3.0 * scale],
        "vi_r1_1#branch": [i],
        "vi_r1_2#branch": [-i],
        "vi_r2_1#branch": [i],
        "vi_r2_2#branch": [-i],
        "vx_vin#branch": [i],
    }


def test_device_peaks_come_from_pin_voltages_and_currents() -> None:
    stress = device_stress(divider_bench(), fake_sim(op_vectors()))
    assert set(stress) == {"R1", "R2"}
    r1, r2 = stress["R1"], stress["R2"]
    assert r1.v_peak == pytest.approx(12.0) and r1.i_peak == pytest.approx(0.0003)
    assert r1.p_peak == pytest.approx(12 * 0.0003 - 3 * 0.0003)  # 两端压差 9 V × 0.3 mA
    assert r2.v_peak == pytest.approx(3.0)
    assert r2.p_peak == pytest.approx(3 * 0.0003)  # 地端是 0 V


def test_network_current_bound_is_half_the_sum_of_the_terminal_currents() -> None:
    nets = net_current_peaks(divider_bench(), fake_sim(op_vectors()))
    # VIN：R1 的 1 脚 0.3 mA + 电源 0.3 mA；MID：R1 的 2 脚 + R2 的 1 脚；
    # GND：R2 的 2 脚 + 电源的负极（它也有 0.3 mA）
    assert nets == {
        "VIN": pytest.approx(0.0003),
        "MID": pytest.approx(0.0003),
        "GND": pytest.approx(0.0003),
    }


def test_the_bound_adds_up_absolute_terminal_currents_and_halves_the_sum() -> None:
    """电流不守恒的假数据，只验证算式：每个端点的电流取绝对值，加起来除以 2。"""
    b = divider_bench()
    vectors = op_vectors()
    vectors["vx_vin#branch"] = [5.0]
    vectors["vi_r1_1#branch"] = [3.0]
    vectors["vi_r1_2#branch"] = [-3.0]
    vectors["vi_r2_1#branch"] = [2.0]
    vectors["vi_r2_2#branch"] = [-2.0]
    nets = net_current_peaks(b, fake_sim(vectors))
    assert nets["VIN"] == pytest.approx((5 + 3) / 2)  # 在 VIN 这一头：电源 5 A + R1 的 3 A
    assert nets["MID"] == pytest.approx((3 + 2) / 2)


def test_negative_power_is_not_counted() -> None:
    """释放储能的器件（电容放电）瞬时功耗是负的，不当成发热。"""
    vectors = op_vectors()
    vectors["vi_r2_1#branch"] = [-0.0003]
    vectors["vi_r2_2#branch"] = [0.0003]
    assert device_stress(divider_bench(), fake_sim(vectors))["R2"].p_peak == 0.0


def test_ac_has_no_peaks() -> None:
    ac = dataclasses.replace(divider_bench(), analysis="ac")
    assert device_stress(ac, fake_sim()) == {}
    assert net_current_peaks(ac, fake_sim()) == {}


def transient(attempt: int) -> tuple[Bench, dict[str, list[float]]]:
    """头两个点是 uic 开始时电源对电容的假冲击（100 A），后面是 0.3 mA。"""
    b = dataclasses.replace(divider_bench(), analysis="transient", attempt=ATTEMPTS[attempt])
    n = 6
    vectors = {
        "time": [0.0, 1e-6, 0.001, 0.002, 0.005, 0.01],
        "vin": [12.0] * n,
        "mid": [3.0] * n,
    }
    spike = [100.0, 100.0] + [0.0003] * (n - 2)
    for probe, sign in (("vi_r1_1", 1), ("vi_r1_2", -1), ("vi_r2_1", 1), ("vi_r2_2", -1)):
        vectors[f"{probe}#branch"] = [sign * x for x in spike]
    vectors["vx_vin#branch"] = list(spike)
    return b, vectors


def test_the_start_up_spike_of_a_uic_run_is_ignored() -> None:
    b, vectors = transient(3)  # 第 4 档：加了 uic
    assert device_stress(b, fake_sim(vectors))["R1"].i_peak == pytest.approx(0.0003)
    assert net_current_peaks(b, fake_sim(vectors))["VIN"] == pytest.approx(0.0003)


def test_without_uic_every_point_counts() -> None:
    b, vectors = transient(0)
    assert device_stress(b, fake_sim(vectors))["R1"].i_peak == pytest.approx(100.0)
