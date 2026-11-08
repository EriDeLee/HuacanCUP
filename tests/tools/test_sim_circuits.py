"""M2 完成标准（PLAN §12）：5 个手写电路的仿真判定与手算一致。需要 ngspice（KiCad 自带的共享库）。

每个电路：一份 spec.json、下面注释里的手算值和推导、允许的误差，以及一个故意不合格的变体
（改一个元件值），G2 必须判 fail。手算值是从公式在测试里直接算出来的，不是抄仿真结果。

1. voltage_divider（分压，op）
   V(MID) = 12 V × 10 k / (30 k + 10 k) = 3.000 V；I(VIN) = 12 V / 40 k = 0.300 mA
2. rc_lowpass（RC 低通，ac）
   fc = 1 / (2π × 10 k × 10 n) = 1591.5 Hz；-3.000 dB 点 = fc × sqrt(10^0.3 − 1)；
   10 kHz 处增益 −10·log10(1 + (10 k / fc)^2)
3. ne555_astable（transient）
   f = 1.44 / ((R1 + 2 R2) C1) = 1.44 / (21 k × 10 µ) = 6.857 Hz
4. lm358_amp（ac、op）
   增益 = 20·log10(1 + R1 / R2) = 20.83 dB；输出直流 = 11 × 0.2 V = 2.2 V
5. overcurrent_cutoff（transient）
   切断时间 = −R2·C2·ln(1 − VREF / Vsense) + 几微秒（见下）

overcurrent_cutoff 的 A3（持续过载时输出保持切断）是个如实记录的发现：电路是「打嗝式」的，
每个导通脉冲都回到 10 A，所以 A3 不通过。验收标准没有为了让它通过而改。
"""

from __future__ import annotations

import copy
import math
from pathlib import Path
from typing import Any

import pytest

from eda_agent.config import REPO_ROOT, load_config
from eda_agent.gates.report import Finding, GateReport
from eda_agent.gates.sim import SimEnv, sim_gate
from eda_agent.ir.models import Design, load_design
from eda_agent.ir.spec import Spec, load_spec

pytestmark = pytest.mark.tools

CIRCUITS = REPO_ROOT / "tests" / "circuits"
EXAMPLES = REPO_ROOT / "examples"


def run(folder: Path, tmp: Path, *, edit: Any = None, only: tuple[str, ...] = ()) -> GateReport:
    design_doc = load_design(folder / "design.json").model_dump(by_alias=True, mode="json")
    if edit is not None:
        edit(design_doc)
    spec = load_spec(folder / "spec.json")
    if only:
        spec = Spec.model_validate(
            spec.model_dump(by_alias=True)
            | {
                "acceptance": [
                    a for a in spec.model_dump(by_alias=True)["acceptance"] if a["id"] in only
                ]
            }
        )
    env = SimEnv(
        tools=load_config().tools,
        sim_dir=tmp / "branches" / "0" / "sim",
        run_dir=tmp,
    )
    return sim_gate(Design.model_validate(design_doc), spec, env)


def finding(report: GateReport, ident: str) -> Finding:
    return next(f for f in report.findings if f.id == ident)


def set_value(ref: str, value: str) -> Any:
    def edit(doc: dict[str, Any]) -> None:
        next(p for p in doc["parts"] if p["ref"] == ref)["value"] = value

    return edit


