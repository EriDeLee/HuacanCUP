"""G1（IR 校验 + 自写规则）在 examples/ 的 11 个样例上的回归结果。
AGENTS.md：改判定逻辑要附样例回归。

需要 KiCad 自带的符号库和封装库（.tools/kicad/share，Linux 上是 /usr/share/kicad）；
ERC 那一步由 tests/tools/test_examples.py 和冒烟覆盖。

已知该过的必须过。uno_like_board 过不了是样例本身的问题（review-queue D14）：
CH340C 的 CTS、DSR、RI、DCD 四个输入脚和 ATmega328P 的 ADC6、ADC7 两个输入脚都没接，也没标 NC。
规则没有为了让它过去而放宽，所以这里把它的失败集合钉住：规则变了、样例变了，都要有意识地改这里。
"""

from __future__ import annotations

import pytest

from eda_agent.config import REPO_ROOT, load_config
from eda_agent.gates.static import static_gate
from eda_agent.ir.models import load_design
from eda_agent.ir.spec import load_spec

pytestmark = pytest.mark.tools

EXAMPLES = sorted((REPO_ROOT / "examples").glob("*/design.json"))
KNOWN_SAMPLE_PROBLEMS = {
    "uno_like_board": {
        ("rule.dangling_pin", "U1.19"),
        ("rule.dangling_pin", "U1.22"),
        ("rule.dangling_pin", "U2.9"),
        ("rule.dangling_pin", "U2.10"),
        ("rule.dangling_pin", "U2.11"),
        ("rule.dangling_pin", "U2.12"),
    }
}


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.parent.name)
def test_example_static_result(path) -> None:  # type: ignore[no-untyped-def]
    cfg = load_config()
    report = static_gate(
        load_design(path),
        None,
        symbol_dirs=(cfg.tools.kicad_share / "symbols",),
        footprint_dir=cfg.tools.kicad_share / "footprints",
        derating=cfg.gates.derating,
    )
    found = {(f.id, f"{f.detail['ref']}.{f.detail['pin']}") for f in report.findings}
    assert found == KNOWN_SAMPLE_PROBLEMS.get(path.parent.name, set()), report.findings
    assert report.passed == (path.parent.name not in KNOWN_SAMPLE_PROBLEMS)


def test_there_are_eleven_examples() -> None:
    assert len(EXAMPLES) == 11


SPEC_FOLDERS = sorted(
    [
        *(REPO_ROOT / "examples").glob("*/spec.json"),
        *(REPO_ROOT / "tests" / "circuits").glob("*/spec.json"),
    ]
)


@pytest.mark.parametrize("path", SPEC_FOLDERS, ids=lambda p: p.parent.name)
def test_every_spec_maps_onto_its_design_and_passes_g1(path) -> None:  # type: ignore[no-untyped-def]
    """接口名 = 网络名（D13），测量里的网络、位号、引脚都找得到，额定值第一轮也过。"""
    cfg = load_config()
    report = static_gate(
        load_design(path.parent / "design.json"),
        load_spec(path),
        symbol_dirs=(cfg.tools.kicad_share / "symbols",),
        footprint_dir=cfg.tools.kicad_share / "footprints",
        derating=cfg.gates.derating,
    )
    assert report.passed, [f.message for f in report.findings]


def test_the_five_hand_circuits_and_the_smoke_circuit_all_have_a_spec() -> None:
    names = {p.parent.name for p in SPEC_FOLDERS}
    assert names == {
        "voltage_divider",
        "rc_lowpass",
        "ne555_astable",
        "lm358_amp",
        "overcurrent_cutoff",
        "led",  # 冒烟用的电路：有 spec，G2、G3 在冒烟里才有东西可查
    }
