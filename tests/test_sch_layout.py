from __future__ import annotations

from pathlib import Path

import pytest

from eda_agent.emit.kicad_sym import Pin
from eda_agent.emit.sch_layout import (
    Drawing,
    Field,
    Label,
    NetSpec,
    Prims,
    SymbolInst,
    Wire,
    _isotonic,
    display_name,
    infer_kind,
    pin_side,
    rotate,
    snap,
)
from eda_agent.gates.schematic_quality import layout_quality, netlist_gate


def test_rotate_follows_the_kicad_convention() -> None:
    # 电阻默认竖着，1 脚在上。逆时针转 90 度后 1 脚应该跑到左边，270 度跑到右边。
    assert rotate(0, 3.81, 0) == (0, -3.81)
    assert rotate(0, 3.81, 90) == (-3.81, 0)
    assert rotate(0, 3.81, 180) == (0, 3.81)
    assert rotate(0, 3.81, 270) == (3.81, 0)


def test_pin_side_before_and_after_rotation_and_mirror() -> None:
    left_pin = Pin("1", "A", -5.08, 0.0, 0, 2.54, "input", False)  # 连接点在本体左边
    assert pin_side(left_pin, 0, False) == "left"
    assert pin_side(left_pin, 0, True) == "right"
    assert pin_side(left_pin, 90, False) == "bottom"
    assert pin_side(left_pin, 180, False) == "right"


def test_net_names_and_kinds() -> None:
    assert display_name("/pic_sockets/VCC_PIC") == "VCC_PIC"
    assert display_name("/VPP{slash}MCLR") == "VPP/MCLR"
    for rail in ("GND", "AGND", "/GND", "VSS"):
        assert infer_kind(rail) == "ground"
    for rail in ("VCC", "+5V", "/pic_sockets/VCC_PIC", "VPP", "VIN"):
        assert infer_kind(rail) == "power"
    for signal in ("Net-(U4-FB+)", "/VPP{slash}MCLR", "SCL", "LED_A"):
        assert infer_kind(signal) == "signal"


def test_snap_lands_on_the_grid() -> None:
    assert snap(2.6) == 2.54
    assert snap(-0.5) == -0.0 or snap(-0.5) == 0.0
    assert snap(143.5) == 143.51


def test_isotonic_keeps_order_and_stays_close() -> None:
    assert _isotonic([1, 2, 3], [1, 1, 1]) == [1, 2, 3]
    assert _isotonic([3, 1], [1, 1]) == [2, 2]
    heavy = _isotonic([5, 0, 10], [1e6, 1, 1])  # 第一个点几乎不能动
    assert heavy[0] == pytest.approx(5, abs=1e-3)
    assert heavy == sorted(heavy)


def _sym(
    ref: str, box: tuple[float, float, float, float], fields: list[Field] | None = None
) -> SymbolInst:
    return SymbolInst(
        ref=ref,
        lib_id="X:Y",
        value="",
        footprint="",
        x=0.0,
        y=0.0,
        rot=0,
        mirror=False,
        fields=fields or [],
        box=box,
        core=box,
        pins={},
    )


def _drawing(prims: Prims) -> Drawing:
    return Drawing(prims, "A4", {})


def test_quality_gate_passes_for_separated_parts() -> None:
    prims = Prims(symbols=[_sym("R1", (0, 0, 5, 5)), _sym("R2", (20, 0, 25, 5))])
    report = layout_quality(_drawing(prims))
    assert report.passed
    assert report.counts["failures"] == 0


def test_quality_gate_fails_for_overlapping_parts() -> None:
    prims = Prims(symbols=[_sym("R1", (0, 0, 5, 5)), _sym("R2", (3, 3, 8, 8))])
    report = layout_quality(_drawing(prims))
    assert not report.passed
    assert report.findings[0]["type"] == "symbol_overlap"


def test_quality_gate_fails_when_a_wire_passes_through_a_body() -> None:
    prims = Prims(symbols=[_sym("U1", (0, 0, 10, 10))], wires=[Wire((-5.0, 5.0), (15.0, 5.0))])
    report = layout_quality(_drawing(prims))
    assert not report.passed
    assert report.findings[0]["type"] == "wire_through_symbol"


def test_a_lead_leaving_a_pin_outward_is_fine_but_one_entering_the_body_is_not() -> None:
    sym = _sym("D1", (0, 0, 10, 12))  # 本体外框比引脚还长，像发光二极管的光线箭头
    sym.pins = {"1": (5.0, 10.0)}
    sym.pin_dirs = {"1": (0.0, 1.0)}  # 引脚在下沿，往外是向下
    outward = Prims(symbols=[sym], wires=[Wire((5.0, 10.0), (5.0, 14.0))])
    assert layout_quality(_drawing(outward)).passed
    inward = Prims(symbols=[sym], wires=[Wire((5.0, 10.0), (5.0, 6.0))])
    assert not layout_quality(_drawing(inward)).passed


def test_quality_gate_fails_when_text_lies_on_a_symbol() -> None:
    prims = Prims(symbols=[_sym("U1", (0, 0, 10, 10))], labels=[Label("NET", 2.0, 6.0, 0)])
    assert not layout_quality(_drawing(prims)).passed


def test_crossings_are_counted_but_do_not_fail_the_gate() -> None:
    prims = Prims(wires=[Wire((0.0, 5.0), (10.0, 5.0)), Wire((5.0, 0.0), (5.0, 10.0))])
    report = layout_quality(_drawing(prims))
    assert report.passed
    assert report.counts["wire_crossings"] == 1


def _netlist(tmp_path: Path, nets: dict[str, list[tuple[str, str]]]) -> Path:
    body = "".join(
        f'(net (code "{i}") (name "{name}")'
        + "".join(f'(node (ref "{r}") (pin "{p}"))' for r, p in nodes)
        + ")"
        for i, (name, nodes) in enumerate(nets.items(), start=1)
    )
    path = tmp_path / "x.net"
    path.write_text(f"(export (nets {body}))", encoding="utf-8")
    return path


EXPECTED = [
    NetSpec("A", (("R1", "1"), ("R2", "1"))),
    NetSpec("B", (("R1", "2"), ("R2", "2"))),
]


def test_netlist_gate_passes_when_nets_match(tmp_path: Path) -> None:
    exported = _netlist(
        tmp_path, {"/A": [("R1", "1"), ("R2", "1")], "/B": [("R1", "2"), ("R2", "2")]}
    )
    assert netlist_gate(EXPECTED, exported).passed


def test_netlist_gate_fails_on_a_split_net(tmp_path: Path) -> None:
    exported = _netlist(
        tmp_path,
        {"/A": [("R1", "1")], "/A2": [("R2", "1")], "/B": [("R1", "2"), ("R2", "2")]},
    )
    report = netlist_gate(EXPECTED, exported)
    assert not report.passed
    assert report.findings[0]["type"] == "net_split"


def test_netlist_gate_fails_on_a_short(tmp_path: Path) -> None:
    exported = _netlist(tmp_path, {"/A": [("R1", "1"), ("R2", "1"), ("R1", "2"), ("R2", "2")]})
    report = netlist_gate(EXPECTED, exported)
    assert not report.passed
    assert {f["type"] for f in report.findings} == {"short"}


def test_netlist_gate_fails_on_a_missing_pin(tmp_path: Path) -> None:
    exported = _netlist(tmp_path, {"/A": [("R1", "1")], "/B": [("R1", "2"), ("R2", "2")]})
    report = netlist_gate(EXPECTED, exported)
    assert not report.passed
    assert report.findings[0]["type"] == "pin_missing"
