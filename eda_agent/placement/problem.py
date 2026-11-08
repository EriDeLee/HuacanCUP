"""把设计和板子上的封装几何整理成摆放问题（ADR-0018）：数组形式，方便 numpy 一次算完所有元件。

- 能动的元件：设计里有、板子上也有的封装（board_only 的安装孔不动，当障碍）。
- 允许的区域：板框往里缩 edge_margin_mm。courtyard 必须整个落在里面。
- 板边连接器（board.edge_connectors）：贴着指定那一边的区域边界，只沿着边上下滑动，不转。
- 网络：每个设计网络里每个引脚对应的焊盘中心（同号的几个焊盘都算）；少于两个焊盘的网络不参与线长。
- 功能块：同一个 parts[].block 里的元件要聚在一起。
- 流向：从左边的板边连接器出发，沿着信号网络（电源、地和扇出很大的网络不算）的跳数，
  给每个元件一个 0 到 1 的位置，左边连接器是 0，越往信号下游越大。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from eda_agent.config import PlacementConfig
from eda_agent.emit.sch_layout import infer_kind
from eda_agent.ir.models import Design
from eda_agent.placement.geometry import ANGLES, BoardGeom, Rect

F64 = NDArray[np.float64]
I64 = NDArray[np.intp]
MAX_FLOW_FANOUT = 8  # 引脚比这多的网络不当成信号流向（多半是电源、总线）


class PlacementError(RuntimeError):
    """放不下，或者找不到合法的摆放。不会返回出板的摆放。"""


@dataclass
class Problem:
    refs: list[str]
    region: Rect
    gap: float
    courtyard: F64  # (n, 4 朝向, 4)：相对封装原点的 x0, y0, x1, y1
    pad_part: I64  # (P,) 每个焊盘属于第几个元件
    pad_offset: F64  # (P, 4 朝向, 2)
    net_starts: I64  # (M,) 焊盘按网络排好以后每个网络的起点
    obstacles: F64  # (k, 4) 不能动的东西的 courtyard（板坐标）
    side: list[str]  # "left" / "right" / ""
    locked_rot: list[bool]
    blocks: list[I64]  # 每个功能块里的元件下标（至少两个）
    flow: F64  # (n,) 流向位置 0..1，没有定义的是 nan
    init_pos: F64  # (n, 2) 开始的位置（当前板上的位置）
    init_rot: I64  # (n,) 开始的朝向下标
    weights: dict[str, float] = field(default_factory=dict)
    edge_preferred: float = 0.0
    index: dict[str, int] = field(default_factory=dict)

    @property
    def n(self) -> int:
        return len(self.refs)


def _rot_index(angle: float) -> int:
    return int(round((angle % 360) / 90)) % 4


def _flow_ranks(design: Design, refs: list[str], left: set[str], right: set[str]) -> F64:
    known = set(refs)
    adjacency: dict[str, set[str]] = {r: set() for r in refs}
    for net in design.nets:
        members = sorted({r for r, _ in net.nodes if r in known})
        if infer_kind(net.name) != "signal" or not 2 <= len(net.nodes) <= MAX_FLOW_FANOUT:
            continue
        for a in members:
            adjacency[a].update(m for m in members if m != a)
    sources, invert = (left, False) if left else (right, True)
    if not sources:
        return np.full(len(refs), np.nan)
    dist: dict[str, int] = {r: 0 for r in sorted(sources) if r in known}
    queue = deque(dist)
    while queue:
        current = queue.popleft()
        for nxt in sorted(adjacency[current]):
            if nxt not in dist:
                dist[nxt] = dist[current] + 1
                queue.append(nxt)
    ends = [dist[r] for r in (right if not invert else left) if r in dist]
    top = max([*ends, *(dist.values())], default=0)
    if top == 0:
        return np.full(len(refs), np.nan)
    ranks = np.array([dist[r] / top if r in dist else np.nan for r in refs])
    return 1.0 - ranks if invert else ranks


def build_problem(design: Design, geom: BoardGeom, cfg: PlacementConfig) -> Problem:
    refs = [
        p.ref
        for p in design.parts
        if p.ref in geom.footprints and not geom.footprints[p.ref].board_only
    ]
    if not refs:
        raise PlacementError("板子上没有设计里的封装可以摆")
    index = {r: i for i, r in enumerate(refs)}
    x0, y0, x1, y1 = geom.outline
    m = cfg.edge_margin_mm
    region: Rect = (x0 + m, y0 + m, x1 - m, y1 - m)
    sides = {c.ref: c.side for c in design.board.edge_connectors}

    courtyard = np.zeros((len(refs), 4, 4))
    init_pos = np.zeros((len(refs), 2))
    init_rot = np.zeros(len(refs), dtype=np.intp)
    locked = []
    for i, ref in enumerate(refs):
        fp = geom.footprints[ref]
        for k, angle in enumerate(ANGLES):
            courtyard[i, k] = fp.shapes[angle].footprint()
        init_pos[i] = (fp.x, fp.y)
        init_rot[i] = _rot_index(fp.rot)
        locked.append(ref in sides)

    # 焊盘按网络排：每个网络连续的一段
    pad_part: list[int] = []
    pad_offset: list[list[tuple[float, float]]] = []
    starts: list[int] = []
    for net in design.nets:
        before = len(pad_part)
        for ref, number in net.nodes:
            if ref not in index:
                continue
            shapes = geom.footprints[ref].shapes
            if number not in shapes[0].pads:
                continue
            for j in range(len(shapes[0].pads[number])):
                pad_part.append(index[ref])
                pad_offset.append([shapes[a].pads[number][j] for a in ANGLES])
        if len(pad_part) - before >= 2:
            starts.append(before)
        else:  # 不足两个焊盘的网络不参与：把刚加的退回去
            del pad_part[before:]
            del pad_offset[before:]

    blocks_of: dict[str, list[int]] = {}
    for part in design.parts:
        if part.block is not None and part.ref in index:
            blocks_of.setdefault(part.block, []).append(index[part.ref])

    left = {r for r, s in sides.items() if s == "left"}
    right = {r for r, s in sides.items() if s == "right"}
    obstacles = np.array(
        [list(f.rect(ANGLES[_rot_index(f.rot)])) for f in geom.fixed()], dtype=float
    ).reshape(-1, 4)
    return Problem(
        refs=refs,
        region=region,
        gap=cfg.min_gap_mm,
        courtyard=courtyard,
        pad_part=np.array(pad_part, dtype=np.intp),
        pad_offset=np.array(pad_offset, dtype=float).reshape(-1, 4, 2),
        net_starts=np.array(starts, dtype=np.intp),
        obstacles=obstacles,
        side=[sides.get(r, "") for r in refs],
        locked_rot=locked,
        blocks=[np.array(v, dtype=np.intp) for _, v in sorted(blocks_of.items()) if len(v) >= 2],
        flow=_flow_ranks(design, refs, left, right),
        init_pos=init_pos,
        init_rot=init_rot,
        weights=cfg.weights.model_dump(),
        edge_preferred=cfg.edge_preferred_mm,
        index=index,
    )
