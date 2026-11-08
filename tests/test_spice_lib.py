"""SPICE 模型文件扫描：哪些 .subckt、哪些 .model，端子顺序。"""

from __future__ import annotations

from pathlib import Path

from eda_agent.config import REPO_ROOT
from eda_agent.sim.spice_lib import MODEL_ELEMENTS, parse_library, scan_library

SAMPLE = """* comment line
.SUBCKT LM358       1   2  99  50  28
R1 1 3 500K
.MODEL DX D(IS=1E-15)
.MODEL QX PNP(BF=1.111E3)
.ENDS
.subckt wrapper OUT1 IN1N params: gain=2 vcc=5
XA OUT1 IN1N lm358
.ends wrapper
.model LED_RED_GENERIC D(IS=2.9e-18 N=2 RS=2)   ; inline comment
.MODEL big NPN (IS=5.07F NF=1
+ BF=100)
.model IRF540 vdmos nchan VTO=3.5
.model IRLZ44N VDMOS(VTO=1.5 KP=16.8)
"""


def test_subcircuits_keep_their_port_order() -> None:
    entries = parse_library(SAMPLE)
    assert entries["lm358"].kind == "subckt"
    assert entries["lm358"].ports == ("1", "2", "99", "50", "28")
    assert entries["lm358"].name == "LM358"
    assert entries["wrapper"].ports == ("OUT1", "IN1N"), "params: 之后的不是端子"


def test_model_cards_report_their_type_in_lower_case() -> None:
    entries = parse_library(SAMPLE)
    assert entries["led_red_generic"].model_type == "d"
    assert entries["big"].model_type == "npn"
    assert entries["irf540"].model_type == "vdmos"
    assert entries["irlz44n"].model_type == "vdmos"


def test_models_inside_a_subcircuit_are_private_to_it() -> None:
    entries = parse_library(SAMPLE)
    assert "dx" not in entries and "qx" not in entries


def test_lookup_is_case_insensitive_and_the_first_definition_wins() -> None:
    entries = parse_library(".model A D(IS=1)\n.MODEL a NPN(BF=1)\n")
    assert entries["a"].model_type == "d"


def test_the_repository_models_are_found(tmp_path: Path) -> None:
    lm358 = scan_library(REPO_ROOT / "models" / "lm358.lib")
    assert lm358["lm358_dual"].ports == (
        "OUT1",
        "IN1N",
        "IN1P",
        "VNEG",
        "IN2P",
        "IN2N",
        "OUT2",
        "VPOS",
    )
    ua555 = scan_library(REPO_ROOT / "models" / "ua555.lib")
    assert ua555["ne555"].ports == ("GND", "TRIG", "OUT", "RESET", "CTRL", "THRES", "DISCH", "VCC")
    assert ua555["ua555"].ports == ("32", "30", "19", "23", "33", "1", "21")
    assert scan_library(REPO_ROOT / "models" / "irlz44n.lib")["irlz44n"].model_type == "vdmos"
    assert (
        scan_library(REPO_ROOT / "models" / "led_red_generic.lib")["led_red_generic"].model_type
        == "d"
    )
    assert scan_library(REPO_ROOT / "models" / "lm393.lib")["lm393_dual"].ports[-1] == "VCC"


def test_every_supported_model_type_has_terminals() -> None:
    for letter, terminals in MODEL_ELEMENTS.values():
        assert letter in "DQM" and terminals
