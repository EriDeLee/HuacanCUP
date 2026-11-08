"""把 ngspice 的结果解释成一个数（eda_agent/sim/measure.py）和 .meas 输出的解析。不需要 ngspice。"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from eda_agent.config import REPO_ROOT
from eda_agent.ir.models import load_design
from eda_agent.ir.spec import Spec, load_spec
from eda_agent.sim.measure import evaluate
from eda_agent.sim.testbench import ATTEMPTS, Bench, build_bench
from eda_agent.tools.ngspice import parse_measurements
from tests.helpers import fake_sim

CIRCUITS = REPO_ROOT / "tests" / "circuits"
EXAMPLES = REPO_ROOT / "examples"


def bench_of(folder: Path, acceptance: str, patch: dict[str, Any] | None = None) -> Bench:
    design = load_design(folder / "design.json")
    spec_doc = load_spec(folder / "spec.json").model_dump(by_alias=True)
    acc = next(a for a in spec_doc["acceptance"] if a["id"] == acceptance)
    for key, value in (patch or {}).items():
        acc["measure"][key] = value
    spec = Spec.model_validate(spec_doc)
    chosen = next(a for a in spec.acceptance if a.id == acceptance)
    return build_bench(design, spec, chosen, models_root=REPO_ROOT, attempt=ATTEMPTS[0])


# ---------------------------------------------------------------- .meas 输出的解析


def test_meas_results_are_read_from_the_ngspice_output() -> None:
    lines = [
        "stdout Doing analysis at TEMP = 27.000000 and TNOM = 27.000000",
        "stdout Measurements for Transient Analysis",
        "stdout vmax                =  4.99938e+00 at=  1.00000e-02",
        "stderr out of interval",
        "stderr .meas tran never when v(out)=10 rise=1 failed!",
        "stdout vavg                =  4.77058e+00 from=  2.00000e-03 to=  1.00000e-02",
        "stdout binary raw file x.raw",
    ]
    assert parse_measurements(lines) == {"vmax": 4.99938, "never": None, "vavg": 4.77058}


def test_meas_results_before_the_header_are_not_measurements() -> None:
    assert parse_measurements(["stdout something = 3", "stdout Note: x"]) == {}


def test_names_are_case_insensitive() -> None:
    lines = ["stdout Measurements for AC Analysis", "stdout M_A1_OUT   =  2.0e+01"]
    assert parse_measurements(lines) == {"m_a1_out": 20.0}


# ---------------------------------------------------------------- op


def test_op_reads_the_operating_point_vector() -> None:
    b = bench_of(CIRCUITS / "voltage_divider", "A1")
    m = evaluate(b, fake_sim({"mid": [3.0], "vin": [12.0]}))
    assert (m.status, m.value) == ("ok", 3.0)


def test_op_missing_vector_or_nan_is_an_error() -> None:
    b = bench_of(CIRCUITS / "voltage_divider", "A1")
    assert evaluate(b, fake_sim({"vin": [12.0]})).status == "error"
    assert evaluate(b, fake_sim({"mid": [math.nan]})).status == "error"


def test_op_current_comes_from_the_interface_probe() -> None:
    b = bench_of(CIRCUITS / "voltage_divider", "A2")
    m = evaluate(b, fake_sim({"vx_vin#branch": [0.0003]}))
    assert (m.status, m.value) == ("ok", 0.0003)


# ---------------------------------------------------------------- .meas 直接给出的


def overcurrent(acceptance: str = "A1", **patch: Any) -> Bench:
    return bench_of(EXAMPLES / "overcurrent_cutoff", acceptance, patch)


def test_a_meas_value_is_the_measurement() -> None:
    m = evaluate(overcurrent("A2"), fake_sim(m_a2=1.98))
    assert (m.status, m.value) == ("ok", 1.98)


def test_a_meas_that_never_ran_is_an_error() -> None:
    m = evaluate(overcurrent("A2"), fake_sim(m_a9=1.98))
    assert m.status == "error" and "m_a2" in m.message


def test_a_non_finite_meas_value_is_an_error() -> None:
    assert evaluate(overcurrent("A2"), fake_sim(m_a2=math.inf)).status == "error"
    assert evaluate(overcurrent("A2"), fake_sim(m_a2=math.nan)).status == "error"


def test_a_failed_meas_that_is_not_about_a_condition_is_an_error() -> None:
    m = evaluate(overcurrent("A2"), fake_sim(m_a2=None))
    assert m.status == "error" and "失败" in m.message


# ---------------------------------------------------------------- time_until


def test_time_until_is_the_meas_interval() -> None:
    m = evaluate(overcurrent("A1"), fake_sim(m_a1=7.2e-5))
    assert (m.status, m.value) == ("ok", 7.2e-5)


def _time_vectors(current: list[float]) -> dict[str, list[float]]:
    return {
        "time": [0.0, 0.001, 0.002, 0.003, 0.004, 0.005, 0.006][: len(current)],
        "vl_load#branch": current,
    }


def test_time_until_when_the_condition_already_holds_at_after_is_zero() -> None:
    """.meas 在没有穿越时失败；after 那一刻条件已经成立，间隔就是 0。"""
    sim = fake_sim(_time_vectors([0.1] * 7), m_a1=None)
    m = evaluate(overcurrent("A1"), sim)
    assert (m.status, m.value) == ("ok", 0.0)
    assert "已经成立" in m.message


def test_time_until_when_the_condition_never_holds_is_unmet_not_an_error() -> None:
    """负载电流一直是 10 A，从没降到 0.5 A 以下：电路没切断，这是 fail，不是 sim_error。"""
    sim = fake_sim(_time_vectors([10.0] * 7), m_a1=None)
    m = evaluate(overcurrent("A1"), sim)
    assert m.status == "unmet" and m.value is None
    assert "没有满足" in m.message


def test_time_until_with_after_beyond_the_simulation_is_an_error() -> None:
    sim = fake_sim(_time_vectors([10.0] * 2), m_a1=None)  # 只仿真到 1 ms，after 是 2 ms
    assert evaluate(overcurrent("A1"), sim).status == "error"


# ---------------------------------------------------------------- frequency


def ne555(**patch: Any) -> Bench:
    return bench_of(EXAMPLES / "ne555_astable", "A1", patch)


def test_frequency_is_cycles_over_the_measured_span() -> None:
    m = evaluate(ne555(), fake_sim(m_a1=0.4))  # 3 个周期共 0.4 s
    assert m.status == "ok" and m.value == pytest.approx(7.5)


def test_frequency_with_too_few_edges_is_unmet() -> None:
    m = evaluate(ne555(), fake_sim(m_a1=None))
    assert m.status == "unmet" and "没有振荡" in m.message


def test_frequency_span_must_be_positive() -> None:
    assert evaluate(ne555(), fake_sim(m_a1=0.0)).status == "error"


# ---------------------------------------------------------------- 交流增益


def rc(acceptance: str) -> Bench:
    return bench_of(CIRCUITS / "rc_lowpass", acceptance)


def test_gain_is_the_output_minus_the_reference_in_db() -> None:
    m = evaluate(rc("A2"), fake_sim(m_a2_out=-26.07, m_a2_ref=-10.0))
    assert m.status == "ok" and m.value == pytest.approx(-16.07)


def lowpass(fc: float = 1000.0, points: int = 301) -> dict[str, tuple[list[float], list[float]]]:
    f = np.logspace(1, 5, points)
    h = 1 / (1 + 1j * f / fc)
    zero = [0.0] * points
    return {
        "frequency": (f.tolist(), zero),
        "out": (h.real.tolist(), h.imag.tolist()),
        "in": ([1.0] * points, zero),
    }


def test_freq_at_db_finds_the_crossing_between_sweep_points() -> None:
    # 一阶低通 fc = 1 kHz：-3.000 dB 在 fc × sqrt(10^0.3 - 1) = 997.63 Hz
    m = evaluate(rc("A1"), fake_sim(complex_vectors=lowpass()))
    assert m.status == "ok" and m.value == pytest.approx(997.63, rel=2e-3)


def test_freq_at_db_divides_by_the_reference_signal() -> None:
    cv = lowpass()
    f = cv["frequency"][0]
    # 输入的幅值是 2：增益是输出除以它；-3 dB 点仍在 997.6 Hz
    cv["in"] = ([2.0] * len(f), [0.0] * len(f))
    cv["out"] = ([2 * x for x in cv["out"][0]], [2 * x for x in cv["out"][1]])
    m = evaluate(rc("A1"), fake_sim(complex_vectors=cv))
    assert m.value == pytest.approx(997.63, rel=2e-3)


def test_freq_at_db_relative_to_a_reference_frequency() -> None:
    cv = lowpass(fc=1000.0)
    # 相对 10 Hz 处的增益（-0.0004 dB）降 3 dB，几乎还是 997.6 Hz
    b = bench_of(CIRCUITS / "rc_lowpass", "A1", {"ref_at": "10", "db": -3.0})
    m = evaluate(b, fake_sim(complex_vectors=cv))
    assert m.status == "ok" and m.value == pytest.approx(997.63, rel=3e-3)
    # 通带本来就有 +20 dB 增益：相对 10 Hz 降 3 dB 的点还是同一个 997.6 Hz，
    # 绝对 -3 dB 的点则在很远的地方
    boosted = {k: (list(v[0]), list(v[1])) for k, v in cv.items()}
    boosted["out"] = ([10 * x for x in cv["out"][0]], [10 * x for x in cv["out"][1]])
    assert evaluate(b, fake_sim(complex_vectors=boosted)).value == pytest.approx(997.63, rel=3e-3)
    absolute = evaluate(rc("A1"), fake_sim(complex_vectors=boosted))
    assert absolute.status == "ok" and absolute.value > 3000, "绝对 -3 dB 是在通带之外很远的地方"


def test_freq_at_db_never_reached_is_unmet() -> None:
    cv = lowpass(fc=1e9)  # 扫描范围内增益一直是 0 dB
    m = evaluate(rc("A1"), fake_sim(complex_vectors=cv))
    assert m.status == "unmet" and "没有到过" in m.message


def test_freq_at_db_with_ref_at_outside_the_sweep_is_an_error() -> None:
    b = bench_of(CIRCUITS / "rc_lowpass", "A1", {"ref_at": "1MEG", "db": -3.0})
    assert evaluate(b, fake_sim(complex_vectors=lowpass())).status == "error"


def test_freq_at_db_missing_vectors_is_an_error() -> None:
    assert evaluate(rc("A1"), fake_sim()).status == "error"
