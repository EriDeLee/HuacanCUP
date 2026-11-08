from __future__ import annotations

from pathlib import Path

from eda_agent.gates.schematic_layout import layout_gate

SYMBOL = '(symbol (lib_id "Device:R") (at {x} {y} 0) (property "Reference" "{ref}" (at 0 0 0)))'


def _sch(tmp_path: Path, paper: str, *points: tuple[str, float, float]) -> Path:
    body = "\n".join(SYMBOL.format(ref=r, x=x, y=y) for r, x, y in points)
    path = tmp_path / "t.kicad_sch"
    path.write_text(f'(kicad_sch (paper "{paper}")\n{body})', encoding="utf-8")
    return path


def test_symbols_on_page_pass(tmp_path: Path) -> None:
    gate = layout_gate(_sch(tmp_path, "A4", ("R1", 100.0, 100.0), ("R2", 290.0, 205.0)))
    assert gate.passed
    assert gate.counts == {"symbols": 2, "outside": 0}


def test_symbol_far_off_page_fails(tmp_path: Path) -> None:
    gate = layout_gate(_sch(tmp_path, "A0", ("R1", 500.0, 400.0), ("R7", 600.0, -2710.2)))
    assert not gate.passed
    assert [f.detail["ref"] for f in gate.findings] == ["R7"]


def test_portrait_page_swaps_width_and_height(tmp_path: Path) -> None:
    path = tmp_path / "p.kicad_sch"
    path.write_text(
        '(kicad_sch (paper "A4" portrait)\n' + SYMBOL.format(ref="R1", x=150, y=280) + ")",
        encoding="utf-8",
    )
    assert layout_gate(path).passed


def test_empty_schematic_fails(tmp_path: Path) -> None:
    assert not layout_gate(_sch(tmp_path, "A4")).passed
