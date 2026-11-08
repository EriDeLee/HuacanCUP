"""M2 完成标准（PLAN §12）的摆放部分：examples/ 的 11 个样例上元件不重叠、不出板、连接器贴边。

需要 KiCad（pcbnew 读几何、写位置，kicad-cli 跑 DRC）。
检查用的是摆好以后用 pcbnew 重新读出来的位置，不是算法自己的账；
另外 KiCad 自己的 DRC 也不许报 courtyard 重叠等违规（未布线，所以「没连上」不算）。
为了省时间，测试里只摆一个种子；参数就是默认的（每个元件退火 1500 步），
位号文字的框算进占位以后 500 步不够把 ldo_3v3 摆得比按行排短。
完整的 rows / auto 对比表见 eval/m2_placement.py。
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from eda_agent.config import REPO_ROOT, PlacementConfig, load_config
from eda_agent.emit.kicad_project import EmittedProject, emit_kicad_project
from eda_agent.flow import copy_project
from eda_agent.gates.kicad_reports import drc_gate
from eda_agent.ir.models import load_design
from eda_agent.placement.geometry import load_geometry
from eda_agent.placement.run import PlacementReport, place_board
from eda_agent.tools.kicad_cli import KicadCli
from eda_agent.tools.runner import ToolError
from eda_agent.trace import TraceWriter, read_trace

pytestmark = pytest.mark.tools

EXAMPLES = sorted((REPO_ROOT / "examples").glob("*/design.json"))
FAST = PlacementConfig(iterations_per_part=1500)


def emit(path: Path, out: Path) -> EmittedProject:
    return emit_kicad_project(
        path,
        out / "kicad",
        path.parent.name,
        load_config().tools,
        log_dir=out / "logs",
        date=f"{datetime.now():%Y-%m-%d}",
    )


def place(path: Path, out: Path, mode: str, trace: TraceWriter | None = None) -> PlacementReport:
    emitted = emit(path, out / "base")
    pcb = copy_project(emitted.pcb, out / mode, path.parent.name)
    return place_board(
        load_config().tools,
        pcb,
        load_design(path),
        FAST,
        mode=mode,
        work_dir=out / mode / "placement",
        log_dir=out / "logs",
        trace=trace,
    )


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.parent.name)
def test_example_placement_is_legal_and_shorter_than_rows(path: Path, tmp_path: Path) -> None:
    report = place(path, tmp_path, "auto")
    assert report.violations == [], report.violations
    assert report.mode == "auto" and report.seed == FAST.seed
    assert report.hpwl_mm <= report.hpwl_rows_mm, "auto 的总线长不能比按行排更长"

    # 独立于算法的第二道检查：KiCad 自己的 DRC（板子还没布线，所以只看违规和原理图一致性）
    pcb = tmp_path / "auto" / f"{path.parent.name}.kicad_pcb"
    tools = load_config().tools
    drc_json = tmp_path / "drc.json"
    KicadCli(tools, log_dir=tmp_path / "logs").pcb_drc(pcb, drc_json, severity="all")
    gate = drc_gate(drc_json)
    assert gate.counts["violations_errors"] == 0, [f.message for f in gate.findings][:5]
    assert gate.counts["parity_errors"] == 0

    # 板边连接器真的贴着指定的那一边，其余元件都在板框里
    design = load_design(path)
    geom = load_geometry(tmp_path / "auto" / "placement" / "geometry_auto.json")
    x0, y0, x1, y1 = geom.outline
    margin = FAST.edge_margin_mm
    for connector in design.board.edge_connectors:
        fp = geom.footprints[connector.ref]
        cx0, _, cx1, _ = fp.rect(int(fp.rot) % 360)
        assert fp.rot % 360 == 0
        if connector.side == "left":
            assert cx0 == pytest.approx(x0 + margin, abs=2e-3)
        else:
            assert cx1 == pytest.approx(x1 - margin, abs=2e-3)
    for part in design.parts:
        px0, py0, px1, py1 = geom.footprints[part.ref].rect(
            int(geom.footprints[part.ref].rot) % 360
        )
        assert x0 <= px0 and px1 <= x1 and y0 <= py0 and py1 <= y1


def test_rows_mode_leaves_the_board_as_board_build_made_it(tmp_path: Path) -> None:
    path = REPO_ROOT / "examples" / "ldo_3v3" / "design.json"
    emitted = emit(path, tmp_path / "base")
    before = emitted.pcb.read_bytes()
    pcb = copy_project(emitted.pcb, tmp_path / "rows", "ldo_3v3")
    report = place_board(
        load_config().tools,
        pcb,
        load_design(path),
        FAST,
        mode="rows",
        work_dir=tmp_path / "rows" / "placement",
        log_dir=tmp_path / "logs",
    )
    assert report.mode == "rows" and report.seed is None and report.hpwl_mm == report.hpwl_rows_mm
    assert pcb.read_bytes() == before


def test_a_failing_placement_leaves_a_fail_event_and_raises(tmp_path: Path) -> None:
    trace = TraceWriter(tmp_path / "trace.jsonl", "placement-test")
    with pytest.raises(ToolError):
        place_board(
            load_config().tools,
            tmp_path / "missing.kicad_pcb",
            load_design(REPO_ROOT / "examples" / "led" / "design.json"),
            FAST,
            mode="auto",
            work_dir=tmp_path / "placement",
            log_dir=tmp_path / "logs",
            trace=trace,
        )
    events = [e for e in read_trace(tmp_path / "trace.jsonl") if e.get("name") == "placement"]
    assert [e["status"] for e in events] == ["start", "fail"]
    assert events[-1]["detail"]["error"]


def test_same_seed_gives_the_same_board_and_the_seed_goes_into_the_trace(tmp_path: Path) -> None:
    path = REPO_ROOT / "examples" / "led" / "design.json"
    trace = TraceWriter(tmp_path / "trace.jsonl", "placement-test")
    first = place(path, tmp_path / "a", "auto", trace)
    second = place(path, tmp_path / "b", "auto")
    geom_a = json.loads(
        (tmp_path / "a" / "auto" / "placement" / "geometry_auto.json").read_text("utf-8")
    )
    geom_b = json.loads(
        (tmp_path / "b" / "auto" / "placement" / "geometry_auto.json").read_text("utf-8")
    )
    assert geom_a["footprints"] == geom_b["footprints"]
    assert first.hpwl_mm == second.hpwl_mm
    events = [e for e in read_trace(tmp_path / "trace.jsonl") if e.get("name") == "placement"]
    assert [e["status"] for e in events] == ["start", "ok"]
    detail = events[-1]["detail"]
    assert detail["seed"] == FAST.seed and detail["mode"] == "auto" and detail["violations"] == []
    assert detail["iterations"] == FAST.iterations_per_part * 3
    shutil.rmtree(tmp_path / "a", ignore_errors=True)
