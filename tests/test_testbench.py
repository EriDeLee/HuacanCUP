"""测试台生成器（eda_agent/sim/testbench.py）：网表快照、模型缺失、连接器、各类激励和测量语句。

快照放在 tests/snapshots/：同样的输入每次生成完全一样的文本。有意改了生成的格式以后，
用 UPDATE_SNAPSHOTS=1 重新生成（cmd：set UPDATE_SNAPSHOTS=1 然后运行 pytest），再看一遍 git diff。
网表里只有相对路径和 ASCII，所以快照在 Windows 和 Linux 上一样。
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

import pytest

from eda_agent.config import REPO_ROOT
from eda_agent.ir.models import Design
from eda_agent.ir.spec import Spec
from eda_agent.sim.accuracy import strict_plan
from eda_agent.sim.testbench import ATTEMPTS, Bench, BenchError, build_bench

SNAPSHOTS = Path(__file__).parent / "snapshots"
CIRCUITS = REPO_ROOT / "tests" / "circuits"
EXAMPLES = REPO_ROOT / "examples"


def docs(folder: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    design = json.loads((folder / "design.json").read_text(encoding="utf-8"))
    spec = json.loads((folder / "spec.json").read_text(encoding="utf-8"))
    return design, spec


def bench(
    design_doc: dict[str, Any],
    spec_doc: dict[str, Any],
    acceptance: str = "A1",
    attempt: int = 0,
) -> Bench:
    design, spec = Design.model_validate(design_doc), Spec.model_validate(spec_doc)
    acc = next(a for a in spec.acceptance if a.id == acceptance)
    return build_bench(design, spec, acc, models_root=REPO_ROOT, attempt=ATTEMPTS[attempt])


def from_folder(folder: Path, acceptance: str = "A1", attempt: int = 0) -> Bench:
    return bench(*docs(folder), acceptance=acceptance, attempt=attempt)


# ---------------------------------------------------------------- 快照

CASES = [
    ("divider_op", CIRCUITS / "voltage_divider", "A1", 0),
    ("divider_op_current", CIRCUITS / "voltage_divider", "A2", 0),
    ("rc_ac_freq_at_db", CIRCUITS / "rc_lowpass", "A1", 0),
    ("rc_ac_gain", CIRCUITS / "rc_lowpass", "A2", 0),
    ("ne555_tran_frequency", EXAMPLES / "ne555_astable", "A1", 0),
    ("lm358_ac_gain", EXAMPLES / "lm358_amp", "A1", 0),
    ("overcurrent_time_until", EXAMPLES / "overcurrent_cutoff", "A1", 0),
    ("overcurrent_time_until_uic", EXAMPLES / "overcurrent_cutoff", "A1", 3),
    ("overcurrent_min_load_dc", EXAMPLES / "overcurrent_cutoff", "A2", 0),
]


@pytest.mark.parametrize("name, folder, acceptance, attempt", CASES, ids=[c[0] for c in CASES])
def test_generated_netlist_matches_the_snapshot(
    name: str, folder: Path, acceptance: str, attempt: int
) -> None:
    text = from_folder(folder, acceptance, attempt).text
    path = SNAPSHOTS / f"{name}.cir"
    if os.environ.get("UPDATE_SNAPSHOTS"):
        SNAPSHOTS.mkdir(exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    assert path.read_text(encoding="utf-8") == text


@pytest.mark.parametrize("name, folder, acceptance, attempt", CASES, ids=[c[0] for c in CASES])
def test_the_same_input_always_gives_the_same_text(
    name: str, folder: Path, acceptance: str, attempt: int
) -> None:
    assert (
        from_folder(folder, acceptance, attempt).text
        == from_folder(folder, acceptance, attempt).text
    )


def test_netlists_are_ascii_and_use_relative_paths() -> None:
    for _, folder, acceptance, attempt in CASES:
        text = from_folder(folder, acceptance, attempt).text
        assert text.isascii()
        for line in text.splitlines():
            if line.lower().startswith(".include"):
                path = line.split(None, 1)[1]
                assert path.startswith("models/") and ":" not in path and "\\" not in path


# ---------------------------------------------------------------- 元件和模型


def test_resistors_and_capacitors_become_native_elements_with_current_probes() -> None:
    text = from_folder(CIRCUITS / "rc_lowpass").text
    assert "R1 R1_1 R1_2 10000" in text
    assert "C1 C1_1 C1_2 1e-08" in text
    assert "VI_R1_1 IN R1_1 DC 0" in text  # 探针：网络 → 器件


def test_connectors_are_left_out_and_the_interfaces_drive_the_nets() -> None:
    b = from_folder(CIRCUITS / "voltage_divider")
    assert "J1" not in b.text and "J2" not in b.text
    assert "VS_VIN x_VIN 0 DC 12" in b.text and "VX_VIN x_VIN VIN DC 0" in b.text
    assert {p.ref for p in b.probes if p.kind == "pin"} == {"R1", "R2"}


def test_a_vendor_subcircuit_is_included_and_ltpsa_is_switched_on() -> None:
    b = from_folder(EXAMPLES / "lm358_amp")
    assert b.includes == ("models/lm358.lib",)
    assert b.settings == ("ngbehavior=ltpsa",)
    assert ".include models/lm358.lib" in b.text
    # 引脚按 pin_map 接到子电路的端子：
    # 1 OUT1、2 IN1N、3 IN1P、4 VNEG、5 IN2P、6 IN2N、7 OUT2、8 VPOS
    line = next(x for x in b.text.splitlines() if x.startswith("XU1"))
    assert line == "XU1 U1_1 U1_2 U1_3 U1_4 U1_5 U1_6 U1_7 U1_8 LM358_DUAL"


def test_without_a_vendor_model_ltpsa_is_not_switched_on() -> None:
    b = from_folder(CIRCUITS / "rc_lowpass")
    assert b.includes == () and b.settings == ()


def test_a_model_card_becomes_a_native_diode_or_mosfet() -> None:
    ne555 = from_folder(EXAMPLES / "ne555_astable").text
    # 二极管的端子：A 接 2 脚（阳极）、K 接 1 脚（阴极）→ 节点顺序 A K = 2 脚、1 脚
    line = next(x for x in ne555.splitlines() if x.startswith("D1 "))
    assert line == "D1 D1_2 D1_1 LED_RED_GENERIC"
    oc = from_folder(EXAMPLES / "overcurrent_cutoff").text
    assert "MQ1 Q1_2 Q1_1 Q1_3 IRLZ44N" in oc  # 漏极 栅极 源极，VDMOS 三个端子
    assert "XU1 " in oc and "LM393_DUAL" in oc


def test_a_pin_without_a_net_gets_a_floating_node_and_no_probe() -> None:
    text = from_folder(EXAMPLES / "overcurrent_cutoff").text
    line = next(x for x in text.splitlines() if x.startswith("XU1"))
    assert "nc_U1_7" in line and "VI_U1_7" not in text


def test_probes_cover_every_connected_pin_of_every_simulated_part() -> None:
    b = from_folder(EXAMPLES / "overcurrent_cutoff")
    assert set(b.part_probes["Q1"]) == {"1", "2", "3"}
    assert set(b.part_probes["U1"]) == {"1", "2", "3", "4", "5", "6", "8"}
    assert "J1" not in b.part_probes and "J2" not in b.part_probes


def _divider() -> tuple[dict[str, Any], dict[str, Any]]:
    return docs(CIRCUITS / "voltage_divider")


def test_a_part_without_a_model_makes_the_acceptance_a_model_missing_error() -> None:
    design, spec = _divider()
    design["parts"].append(
        {
            "ref": "U1",
            "symbol": "Sensor_Temperature:LM75B",
            "footprint": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
            "value": "LM75B",
        }
    )
    design["nets"][1]["nodes"].append(["U1", "1"])
    with pytest.raises(BenchError, match="模型缺失") as info:
        bench(design, spec)
    assert info.value.kind == "model_missing" and info.value.refs == ("U1",)
    assert "LM75B" in str(info.value)


def _with_spice(**spice: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    design, spec = docs(EXAMPLES / "lm358_amp")
    u1 = next(p for p in design["parts"] if p["ref"] == "U1")
    u1["spice"] = {**u1["spice"], **spice}
    return design, spec


@pytest.mark.parametrize(
    "spice, text",
    [
        ({"lib": "models/nope.lib"}, "不存在"),
        ({"lib": "../outside.lib"}, "相对路径"),
        ({"lib": "/etc/passwd"}, "相对路径"),
        ({"lib": None}, "没有写 lib"),
        ({"model": "NOT_THERE"}, "没有叫 NOT_THERE"),
        ({"pin_map": {"1": "OUT1", "2": "IN1N", "3": "IN1P", "4": "VNEG"}}, "没有它们"),
        (
            {
                "pin_map": {
                    "1": "OUT1",
                    "2": "IN1N",
                    "3": "IN1P",
                    "4": "VNEG",
                    "5": "IN2P",
                    "6": "IN2N",
                    "7": "OUT2",
                    "8": "WRONG",
                }
            },
            "不是模型",
        ),
    ],
)
def test_broken_spice_fields_are_model_missing_errors(spice: dict[str, Any], text: str) -> None:
    design, spec = _with_spice(**spice)
    with pytest.raises(BenchError, match=text) as info:
        bench(design, spec)
    assert info.value.kind == "model_missing"


def test_a_model_terminal_without_a_symbol_pin_is_reported() -> None:
    design, spec = _with_spice()
    u1 = next(p for p in design["parts"] if p["ref"] == "U1")
    del u1["spice"]["pin_map"]["8"]
    design["nets"][0]["nodes"] = [n for n in design["nets"][0]["nodes"] if n != ["U1", "8"]]
    with pytest.raises(BenchError, match="VPOS 在 spice.pin_map 里没有对应"):
        bench(design, spec)


def test_a_value_that_is_not_a_number_is_a_design_error() -> None:
    design, spec = _divider()
    design["parts"][1]["value"] = "lots"
    with pytest.raises(BenchError, match="读不出来") as info:
        bench(design, spec)
    assert info.value.kind == "design" and info.value.refs == ("R1",)


def test_a_resistor_with_an_open_pin_is_a_design_error() -> None:
    design, spec = _divider()
    design["nets"][1]["nodes"].remove(["R2", "1"])
    with pytest.raises(BenchError, match="1、2 脚必须都接了网络"):
        bench(design, spec)


# ---------------------------------------------------------------- 地、节点命名、接口


def test_a_design_needs_one_ground_net() -> None:
    design, spec = _divider()
    for net in design["nets"]:
        net["name"] = {"GND": "SHIELD"}.get(net["name"], net["name"])
    with pytest.raises(BenchError, match="找不到地网络"):
        bench(design, spec)


def test_several_ground_nets_need_a_decision_unless_one_is_called_gnd() -> None:
    design, spec = _divider()
    design["nets"].append({"name": "AGND", "nodes": [["J1", "9"]]})
    assert "net GND = node 0" in bench(design, spec).text
    for net in design["nets"]:
        net["name"] = {"GND": "DGND"}.get(net["name"], net["name"])
    with pytest.raises(BenchError, match="多个地网络"):
        bench(design, spec)
    spec["interfaces"].append({"name": "AGND", "kind": "ground"})
    assert "net AGND = node 0" in bench(design, spec).text


def test_node_names_are_ascii_unique_and_never_gnd() -> None:
    design, spec = _divider()
    design["nets"] += [
        {"name": "+5V", "nodes": [["J1", "7"]]},
        {"name": "5V", "nodes": [["J1", "8"]]},
        {"name": "Mid", "nodes": [["J1", "9"]]},
        {"name": "电源", "nodes": [["J1", "10"]]},
    ]
    b = bench(design, spec)
    assert b.nodes["+5V"] == "p5V" and b.nodes["5V"] == "n_5V"
    assert b.nodes["Mid"].lower() != b.nodes["MID"].lower(), "ngspice 不分大小写"
    assert b.nodes["电源"].isascii()
    assert len({v.lower() for v in b.nodes.values()}) == len(b.nodes)
    assert "gnd" not in {v.lower() for v in b.nodes.values()}


def test_an_interface_without_a_net_is_a_spec_error() -> None:
    design, spec = _divider()
    spec["interfaces"].append({"name": "NOPE", "kind": "signal_out"})
    with pytest.raises(BenchError, match="没有网络 NOPE") as info:
        bench(design, spec)
    assert info.value.kind == "spec"


def test_a_power_input_needs_a_voltage() -> None:
    design, spec = _divider()
    spec["interfaces"][0] = {"name": "VIN", "kind": "power_in"}
    with pytest.raises(BenchError, match="没有电压"):
        bench(design, spec)


def test_the_supply_voltage_is_the_nominal_or_the_midpoint() -> None:
    design, spec = _divider()
    spec["interfaces"][0] = {"name": "VIN", "kind": "power_in", "v_min": 10, "v_max": 14}
    assert "VS_VIN x_VIN 0 DC 12" in bench(design, spec).text
    spec["interfaces"][0] = {
        "name": "VIN",
        "kind": "power_in",
        "v_min": 10,
        "v_nom": 11,
        "v_max": 14,
    }
    assert "VS_VIN x_VIN 0 DC 11" in bench(design, spec).text


def test_a_drive_replaces_the_default_supply() -> None:
    design, spec = _divider()
    spec["acceptance"][0]["testbench"]["stimulus"] = {
        "drive": {"VIN": {"kind": "sine", "offset": 5, "amplitude": 1, "freq": "1k"}}
    }
    assert "VS_VIN x_VIN 0 SIN(5 1 1000)" in bench(design, spec).text


@pytest.mark.parametrize(
    "drive, expected",
    [
        ({"kind": "dc", "value": 3.3}, "DC 3.3"),
        (
            {"kind": "step", "from": 0, "to": 5, "at": "1ms", "rise": "10us"},
            "PWL(0 0 0.001 0 0.00101 5)",
        ),
        ({"kind": "step", "from": 1, "to": 2, "at": 0, "rise": "1us"}, "PWL(0 1 1e-06 2)"),
        (
            {
                "kind": "pulse",
                "low": 0,
                "high": 5,
                "delay": "1ms",
                "rise": "1us",
                "fall": "2us",
                "width": "3ms",
                "period": "10ms",
            },
            "PULSE(0 5 0.001 1e-06 2e-06 0.003 0.01)",
        ),
        ({"kind": "ac", "dc": 0.2, "magnitude": 0.5}, "DC 0.2 AC 0.5"),
    ],
)
def test_voltage_source_waveforms(drive: dict[str, Any], expected: str) -> None:
    design, spec = _divider()
    spec["acceptance"][0]["testbench"]["stimulus"] = {"drive": {"VIN": drive}}
    assert f"VS_VIN x_VIN 0 {expected}" in bench(design, spec).text


def test_a_source_cannot_drive_the_reference_ground() -> None:
    design, spec = _divider()
    spec["interfaces"].append({"name": "GND", "kind": "power_in", "v_nom": 1.0})
    with pytest.raises(BenchError, match="参考地"):
        bench(design, spec)


# ---------------------------------------------------------------- 负载


def _loaded(load: dict[str, Any], **iface: Any) -> Bench:
    design, spec = _divider()
    spec["interfaces"][1] = {"name": "MID", "kind": "power_out"} | iface
    spec["acceptance"][0]["testbench"]["stimulus"] = {"load": load}
    spec["acceptance"][0]["testbench"]["type"] = "op"
    spec["acceptance"][0]["measure"] = {"kind": "value_at", "signal": "I(MID)"}
    return bench(design, spec)


def test_a_dc_current_load_has_the_undervoltage_cutout() -> None:
    b = _loaded({"kind": "current_dc", "value": 0.001})
    assert "VL_load MID load_mid DC 0" in b.text
    assert "BL_load load_mid 0 I = 0.001*0.5*(1+tanh((V(load_mid,0)-1)/0.1))" in b.text


def test_a_step_load_uses_a_pwl_set_point_so_that_the_edges_are_breakpoints() -> None:
    b = _loaded({"kind": "current_step", "from": 0.5, "to": 2, "at": "2ms"})
    assert "VSET_load load_set 0 PWL(0 0.5 0.002 0.5 0.002001 2)" in b.text
    assert "BL_load load_mid 0 I = V(load_set)*0.5*(1+tanh((V(load_mid,0)-1)/0.1))" in b.text


def test_a_resistor_load() -> None:
    b = _loaded({"kind": "resistor", "value": "4k7"})
    assert "RL_load load_mid 0 4700" in b.text


def test_the_load_goes_between_the_nets_the_spec_names() -> None:
    b = _loaded({"kind": "resistor", "value": 100, "between": ["VIN", "MID"]})
    assert "VL_load VIN load_mid DC 0" in b.text and "RL_load load_mid MID 100" in b.text


def test_current_through_an_output_interface_reads_the_load_probe() -> None:
    b = _loaded({"kind": "current_dc", "value": 0.001})
    assert b.plan.signal.probe == "VL_load"
    assert b.plan.meas_names == ()  # op 没有 .meas


def test_a_load_probe_counts_as_a_terminal_of_both_nets() -> None:
    b = _loaded({"kind": "resistor", "value": 100, "between": ["VIN", "MID"]})
    load = next(p for p in b.probes if p.kind == "load")
    assert (load.net, load.other_net) == ("VIN", "MID")


# ---------------------------------------------------------------- 信号


def _measure(signal: str, kind: str = "value_at", **extra: Any) -> Bench:
    design, spec = _divider()
    spec["acceptance"][0]["measure"] = {"kind": kind, "signal": signal} | extra
    return bench(design, spec)


def test_voltage_signals() -> None:
    assert _measure("V(MID)").plan.signal.spice == "v(MID)"
    assert _measure("V(MID,GND)").plan.signal.spice == "v(MID,0)"
    assert _measure("V(VIN,MID)").plan.signal.spice == "v(VIN,MID)"


def test_current_signals() -> None:
    assert _measure("I(VIN)").plan.signal.spice == "i(VX_VIN)"  # 接口：电源 → 电路
    assert _measure("I(R1)").plan.signal.spice == "i(VI_R1_1)"  # 两脚元件：1 脚 → 2 脚
    assert _measure("I(R2.2)").plan.signal.spice == "i(VI_R2_2)"  # 流进该引脚


@pytest.mark.parametrize(
    "signal, text",
    [
        ("V(NOPE)", "没有网络 NOPE"),
        ("I(J1.1)", "连接器不进网表"),
        ("I(NOPE)", "设计里没有这个元件"),
        ("I(MID)", "上没有源也没有负载"),
        ("I(R1.7)", "没接网络"),
    ],
)
def test_signals_that_cannot_be_probed_are_spec_errors(signal: str, text: str) -> None:
    with pytest.raises(BenchError, match=text) as info:
        _measure(signal)
    assert info.value.kind == "spec"


# ---------------------------------------------------------------- 分析和 .meas


def _tran(measure: dict[str, Any], sim_time: str = "10ms", **tb: Any) -> Bench:
    design, spec = docs(EXAMPLES / "overcurrent_cutoff")
    acc = spec["acceptance"][0]
    acc["testbench"] = {**acc["testbench"], "sim_time": sim_time, "max_step": "10us"} | tb
    acc["measure"] = measure
    return bench(design, spec)


def test_transient_analysis_line_and_the_meas_for_each_kind() -> None:
    value_at = _tran({"kind": "value_at", "signal": "V(GATE)", "at": "2.5ms"})
    assert ".tran 1e-05 0.01 0 1e-05" in value_at.text
    assert ".meas tran m_a1 FIND v(GATE) AT=0.0025" in value_at.text
    window = _tran({"kind": "max", "signal": "V(GATE)", "from": "1ms", "to": "9ms"})
    assert ".meas tran m_a1 MAX v(GATE) FROM=0.001 TO=0.009" in window.text
    assert ".meas tran m_a1 MIN v(GATE)" in _tran({"kind": "min", "signal": "V(GATE)"}).text
    assert ".meas tran m_a1 AVG v(GATE)" in _tran({"kind": "avg", "signal": "V(GATE)"}).text


def test_time_until_measures_from_after_to_the_first_crossing() -> None:
    falling = _tran(
        {"kind": "time_until", "signal": "I(LOAD_N)", "condition": "< 0.5", "after": "2ms"}
    )
    assert ".meas tran m_a1 TRIG AT=0.002 TARG i(VL_load) VAL=0.5 FALL=1 TD=0.002" in falling.text
    assert falling.plan.condition == ("<", 0.5) and falling.plan.after == 0.002
    rising = _tran({"kind": "time_until", "signal": "V(GATE)", "condition": ">= 4.5"})
    assert ".meas tran m_a1 TRIG AT=0 TARG v(GATE) VAL=4.5 RISE=1 TD=0" in rising.text


def test_frequency_measures_cycles_between_rising_edges() -> None:
    b = _tran({"kind": "frequency", "signal": "V(GATE)", "level": 2.5, "from": "1ms", "cycles": 3})
    assert (
        ".meas tran m_a1 TRIG v(GATE) VAL=2.5 RISE=1 TD=0.001 TARG v(GATE) VAL=2.5 RISE=4 TD=0.001"
        in b.text
    )


def test_ac_analysis_line_and_the_meas_for_each_kind() -> None:
    design, spec = docs(CIRCUITS / "rc_lowpass")
    b = bench(design, spec, "A2")
    assert ".ac dec 50 10 100000" in b.text
    assert ".meas ac m_a2_out FIND vdb(OUT) AT=10000" in b.text
    assert ".meas ac m_a2_ref FIND vdb(IN) AT=10000" in b.text, "参考信号缺省是被交流源驱动的输入"
    assert b.plan.meas_names == ("m_a2_out", "m_a2_ref")
    assert "VS_IN x_IN 0 DC 0 AC 1" in b.text
    # freq_at_db：向量上找交点，网表里没有 .meas
    assert ".meas" not in bench(design, spec, "A1").text


def test_a_dc_sweep_needs_a_source_on_the_swept_interface() -> None:
    design, spec = _divider()
    spec["acceptance"][0]["testbench"] = {
        "type": "dc",
        "sweep": {"source": "VIN", "start": 0, "stop": 12, "step": 1},
    }
    spec["acceptance"][0]["measure"] = {"kind": "value_at", "signal": "V(MID)", "at": 6}
    b = bench(design, spec)
    assert ".dc VS_VIN 0 12 1" in b.text and ".meas dc m_a1 FIND v(MID) AT=6" in b.text
    spec["acceptance"][0]["testbench"]["sweep"]["source"] = "MID"
    with pytest.raises(BenchError, match="没有电压源"):
        bench(design, spec)


def test_op_analysis_has_no_meas() -> None:
    text = from_folder(CIRCUITS / "voltage_divider").text
    assert ".op\n" in text and ".meas" not in text


# ---------------------------------------------------------------- 收敛重试的各档设置


def test_each_attempt_adds_more_to_the_previous_one() -> None:
    texts = [from_folder(EXAMPLES / "overcurrent_cutoff", "A1", i).text for i in range(4)]
    assert ".options" not in texts[0]
    assert ".options gmin=1e-9 reltol=0.01 abstol=1e-9 vntol=0.0001\n" in texts[1]
    assert "method=gear" in texts[2] and "gmin=1e-9" in texts[2]
    assert "itl1=1000" in texts[3] and "method=gear" in texts[3]
    assert [" uic" in t for t in texts] == [False, False, False, True]
    steps = [next(x for x in t.splitlines() if x.startswith(".tran")).split()[1] for t in texts]
    assert steps == ["5e-06", "5e-06", "5e-06", "5e-07"]
    assert [a.name for a in ATTEMPTS] == ["default", "relaxed", "gear", "short_step"]


def test_attempts_do_not_change_the_circuit_itself() -> None:
    def circuit(text: str) -> list[str]:
        return [x for x in text.splitlines() if not x.startswith((".options", ".tran", "*"))]

    base = circuit(from_folder(EXAMPLES / "overcurrent_cutoff", "A1", 0).text)
    for i in (1, 2, 3):
        assert circuit(from_folder(EXAMPLES / "overcurrent_cutoff", "A1", i).text) == base


def strict(folder: Path, acceptance: str, round_number: int, options: tuple[str, ...]) -> Bench:
    design_doc, spec_doc = docs(folder)
    design, spec = Design.model_validate(design_doc), Spec.model_validate(spec_doc)
    acc = next(a for a in spec.acceptance if a.id == acceptance)
    return build_bench(
        design,
        spec,
        acc,
        models_root=REPO_ROOT,
        attempt=strict_plan(options, round_number),
    )


def test_strict_settings_tighten_the_error_control_and_shrink_the_step() -> None:
    """复核峰值用的设置（D39）：第 k 轮误差系数和最大步长都是第 0 轮的 1 / 2^k。"""
    folder = EXAMPLES / "overcurrent_cutoff"
    texts = [strict(folder, "A1", k, ("method=gear",)).text for k in range(3)]
    options = [next(x for x in t.splitlines() if x.startswith(".options")) for t in texts]
    assert options == [
        ".options method=gear trtol=1",
        ".options method=gear trtol=0.5",
        ".options method=gear trtol=0.25",
    ]
    steps = [next(x for x in t.splitlines() if x.startswith(".tran")).split()[1] for t in texts]
    assert steps == ["5e-06", "2.5e-06", "1.25e-06"]
    assert all(" uic" not in t for t in texts), "严格设置不跳过直流工作点"
    assert "attempt accuracy" in texts[0]


def test_strict_settings_leave_the_circuit_itself_alone() -> None:
    def circuit(text: str) -> list[str]:
        return [x for x in text.splitlines() if not x.startswith((".options", ".tran", "*"))]

    folder = EXAMPLES / "overcurrent_cutoff"
    base = circuit(from_folder(folder, "A1", 0).text)
    assert circuit(strict(folder, "A1", 2, ("method=gear",)).text) == base


def test_the_error_factor_is_only_written_for_transient_analyses() -> None:
    text = strict(CIRCUITS / "voltage_divider", "A1", 1, ("method=gear",)).text
    assert "trtol" not in text and ".options method=gear\n" in text


def test_the_four_convergence_attempts_do_not_set_the_error_factor() -> None:
    """收敛重试只负责出判定，维持 ngspice 默认的误差控制；峰值另走严格设置。"""
    assert all(a.trtol is None for a in ATTEMPTS)


def test_building_does_not_modify_its_inputs() -> None:
    design, spec = docs(EXAMPLES / "lm358_amp")
    before = copy.deepcopy((design, spec))
    bench(design, spec)
    assert (design, spec) == before
