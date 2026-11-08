"""合法化：保证最后的摆放没有 courtyard 重叠、不出区域、板边连接器贴边。
做不到就报错，不会把元件放到板外。

1. 先把每个元件推回允许的区域（板边连接器贴边）；
2. 重叠的一对沿着穿透较浅的方向各退一半（和障碍重叠的只有元件退；板边连接器只沿着边退），
   推出新的重叠就再推，最多推 MAX_ROUNDS 轮；
3. 还有重叠，退到「第一个放得下的位置」：元件按面积从大到小，
   在 0.25 mm 的网格上从上到下、从左到右找第一个不碰任何已放元件和障碍的位置，转角也试；
   有元件找不到位置就报 PlacementError（放不下）。
   这一档的线长不好，结果里 method 写 "first_fit"，让人看得出来。
"""

from __future__ import annotations

import numpy as np

from eda_agent.placement.problem import F64, I64, PlacementError, Problem
from eda_agent.placement.score import overlap_depths, rects, snap_connectors, violations

MAX_ROUNDS = 400
EPS = 1e-6
GRID_MM = 0.25


def _clamp_all(p: Problem, pos: F64, rot: I64) -> None:
    rx0, ry0, rx1, ry1 = p.region
    for i in range(p.n):
        x0, y0, x1, y1 = p.courtyard[i, rot[i]]
        if x1 - x0 <= rx1 - rx0:
            pos[i, 0] = min(max(pos[i, 0], rx0 - x0), rx1 - x1)
        if y1 - y0 <= ry1 - ry0:
            pos[i, 1] = min(max(pos[i, 1], ry0 - y0), ry1 - y1)
    snap_connectors(p, pos, rot)


def _push_pairs(p: Problem, pos: F64, rot: I64) -> bool:
    """推开一轮。返回这一轮开始时是不是还有重叠。"""
    r = rects(p, pos, rot)
    depth = overlap_depths(p, r)
    if depth.max(initial=0.0) <= EPS:
        return False
    half = p.gap / 2
    grown = r + np.array([-half, -half, half, half])
    obstacles = (
        p.obstacles + np.array([-half, -half, half, half]) if len(p.obstacles) else p.obstacles
    )
    for i, j in zip(*np.nonzero(depth > EPS), strict=True):
        a = grown[i]
        b = grown[j] if j < p.n else obstacles[j - p.n]
        dx = min(a[2], b[2]) - max(a[0], b[0])
        dy = min(a[3], b[3]) - max(a[1], b[1])
        if dx <= 0 or dy <= 0:
            continue  # 前面的推动已经把这一对分开了
        members = [int(i)] if j >= p.n else [int(i), int(j)]
        axis = 0 if dx <= dy else 1
        # 板边连接器只能沿着边（y 方向）动：x 方向推不动的就不让它动，两个都推不动就改沿 y 推
        movers = [m for m in members if not (axis == 0 and p.side[m])]
        if not movers:
            axis = 1
            movers = members
        amount = (dx if axis == 0 else dy) + 1e-3
        lo, hi = (a[0], a[2]) if axis == 0 else (a[1], a[3])
        other_lo, other_hi = (b[0], b[2]) if axis == 0 else (b[1], b[3])
        sign = 1.0 if lo + hi >= other_lo + other_hi else -1.0  # a 在 b 的哪一边
        for m in movers:
            pos[m, axis] += (sign if m == i else -sign) * amount / len(movers)
    _clamp_all(p, pos, rot)
    return True


def _first_fit(p: Problem, rot: I64) -> tuple[F64, I64]:
    """最后一档：按面积从大到小，每个元件放到网格上第一个放得下的位置。"""
    rx0, ry0, rx1, ry1 = p.region
    half = p.gap / 2
    pos = p.init_pos.copy()
    out_rot = rot.copy()
    placed: list[np.ndarray] = [o + np.array([-half, -half, half, half]) for o in p.obstacles]
    area = [
        (p.courtyard[i, out_rot[i], 2] - p.courtyard[i, out_rot[i], 0])
        * (p.courtyard[i, out_rot[i], 3] - p.courtyard[i, out_rot[i], 1])
        for i in range(p.n)
    ]
    order = sorted(range(p.n), key=lambda i: (not p.side[i], -area[i], p.refs[i]))
    for i in order:
        options = (
            [int(out_rot[i])]
            if p.locked_rot[i]
            else [int(out_rot[i]), *(k for k in range(4) if k != out_rot[i])]
        )
        done = False
        for k in options:
            x0, y0, x1, y1 = p.courtyard[i, k]
            w, h = x1 - x0, y1 - y0
            if w > rx1 - rx0 + EPS or h > ry1 - ry0 + EPS:
                continue
            if p.side[i] == "left":
                xs = np.array([rx0])
            elif p.side[i] == "right":
                xs = np.array([rx1 - w])
            else:
                xs = np.arange(rx0, rx1 - w + EPS, GRID_MM)
            ys = np.arange(ry0, ry1 - h + EPS, GRID_MM)
            gx, gy = np.meshgrid(xs, ys)  # 形状 (len(ys), len(xs))：先扫完一行再下一行
            cand = np.stack([gx - half, gy - half, gx + w + half, gy + h + half], axis=-1).reshape(
                -1, 4
            )
            free = np.ones(len(cand), dtype=bool)
            for other in placed:
                dx = np.minimum(cand[:, 2], other[2]) - np.maximum(cand[:, 0], other[0])
                dy = np.minimum(cand[:, 3], other[3]) - np.maximum(cand[:, 1], other[1])
                free &= ~((dx > EPS) & (dy > EPS))
            if free.any():
                n = int(np.argmax(free))
                x, y = float(cand[n, 0] + half), float(cand[n, 1] + half)
                pos[i] = (x - x0, y - y0)
                out_rot[i] = k
                placed.append(cand[n])
                done = True
                break
        if not done:
            raise PlacementError(f"{p.refs[i]} 放不下：板子上找不到不碰别的元件、不出区域的位置")
    return pos, out_rot


def legalize(p: Problem, pos: F64, rot: I64) -> tuple[F64, I64, str]:
    """返回 (位置, 朝向, 方法)。
    方法：anneal = 本来就合法；pushed = 推开以后合法；first_fit = 退到了最后一档。"""
    pos, rot = pos.copy(), rot.copy()
    _clamp_all(p, pos, rot)
    if not violations(p, pos, rot):
        return pos, rot, "anneal"
    for _ in range(MAX_ROUNDS):
        if not _push_pairs(p, pos, rot):
            break
    if not violations(p, pos, rot):
        return pos, rot, "pushed"
    pos, rot = _first_fit(p, rot)
    problems = violations(p, pos, rot)
    if problems:
        raise PlacementError("放不下：" + "；".join(problems[:3]))
    return pos, rot, "first_fit"
