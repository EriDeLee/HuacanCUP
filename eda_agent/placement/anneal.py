"""模拟退火摆放（ADR-0018 的「没定的」一节记了选择和理由，review-queue D32）。

状态是每个元件封装原点的位置和朝向（0、90、180、270 度）。四种走法：
- 平移：高斯步长，随温度缩小；板边连接器只沿着边上下滑；
- 互换：两个元件互换中心；
- 转 90 / 180 / 270 度：保持 courtyard 中心不动（板边连接器不转）；
- 靠近：挑一个元件，挑它所在网络里的另一个元件，把它移到那个元件旁边的随机一侧。
分数见 score.py。每次走法以后把元件推回允许的区域里，板边连接器重新贴边，
所以区域和贴边在整个过程里都是满足的；
重叠用很大的惩罚压住，退火结束再由 legalize.py 保证为零。

同样的输入和种子得到同样的结果：随机数只来自 numpy 的 PCG64（种子决定），迭代顺序固定。
起始温度由初始状态下随机走法的平均上坡量决定，按几何级数降到起始的 0.2 %。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from eda_agent.placement.problem import F64, I64, Problem
from eda_agent.placement.score import Energy, energy, snap_connectors

SAMPLES_FOR_T0 = 200
T_END_RATIO = 0.002
MOVE_WEIGHTS = (0.55, 0.15, 0.10, 0.20)  # 平移、互换、转、靠近


@dataclass
class AnnealResult:
    pos: F64
    rot: I64
    energy: Energy
    iterations: int
    accepted: int
    seed: int
    t_start: float


def _neighbors(p: Problem) -> list[list[int]]:
    """每个元件在网络里的邻居（共用一个网络的别的元件），下标升序。"""
    near: list[set[int]] = [set() for _ in range(p.n)]
    ends = [*p.net_starts[1:], len(p.pad_part)]
    for start, end in zip(p.net_starts, ends, strict=True):
        parts = {int(i) for i in p.pad_part[start:end]}
        for i in parts:
            near[i] |= parts - {i}
    return [sorted(s) for s in near]


def _center_offset(p: Problem, i: int, rot: int) -> tuple[float, float]:
    x0, y0, x1, y1 = p.courtyard[i, rot]
    return ((x0 + x1) / 2, (y0 + y1) / 2)


def _size(p: Problem, i: int, rot: int) -> tuple[float, float]:
    x0, y0, x1, y1 = p.courtyard[i, rot]
    return (x1 - x0, y1 - y0)


def _clamp(p: Problem, pos: F64, rot: I64, i: int) -> None:
    """把第 i 个元件推回允许的区域（放得下才推；本来就比区域大的不动，由惩罚和合法化处理）。"""
    rx0, ry0, rx1, ry1 = p.region
    x0, y0, x1, y1 = p.courtyard[i, rot[i]]
    if x1 - x0 <= rx1 - rx0:
        pos[i, 0] = min(max(pos[i, 0], rx0 - x0), rx1 - x1)
    if y1 - y0 <= ry1 - ry0:
        pos[i, 1] = min(max(pos[i, 1], ry0 - y0), ry1 - y1)


def propose(
    p: Problem,
    pos: F64,
    rot: I64,
    rng: np.random.Generator,
    scale: float,
    neighbors: list[list[int]],
) -> tuple[F64, I64]:
    """从当前状态走一步，返回新的位置和朝向（不改传入的数组）。scale：0..1，平移步长的缩放。"""
    new_pos, new_rot = pos.copy(), rot.copy()
    i = int(rng.integers(p.n))
    kind = rng.choice(4, p=MOVE_WEIGHTS)
    free = [k for k in range(p.n) if not p.side[k]]
    if p.side[i]:
        kind = 0  # 板边连接器只能平移（沿着边）
    if kind == 1 and len(free) >= 2 and not p.side[i]:
        j = int(free[int(rng.integers(len(free)))])
        if j != i:
            ci, cj = _center_offset(p, i, rot[i]), _center_offset(p, j, rot[j])
            center_i, center_j = pos[i] + ci, pos[j] + cj
            new_pos[i] = center_j - ci
            new_pos[j] = center_i - cj
            _clamp(p, new_pos, new_rot, i)
            _clamp(p, new_pos, new_rot, j)
            return new_pos, new_rot
        kind = 0
    if kind == 2 and not p.locked_rot[i]:
        before = _center_offset(p, i, rot[i])
        new_rot[i] = (rot[i] + int(rng.integers(1, 4))) % 4
        after = _center_offset(p, i, new_rot[i])
        new_pos[i] += np.array(before) - np.array(after)
        _clamp(p, new_pos, new_rot, i)
        return new_pos, new_rot
    if kind == 3 and neighbors[i] and not p.side[i]:
        j = neighbors[i][int(rng.integers(len(neighbors[i])))]
        wi, hi = _size(p, i, rot[i])
        wj, hj = _size(p, j, rot[j])
        cj = pos[j] + _center_offset(p, j, rot[j])
        dx = (wi + wj) / 2 + p.gap
        dy = (hi + hj) / 2 + p.gap
        side = int(rng.integers(4))
        jitter = rng.uniform(-0.5, 0.5)
        offset = [(dx, jitter * hj), (-dx, jitter * hj), (jitter * wj, dy), (jitter * wj, -dy)][
            side
        ]
        new_pos[i] = cj + np.array(offset) - np.array(_center_offset(p, i, rot[i]))
        _clamp(p, new_pos, new_rot, i)
        return new_pos, new_rot
    # 平移
    rx0, ry0, rx1, ry1 = p.region
    sigma = max(0.3, 0.35 * scale * max(rx1 - rx0, ry1 - ry0))
    step = rng.normal(0.0, sigma, size=2)
    if p.side[i]:
        step[0] = 0.0
    new_pos[i] += step
    _clamp(p, new_pos, new_rot, i)
    return new_pos, new_rot


def anneal(p: Problem, seed: int, iterations: int) -> AnnealResult:
    rng = np.random.default_rng(seed)
    neighbors = _neighbors(p)
    pos, rot = p.init_pos.copy(), p.init_rot.copy()
    snap_connectors(p, pos, rot)
    for i in range(p.n):
        _clamp(p, pos, rot, i)
    current = energy(p, pos, rot)

    # 起始温度：随机走法的平均上坡量
    uphill = []
    for _ in range(SAMPLES_FOR_T0):
        trial_pos, trial_rot = propose(p, pos, rot, rng, 1.0, neighbors)
        snap_connectors(p, trial_pos, trial_rot)
        delta = energy(p, trial_pos, trial_rot).total - current.total
        if delta > 0:
            uphill.append(delta)
    t_start = float(np.mean(uphill)) if uphill else 1.0
    t_start = max(t_start, 1e-3)
    cooling = T_END_RATIO ** (1.0 / max(iterations, 1))

    best_pos, best_rot, best = pos.copy(), rot.copy(), current
    temperature = t_start
    accepted = 0
    for _ in range(iterations):
        scale = temperature / t_start
        trial_pos, trial_rot = propose(p, pos, rot, rng, scale, neighbors)
        snap_connectors(p, trial_pos, trial_rot)
        trial = energy(p, trial_pos, trial_rot)
        delta = trial.total - current.total
        if delta <= 0 or rng.random() < math.exp(-delta / temperature):
            pos, rot, current = trial_pos, trial_rot, trial
            accepted += 1
            if current.total < best.total:
                best_pos, best_rot, best = pos.copy(), rot.copy(), current
        temperature *= cooling
    return AnnealResult(best_pos, best_rot, best, iterations, accepted, seed, t_start)
