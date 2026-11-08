"""自动摆放接进流水线：读板上的封装几何 → 算 → 写回，并留下前后对比（ADR-0018）。

    板子（board_build.py 按行排好的）
      → kicad_side/footprint_geometry.py  读几何，写 geometry_rows.json
      → auto_place                        算位置（mode 为 auto 时）
      → kicad_side/apply_placement.py     写回，得到新板子
      → kicad_side/footprint_geometry.py  重新读一遍，写 geometry_<mode>.json，用它做检查和算线长
最后的检查和线长都用 pcbnew 重新读出来的位置，不信算法自己说的：它们是独立于算法的。
mode 为 rows 时不动板子（原来按行排的就是结果），只读几何、算线长，让两种方式可以用同一把尺子比。
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from eda_agent.config import PlacementConfig, ToolsConfig
from eda_agent.ir.models import Design
from eda_agent.placement import PlacementResult, auto_place, board_hpwl, check_board
from eda_agent.placement.geometry import BoardGeom, load_geometry
from eda_agent.tools.kicad_python import run_kicad_script
from eda_agent.trace import TraceWriter

MODES = ("auto", "rows")


@dataclass
class PlacementReport:
    mode: str
    seed: int | None
    hpwl_mm: float  # 最后板子上的 HPWL（pcbnew 重读的位置）
    hpwl_rows_mm: float  # 按行排的 HPWL
    violations: list[str]  # 最后板子上的违规（空 = 合格）
    seconds: float
    method: str = ""
    iterations: int = 0
    energy: dict[str, float] = field(default_factory=dict)
    scores: list[dict[str, float]] = field(default_factory=list)
    geometry_json: str = ""
    pcb: str = ""

    @property
    def ok(self) -> bool:
        return not self.violations


def read_geometry(
    tools: ToolsConfig,
    pcb: Path,
    out: Path,
    *,
    log_dir: Path,
    trace: TraceWriter | None = None,
) -> BoardGeom:
    run_kicad_script(
        tools,
        "footprint_geometry.py",
        ["--pcb", pcb.absolute(), "--out", out.absolute()],
        cwd=out.parent,
        log_dir=log_dir,
        trace=trace,
    )
    return load_geometry(out)


def apply_positions(
    tools: ToolsConfig,
    pcb_in: Path,
    pcb_out: Path,
    result: PlacementResult,
    *,
    work_dir: Path,
    log_dir: Path,
    trace: TraceWriter | None = None,
) -> None:
    spec = work_dir / "placement.json"
    spec.write_text(
        json.dumps(
            {
                "parts": {
                    ref: {"x_mm": x, "y_mm": y, "rot_deg": rot}
                    for ref, (x, y, rot) in sorted(result.positions.items())
                }
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    run_kicad_script(
        tools,
        "apply_placement.py",
        ["--pcb", pcb_in.absolute(), "--placement", spec.absolute(), "--out", pcb_out.absolute()],
        cwd=work_dir,
        log_dir=log_dir,
        trace=trace,
    )


def place_board(
    tools: ToolsConfig,
    pcb: Path,
    design: Design,
    cfg: PlacementConfig,
    *,
    mode: str | None = None,
    seed: int | None = None,
    work_dir: Path,
    log_dir: Path,
    trace: TraceWriter | None = None,
    branch: int | None = None,
) -> PlacementReport:
    """板子 pcb 是按行排好的。mode 缺省用 cfg.mode。auto：原地改 pcb 的摆放；rows：不改。

    给了 trace 就记一个叫 placement 的步骤：开始、结束（ok / fail，detail 里有种子、线长、违规），
    出错也记一条 fail 再抛出去。所以调用方不用再包一层。"""
    mode = mode or cfg.mode
    if mode not in MODES:
        raise ValueError(f"不认识的摆放方式：{mode}（可选 {MODES}）")
    extra: dict[str, Any] = {} if branch is None else {"branch": branch}
    if trace is not None:
        trace.emit("step", name="placement", status="start", detail={"mode": mode}, **extra)
    try:
        report = _place(tools, pcb, design, cfg, mode, seed, work_dir, log_dir, trace)
    except Exception as exc:
        if trace is not None:
            detail = {"mode": mode, "error": str(exc)[-2000:]}
            trace.emit("step", name="placement", status="fail", detail=detail, **extra)
        raise
    if trace is not None:
        trace.emit(
            "step",
            name="placement",
            status="ok" if report.ok else "fail",
            detail={k: v for k, v in asdict(report).items() if k != "pcb"},
            **extra,
        )
    return report


def _place(
    tools: ToolsConfig,
    pcb: Path,
    design: Design,
    cfg: PlacementConfig,
    mode: str,
    seed: int | None,
    work_dir: Path,
    log_dir: Path,
    trace: TraceWriter | None,
) -> PlacementReport:
    start = time.monotonic()
    work_dir.mkdir(parents=True, exist_ok=True)
    rows_json = work_dir / "geometry_rows.json"
    rows = read_geometry(tools, pcb, rows_json, log_dir=log_dir, trace=trace)
    rows_hpwl = board_hpwl(design, rows, cfg)

    result: PlacementResult | None = None
    final_json = rows_json
    if mode == "auto":
        result = auto_place(design, rows, cfg, seed)
        placed = pcb.with_name(pcb.stem + "_placed.kicad_pcb")
        apply_positions(tools, pcb, placed, result, work_dir=work_dir, log_dir=log_dir, trace=trace)
        placed.replace(pcb)
        # pcbnew 存板子时顺手在旁边写的工程文件；真正的工程文件是 pcb 同名的那个，没有被动过
        for suffix in (".kicad_pro", ".kicad_prl"):
            placed.with_suffix(suffix).unlink(missing_ok=True)
        final_json = work_dir / "geometry_auto.json"
        final = read_geometry(tools, pcb, final_json, log_dir=log_dir, trace=trace)
    else:
        final = rows
    return PlacementReport(
        mode=mode,
        seed=result.seed if result else None,
        hpwl_mm=board_hpwl(design, final, cfg),
        hpwl_rows_mm=rows_hpwl,
        violations=check_board(design, final, cfg),
        seconds=round(time.monotonic() - start, 2),
        method=result.method if result else "rows",
        iterations=result.iterations if result else 0,
        energy=result.energy if result else {},
        scores=result.scores if result else [],
        geometry_json=final_json.name,
        pcb=str(pcb),
    )
