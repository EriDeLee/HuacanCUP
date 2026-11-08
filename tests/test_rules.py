"""G1 的自写电气规则：每条规则至少一个「应报错」和一个「不应报错」的用例（AGENTS.md）。

符号库和封装库见 tests/helpers.py，不需要装 KiCad。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from eda_agent.gates.report import DeviceStress, Finding, make_report
from eda_agent.gates.rules import RULES
from eda_agent.gates.rules.assembly import (
    bottom_pad_package,
    chip_size_area,
    package_assemblable,
)
from eda_agent.gates.rules.context import RuleContext
from eda_agent.gates.rules.dangling import dangling_pins
from eda_agent.gates.rules.decoupling import missing_decoupling
from eda_agent.gates.rules.drivers import output_conflicts
from eda_agent.gates.rules.power_pins import power_pin_nets
from eda_agent.gates.rules.ratings import rating_exceeded
from eda_agent.ir.models import Design
from eda_agent.ir.spec import Spec
from tests.helpers import design, net, part, symbols_for, write_libs


def ctx(
    tmp_path: Path,
    d: Design,
    spec: Spec | None = None,
    sim: Any = None,
    derating: float = 0.8,
) -> RuleContext:
    lib_dirs, _ = write_libs(tmp_path)
    return RuleContext(
        design=d, spec=spec, symbols=symbols_for(d, lib_dirs), derating=derating, sim=sim
    )


def where(findings: list[Finding]) -> list[tuple[str, str]]:
    return sorted((f.id, f"{f.detail.get('ref', '')}.{f.detail.get('pin', '')}") for f in findings)


def spec(**data: Any) -> Spec:
    return Spec.model_validate({"id": "s", "summary": "s"} | data)


def ic(ref: str = "U1", **extra: Any) -> dict[str, Any]:
    return part(ref, "T:IC8", "IC8", **extra)


def test_the_rule_list_is_what_plan_7_asks_for() -> None:
    assert [name for name, _ in RULES] == [
        "dangling_pins",
        "power_pin_nets",
        "output_conflicts",
        "missing_decoupling",
        "rating_exceeded",
        "package_assemblable",
    ]


# ---------------------------------------------------------------- 悬空引脚


def test_unconnected_inputs_and_power_pins_are_reported(tmp_path: Path) -> None:
    d = design([ic()], [net("+5V", "U1.1", external_supply=True), net("GND", "U1.2")])
    found = where(dangling_pins(ctx(tmp_path, d)))
    # 3 脚是输入、7 脚是电源输入；4 输出、5 双向、6 开集、8 no_connect 型的不用接
    assert found == [("dangling_pin", "U1.3"), ("dangling_pin", "U1.7")]


def test_pins_marked_nc_in_the_ir_are_not_reported(tmp_path: Path) -> None:
    d = design(
        [ic(nc_pins=["3", "7"])], [net("+5V", "U1.1", external_supply=True), net("GND", "U1.2")]
    )
    assert dangling_pins(ctx(tmp_path, d)) == []


def test_fully_connected_pins_are_fine(tmp_path: Path) -> None:
    d = design(
        [ic()],
        [
            net("+5V", "U1.1", external_supply=True),
            net("GND", "U1.2", "U1.7"),
            net("SIG", "U1.3", "U1.4"),
        ],
    )
    assert dangling_pins(ctx(tmp_path, d)) == []


def test_a_pin_both_nc_and_wired_is_rejected_when_the_design_is_read() -> None:
    with pytest.raises(ValueError, match="NC"):
        design([ic(nc_pins=["3"])], [net("SIG", "U1.3", "U1.4")])


# ---------------------------------------------------------------- 电源脚、地脚


def test_power_pin_on_the_ground_net_is_a_short(tmp_path: Path) -> None:
    d = design([ic()], [net("GND", "U1.1", "U1.2")])
    assert where(power_pin_nets(ctx(tmp_path, d))) == [("power_pin_on_ground", "U1.1")]


def test_ground_pin_must_be_on_a_ground_net(tmp_path: Path) -> None:
    d = design([ic()], [net("+5V", "U1.1", "U1.2", external_supply=True)])
    assert where(power_pin_nets(ctx(tmp_path, d))) == [("ground_pin_not_grounded", "U1.2")]


def test_power_pin_on_a_signal_net_has_no_supply(tmp_path: Path) -> None:
    d = design([ic()], [net("RAW", "U1.1"), net("GND", "U1.2")])
    assert where(power_pin_nets(ctx(tmp_path, d))) == [("power_pin_not_on_power_net", "U1.1")]


def test_a_net_name_that_looks_like_power_is_enough_when_a_power_pin_hangs_on_it(
    tmp_path: Path,
) -> None:
    d = design([ic()], [net("VCC", "U1.1"), net("GND", "U1.2")])
    assert power_pin_nets(ctx(tmp_path, d)) == []


def test_power_net_by_driver_is_enough_even_with_an_odd_name(tmp_path: Path) -> None:
    reg = part("U2", "T:REG", "REG")
    d = design(
        [ic(), reg],
        [net("RAIL", "U2.2", "U1.1"), net("GND", "U1.2", "U2.3"), net("VIN", "U2.1")],
    )
    found = where(power_pin_nets(ctx(tmp_path, d)))
    assert found == [], "RAIL 由稳压器的电源输出脚驱动，是电源网络；VIN 名字像电源"


def test_negative_supply_pin_may_sit_on_ground_or_a_power_rail(tmp_path: Path) -> None:
    on_ground = design(
        [ic()], [net("GND", "U1.2", "U1.7"), net("+5V", "U1.1", external_supply=True)]
    )
    assert power_pin_nets(ctx(tmp_path, on_ground)) == []
    on_rail = design(
        [ic()],
        [
            net("GND", "U1.2"),
            net("+5V", "U1.1", external_supply=True),
            net("-5V", "U1.7", external_supply=True),
        ],
    )
    assert power_pin_nets(ctx(tmp_path / "b", on_rail)) == []


def test_negative_supply_pin_on_a_signal_net_is_reported(tmp_path: Path) -> None:
    d = design([ic()], [net("GND", "U1.2"), net("X", "U1.7")])
    assert where(power_pin_nets(ctx(tmp_path, d))) == [("power_pin_not_on_power_net", "U1.7")]


def test_non_power_pins_are_not_looked_at(tmp_path: Path) -> None:
    d = design([ic()], [net("GND", "U1.3"), net("+5V", "U1.4", external_supply=True)])
    assert power_pin_nets(ctx(tmp_path, d)) == []


# ---------------------------------------------------------------- 两个输出驱动同一网络


def test_two_outputs_on_one_net_conflict(tmp_path: Path) -> None:
    d = design([ic("U1"), ic("U2")], [net("BUS", "U1.4", "U2.4")])
    found = output_conflicts(ctx(tmp_path, d))
    assert [f.id for f in found] == ["output_conflict"]
    assert found[0].detail == {"net": "BUS", "drivers": ["U1.4", "U2.4"]}


def test_an_output_and_a_power_output_conflict(tmp_path: Path) -> None:
    d = design([ic("U1"), part("U2", "T:REG", "REG")], [net("X", "U1.4", "U2.2")])
    assert [f.id for f in output_conflicts(ctx(tmp_path, d))] == ["output_conflict"]


def test_outputs_of_one_part_on_the_same_net_also_conflict(tmp_path: Path) -> None:
    opa = part("U1", "T:OPA", "IC5")
    d = design([opa, ic("U2")], [net("X", "U1.1", "U2.4")])
    assert [f.id for f in output_conflicts(ctx(tmp_path, d))] == ["output_conflict"]


def test_one_output_with_inputs_bidirectional_and_open_collector_is_fine(tmp_path: Path) -> None:
    d = design(
        [ic("U1"), ic("U2"), ic("U3")],
        [net("BUS", "U1.4", "U2.3", "U2.5", "U3.5", "U3.6", "U1.6")],
    )
    assert output_conflicts(ctx(tmp_path, d)) == []


# ---------------------------------------------------------------- 去耦电容


def _decoupling_design(*, cap: bool = True, cap_to: str = "GND") -> Design:
    parts = [ic("U1")]
    nets = [net("+5V", "U1.1", external_supply=True), net("GND", "U1.2", "U1.7")]
    if cap:
        parts.append(part("C1", "Device:C", "C_0805_2012Metric"))
        nets[0]["nodes"].append(["C1", "1"])
        if cap_to == "GND":
            nets[1]["nodes"].append(["C1", "2"])
        else:
            nets.append(net(cap_to, "C1.2"))
    return design(parts, nets)


def test_an_ic_without_a_decoupling_capacitor_is_reported(tmp_path: Path) -> None:
    found = missing_decoupling(ctx(tmp_path, _decoupling_design(cap=False)))
    assert [(f.id, f.detail) for f in found] == [
        ("missing_decoupling", {"ref": "U1", "net": "+5V"})
    ]


def test_a_capacitor_from_the_supply_to_ground_decouples_it(tmp_path: Path) -> None:
    assert missing_decoupling(ctx(tmp_path, _decoupling_design())) == []


def test_a_capacitor_to_a_signal_net_is_not_decoupling(tmp_path: Path) -> None:
    found = missing_decoupling(ctx(tmp_path, _decoupling_design(cap_to="SIG")))
    assert [f.id for f in found] == ["missing_decoupling"]


def test_a_capacitor_between_two_supplies_is_not_decoupling(tmp_path: Path) -> None:
    found = missing_decoupling(ctx(tmp_path, _decoupling_design(cap_to="+3V3")))
    assert [f.id for f in found] == ["missing_decoupling"]


def test_one_capacitor_serves_every_ic_on_the_same_rail(tmp_path: Path) -> None:
    d = design(
        [ic("U1"), ic("U2"), part("C1", "Device:C", "C_0805_2012Metric")],
        [
            net("+5V", "U1.1", "U2.1", "C1.1", external_supply=True),
            net("GND", "U1.2", "U1.7", "U2.2", "U2.7", "C1.2"),
        ],
    )
    assert missing_decoupling(ctx(tmp_path, d)) == []


def test_ground_pins_and_connectors_do_not_need_decoupling(tmp_path: Path) -> None:
    conn = part("J1", "Connector_T:CONN2", "CONN2")
    d = design([conn], [net("+5V", "J1.1", external_supply=True), net("GND", "J1.2")])
    assert missing_decoupling(ctx(tmp_path, d)) == []


def test_each_power_net_of_an_ic_is_checked_once(tmp_path: Path) -> None:
    opa = part("U1", "T:OPA", "IC5")
    d = design(
        [opa], [net("+5V", "U1.5", external_supply=True), net("-5V", "U1.4", external_supply=True)]
    )
    found = missing_decoupling(ctx(tmp_path, d))
    assert sorted(f.detail["net"] for f in found) == ["+5V", "-5V"]


# ---------------------------------------------------------------- 额定值


def _rated(**ratings: float) -> Design:
    q = part("Q1", "T:IC8", "IC8", ratings=ratings)
    return design([q], [net("VBAT", "Q1.1", external_supply=True), net("GND", "Q1.2")])


def test_voltage_above_derated_rating_is_reported(tmp_path: Path) -> None:
    s = spec(interfaces=[{"name": "VBAT", "kind": "power_in", "v_min": 10, "v_max": 60}])
    found = rating_exceeded(ctx(tmp_path, _rated(vds_max=55), s))
    assert [f.id for f in found] == ["rating_exceeded"]
    f = found[0]
    assert (f.measured, f.threshold, f.op, f.unit) == (60, pytest.approx(44.0), "<=", "V")
    assert f.margin == pytest.approx((44 - 60) / 44) and f.margin < 0
    assert f.detail["key"] == "vds_max" and f.detail["rating"] == 55


def test_voltage_within_derated_rating_is_fine(tmp_path: Path) -> None:
    s = spec(interfaces=[{"name": "VBAT", "kind": "power_in", "v_max": 44.0}])
    assert rating_exceeded(ctx(tmp_path, _rated(vds_max=55), s)) == []  # 刚好等于 55 × 0.8


def test_the_derating_factor_comes_from_the_caller(tmp_path: Path) -> None:
    s = spec(interfaces=[{"name": "VBAT", "kind": "power_in", "v_max": 50}])
    assert rating_exceeded(ctx(tmp_path, _rated(vds_max=55), s)) != []
    assert rating_exceeded(ctx(tmp_path / "b", _rated(vds_max=55), s, derating=1.0)) == []


def test_networks_with_unknown_voltage_and_unknown_keys_are_not_checked(tmp_path: Path) -> None:
    assert rating_exceeded(ctx(tmp_path, _rated(vds_max=5))) == [], "没有 spec，也没有仿真"
    s = spec(interfaces=[{"name": "VBAT", "kind": "power_in", "v_max": 60}])
    assert rating_exceeded(ctx(tmp_path / "b", _rated(color=1.0, rth_max=1.0), s)) == []
    s2 = spec(interfaces=[{"name": "OTHER", "kind": "power_in", "v_max": 60}])
    assert rating_exceeded(ctx(tmp_path / "c", _rated(vds_max=5), s2)) == []


def _sim(**stress: tuple[float, float, float]) -> Any:
    return make_report(
        "sim",
        [],
        device_stress={
            ref: DeviceStress(v_peak=v, i_peak=i, p_peak=p) for ref, (v, i, p) in stress.items()
        },
    )


def test_second_round_uses_the_simulated_peaks(tmp_path: Path) -> None:
    d = _rated(vds_max=55, id_max=10, pd_max=1.0)
    sim = _sim(Q1=(50.0, 9.0, 0.9))
    found = rating_exceeded(ctx(tmp_path, d, sim=sim))
    assert sorted(f.detail["key"] for f in found) == ["id_max", "pd_max", "vds_max"]
    assert all("仿真" in f.message for f in found)
    units = {f.detail["key"]: f.unit for f in found}
    assert units == {"vds_max": "V", "id_max": "A", "pd_max": "W"}


def test_second_round_is_quiet_when_the_peaks_are_within_limits(tmp_path: Path) -> None:
    d = _rated(vds_max=55, id_max=10, pd_max=1.0)
    assert rating_exceeded(ctx(tmp_path, d, sim=_sim(Q1=(40.0, 7.0, 0.7)))) == []
    assert rating_exceeded(ctx(tmp_path / "b", d, sim=_sim(R9=(900.0, 900.0, 900.0)))) == []


def test_second_round_takes_the_larger_of_spec_and_simulated_voltage(tmp_path: Path) -> None:
    s = spec(interfaces=[{"name": "VBAT", "kind": "power_in", "v_max": 60}])
    found = rating_exceeded(ctx(tmp_path, _rated(vds_max=55), s, sim=_sim(Q1=(12.0, 1.0, 1.0))))
    assert found[0].measured == 60 and "spec" in found[0].message


# ---------------------------------------------------------------- 封装可装配


def _packaged(footprint: str) -> Design:
    return design([part("R1", "Device:R", footprint)], [net("A", "R1.1", "R1.2")])


@pytest.mark.parametrize(
    "footprint, area",
    [
        ("FP:R_0805_2012Metric", 40),
        ("R_0402_1005Metric", 8),
        ("Capacitor_SMD:C_1206_3216Metric", 72),
        ("LED_SMD:LED_0603_1608Metric", 18),
        ("Package_SO:SOIC-8", None),
        ("Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal", None),
    ],
)
def test_chip_size_area(footprint: str, area: int | None) -> None:
    assert chip_size_area(footprint) == area


@pytest.mark.parametrize(
    "footprint, bottom",
    [
        ("Package_DFN_QFN:QFN-16-1EP_3x3mm_P0.5mm_EP1.9x1.9mm", True),
        ("Package_DFN_QFN:DFN-8-1EP_3x3mm", True),
        ("Package_DFN_QFN:WQFN-24-1EP_4x4mm", True),
        ("Package_DFN_QFN:HVQFN-32-1EP_5x5mm", True),
        ("Package_SON:WSON-8-1EP_3x3mm", True),
        ("Package_BGA:BGA-64_5x5mm", True),
        ("Package_LGA:LGA-14_3x2.5mm", True),
        ("Package_CSP:WLCSP-9_1.4x1.4mm", True),
        ("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", False),
        ("Package_QFP:TQFP-32_7x7mm_P0.8mm", False),
        ("Package_TO_SOT_SMD:SOT-223-3_TabPin2", False),
        ("Package_DIP:DIP-8_W7.62mm", False),
        ("Package_SO:TSSOP-16_4.4x5mm_P0.65mm", False),
    ],
)
def test_bottom_pad_package(footprint: str, bottom: bool) -> None:
    assert bottom_pad_package(footprint) is bottom


def test_package_smaller_than_min_package_is_reported(tmp_path: Path) -> None:
    s = spec(constraints={"min_package": "0805"})
    found = package_assemblable(ctx(tmp_path, _packaged("R_0603_1608Metric"), s))
    assert [(f.id, f.detail["ref"]) for f in found] == [("package_too_small", "R1")]


def test_package_at_or_above_min_package_is_fine(tmp_path: Path) -> None:
    s = spec(constraints={"min_package": "0805"})
    for i, footprint in enumerate(["R_0805_2012Metric", "R_1206_3216Metric", "SOIC-8"]):
        assert package_assemblable(ctx(tmp_path / str(i), _packaged(footprint), s)) == []


def test_qfn_cannot_be_hand_soldered(tmp_path: Path) -> None:
    s = spec(constraints={"assembly": "hand_solder"})
    found = package_assemblable(ctx(tmp_path, _packaged("QFN-8-1EP_3x3mm"), s))
    assert [f.id for f in found] == ["package_not_hand_solderable"]


def test_qfn_is_fine_for_reflow_and_a_smt_service_and_soic_for_hand_soldering(
    tmp_path: Path,
) -> None:
    for i, (assembly, footprint) in enumerate(
        [
            ("reflow", "QFN-8-1EP_3x3mm"),
            ("smt_service", "BGA-8"),
            ("hand_solder", "SOIC-8"),
            ("hand_solder", "TQFP-8"),
        ]
    ):
        s = spec(constraints={"assembly": assembly})
        assert package_assemblable(ctx(tmp_path / str(i), _packaged(footprint), s)) == []


def test_without_a_spec_nothing_is_checked(tmp_path: Path) -> None:
    assert package_assemblable(ctx(tmp_path, _packaged("QFN-8-1EP_3x3mm"))) == []
