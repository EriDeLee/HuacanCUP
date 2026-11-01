"""原理图画法（ADR-0014，用户 2026-11-01 选定的改法）在真实样例上的效果。

需要 .tools 里的 KiCad 符号库。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eda_agent.config import REPO_ROOT, ToolsConfig, load_config
from eda_agent.emit.sch_layout import SymbolInst
from eda_agent.emit.sch_project import SchematicProject, build_project, write_project
from eda_agent.gates.kicad_reports import erc_gate
from eda_agent.gates.schematic_quality import layout_quality, netlist_gate
from eda_agent.ir.models import load_design
from eda_agent.tools.kicad_cli import KicadCli

pytestmark = pytest.mark.tools

EXAMPLES = sorted((REPO_ROOT / "examples").glob("*/design.json"))


@pytest.fixture(scope="module")
def tools() -> ToolsConfig:
    return load_config().tools


def _project(tools: ToolsConfig, name: str, *, single_page: bool = True) -> SchematicProject:
    design = load_design(REPO_ROOT / "examples" / name / "design.json")
    return build_project(design, name, (tools.kicad_share / "symbols",), single_page=single_page)


def _symbols(project: SchematicProject) -> list[SymbolInst]:
    return [s for page in project.pages for s in page.drawing.prims.symbols]


@pytest.mark.parametrize("design_path", EXAMPLES, ids=lambda p: p.parent.name)
def test_power_arrows_point_up_and_grounds_point_down(
    tools: ToolsConfig, design_path: Path
) -> None:
    project = _project(tools, design_path.parent.name)
    flipped = [s.net for s in _symbols(project) if s.prefix == "#PWR" and s.rot != 0]
    assert not flipped, f"倒着画的电源符号：{flipped}"


@pytest.mark.parametrize("design_path", EXAMPLES, ids=lambda p: p.parent.name)
def test_no_ground_symbol_sits_right_above_a_supply_symbol(
    tools: ToolsConfig, design_path: Path
) -> None:
    """ne555_astable 的 C2、overcurrent_cutoff 的 U1B：地符号正对着下面的电源符号（用户指出）。"""
    power = [s for s in _symbols(_project(tools, design_path.parent.name)) if s.prefix == "#PWR"]
    grounds = [s for s in power if s.lib_id == "power:GND"]
    supplies = [s for s in power if s.lib_id != "power:GND"]
    facing = [
        (g.net, v.net)
        for g in grounds
        for v in supplies
        if abs(g.x - v.x) < 2.6 and 0 < v.y - g.y < 10.0
    ]
    assert not facing


def test_the_feedback_resistor_spans_its_amplifier_and_every_net_is_wired(
    tools: ToolsConfig,
) -> None:
    drawing = _project(tools, "lm358_amp").pages[0].drawing
    syms = {(s.ref, s.unit): s for s in drawing.prims.symbols if not s.prefix}
    amp, r1 = syms[("U1", 1)], syms[("R1", 1)]
    assert amp.core is not None
    above = r1.box[3] <= amp.core[1]
    below = r1.box[1] >= amp.core[3]
    assert above or below, "反馈电阻要横在运放上方或下方，不在输入端旁边"
    middle = (r1.box[0] + r1.box[2]) / 2
    assert amp.core[0] - 2.6 <= middle <= amp.core[2] + 2.6
    assert drawing.route_stats["wired"] == drawing.route_stats["nets"] == 4


def test_a_small_design_with_blocks_is_one_page_with_a_frame_per_block(
    tools: ToolsConfig,
) -> None:
    project = _project(tools, "attiny85_blink")
    assert project.root is None and len(project.pages) == 1
    prims = project.pages[0].drawing.prims
    assert len(prims.frames) == 4
    titles = [n.text for n in prims.notes]
    for block in ("power", "mcu", "isp", "led"):
        assert any(t.startswith(f"{block}：") for t in titles), block
    for frame in prims.frames:  # 每个元件都在某个方框里
        assert frame.x0 < frame.x1 and frame.y0 < frame.y1
    parts = [s for s in prims.symbols if not s.prefix]
    for s in parts:
        assert any(
            f.x0 <= s.box[0] and s.box[2] <= f.x1 and f.y0 <= s.box[1] and s.box[3] <= f.y1
            for f in prims.frames
        ), s.ref


def test_one_page_per_block_has_a_wired_overview_with_rails_and_arrows(
    tools: ToolsConfig, tmp_path: Path
) -> None:
    project = _project(tools, "attiny85_blink", single_page=False)
    assert project.root is not None
    root = project.root
    assert root.prims.labels == [], "框和框之间应该全部连成导线"
    assert root.route_stats["wired"] == root.route_stats["nets"] == 5
    notes = [n.text for n in root.prims.notes]
    assert "电源：+5V（出）、GND" in notes  # 电源插座那一块供 +5V
    assert root.prims.lines and all(ln.arrow for ln in root.prims.lines)
    assert layout_quality(root).passed

    root_file = write_project(project, tmp_path, title="t", date="d")
    kicad = KicadCli(tools, log_dir=tmp_path / "logs")
    kicad.sch_erc(root_file, tmp_path / "erc.json", severity="all")
    kicad.sch_export_netlist(root_file, tmp_path / "x.net")
    assert erc_gate(tmp_path / "erc.json").passed
    assert netlist_gate(project.nets, tmp_path / "x.net").passed
