"""版面引擎端到端：LED 小电路画成 .kicad_sch，再让 KiCad 自己检查（需要 .tools 里的 KiCad）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from eda_agent.config import ToolsConfig, load_config
from eda_agent.emit.sch_layout import NetSpec, PartSpec, build_drawing
from eda_agent.emit.sch_writer import render_schematic
from eda_agent.gates.kicad_reports import erc_gate
from eda_agent.gates.schematic_layout import layout_gate
from eda_agent.gates.schematic_quality import layout_quality, netlist_gate
from eda_agent.tools.kicad_cli import KicadCli

pytestmark = pytest.mark.tools

PARTS = [
    PartSpec("J1", "Connector_Generic:Conn_01x02", "5V_IN", role="connector"),
    PartSpec("R1", "Device:R", "330"),
    PartSpec("D1", "Device:LED", "Red"),
]
NETS = [
    NetSpec("VIN", (("J1", "1"), ("R1", "1")), supply=True),
    NetSpec("LED_A", (("R1", "2"), ("D1", "2"))),
    NetSpec("GND", (("D1", "1"), ("J1", "2")), supply=True),
]


@pytest.fixture(scope="module")
def tools() -> ToolsConfig:
    return load_config().tools


def test_led_sheet_passes_every_check(tools: ToolsConfig, tmp_path: Path) -> None:
    drawing = build_drawing(PARTS, NETS, (tools.kicad_share / "symbols",))
    sch = tmp_path / "led.kicad_sch"
    sch.write_text(
        render_schematic(drawing, project="led", title="led", date="2026-10-31"), encoding="utf-8"
    )

    kicad = KicadCli(tools, log_dir=tmp_path / "logs")
    erc_json = tmp_path / "erc.json"
    kicad.sch_erc(sch, erc_json, severity="all")
    netlist = tmp_path / "led.net"
    kicad.sch_export_netlist(sch, netlist)

    assert erc_gate(erc_json).passed, erc_gate(erc_json).findings
    assert netlist_gate(NETS, netlist).passed, netlist_gate(NETS, netlist).findings
    assert layout_quality(drawing).passed, layout_quality(drawing).findings
    assert layout_gate(sch).passed


def test_output_is_reproducible(tools: ToolsConfig) -> None:
    libs = (tools.kicad_share / "symbols",)
    first = render_schematic(build_drawing(PARTS, NETS, libs), project="led", title="t", date="d")
    second = render_schematic(build_drawing(PARTS, NETS, libs), project="led", title="t", date="d")
    assert first == second
