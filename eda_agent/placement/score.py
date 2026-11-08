"""摆放的打分（ADR-0018）：总线长 HPWL、courtyard 重叠、出区域、离板边距离、功能块聚拢、信号流向。

分数是各项加权和，权重在 config.yaml 的 placement.weights，各项都换算成 mm 量级：
- hpwl：每个网络的焊盘外包框的半周长（mm）之和，没有权重的那个真线长；
- overlap：两个 courtyard（各向外鼓 min_gap_mm / 2）重叠的穿透深度之和，
  加上 courtyard 出了允许区域的距离之和；
  乘上很大的权重，等于硬约束（最后还要合法化，保证是零）；
- edge：非连接器元件的 courtyard 离板边比 edge_preferred_mm 近多少；
  板边连接器没贴在线上的距离；
- block：每个功能块里元件中心外包框的半周长；
- flow：有流向位置的元件，中心离它应该在的 x 位置的距离。
所有函数只依赖数组，既给退火用，也给事后的检查和报告用
（hpwl 的对比表就是用 pcbnew 重读的位置算的）。
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np
from numpy.typing import NDArray

from eda_agent.placement.problem import F64, I64, Problem


class Energy(NamedTuple):
    total: float
    hpwl: float
    overlap: float
    edge: float
    block: float
    flow: float


def pad_positions(p: Problem, pos: F64, rot: I64) -> F64:
    count = len(p.pad_part)
    return np.asarray(pos[p.pad_part] + p.pad_offset[np.arange(count), rot[p.pad_part]])


def hpwl(p: Problem, pos: F64, rot: I64) -> float:
    if not len(p.net_starts):
        return 0.0
    xy = pad_positions(p, pos, rot)
    hi = np.maximum.reduceat(xy, p.net_starts, axis=0)
    lo = np.minimum.reduceat(xy, p.net_starts, axis=0)
    return float((hi - lo).sum())


def rects(p: Problem, pos: F64, rot: I64) -> F64:
    """每个元件现在的 courtyard（板坐标，n × 4）。"""
    rel = p.courtyard[np.arange(p.n), rot]
    return np.asarray(rel + np.concatenate([pos, pos], axis=1))


def snap_connectors(p: Problem, pos: F64, rot: I64) -> None:
    """板边连接器的 x 贴着区域边界（就地修改 pos）。"""
    rx0, _, rx1, _ = p.region
    for i, side in enumerate(p.side):
        if side == "left":
            pos[i, 0] = rx0 - p.courtyard[i, rot[i], 0]
        elif side == "right":
            pos[i, 0] = rx1 - p.courtyard[i, rot[i], 2]


def _pair_depth(a: F64, b: F64) -> F64:
    """a 的每个矩形和 b 的每个矩形重叠的穿透深度（较小方向），不重叠是 0。形状 (len(a), len(b))。"""
    dx = np.minimum(a[:, None, 2], b[None, :, 2]) - np.maximum(a[:, None, 0], b[None, :, 0])
    dy = np.minimum(a[:, None, 3], b[None, :, 3]) - np.maximum(a[:, None, 1], b[None, :, 1])
    return np.where((dx > 0) & (dy > 0), np.minimum(dx, dy), 0.0)


def overlap_depths(p: Problem, r: F64) -> F64:
    """元件之间（上三角）和元件与障碍之间的穿透深度，min_gap 已算进去。"""
    half = p.gap / 2
    grown = r + np.array([-half, -half, half, half])
    depth = np.triu(_pair_depth(grown, grown), k=1)
    if len(p.obstacles):
        depth = np.concatenate(
            [depth, _pair_depth(grown, p.obstacles + np.array([-half, -half, half, half]))],
            axis=1,
        )
    return np.asarray(depth)


def region_excess(p: Problem, r: F64) -> float:
    rx0, ry0, rx1, ry1 = p.region
    return float(
        np.clip(rx0 - r[:, 0], 0, None).sum()
        + np.clip(r[:, 2] - rx1, 0, None).sum()
        + np.clip(ry0 - r[:, 1], 0, None).sum()
        + np.clip(r[:, 3] - ry1, 0, None).sum()
    )


def edge_term(p: Problem, r: F64) -> float:
    """离板边比 edge_preferred 近多少（非连接器）。板边连接器贴不贴线由 snap_connectors 保证。"""
    rx0, ry0, rx1, ry1 = p.region
    margin = p.edge_preferred
    gap = np.minimum.reduce([r[:, 0] - rx0, rx1 - r[:, 2], r[:, 1] - ry0, ry1 - r[:, 3]])
    free = np.array([not s for s in p.side])
    return float(np.clip(margin - gap, 0, None)[free].sum())


def block_spread(p: Problem, r: F64) -> float:
    centers = np.stack([(r[:, 0] + r[:, 2]) / 2, (r[:, 1] + r[:, 3]) / 2], axis=1)
    total = 0.0
    for members in p.blocks:
        sub = centers[members]
        total += float((sub.max(axis=0) - sub.min(axis=0)).sum())
    return total


def flow_error(p: Problem, r: F64) -> float:
    known = ~np.isnan(p.flow)
    if not known.any():
        return 0.0
    rx0, _, rx1, _ = p.region
    target = rx0 + p.flow[known] * (rx1 - rx0)
    cx = (r[known, 0] + r[known, 2]) / 2
    return float(np.abs(cx - target).sum())


def energy(p: Problem, pos: F64, rot: I64) -> Energy:
    r = rects(p, pos, rot)
    w = p.weights
    wire = hpwl(p, pos, rot)
    over = float(overlap_depths(p, r).sum()) + region_excess(p, r)
    edge = edge_term(p, r)
    block = block_spread(p, r)
    flow = flow_error(p, r)
    total = (
        w["hpwl"] * wire
        + w["overlap"] * over
        + w["edge"] * edge
        + w["block"] * block
        + w["flow"] * flow
    )
    return Energy(total, wire, over, edge, block, flow)


def violations(p: Problem, pos: F64, rot: I64, tolerance: float = 1e-6) -> list[str]:
    """合法性检查：重叠、出区域、板边连接器没贴边。空列表 = 合法。"""
    r = rects(p, pos, rot)
    found: list[str] = []
    depth = overlap_depths(p, r)
    for i, j in zip(*np.nonzero(depth > tolerance), strict=True):
        other = p.refs[j] if j < p.n else f"障碍 {j - p.n}"
        found.append(
            f"{p.refs[i]} 和 {other} 的 courtyard 重叠"
            f"（深度 {depth[i, j]:.3f} mm，含 {p.gap} mm 间隙）"
        )
    rx0, ry0, rx1, ry1 = p.region
    for i, ref in enumerate(p.refs):
        x0, y0, x1, y1 = r[i]
        if (
            x0 < rx0 - tolerance
            or x1 > rx1 + tolerance
            or y0 < ry0 - tolerance
            or y1 > ry1 + tolerance
        ):
            found.append(f"{ref} 的 courtyard 出了允许区域 {p.region}")
        if p.side[i] == "left" and abs(x0 - rx0) > tolerance:
            found.append(f"{ref} 应该贴左边，离边界 {x0 - rx0:.3f} mm")
        if p.side[i] == "right" and abs(rx1 - x1) > tolerance:
            found.append(f"{ref} 应该贴右边，离边界 {rx1 - x1:.3f} mm")
    return found


def centers(r: NDArray[np.float64]) -> F64:
    return np.stack([(r[:, 0] + r[:, 2]) / 2, (r[:, 1] + r[:, 3]) / 2], axis=1)
