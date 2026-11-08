"""G3：KiCad DRC 之外的三项检查（未布通网络数、板尺寸、线宽）。
不需要 KiCad：PCB 和 DRC 报告都是手写的文本。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from eda_agent.gates.drc import (
    drc_checks,
    estimate_net_currents,
    pcb_net_names,
    read_pcb,
    unrouted_nets,
)
from eda_agent.gates.report import DeviceStress, GateReport, make_report
from eda_agent.ir.models import Design
from eda_agent.ir.spec import Spec
from eda_agent.pcb.trace_width import min_width_mm

Segment = tuple[str, str, float, tuple[float, float], tuple[float, float]]


def _pcb(
    tmp_path: Path,
    segments: list[Segment] | None = None,
    outline: tuple[float, float, float, float] = (100, 100, 130, 120),
    declare_nets: list[str] | None = None,
) -> Path:
    """KiCad 10 的写法：网络直接写名字。
    declare_nets 给了就改成早期的写法：开头声明、走线里用编号。"""
    ids = {name: i + 1 for i, name in enumerate(declare_nets or [])}
    lines = ['(kicad_pcb (version 20260101) (generator "test")']
    lines += [f'(net {i} "{name}")' for name, i in ids.items()]
    x0, y0, x1, y1 = outline
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    for (ax, ay), (bx, by) in zip(corners, corners[1:] + corners[:1], strict=True):
        lines.append(
            f"(gr_line (start {ax} {ay}) (end {bx} {by}) (stroke (width 0.1) (type default))"
            ' (layer "Edge.Cuts") (uuid "u"))'
        )
    for net, layer, width, (sx, sy), (ex, ey) in segments or []:
        net_ref = ids[net] if declare_nets else f'"{net}"'
        lines.append(
            f"(segment (start {sx} {sy}) (end {ex} {ey}) (width {width}) "
            f'(layer "{layer}") (net {net_ref}) (uuid "u"))'
        )
    lines.append(")")
    path = tmp_path / "board.kicad_pcb"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _drc(tmp_path: Path, unconnected: list[list[str]] | None = None, **extra: Any) -> Path:
    """unconnected：每处缺连接是一个描述列表，描述里带 [网络名]。"""
    items = [
        {
            "description": "Missing connection between items",
            "severity": "error",
            "type": "unconnected_items",
            "items": [{"description": d, "pos": {"x": 0, "y": 0}} for d in descs],
        }
        for descs in (unconnected or [])
    ]
    data = {"violations": [], "unconnected_items": items, "schematic_parity": []} | extra
    path = tmp_path / "drc.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _spec(**constraints: Any) -> Spec:
    return Spec.model_validate(
        {
            "id": "s",
            "summary": "s",
            "interfaces": [{"name": "VIN", "kind": "power_in", "v_nom": 5, "i_max": 2.0}],
            "constraints": constraints,
        }
    )


def _design() -> Design:
    return Design.model_validate(
        {
            "parts": [
                {"ref": "R1", "symbol": "Device:R", "footprint": "a:b", "value": "1k"},
                {"ref": "D1", "symbol": "Device:LED", "footprint": "a:b", "value": "red"},
            ],
            "nets": [
                {"name": "VIN", "nodes": [["R1", "1"]]},
                {"name": "LED_A", "nodes": [["R1", "2"], ["D1", "2"]]},
            ],
            "board": {"size_mm": [30, 20]},
        }
    )


def _ids(report: GateReport) -> list[str]:
    return [f.id for f in report.findings]


def test_a_clean_board_passes(tmp_path: Path) -> None:
    pcb = _pcb(tmp_path, [("VIN", "F.Cu", 1.0, (101, 101), (110, 101))])
    report = drc_checks(
        drc_report=_drc(tmp_path),
        pcb=pcb,
        spec=_spec(board_max_mm=[60, 40]),
        currents={"VIN": 2.0},
    )
    assert report.passed, report.findings
    assert report.counts["unrouted_nets"] == 0
    assert report.counts["width_checked"] == 1 and report.counts["width_failed"] == 0


def test_unrouted_nets_are_counted_per_net_in_any_language(tmp_path: Path) -> None:
    drc = _drc(
        tmp_path,
        [
            ["PTH pad 1 [VIN] of J1", "PTH pad 1 [VIN] of R1"],
            ["J1 的PTH 焊盘 1 [VIN]", "D1 的PTH 焊盘 2 [VIN]"],  # 同一个网络的第二处缺口
            ["J1 的PTH 焊盘 2 [GND]", "D1 的PTH 焊盘 1 [GND]"],
        ],
    )
    assert unrouted_nets(drc) == {"GND": 1, "VIN": 2}
    report = drc_checks(drc_report=drc, pcb=_pcb(tmp_path))
    assert not report.passed
    assert report.counts["unrouted_nets"] == 2
    assert _ids(report) == ["unrouted_nets"], "KiCad 原始的缺连接条目不重复报"
    finding = report.findings[0]
    assert finding.measured == 2 and finding.threshold == 0 and finding.status == "fail"
    assert finding.detail["nets"] == {"GND": 1, "VIN": 2}


def test_no_unrouted_nets_means_no_finding(tmp_path: Path) -> None:
    report = drc_checks(drc_report=_drc(tmp_path), pcb=_pcb(tmp_path))
    assert report.passed and report.counts["unrouted_nets"] == 0


def test_kicad_violations_still_fail_the_gate(tmp_path: Path) -> None:
    drc = _drc(
        tmp_path,
        violations=[{"type": "clearance", "severity": "error", "description": "间距不够"}],
    )
    report = drc_checks(drc_report=drc, pcb=_pcb(tmp_path))
    assert not report.passed and _ids(report) == ["clearance"]


def test_board_larger_than_the_limit_fails(tmp_path: Path) -> None:
    pcb = _pcb(tmp_path, outline=(100, 100, 140, 130))  # 40 × 30
    report = drc_checks(drc_report=_drc(tmp_path), pcb=pcb, spec=_spec(board_max_mm=[30, 20]))
    assert _ids(report) == ["board_size", "board_size"]
    assert report.counts["board_oversize"] == 2
    long_side = report.findings[0]
    assert (long_side.measured, long_side.threshold) == (40.0, 30.0)
    assert long_side.margin is not None and long_side.margin < 0


def test_board_that_fits_after_a_quarter_turn_passes(tmp_path: Path) -> None:
    pcb = _pcb(tmp_path, outline=(100, 100, 140, 130))  # 40 × 30，限制 30 × 40 = 转 90° 就放得下
    report = drc_checks(drc_report=_drc(tmp_path), pcb=pcb, spec=_spec(board_max_mm=[30, 40]))
    assert report.passed
    assert drc_checks(drc_report=_drc(tmp_path), pcb=pcb, spec=_spec(board_max_mm=[40, 30])).passed


def test_only_one_side_too_long_gives_one_finding(tmp_path: Path) -> None:
    pcb = _pcb(tmp_path, outline=(100, 100, 150, 120))  # 50 × 20
    report = drc_checks(drc_report=_drc(tmp_path), pcb=pcb, spec=_spec(board_max_mm=[40, 40]))
    assert _ids(report) == ["board_size"] and "长边" in report.findings[0].message


def test_no_board_limit_in_the_spec_means_no_size_check(tmp_path: Path) -> None:
    pcb = _pcb(tmp_path, outline=(0, 0, 500, 500))
    assert drc_checks(drc_report=_drc(tmp_path), pcb=pcb, spec=_spec()).passed
    assert drc_checks(drc_report=_drc(tmp_path), pcb=pcb, spec=None).passed


def test_board_outline_reads_arcs_circles_and_ignores_footprint_graphics(tmp_path: Path) -> None:
    text = (
        '(kicad_pcb (gr_circle (center 10 10) (end 15 10) (layer "Edge.Cuts"))'
        ' (gr_rect (start 0 0) (end 4 4) (layer "F.SilkS"))'
        ' (footprint "x" (fp_line (start -50 -50) (end 50 50) (layer "Edge.Cuts"))))'
    )
    pcb = tmp_path / "b.kicad_pcb"
    pcb.write_text(text, encoding="utf-8")
    assert read_pcb(pcb).outline == (5.0, 5.0, 15.0, 15.0)


def test_a_trace_narrower_than_ipc_2221_fails(tmp_path: Path) -> None:
    need = min_width_mm(2.0)  # 0.78 mm
    pcb = _pcb(
        tmp_path,
        [
            ("VIN", "F.Cu", 0.5, (101, 101), (110, 101)),
            ("VIN", "F.Cu", 1.0, (110, 101), (120, 101)),
        ],
    )
    report = drc_checks(drc_report=_drc(tmp_path), pcb=pcb, currents={"VIN": 2.0})
    assert _ids(report) == ["trace_width"]
    finding = report.findings[0]
    assert finding.measured == 0.5 and finding.threshold == pytest.approx(need)
    assert finding.margin == pytest.approx((0.5 - need) / need)
    assert finding.detail["layer"] == "F.Cu" and finding.detail["current_a"] == 2.0
    assert report.counts["width_failed"] == 1


def test_a_trace_exactly_as_wide_as_needed_passes(tmp_path: Path) -> None:
    need = round(min_width_mm(2.0) + 0.0005, 3)
    pcb = _pcb(tmp_path, [("VIN", "B.Cu", need, (101, 101), (110, 101))])
    assert drc_checks(drc_report=_drc(tmp_path), pcb=pcb, currents={"VIN": 2.0}).passed


def test_inner_layers_need_wider_traces(tmp_path: Path) -> None:
    width = 1.0  # 外层 2 A 够了（0.78 mm），内层要 2.03 mm
    outer = _pcb(tmp_path, [("VIN", "F.Cu", width, (101, 101), (110, 101))])
    assert drc_checks(drc_report=_drc(tmp_path), pcb=outer, currents={"VIN": 2.0}).passed
    inner = _pcb(tmp_path, [("VIN", "In1.Cu", width, (101, 101), (110, 101))])
    report = drc_checks(drc_report=_drc(tmp_path), pcb=inner, currents={"VIN": 2.0})
    assert _ids(report) == ["trace_width"] and report.findings[0].detail["layer"] == "In1.Cu"


def test_copper_thickness_and_temperature_rise_come_from_the_caller(tmp_path: Path) -> None:
    pcb = _pcb(tmp_path, [("VIN", "F.Cu", 0.5, (101, 101), (110, 101))])
    kwargs = {"drc_report": _drc(tmp_path), "pcb": pcb, "currents": {"VIN": 2.0}}
    assert not drc_checks(**kwargs).passed  # 1 oz、10 °C 要 0.78 mm
    assert drc_checks(**kwargs, copper_um=70.0).passed  # 2 oz 只要 0.39 mm
    assert drc_checks(**kwargs, temp_rise_c=40.0).passed  # 温升放宽到 40 °C 只要 0.37 mm


def test_nets_without_current_data_are_not_checked_but_counted(tmp_path: Path) -> None:
    pcb = _pcb(tmp_path, [("VIN", "F.Cu", 0.1, (101, 101), (110, 101))])
    # 没给电流：不查
    report = drc_checks(drc_report=_drc(tmp_path), pcb=pcb)
    assert report.passed and report.counts["width_checked"] == 0
    # 给了电流但板上没有这个网络的走线：算没查
    report = drc_checks(drc_report=_drc(tmp_path), pcb=pcb, currents={"GND": 3.0})
    assert report.passed and report.counts["width_unchecked"] == 1


def test_design_net_names_are_mapped_to_the_pcb_net_names(tmp_path: Path) -> None:
    pcb = _pcb(tmp_path, [("Net-(D1-A)", "F.Cu", 0.1, (101, 101), (110, 101))])
    actual = {("R1", "1"): "VIN", ("R1", "2"): "Net-(D1-A)", ("D1", "2"): "Net-(D1-A)"}
    names = pcb_net_names(_design(), actual)
    assert names == {"VIN": "VIN", "LED_A": "Net-(D1-A)"}
    report = drc_checks(
        drc_report=_drc(tmp_path), pcb=pcb, net_names=names, currents={"LED_A": 2.0, "NOPE": 1.0}
    )
    assert _ids(report) == ["trace_width"]
    assert report.findings[0].detail["pcb_net"] == "Net-(D1-A)"
    assert report.counts["width_unchecked"] == 1, "NOPE 不在网表里"


def test_a_net_split_in_the_netlist_is_left_out_of_the_mapping() -> None:
    actual = {("R1", "1"): "VIN", ("R1", "2"): "A", ("D1", "2"): "B"}
    assert pcb_net_names(_design(), actual) == {"VIN": "VIN"}


def test_older_pcb_files_use_net_numbers(tmp_path: Path) -> None:
    pcb = _pcb(
        tmp_path, [("VIN", "F.Cu", 0.1, (101, 101), (110, 101))], declare_nets=["GND", "VIN"]
    )
    info = read_pcb(pcb)
    assert [t.net for t in info.tracks] == ["VIN"]
    assert not drc_checks(drc_report=_drc(tmp_path), pcb=pcb, currents={"VIN": 2.0}).passed


def test_short_segments_can_be_left_out_of_the_width_check(tmp_path: Path) -> None:
    """布线器在焊盘旁边会把线收窄一小段。"""
    pcb = _pcb(
        tmp_path,
        [
            ("VIN", "F.Cu", 0.2, (101, 101), (101.3, 101)),
            ("VIN", "F.Cu", 1.0, (101.3, 101), (111, 101)),
        ],
    )
    kwargs = {"drc_report": _drc(tmp_path), "pcb": pcb, "currents": {"VIN": 2.0}}
    assert not drc_checks(**kwargs).passed
    assert drc_checks(**kwargs, min_segment_mm=1.0).passed


def test_net_currents_take_the_larger_of_spec_and_simulation() -> None:
    sim = make_report(
        "sim",
        [],
        device_stress={"R1": DeviceStress(v_peak=1, i_peak=1, p_peak=1)},
        net_current_peak={"VIN": 1.5, "LED_A": 0.01},
    )
    spec = _spec()  # VIN 的 i_max = 2.0
    assert estimate_net_currents(spec, sim) == {"LED_A": 0.01, "VIN": 2.0}
    assert estimate_net_currents(None, sim) == {"LED_A": 0.01, "VIN": 1.5}
    assert estimate_net_currents(spec, None) == {"VIN": 2.0}
    assert estimate_net_currents(None, None) == {}