@pytest.fixture(scope="module")
def tmp(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("sim")


# ---------------------------------------------------------------- 1. 分压（op）


def test_voltage_divider_matches_the_hand_calculation(tmp: Path) -> None:
    report = run(CIRCUITS / "voltage_divider", tmp / "div")
    assert report.passed and report.counts["pass"] == 2
    assert finding(report, "A1").measured == pytest.approx(12 * 10e3 / (30e3 + 10e3), abs=1e-3)
    assert finding(report, "A2").measured == pytest.approx(12 / (30e3 + 10e3), rel=1e-3)


def test_voltage_divider_with_the_wrong_resistor_fails(tmp: Path) -> None:
    report = run(CIRCUITS / "voltage_divider", tmp / "div-bad", edit=set_value("R2", "12k"))
    a1 = finding(report, "A1")
    assert a1.status == "fail" and not report.passed
    assert a1.measured == pytest.approx(12 * 12e3 / (30e3 + 12e3), abs=1e-3)  # 3.43 V
    assert a1.margin is not None and a1.margin < 0


# ---------------------------------------------------------------- 2. RC 低通（ac）


def test_rc_lowpass_matches_the_hand_calculation(tmp: Path) -> None:
    fc = 1 / (2 * math.pi * 10e3 * 10e-9)  # 1591.5 Hz
    report = run(CIRCUITS / "rc_lowpass", tmp / "rc")
    assert report.passed
    # -3.000 dB 点：|H|^2 = 1 / (1 + (f/fc)^2) = 10^(-0.3)；在两个扫描点之间插值，误差 0.1 % 以内
    expected_f = fc * math.sqrt(10**0.3 - 1)
    assert finding(report, "A1").measured == pytest.approx(expected_f, rel=1e-3)
    gain_10k = -10 * math.log10(1 + (10e3 / fc) ** 2)
    assert finding(report, "A2").measured == pytest.approx(gain_10k, abs=0.01)


def test_rc_lowpass_with_the_wrong_capacitor_fails(tmp: Path) -> None:
    report = run(CIRCUITS / "rc_lowpass", tmp / "rc-bad", edit=set_value("C1", "22nF"))
    assert finding(report, "A1").status == "fail" and finding(report, "A2").status == "fail"
    fc = 1 / (2 * math.pi * 10e3 * 22e-9)  # 723.4 Hz
    assert finding(report, "A1").measured == pytest.approx(fc * math.sqrt(10**0.3 - 1), rel=1e-3)


# ---------------------------------------------------------------- 3. NE555 无稳态（transient）


def test_ne555_frequency_matches_the_hand_calculation_within_ten_percent(tmp: Path) -> None:
    f_formula = 1.44 / ((1e3 + 2 * 10e3) * 10e-6)  # 6.857 Hz
    report = run(EXAMPLES / "ne555_astable", tmp / "555")
    assert report.passed
    measured = finding(report, "A1").measured
    assert measured == pytest.approx(f_formula, rel=0.10)
    # 模型的阈值比理想的 1/3、2/3 偏，仿真比公式快；方向和数量级要对（见 models/ua555.lib 的说明）
    assert measured is not None and measured > f_formula


def test_ne555_with_a_bigger_timing_capacitor_fails(tmp: Path) -> None:
    report = run(EXAMPLES / "ne555_astable", tmp / "555-bad", edit=set_value("C1", "22uF"))
    a1 = finding(report, "A1")
    assert a1.status == "fail" and a1.measured is not None
    assert a1.measured == pytest.approx(1.44 / (21e3 * 22e-6), rel=0.15)  # 3.1 Hz


# ---------------------------------------------------------------- 4. LM358 同相放大（ac、op）


def test_lm358_gain_matches_the_hand_calculation(tmp: Path) -> None:
    report = run(EXAMPLES / "lm358_amp", tmp / "lm358")
    assert report.passed
    gain_db = 20 * math.log10(1 + 10e3 / 1e3)  # 20.828 dB
    assert finding(report, "A1").measured == pytest.approx(gain_db, abs=0.1)
    assert finding(report, "A2").measured == pytest.approx(11 * 0.2, abs=0.05)  # 失调约 22 mV


def test_lm358_with_the_wrong_gain_resistor_fails(tmp: Path) -> None:
    report = run(EXAMPLES / "lm358_amp", tmp / "lm358-bad", edit=set_value("R2", "2k"))
    assert finding(report, "A1").status == "fail" and finding(report, "A2").status == "fail"
    assert finding(report, "A1").measured == pytest.approx(20 * math.log10(1 + 10e3 / 2e3), abs=0.1)


# ---------------------------------------------------------------- 5. 过流切断（transient）


def test_overcurrent_cutoff_time_matches_the_hand_calculation(tmp: Path) -> None:
    """VREF = 5 V × 1 k / 101 k = 49.5 mV；10 A × 10 mΩ = 100 mV；
    R2·C2 = 1 k × 100 n = 100 µs 的滤波，到基准的时间 = −τ·ln(1 − 49.5 / 100) = 68 µs，
    再加比较器响应和栅极放电几微秒，约 73 µs。允许 ±25 µs（比较器输入失调最多 5 mV，约 ±10 µs）。"""
    vref = 5 * 1e3 / (100e3 + 1e3)
    t_filter = -1e3 * 100e-9 * math.log(1 - vref / (10 * 0.01))
    report = run(EXAMPLES / "overcurrent_cutoff", tmp / "oc", only=("A1", "A2"))
    assert report.passed
    a1 = finding(report, "A1")
    assert a1.measured == pytest.approx(t_filter + 5e-6, abs=25e-6)
    assert a1.measured is not None and a1.measured > t_filter, (
        "比纯滤波的时间长：还有比较器和 MOSFET 的延迟"
    )
    assert finding(report, "A2").measured == pytest.approx(2.0, abs=0.01)
    # 额外测量：Q1 的电流峰值是 10 A；断开以后漏极被负载拉到电池电压附近
    # （12 V 减去负载退出的 1 V）。
    # 电压不设上限：理想恒流负载接到刚断开的开关上，漏极的边沿是每纳秒十几伏，
    # 积分一振铃就冲过电池电压，冲多少跟 ngspice 的版本和收敛重试走到哪一档有关：
    # KiCad 自带的 ngspice（第 4 档收敛）量到 13.8 V，CI 上 Ubuntu 的 libngspice 量到 55.4 V
    # （review-queue D39）。A1、A2 的判定和电流峰值两边一致。
    q1 = report.device_stress["Q1"]
    assert q1.i_peak == pytest.approx(10.0, rel=0.02)
    assert q1.v_peak > 10.0


def test_overcurrent_cutoff_sustained_overload_is_an_honest_failure(tmp: Path) -> None:
    """A3：切断以后（3 ms 起）负载电流最大值不超过 5 A。电路是「打嗝式」的：关断后过流消失，
    比较器放开，栅极重新充电，负载（恒流 10 A）又拉回 10 A，几十微秒一个周期。如实记为 fail。"""
    report = run(EXAMPLES / "overcurrent_cutoff", tmp / "oc3", only=("A3",))
    a3 = finding(report, "A3")
    assert a3.status == "fail" and not report.passed
    assert a3.measured == pytest.approx(10.0, rel=0.02)
    assert a3.margin is not None and a3.margin < -0.9


def test_overcurrent_cutoff_with_a_wrong_reference_divider_never_cuts(tmp: Path) -> None:
    """R4 写成 10 k：VREF = 5 V × 1 k / 11 k = 0.45 V，需要 45 A 才跳，10 A 的过载根本切不断。"""
    report = run(
        EXAMPLES / "overcurrent_cutoff",
        tmp / "oc-bad",
        edit=set_value("R4", "10k"),
        only=("A1",),
    )
    a1 = finding(report, "A1")
    assert a1.status == "fail" and a1.measured is None
    assert "没有满足" in a1.message
    assert report.counts["fail"] == 1 and report.counts["sim_error"] == 0


def test_the_copied_design_is_not_modified_by_the_variants() -> None:
    doc = load_design(CIRCUITS / "voltage_divider" / "design.json").model_dump(by_alias=True)
    before = copy.deepcopy(doc)
    set_value("R2", "12k")(doc)
    assert (
        doc != before
        and load_design(CIRCUITS / "voltage_divider" / "design.json").parts[2].value == "10k"
    )
