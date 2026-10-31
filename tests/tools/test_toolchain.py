"""工具链本身能用（M0）。需要 .tools 里的 KiCad、Freerouting、KiCadRoutingTools。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eda_agent.config import REPO_ROOT, ToolsConfig, load_config
from eda_agent.tools.kicad_cli import KicadCli
from eda_agent.tools.kicad_python import run_kicad_script
from eda_agent.tools.ngspice import run_netlist
from eda_agent.tools.runner import run_tool
from eda_agent.tools.sandbox import sandbox_env

pytestmark = pytest.mark.tools


@pytest.fixture(scope="module")
def tools() -> ToolsConfig:
    return load_config().tools


def test_kicad_cli_version(tools: ToolsConfig, tmp_path: Path) -> None:
    assert KicadCli(tools, log_dir=tmp_path).version() == "10.0.8"


def test_kicad_python_has_pcbnew_and_specctra_functions(tools: ToolsConfig, tmp_path: Path) -> None:
    result = run_kicad_script(tools, "probe.py", [], cwd=tmp_path, log_dir=tmp_path)
    info = json.loads(result.stdout.strip().splitlines()[-1])
    assert info["pcbnew"] == "10.0.8"
    assert info["export_dsn"] and info["import_ses"]


def test_ngspice_led_current_matches_hand_calculation(tools: ToolsConfig, tmp_path: Path) -> None:
    """手算：5 V = 332 Ω × I + 2·Vt·ln(I/IS)，Vt = 25.87 mV、IS = 2.9e-18 A，迭代得 I ≈ 9.49 mA。"""
    model = REPO_ROOT / "models" / "led_red_generic.lib"
    netlist = tmp_path / "led_op.cir"
    netlist.write_text(
        "\n".join(
            [
                "* LED operating point",
                "V1 VIN 0 DC 5",
                "R1 VIN LED_A 330",
                "D1 LED_A 0 LED_RED_GENERIC",
                f".include {model.as_posix()}",
                ".op",
                ".end",
            ]
        ),
        encoding="utf-8",
    )
    sim = run_netlist(tools, netlist, tmp_path / "led_op.json", log_dir=tmp_path)
    assert sim.ok, sim.errors or sim.tool.stderr
    current = -sim.scalar("v1#branch")
    assert 9.3e-3 < current < 9.7e-3


@pytest.mark.parametrize(
    "name,argv",
    [
        ("freerouting", lambda t: [t.freerouting, "--help"]),
        ("kicad-routing-tools", lambda t: [t.krt_python, t.krt_route, "--help"]),
    ],
)
def test_router_cli_starts(tools: ToolsConfig, tmp_path: Path, name: str, argv) -> None:  # type: ignore[no-untyped-def]
    result = run_tool(
        name, argv(tools), cwd=tmp_path, env=sandbox_env(tools), timeout_s=120, log_dir=tmp_path
    )
    assert result.exit_code == 0, result.stderr
