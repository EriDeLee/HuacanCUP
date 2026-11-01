"""M1 完成标准（PLAN §12）：examples/ 下每个手写 IR 样例都生成可打开、并且和 IR 一致的 KiCad 工程。

需要 .tools 里的 KiCad。检查内容见 eval/m1_examples.py：ERC 无错误、网表与 IR 逐网络一致、
每页版面检查通过、符号都在图纸内、PCB 与原理图一致（这时还没布线）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from eda_agent.config import REPO_ROOT, load_config
from eda_agent.emit.kicad_sym import load_symbol
from eda_agent.trace import TraceWriter

pytestmark = pytest.mark.tools

EXAMPLES = sorted((REPO_ROOT / "examples").glob("*/design.json"))


@pytest.fixture(scope="module")
def runner() -> ModuleType:
    path = REPO_ROOT / "eval" / "m1_examples.py"
    spec = importlib.util.spec_from_file_location("m1_examples", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_there_are_at_least_ten_examples() -> None:
    assert len(EXAMPLES) >= 10


@pytest.mark.parametrize("design_path", EXAMPLES, ids=lambda p: p.parent.name)
def test_example_becomes_a_consistent_kicad_project(
    runner: ModuleType, design_path: Path, tmp_path: Path
) -> None:
    trace = TraceWriter(tmp_path / "trace.jsonl", "test")
    result = runner.check_example(design_path, tmp_path / design_path.parent.name, trace)
    failed = {k: v["findings"][:3] for k, v in result["gates"].items() if not v["passed"]}
    assert result["passed"], failed


def test_multi_unit_and_derived_symbols_come_from_the_real_library() -> None:
    libs = (load_config().tools.kicad_share / "symbols",)
    lm358 = load_symbol("Amplifier_Operational:LM358", libs)
    assert sorted(lm358.units) == [1, 2, 3]
    assert {p.number for p in lm358.units[3].pins} == {"4", "8"}
    assert lm358.units_with_pin("7") == [2]
    # MAX485E 派生自 MAX481E，MAX481E 本身也是派生的：两层都要展平
    max485 = load_symbol("Interface_UART:MAX485E", libs)
    assert {p.number for p in max485.pins} == {str(n) for n in range(1, 9)}
    assert "(extends" not in max485.raw
    assert max485.properties["Value"] == "MAX485E"
    assert '(symbol "MAX485E_' in max485.raw, "单元的名字要换成子符号的名字"
