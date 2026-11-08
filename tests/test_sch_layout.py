from __future__ import annotations

from pathlib import Path

import pytest

from eda_agent.emit.kicad_sym import Pin
from eda_agent.emit.sch_layout import (
    Drawing,
    Field,
    Label,
    Line,
    NetSpec,
    Note,
    Prims,
    SymbolInst,
    Wire,
    _isotonic,
    column_gaps,
    display_name,
    flip_justify,
    flow_columns,
    infer_kind,
    pin_side,
    rotate,
    snap,
    text_box,
    text_width,
)
from eda_agent.gates.report import GateReport
from eda_agent.gates.schematic_quality import layout_quality, netlist_gate


def _kinds(report: GateReport) -> list[tuple[str, object]]:
    """(finding 的 id, detail 里的 items)：用来判断某种问题出现在了哪些东西上。"""
    return [(f.id, f.detail.get("items")) for f in report.findings]


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


def test_flow_columns_start_at_the_sources_and_put_the_right_edge_last() -> None:
    # a(左边连接器) → b → c；d 没连上任何东西；e 是右边连接器，也连着 c
    links = {("a", "b"), ("b", "c"), ("c", "e")}

    def linked(x: str, y: str) -> bool:
        return (x, y) in links or (y, x) in links

    columns = flow_columns(["a", "b", "c", "d", "e"], ["a"], {"e"}, linked)
    assert columns == [["a"], ["b", "d"], ["c"], ["e"]]


def test_flow_columns_without_sources_start_at_the_first_item() -> None:
    assert flow_columns(["x", "y"], [], set(), lambda a, b: True) == [["x"], ["y"]]


def test_text_boxes_follow_kicad_justification() -> None:
    centered = text_box(10.0, 10.0, "AB")
    assert centered[0] < 10.0 < centered[2]
    left = text_box(10.0, 10.0, "AB", "left")
    assert left[0] == 10.0 and left[2] > 10.0
    right_top = text_box(10.0, 10.0, "AB", "right top")
    assert right_top[2] == pytest.approx(10.0) and right_top[1] >= 9.8
    assert flip_justify("left bottom") == "right bottom"
    assert flip_justify("") == ""


def test_chinese_text_is_estimated_wider_than_latin() -> None:
    assert text_width("AB") == pytest.approx(2 * 1.05)
    assert text_width("电源") == pytest.approx(2 * 1.05 * 1.6)
    assert text_width("") > 0


def test_column_gaps_grow_with_the_nets_that_cross_them() -> None:
    gaps = column_gaps([{"A", "B"}, {"A", "B", "C"}, {"C"}, set()])
    assert gaps[0] > gaps[1] > gaps[2]  # 2 根、1 根、0 根线要穿过
    assert gaps[2] == pytest.approx(1.27 * 8)


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
    assert report.findings[0].id == "symbol_overlap"


def test_quality_gate_fails_when_a_wire_passes_through_a_body() -> None:
    prims = Prims(symbols=[_sym("U1", (0, 0, 10, 10))], wires=[Wire((-5.0, 5.0), (15.0, 5.0))])
    report = layout_quality(_drawing(prims))
    assert not report.passed
    assert report.findings[0].id == "wire_through_symbol"


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


def test_quality_gate_fails_when_a_wire_runs_through_text() -> None:
    """ADR-0014：ldo_3v3 的 +3V3 导线从 U1 的型号文字上穿过，原来的检查没发现。"""
    u1 = _sym("U1", (0, 0, 10, 10), [Field("Value", "AMS1117-3.3", 0.0, -2.0, "left")])
    prims = Prims(symbols=[u1], wires=[Wire((8.0, -5.0), (8.0, 1.0))])
    report = layout_quality(_drawing(prims))
    assert not report.passed
    assert ("wire_through_text", ["U1"]) in _kinds(report)


def test_a_wire_ending_at_its_own_label_or_under_a_note_edge_is_fine() -> None:
    prims = Prims(
        wires=[Wire((0.0, 5.08), (10.16, 5.08)), Wire((20.32, 0.0), (20.32, 5.08))],
        labels=[Label("SDA", 10.16, 5.08, 0), Label("SCL", 20.32, 5.08, 180)],
        notes=[Note("mcu：单片机", 0.0, 7.62)],
    )
    assert layout_quality(_drawing(prims)).passed


def test_quality_gate_fails_when_a_wire_runs_through_a_block_title() -> None:
    prims = Prims(notes=[Note("mcu：单片机", 0.0, 0.0)], wires=[Wire((3.0, -2.0), (3.0, 4.0))])
    assert not layout_quality(_drawing(prims)).passed


def test_quality_gate_fails_when_a_wire_runs_through_a_sheet_name() -> None:
    """根页图纸框的框名是 KiCad 自己画的，不在 notes 里，靠 extra_texts 让检查关看见。"""
    prims = Prims(extra_texts=[("sensor", (10.0, -2.0, 16.0, -0.6))])
    prims.wires.append(Wire((12.0, -5.0), (12.0, 0.0)))
    report = layout_quality(_drawing(prims))
    assert ("wire_through_text", ["sensor"]) in _kinds(report)


def test_quality_gate_fails_when_an_arrow_head_covers_a_sheet_name() -> None:
    """uno_like_board 的 +3V3 箭头从上面扎进 sensor 框，三角正好盖住框名。"""
    prims = Prims(extra_texts=[("sensor", (10.0, -2.0, 16.0, -0.6))])
    prims.lines.append(Line(((11.43, -10.0), (11.43, -2.54), (11.43, -0.3)), True))
    report = layout_quality(_drawing(prims))
    assert ("line_through_text", ["sensor"]) in _kinds(report)


def test_an_arrow_beside_text_is_fine() -> None:
    prims = Prims(extra_texts=[("sensor", (10.0, -2.0, 16.0, -0.6))])
    prims.notes.append(Note("+3V3", 20.32, -13.97))
    prims.lines.append(Line(((19.05, -10.0), (19.05, -2.54), (19.05, -0.3)), True))
    assert layout_quality(_drawing(prims)).passed


def test_an_arrow_head_is_part_of_the_line_outline() -> None:
    line = Line(((0.0, 0.0), (0.0, 10.0)), True)
    head = line.boxes()[-1]
    assert head == pytest.approx((-0.7, 8.2, 0.7, 10.0))
    assert len(Line(((0.0, 0.0), (0.0, 10.0))).boxes()) == 1


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
    assert report.findings[0].id == "net_split"


def test_netlist_gate_fails_on_a_short(tmp_path: Path) -> None:
    exported = _netlist(tmp_path, {"/A": [("R1", "1"), ("R2", "1"), ("R1", "2"), ("R2", "2")]})
    report = netlist_gate(EXPECTED, exported)
    assert not report.passed
    assert {f.id for f in report.findings} == {"short"}


def test_netlist_gate_fails_on_a_missing_pin(tmp_path: Path) -> None:
    exported = _netlist(tmp_path, {"/A": [("R1", "1")], "/B": [("R1", "2"), ("R2", "2")]})
    report = netlist_gate(EXPECTED, exported)
    assert not report.passed
    assert report.findings[0].id == "pin_missing"
