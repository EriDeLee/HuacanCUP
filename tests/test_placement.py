"""自动摆放（eda_agent/placement/）：打分、合法化、确定性、放不下就报错。

不需要 KiCad：封装几何用手写的矩形和焊盘（旋转的算法和 pcbnew 一致：正角度在屏幕上逆时针，
y 向下，所以 (x, y) 转 90 度变成 (y, -x)）。
pcbnew 读出来的真几何在 tests/tools/test_placement_examples.py 里测。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from eda_agent.config import PlacementConfig, PlacementWeights, load_config
from eda_agent.ir.models import Design
from eda_agent.placement import (
    PlacementError,
    auto_place,
    board_hpwl,
    check_board,
    feasibility_error,
)
from eda_agent.placement.geometry import ANGLES, BoardGeom, FootprintGeom, Shape
from eda_agent.placement.legalize import _first_fit, legalize
from eda_agent.placement.problem import Problem, build_problem
from eda_agent.placement.run import place_board
from eda_agent.placement.score import (
    block_spread,
    edge_term,
    energy,
    flow_error,
    hpwl,
    overlap_depths,
    rects,
    region_excess,
    snap_connectors,
    violations,
)


def _turn(point: tuple[float, float], angle: int) -> tuple[float, float]:
    x, y = point
    return {0: (x, y), 90: (y, -x), 180: (-x, -y), 270: (-y, x)}[angle]


def fp(
    ref: str,
    w: float,
    h: float,
    pads: dict[str, tuple[float, float]] | None = None,
    x: float = 0.0,
    y: float = 0.0,
    rot: int = 0,
    board_only: bool = False,
) -> FootprintGeom:
    """courtyard 以封装原点为中心的 w × h 矩形，焊盘位置相对原点。"""
    pads = pads or {"1": (-w / 2 + 0.5, 0.0), "2": (w / 2 - 0.5, 0.0)}
    shapes = {}
    for angle in ANGLES:
        corners = [_turn(c, angle) for c in ((-w / 2, -h / 2), (w / 2, h / 2))]
        xs, ys = [c[0] for c in corners], [c[1] for c in corners]
        shapes[angle] = Shape(
            courtyard=(min(xs), min(ys), max(xs), max(ys)),
            pads={n: [_turn(p, angle)] for n, p in pads.items()},
        )
    return FootprintGeom(ref, x, y, float(rot), board_only, False, shapes)


def board(width: float, height: float, *footprints: FootprintGeom) -> BoardGeom:
    return BoardGeom(
        outline=(100.0, 100.0, 100.0 + width, 100.0 + height),
        footprints={f.ref: f for f in footprints},
    )


def design_of(
    refs: list[str],
    nets: dict[str, list[str]],
    blocks: dict[str, list[str]] | None = None,
    edges: dict[str, str] | None = None,
) -> Design:
    block_of = {r: b for b, members in (blocks or {}).items() for r in members}
    return Design.model_validate(
        {
            "blocks": [{"id": b, "purpose": b} for b in (blocks or {})],
            "parts": [
                {
                    "ref": r,
                    "symbol": "Device:R",
                    "footprint": "a:b",
                    "value": "1k",
                    **({"block": block_of[r]} if r in block_of else {}),
                }
                for r in refs
            ],
            "nets": [
                {"name": name, "nodes": [n.split(".") for n in nodes]}
                for name, nodes in nets.items()
            ]
            or [{"name": "N", "nodes": [[refs[0], "1"]]}],
            "board": {
                "size_mm": [10, 10],
                "edge_connectors": [{"ref": r, "side": s} for r, s in (edges or {}).items()],
            },
        }
    )


CFG = PlacementConfig(iterations_per_part=400)


def problem_of(design: Design, geom: BoardGeom, cfg: PlacementConfig = CFG) -> Problem:
    return build_problem(design, geom, cfg)


# ---------------------------------------------------------------- 打分


def test_hpwl_is_the_half_perimeter_of_the_pad_bounding_boxes() -> None:
    geom = board(
        60,
        40,
        fp("R1", 4, 2, x=110, y=110),
        fp("R2", 4, 2, x=120, y=115),
        fp("R3", 4, 2, x=130, y=105),
    )
    d = design_of(["R1", "R2", "R3"], {"A": ["R1.2", "R2.1"], "B": ["R1.1", "R2.2", "R3.1"]})
    p = problem_of(d, geom)
    # 焊盘在 x ± 1.5：A = R1.2 (111.5, 110) 与 R2.1 (118.5, 115) → 7 + 5 = 12
    # B = R1.1 (108.5, 110)、R2.2 (121.5, 115)、R3.1 (128.5, 105)
    #   → (128.5 − 108.5) + (115 − 105) = 30
    assert hpwl(p, p.init_pos, p.init_rot) == pytest.approx(12 + 30)
    assert board_hpwl(d, geom, CFG) == pytest.approx(42.0)


def test_nets_with_fewer_than_two_pads_are_ignored() -> None:
    geom = board(60, 40, fp("R1", 4, 2, x=110, y=110), fp("R2", 4, 2, x=120, y=115))
    # A 只有一个焊盘；C 的第二个焊盘（R2 的 9 脚）封装上没有 → 也只剩一个
    d = design_of(["R1", "R2"], {"A": ["R1.1"], "B": ["R1.2", "R2.1"], "C": ["R2.2", "R2.9"]})
    p = problem_of(d, geom)
    assert hpwl(p, p.init_pos, p.init_rot) == pytest.approx(abs(111.5 - 118.5) + 5)


def test_a_rotated_part_moves_its_pads() -> None:
    geom = board(60, 40, fp("R1", 10, 2, x=110, y=110), fp("R2", 4, 2, x=120, y=110))
    d = design_of(["R1", "R2"], {"A": ["R1.2", "R2.1"]})
    p = problem_of(d, geom)
    flat = hpwl(p, p.init_pos, p.init_rot)
    rot = p.init_rot.copy()
    rot[0] = 1  # 转 90 度：R1 的 2 脚从 (4.5, 0) 跑到 (0, -4.5)
    assert hpwl(p, p.init_pos, rot) == pytest.approx(abs(110 - 118.5) + abs(105.5 - 110))
    assert hpwl(p, p.init_pos, rot) != flat


def test_overlap_depth_counts_the_minimum_gap() -> None:
    geom = board(60, 40, fp("R1", 4, 4, x=110, y=110), fp("R2", 4, 4, x=114.5, y=110))
    d = design_of(["R1", "R2"], {"A": ["R1.1", "R2.1"]})
    p = problem_of(d, geom)  # 间隙 1 mm，两个 courtyard 之间只有 0.5 mm
    depth = overlap_depths(p, rects(p, p.init_pos, p.init_rot))
    assert depth[0, 1] == pytest.approx(0.5)
    assert violations(p, p.init_pos, p.init_rot)
    far = p.init_pos.copy()
    far[1, 0] = 115.0  # 刚好 1 mm
    assert overlap_depths(p, rects(p, far, p.init_rot)).max() == pytest.approx(0.0)
    assert not [v for v in violations(p, far, p.init_rot) if "重叠" in v]


def test_mounting_holes_are_obstacles() -> None:
    hole = fp("H1", 8, 8, x=105, y=105, board_only=True)
    geom = board(60, 40, hole, fp("R1", 4, 2, x=107, y=107))
    d = design_of(["R1"], {"A": ["R1.1"]})
    p = problem_of(d, geom)
    assert len(p.obstacles) == 1 and p.refs == ["R1"]
    assert any("障碍" in v for v in violations(p, p.init_pos, p.init_rot))


def test_region_edge_and_block_terms() -> None:
    geom = board(40, 30, fp("R1", 4, 2, x=101.5, y=115), fp("R2", 4, 2, x=135, y=115))
    d = design_of(["R1", "R2"], {"A": ["R1.1", "R2.1"]}, blocks={"b": ["R1", "R2"]})
    p = problem_of(d, geom)
    r = rects(p, p.init_pos, p.init_rot)
    # 板框 (100, 100)–(140, 130)，留 1 mm 板边 → 区域 (101, 101)–(139, 129)
    assert p.region == (101.0, 101.0, 139.0, 129.0)
    # R1 的 courtyard 是 [99.5, 103.5]，出了区域左边界 1.5；R2 是 [133, 137]，在里面
    assert region_excess(p, r) == pytest.approx(1.5)
    # 离板边最好不小于 2.5：R1 距离是 −1.5 → 差 4.0；R2 最近的是右边 139 − 137 = 2 → 差 0.5
    assert edge_term(p, r) == pytest.approx(4.5)
    # 同一功能块：两个元件中心的 x 差 33.5，y 一样
    assert block_spread(p, r) == pytest.approx(33.5)


def test_flow_ranks_follow_the_signal_nets_from_the_left_connector() -> None:
    geom = board(
        60,
        40,
        fp("J1", 3, 6, x=102, y=120),
        fp("R1", 4, 2, x=115, y=120),
        fp("U1", 6, 6, {"1": (-2, 0), "2": (2, 0), "3": (0, 2)}, x=125, y=120),
        fp("J2", 3, 6, x=158, y=120),
        fp("C1", 3, 2, x=125, y=110),
    )
    d = design_of(
        ["J1", "R1", "U1", "J2", "C1"],
        {
            "IN": ["J1.1", "R1.1"],
            "MID": ["R1.2", "U1.1"],
            "OUT": ["U1.2", "J2.1"],
            "VCC": ["U1.3", "C1.1"],  # 名字像电源：不算信号流向
        },
        edges={"J1": "left", "J2": "right"},
    )
    p = problem_of(d, geom)
    ranks = dict(zip(p.refs, p.flow, strict=True))
    assert ranks["J1"] == 0.0 and ranks["R1"] == pytest.approx(1 / 3)
    assert ranks["U1"] == pytest.approx(2 / 3) and ranks["J2"] == pytest.approx(1.0)
    assert math.isnan(ranks["C1"]), "只通过电源网络相连的元件没有流向位置"


def test_no_connector_means_no_flow() -> None:
    geom = board(60, 40, fp("R1", 4, 2, x=110, y=110), fp("R2", 4, 2, x=120, y=110))
    p = problem_of(design_of(["R1", "R2"], {"A": ["R1.1", "R2.1"]}), geom)
    assert np.isnan(p.flow).all() and flow_error(p, rects(p, p.init_pos, p.init_rot)) == 0.0


# ---------------------------------------------------------------- 摆放


def scatter(n: int, size: tuple[float, float] = (4.0, 2.0)) -> list[FootprintGeom]:
    """n 个元件堆在板子左上角附近（开始的位置不用合法，算法要自己摆开）。"""
    return [fp(f"R{i + 1}", *size, x=108 + (i % 3) * 0.2, y=108 + (i // 3) * 0.2) for i in range(n)]


def chain_design(n: int, **kw: Any) -> Design:
    refs = [f"R{i + 1}" for i in range(n)]
    nets = {f"N{i}": [f"R{i + 1}.2", f"R{i + 2}.1"] for i in range(n - 1)}
    return design_of(refs, nets, **kw)


def test_the_result_is_legal_and_inside_the_board() -> None:
    hole = [
        fp(f"H{i}", 6, 6, x=x, y=y, board_only=True)
        for i, (x, y) in enumerate([(104, 104), (136, 104), (136, 126), (104, 126)], start=1)
    ]
    geom = board(40, 30, *hole, *scatter(10))
    d = chain_design(10)
    result = auto_place(d, geom, CFG)
    assert result.method in ("anneal", "pushed", "first_fit")
    placed = BoardGeom(
        geom.outline,
        {
            **{f.ref: f for f in hole},
            **{r: fp(r, 4, 2, x=x, y=y, rot=a) for r, (x, y, a) in result.positions.items()},
        },
    )
    assert check_board(d, placed, CFG) == []
    # 每个 courtyard 都在板框里
    for ref, (_, _, a) in result.positions.items():
        x0, y0, x1, y1 = placed.footprints[ref].rect(a)
        assert x0 >= 100 and x1 <= 140 and y0 >= 100 and y1 <= 130


def test_edge_connectors_stay_on_their_side_unrotated() -> None:
    parts = [fp("J1", 4, 8, x=110, y=115), fp("J2", 4, 8, x=125, y=115), *scatter(5)]
    geom = board(40, 30, *parts)
    d = design_of(
        ["J1", "J2", *[f"R{i}" for i in range(1, 6)]],
        {"A": ["J1.1", "R1.1"], "B": ["R1.2", "R2.1"], "C": ["R2.2", "J2.1"]},
        edges={"J1": "left", "J2": "right"},
    )
    result = auto_place(d, geom, CFG)
    j1, j2 = result.positions["J1"], result.positions["J2"]
    assert j1[2] == 0 and j2[2] == 0
    assert j1[0] - 2 == pytest.approx(101.0, abs=1e-2), "左边连接器的 courtyard 贴着区域左边界"
    assert j2[0] + 2 == pytest.approx(139.0, abs=1e-2), "右边连接器贴着右边界"
    assert j1[1] - 4 >= 101 and j1[1] + 4 <= 129


def test_same_input_and_seed_give_the_same_placement_and_seeds_matter() -> None:
    geom, d = board(40, 30, *scatter(8)), chain_design(8)
    first, second = auto_place(d, geom, CFG, seed=7), auto_place(d, geom, CFG, seed=7)
    assert first.positions == second.positions and first.hpwl_mm == second.hpwl_mm
    assert first.seed == 7
    other = auto_place(d, geom, CFG, seed=8)
    assert other.positions != first.positions


def test_restarts_use_consecutive_seeds_and_keep_the_best() -> None:
    geom, d = board(40, 30, *scatter(8)), chain_design(8)
    cfg = PlacementConfig(iterations_per_part=300, restarts=3, seed=5)
    result = auto_place(d, geom, cfg)
    assert [s["seed"] for s in result.scores] == [5.0, 6.0, 7.0]
    best = min(result.scores, key=lambda s: s["energy"])
    assert result.seed == int(best["seed"]) and result.energy["total"] == pytest.approx(
        best["energy"], abs=1e-3
    )


def test_it_shortens_a_deliberately_bad_start() -> None:
    """连着的元件开始时放在板子的两头，auto 要把它们拉近。"""
    far = [
        fp("R1", 4, 2, x=106, y=106),
        fp("R2", 4, 2, x=134, y=124),
        fp("R3", 4, 2, x=106, y=124),
        fp("R4", 4, 2, x=134, y=106),
    ]
    geom = board(40, 30, *far)
    d = design_of(
        ["R1", "R2", "R3", "R4"],
        {"A": ["R1.2", "R2.1"], "B": ["R3.2", "R4.1"], "C": ["R1.1", "R3.1"]},
    )
    before = board_hpwl(d, geom, CFG)
    result = auto_place(d, geom, CFG)
    assert result.hpwl_mm < before / 2


def test_weights_pull_a_block_together() -> None:
    """两个功能块，块里的元件之间没有网络相连：只有 block 权重能把它们聚起来。"""
    parts = scatter(6)
    geom = board(60, 40, *parts)
    d = design_of(
        [f"R{i}" for i in range(1, 7)],
        {"X": ["R1.1", "R4.1"], "Y": ["R2.1", "R5.1"], "Z": ["R3.1", "R6.1"]},
        blocks={"a": ["R1", "R2", "R3"], "b": ["R4", "R5", "R6"]},
    )

    def spread(weight: float) -> float:
        cfg = PlacementConfig(
            iterations_per_part=600, weights=PlacementWeights(block=weight, flow=0.0)
        )
        result = auto_place(d, geom, cfg)
        p = problem_of(d, geom, cfg)
        pos, rot = (
            np.array([result.positions[r][:2] for r in p.refs]),
            np.array([ANGLES.index(result.positions[r][2]) for r in p.refs], dtype=np.intp),
        )
        return block_spread(p, rects(p, pos, rot))

    assert spread(4.0) < spread(0.0) * 0.8


# ---------------------------------------------------------------- 放不下就报错


def test_too_many_parts_is_an_error_never_an_overflow() -> None:
    geom = board(20, 15, *scatter(30, (6.0, 4.0)))
    d = chain_design(30)
    assert feasibility_error(problem_of(d, geom)) is not None
    with pytest.raises(PlacementError, match="放不下"):
        auto_place(d, geom, CFG)


def test_a_part_bigger_than_the_board_is_an_error() -> None:
    geom = board(20, 15, fp("R1", 30, 3, x=110, y=107), fp("R2", 4, 2, x=110, y=112))
    with pytest.raises(PlacementError, match="R1 放不下"):
        auto_place(chain_design(2), geom, CFG)


def test_a_board_smaller_than_the_margins_is_an_error() -> None:
    geom = board(1.5, 1.5, fp("R1", 1, 1, x=100.5, y=100.5))
    with pytest.raises(PlacementError, match="板子太小"):
        auto_place(design_of(["R1"], {"A": ["R1.1"]}), geom, CFG)


def test_a_long_part_rotates_to_fit_a_tall_board() -> None:
    geom = board(12, 40, fp("R1", 20, 3, x=106, y=120), fp("R2", 4, 2, x=106, y=110))
    result = auto_place(chain_design(2), geom, CFG)
    assert result.positions["R1"][2] in (90, 270)


def test_a_tight_board_still_gets_a_legal_placement() -> None:
    """面积用了七成：不一定好看，但必须合法（退到推开或第一个放得下的位置也行）。"""
    parts = [fp(f"R{i + 1}", 5, 3, x=105 + 0.1 * i, y=105) for i in range(10)]
    geom = board(30, 25, *parts)
    d = chain_design(10)
    result = auto_place(d, geom, CFG)
    placed = BoardGeom(
        geom.outline,
        {r: fp(r, 5, 3, x=x, y=y, rot=a) for r, (x, y, a) in result.positions.items()},
    )
    assert check_board(d, placed, CFG) == []


# ---------------------------------------------------------------- 合法化


def stacked(n: int) -> tuple[Problem, Design, BoardGeom]:
    geom = board(40, 30, *[fp(f"R{i + 1}", 4, 2, x=120, y=115) for i in range(n)])
    d = chain_design(n)
    return problem_of(d, geom), d, geom


def test_overlapping_parts_are_pushed_apart() -> None:
    p, _, _ = stacked(5)
    pos, rot, method = legalize(p, p.init_pos, p.init_rot)
    assert method in ("pushed", "first_fit") and not violations(p, pos, rot)


def test_legalizing_a_legal_state_changes_nothing() -> None:
    geom = board(40, 30, fp("R1", 4, 2, x=110, y=110), fp("R2", 4, 2, x=125, y=115))
    p = problem_of(chain_design(2), geom)
    pos, rot, method = legalize(p, p.init_pos, p.init_rot)
    assert method == "anneal" and np.allclose(pos, p.init_pos)


def test_first_fit_places_everything_that_fits_and_complains_when_it_cannot() -> None:
    p, _, _ = stacked(6)
    pos, rot = _first_fit(p, p.init_rot)
    assert not violations(p, pos, rot)
    full = board(10, 8, *[fp(f"R{i + 1}", 5, 3, x=105, y=105) for i in range(3)])
    q = problem_of(chain_design(3), full)
    with pytest.raises(PlacementError, match="放不下"):
        _first_fit(q, q.init_rot)


def test_connectors_are_snapped_to_the_edge_line() -> None:
    geom = board(
        40,
        30,
        fp("J1", 4, 8, x=125, y=115),
        fp("J2", 4, 8, x=110, y=115),
        fp("R1", 4, 2, x=120, y=110),
    )
    d = design_of(
        ["J1", "J2", "R1"],
        {"A": ["J1.1", "R1.1"], "B": ["J2.1", "R1.2"]},
        edges={"J1": "left", "J2": "right"},
    )
    p = problem_of(d, geom)
    pos, rot = p.init_pos.copy(), p.init_rot.copy()
    snap_connectors(p, pos, rot)
    r = rects(p, pos, rot)
    assert r[0, 0] == pytest.approx(p.region[0]), "J1 在左边：courtyard 左边贴着区域左边界"
    assert r[1, 2] == pytest.approx(p.region[2]), "J2 在右边：courtyard 右边贴着区域右边界"
    assert pos[2, 0] == 120, "不是板边连接器的元件不动"


def test_energy_has_the_expected_parts() -> None:
    p, _, _ = stacked(3)
    e = energy(p, p.init_pos, p.init_rot)
    assert e.overlap > 0 and e.total >= p.weights["overlap"] * e.overlap
    assert set(e._asdict()) == {"total", "hpwl", "overlap", "edge", "block", "flow"}


# ---------------------------------------------------------------- 接进流水线


def test_an_unknown_placement_mode_is_rejected_before_anything_runs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="不认识的摆放方式"):
        place_board(
            load_config().tools,
            tmp_path / "x.kicad_pcb",
            chain_design(2),
            CFG,
            mode="grid",
            work_dir=tmp_path / "work",
            log_dir=tmp_path / "logs",
        )
    assert not (tmp_path / "work").exists()
