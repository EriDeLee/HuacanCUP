"""自动摆放（PLAN S7、ADR-0018）：布线之前，按功能块分区、按连接关系靠拢、信号按流向排，
程序打分、模拟退火优化。不调用模型，确定性：同样的输入和种子得到同样的结果，种子写进 trace。

    geometry = 板上的封装几何（kicad_side/footprint_geometry.py 用 pcbnew 读出来）
    result   = auto_place(design, geometry, cfg.placement)        # 算出每个元件的位置和朝向
    问题 = 放不下 → PlacementError，不会返回出板的摆放

读封装几何和把位置写回板子在 kicad_side/（ADR-0005：只用标准库和 pcbnew）；
这里只做算法，可以用 numpy。
placement.mode = rows 时不用这个包，板子保持 kicad_side/board_build.py 按行排的结果（用来对比）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from eda_agent.config import PlacementConfig
from eda_agent.ir.models import Design
from eda_agent.placement.anneal import anneal
from eda_agent.placement.geometry import ANGLES, BoardGeom
from eda_agent.placement.legalize import legalize
from eda_agent.placement.problem import F64, I64, PlacementError, Problem, build_problem
from eda_agent.placement.score import energy, hpwl, violations

__all__ = [
    "PlacementError",
    "PlacementResult",
    "auto_place",
    "board_hpwl",
    "check_board",
    "feasibility_error",
]


@dataclass(frozen=True)
class PlacementResult:
    positions: dict[str, tuple[float, float, int]]  # 位号 → (x mm, y mm, 角度)，封装原点的位置
    seed: int
    restarts: int
    iterations: int
    method: str  # anneal / pushed / first_fit：合法化走到了哪一档
    hpwl_mm: float
    energy: dict[str, float] = field(default_factory=dict)
    scores: list[dict[str, float]] = field(default_factory=list)  # 每次重来（restart）的种子和分数


def feasibility_error(p: Problem) -> str | None:
    """一眼就放不下的情况：有元件比允许的区域还大，或者元件面积加起来比区域还大。"""
    rx0, ry0, rx1, ry1 = p.region
    width, height = rx1 - rx0, ry1 - ry0
    if width <= 0 or height <= 0:
        return f"板子太小：留出板边的间距以后没有可用的区域（{width:.1f} × {height:.1f} mm）"
    total = 0.0
    for i, ref in enumerate(p.refs):
        sizes = [
            (
                p.courtyard[i, k, 2] - p.courtyard[i, k, 0],
                p.courtyard[i, k, 3] - p.courtyard[i, k, 1],
            )
            for k in range(4)
        ]
        if not any(w <= width + 1e-9 and h <= height + 1e-9 for w, h in sizes):
            w, h = sizes[0]
            return (
                f"{ref} 放不下：courtyard {w:.1f} × {h:.1f} mm，"
                f"可用区域 {width:.1f} × {height:.1f} mm"
            )
        total += min((w + p.gap) * (h + p.gap) for w, h in sizes)
    if total > width * height:
        return (
            f"放不下：元件 courtyard（含间隙）加起来 {total:.0f} mm²，"
            f"可用区域只有 {width * height:.0f} mm²"
        )
    return None


def _one_run(p: Problem, seed: int, iterations: int) -> tuple[F64, I64, str, float]:
    run = anneal(p, seed, iterations)
    pos, rot, method = legalize(p, run.pos, run.rot)
    return pos, rot, method, energy(p, pos, rot).total


def auto_place(
    design: Design, geom: BoardGeom, cfg: PlacementConfig, seed: int | None = None
) -> PlacementResult:
    """算出每个元件的位置和朝向。seed 不给就用 cfg.seed。
    cfg.restarts > 1 时用 seed、seed+1… 各摆一次，取分数最好的。"""
    p = build_problem(design, geom, cfg)
    problem = feasibility_error(p)
    if problem is not None:
        raise PlacementError(problem)
    base = cfg.seed if seed is None else seed
    iterations = cfg.iterations_per_part * p.n
    best: tuple[F64, I64, str, float, int] | None = None
    scores: list[dict[str, float]] = []
    for k in range(cfg.restarts):
        pos, rot, method, score = _one_run(p, base + k, iterations)
        scores.append({"seed": float(base + k), "energy": round(score, 6)})
        if best is None or score < best[3]:
            best = (pos, rot, method, score, base + k)
    assert best is not None
    pos, rot, method, _, used = best
    e = energy(p, pos, rot)
    return PlacementResult(
        positions={
            ref: (round(float(pos[i, 0]), 3), round(float(pos[i, 1]), 3), ANGLES[int(rot[i])])
            for i, ref in enumerate(p.refs)
        },
        seed=used,
        restarts=cfg.restarts,
        iterations=iterations,
        method=method,
        hpwl_mm=round(e.hpwl, 3),
        energy=e._asdict(),
        scores=scores,
    )


def board_hpwl(design: Design, geom: BoardGeom, cfg: PlacementConfig) -> float:
    """板子上现在的摆放的总线长 HPWL（mm）：按焊盘中心算，所有两个焊盘以上的网络。"""
    p = build_problem(design, geom, cfg)
    return round(hpwl(p, p.init_pos, p.init_rot), 3)


def check_board(design: Design, geom: BoardGeom, cfg: PlacementConfig) -> list[str]:
    """用 pcbnew 重新读出来的几何检查：courtyard 重叠（含最小间隙）、
    出允许的区域、板边连接器没贴边。空列表 = 合格。"""
    p = build_problem(design, geom, cfg)
    return violations(p, p.init_pos, p.init_rot, tolerance=2e-3)


def positions_array(result: PlacementResult, refs: list[str]) -> tuple[F64, I64]:
    pos = np.array([result.positions[r][:2] for r in refs], dtype=float)
    rot = np.array([ANGLES.index(result.positions[r][2]) for r in refs], dtype=np.intp)
    return pos, rot
