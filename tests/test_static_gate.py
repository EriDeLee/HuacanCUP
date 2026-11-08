"""G1 的 IR 校验、spec 与 design 的对应、以及三步合在一起的 static_gate。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from eda_agent.emit.kicad_mod import FootprintNotFound, pad_numbers
from eda_agent.gates.ir_checks import signal_names, validate_document
from eda_agent.gates.kicad_reports import erc_gate
from eda_agent.gates.report import Finding, GateReport, make_report
from eda_agent.gates.static import static_gate, static_gate_from_document
from eda_agent.ir.models import Design
from eda_agent.ir.spec import Spec
from tests.helpers import design, net, part, write_libs


def run(tmp_path: Path, d: Design, spec: Spec | None = None, **kw: Any) -> GateReport:
    lib_dirs, footprints = write_libs(tmp_path)
    return static_gate(d, spec, symbol_dirs=lib_dirs, footprint_dir=footprints, **kw)


def ids(report: GateReport) -> list[str]:
    return [f.id for f in report.findings]


def good_design() -> Design:
    """一个什么毛病都没有的设计：IC8 的每个脚都接了或标了 NC，电源有去耦电容。"""
    return design(
        [
            part("U1", "T:IC8", "IC8", nc_pins=["8"]),
            part("R1", "Device:R", "R2"),
            part("C1", "Device:C", "C_0805_2012Metric"),
        ],
        [
            net("+5V", "U1.1", "C1.1", external_supply=True),
            net("GND", "U1.2", "U1.7", "C1.2"),
            net("SIG", "U1.3", "R1.1", "U1.4"),
            net("IO", "U1.5", "R1.2", "U1.6"),
        ],
    )


def test_a_clean_design_passes_all_three_steps(tmp_path: Path) -> None:
    report = run(tmp_path, good_design())
    assert report.passed, [f.message for f in report.findings]
    assert report.counts == {
        "ir_errors": 0,
        "rule_errors": 0,
        "rule_warnings": 0,
        "erc_errors": 0,
        "erc_warnings": 0,
    }


# ---------------------------------------------------------------- IR 校验


def test_duplicate_references_are_found_in_the_raw_json() -> None:
    doc = json.loads(good_design().model_dump_json(by_alias=True))
    doc["parts"][1]["ref"] = "U1"
    doc["nets"].append(dict(doc["nets"][0]))
    parsed, problems = validate_document(doc)
    assert parsed is None
    assert sorted(f.id for f in problems) == ["ir.duplicate_net", "ir.duplicate_ref"]
    assert any("U1" in f.message for f in problems)


def test_an_unreadable_document_is_a_report_not_an_exception(tmp_path: Path) -> None:
    doc = json.loads(good_design().model_dump_json(by_alias=True))
    doc["parts"][0]["lcsc"] = "bad"
    parsed, problems = validate_document(doc)
    assert parsed is None and problems[0].id == "ir.invalid"
    assert problems[0].detail["loc"][:2] == ["parts", "0"]
    lib_dirs, footprints = write_libs(tmp_path)
    report = static_gate_from_document(doc, None, symbol_dirs=lib_dirs, footprint_dir=footprints)
    assert not report.passed and report.counts["ir_errors"] == 1


def test_a_valid_document_goes_through_the_whole_gate(tmp_path: Path) -> None:
    lib_dirs, footprints = write_libs(tmp_path)
    doc = json.loads(good_design().model_dump_json(by_alias=True))
    assert static_gate_from_document(
        doc, None, symbol_dirs=lib_dirs, footprint_dir=footprints
    ).passed


def test_a_pin_that_the_symbol_does_not_have_is_reported(tmp_path: Path) -> None:
    d = design([part("R1", "Device:R", "R2")], [net("A", "R1.1", "R1.3")])
    report = run(tmp_path, d)
    assert ids(report) == ["ir.pin_missing"]
    assert report.findings[0].detail == {"net": "A", "ref": "R1", "pin": "3"}


def test_a_pin_with_no_pad_on_the_footprint_is_reported(tmp_path: Path) -> None:
    # 符号 T:IC8 有 8 个脚；封装 IC5 只有 5 个焊盘，8 个脚里用到 7 号
    d = design([part("U1", "T:IC8", "IC5")], [net("A", "U1.1", "U1.7")])
    report = run(tmp_path, d)
    assert sorted(ids(report)) == ["ir.pad_missing", "ir.pin_pad_count"]


def test_pin_count_must_equal_pad_count(tmp_path: Path) -> None:
    d = design([part("U1", "T:IC8", "IC5")], [net("A", "U1.1", "U1.2")])
    f = run(tmp_path, d).findings[0]
    assert f.id == "ir.pin_pad_count"
    assert f.detail["pins"] == 8 and f.detail["pads"] == 5
    assert f.detail["only_in_symbol"] == ["6", "7", "8"]


def test_matching_counts_pass_even_when_a_pad_number_repeats(tmp_path: Path) -> None:
    """稳压器的封装有一个和 2 脚同号的散热片焊盘：同号的算一个，3 个引脚对 3 个编号。"""
    d = design([part("U1", "T:REG", "REG")], [net("VIN", "U1.1"), net("GND", "U1.3")])
    assert "ir.pin_pad_count" not in ids(run(tmp_path, d))


def test_missing_symbol_and_footprint_stop_the_gate_after_step_1(tmp_path: Path) -> None:
    d = design(
        [part("U9", "T:NOPE", "IC8"), part("U8", "Device:R", "NOPE")], [net("A", "U9.1", "U8.1")]
    )
    report = run(tmp_path, d)
    assert sorted(ids(report)) == ["ir.footprint_missing", "ir.symbol_missing"]
    assert report.counts["ir_errors"] == 2 and report.counts["rule_errors"] == 0


def test_an_nc_pin_that_does_not_exist_is_reported(tmp_path: Path) -> None:
    d = design([part("R1", "Device:R", "R2", nc_pins=["9"])], [net("A", "R1.1", "R1.2")])
    assert ids(run(tmp_path, d)) == ["ir.nc_pin_unknown"]


def test_the_rules_do_not_run_when_the_ir_is_broken(tmp_path: Path) -> None:
    """IR 有 error 时规则读不到符号，不跑；报告里只有 ir.* 的 finding。"""
    d = design([part("U1", "T:IC8", "IC8")], [net("A", "U1.1", "U1.99")])
    report = run(tmp_path, d)
    assert all(i.startswith("ir.") for i in ids(report))


def test_footprint_pads_can_be_read(tmp_path: Path) -> None:
    _, footprints = write_libs(tmp_path)
    assert pad_numbers("FP:REG", footprints) == frozenset({"1", "2", "3"})
    with pytest.raises(FootprintNotFound):
        pad_numbers("FP:NOPE", footprints)


# ---------------------------------------------------------------- spec 和 design 的对应


def base_spec(**extra: Any) -> Spec:
    data: dict[str, Any] = {
        "id": "s",
        "summary": "s",
        "interfaces": [
            {"name": "+5V", "kind": "power_in", "v_nom": 5.0},
            {"name": "SIG", "kind": "signal_out"},
        ],
    }
    return Spec.model_validate(data | extra)


def test_interfaces_with_matching_net_names_pass(tmp_path: Path) -> None:
    assert run(tmp_path, good_design(), base_spec()).passed


def test_an_interface_without_a_net_of_the_same_name_is_reported(tmp_path: Path) -> None:
    spec = base_spec(interfaces=[{"name": "VIN", "kind": "power_in", "v_nom": 5.0}])
    report = run(tmp_path, good_design(), spec)
    assert ids(report) == ["ir.spec_interface_net_missing"]
    assert "接口名 = 网络名" in report.findings[0].message


def _acceptance(measure_signal: str, **load: Any) -> dict[str, Any]:
    stimulus: dict[str, Any] = {"load": load} if load else {}
    return {
        "id": "A1",
        "text": "t",
        "testbench": {"type": "transient", "sim_time": "1ms", "stimulus": stimulus},
        "measure": {"kind": "max", "signal": measure_signal},
        "pass": {"op": "<=", "value": 1.0, "unit": "V"},
    }


@pytest.mark.parametrize(
    "signal, expected",
    [
        ("V(SIG)", []),
        ("V(SIG,GND)", []),
        ("V(+5V)", []),
        ("I(R1)", []),
        ("I(U1.4)", []),
        ("I(+5V)", []),
        ("V(NOPE)", ["ir.spec_signal_unknown"]),
        ("V(SIG,NOPE)", ["ir.spec_signal_unknown"]),
        ("I(R9)", ["ir.spec_signal_unknown"]),
        ("I(U1.8)", ["ir.spec_signal_unknown"]),  # 8 脚标了 NC，没接网络
    ],
)
def test_measure_signals_must_exist_in_the_design(
    tmp_path: Path, signal: str, expected: list[str]
) -> None:
    spec = base_spec(acceptance=[_acceptance(signal)])
    assert ids(run(tmp_path, good_design(), spec)) == expected


def test_load_nets_must_exist_in_the_design(tmp_path: Path) -> None:
    def spec_with(between: list[str]) -> Spec:
        load = {"kind": "current_dc", "value": 1.0, "between": between}
        return base_spec(acceptance=[_acceptance("V(SIG)", **load)])

    report = run(tmp_path, good_design(), spec_with(["+5V", "NOPE"]))
    assert ids(report) == ["ir.spec_signal_unknown"] and "A1" in report.findings[0].message
    assert run(tmp_path / "b", good_design(), spec_with(["+5V", "GND"])).passed


def test_signal_names() -> None:
    assert signal_names("V(OUT,FB)") == ("V", ["OUT", "FB"])
    assert signal_names("I(Q1.2)") == ("I", ["Q1.2"])
    with pytest.raises(ValueError):
        signal_names("OUT")


# ---------------------------------------------------------------- 三步合在一起


def test_rule_findings_get_the_rule_prefix_and_fail_the_gate(tmp_path: Path) -> None:
    d = design(
        [part("U1", "T:IC8", "IC8")], [net("+5V", "U1.1", external_supply=True), net("GND", "U1.2")]
    )
    report = run(tmp_path, d)
    assert not report.passed
    assert set(ids(report)) == {"rule.dangling_pin", "rule.missing_decoupling"}
    assert report.counts["rule_errors"] == len(report.findings) and report.counts["ir_errors"] == 0


def test_the_erc_result_is_merged_in_and_counts_as_a_step(tmp_path: Path) -> None:
    erc_json = tmp_path / "erc.json"
    erc_json.write_text(
        json.dumps(
            {
                "sheets": [
                    {
                        "violations": [
                            {"type": "pin_not_connected", "severity": "error", "description": "x"},
                            {"type": "lib_symbol_issues", "severity": "warning"},
                        ]
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    report = run(tmp_path, good_design(), erc=erc_gate(erc_json))
    assert not report.passed
    assert ids(report) == ["erc.pin_not_connected"]
    assert report.counts["erc_errors"] == 1 and report.counts["erc_warnings"] == 1


def test_the_second_round_of_the_rating_check_uses_the_simulation_report(tmp_path: Path) -> None:
    d = design(
        [
            part("U1", "T:IC8", "IC8", nc_pins=["8"], ratings={"vcc_max": 6.0}),
            part("C1", "Device:C", "C_0805_2012Metric"),
        ],
        [
            net("+5V", "U1.1", "C1.1", external_supply=True),
            net("GND", "U1.2", "U1.7", "C1.2"),
            net("SIG", "U1.3", "U1.4"),
            net("IO", "U1.5", "U1.6"),
        ],
    )
    assert run(tmp_path, d).passed
    from eda_agent.gates.report import DeviceStress

    sim = make_report("sim", [], device_stress={"U1": DeviceStress(v_peak=5.0, i_peak=0, p_peak=0)})
    report = run(tmp_path / "b", d, sim=sim)
    assert ids(report) == ["rule.rating_exceeded"], "5 V > 6 V × 0.8"
    assert isinstance(report.findings[0], Finding)
