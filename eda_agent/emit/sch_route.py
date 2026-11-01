"""同一页上的同名标签 → 导线（ADR-0014；用户 2026-11-01 选「能连的信号都连上」）。

版面引擎按团排好位置后，团和团之间、同一团里绕回来的信号都只画了同名标签。这里在 1.27 mm 网格上找路
（A* 搜索：每走一格 1 分，拐弯加分，贴着障碍走加分），把同名标签的端点用导线连起来：

- 不穿过任何符号、文字，不碰别的网络的导线和引脚，也不和别的线交叉。
- 一个网络有好几个端点时，从离已连好的部分最近的端点开始，一个个接上；
  接在导线中间时把那根线断开，三根以上的线相会处加连接点。
- 路太长（比两点间的直角距离长出很多）或拐弯太多就放弃，这个端点照旧用标签。
- 接上的端点去掉标签；第一个端点的标签留下来当这个网络的名字（通到别页的网络留的是层次标签）。
  没接上的端点照旧用标签，和留下的那个同名，连接关系不变。

只认网格上的横线和竖线；不在网格上的东西一律当障碍。同样的输入每次连出一样的线。
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from eda_agent.emit.sch_layout import GRID, Box, Point, Prims, Wire, r4

Node = tuple[int, int]
Seg = tuple[int, int, int, int]  # 网格坐标；只有横线和竖线

BEND = 6.0  # 每拐一次弯，相当于多走几格
NEAR = 1.0  # 贴着障碍走一格的额外代价：尽量离符号和文字留一格
MAX_EXPAND = 300_000
MARGIN = 10  # 搜索范围比内容外框四周各大出几格
MAX_BENDS = 4
MAX_LEN_MM = 160.0
DETOUR = 1.4  # 路长最多是两头直角距离的这么多倍，再加 DETOUR_MM
DETOUR_MM = 12.7
_DIRS: tuple[Node, ...] = ((1, 0), (-1, 0), (0, 1), (0, -1))  # 0/1、2/3 互为反方向


@dataclass
class RouteStats:
    nets: int = 0  # 同一页上有两个以上同名标签的网络
    wired: int = 0  # 全部端点都连上了
    partial: int = 0  # 连上了一部分（没接上的照旧用标签）
    failed: int = 0  # 一根线也没连
    wires: int = 0  # 新加的导线段数


def _gi(v: float) -> int | None:
    k = round(v / GRID)
    return k if abs(k * GRID - v) < 1e-3 else None


def _node(p: Point) -> Node | None:
    gx, gy = _gi(p[0]), _gi(p[1])
    if gx is None or gy is None:
        return None
    return gx, gy


def _pt(n: Node) -> Point:
    return r4(n[0] * GRID), r4(n[1] * GRID)


def _seg_of(w: Wire) -> Seg | None:
    a, b = _node(w.a), _node(w.b)
    if a is None or b is None or (a[0] != b[0] and a[1] != b[1]):
        return None
    return (a[0], a[1], b[0], b[1])


def _nodes(s: Seg) -> list[Node]:
    ax, ay, bx, by = s
    if ax == bx:
        lo, hi = sorted((ay, by))
        return [(ax, y) for y in range(lo, hi + 1)]
    lo, hi = sorted((ax, bx))
    return [(x, ay) for x in range(lo, hi + 1)]


def box_nodes(box: Box, clear: float) -> list[Node]:
    """严格落在外框（四周各放宽 clear 毫米）里面的网格点。"""
    x0, y0, x1, y1 = box[0] - clear, box[1] - clear, box[2] + clear, box[3] + clear
    gx0 = math.floor(x0 / GRID + 1e-9) + 1
    gx1 = math.ceil(x1 / GRID - 1e-9) - 1
    gy0 = math.floor(y0 / GRID + 1e-9) + 1
    gy1 = math.ceil(y1 / GRID - 1e-9) - 1
    return [(gx, gy) for gx in range(gx0, gx1 + 1) for gy in range(gy0, gy1 + 1)]


class _Wires:
    """导线的网格表示：每根线盖住哪些网格点；哪些线连成一组（端点重合，或端点落在另一根线上）。

    两根线只是交叉（交点都不是端点）不算连上，和 KiCad 一样。不在网格上的线当作不存在，
    它们周围的点由 hard_nodes 里的符号和文字挡住。
    """

    def __init__(self, wires: list[Wire]) -> None:
        self.segs: list[Seg | None] = [_seg_of(w) for w in wires]
        self.cover: dict[Node, list[int]] = {}
        for i, s in enumerate(self.segs):
            if s is not None:
                for n in _nodes(s):
                    self.cover.setdefault(n, []).append(i)
        parent = list(range(len(wires)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i, s in enumerate(self.segs):
            if s is None:
                continue
            for end in ((s[0], s[1]), (s[2], s[3])):
                for j in self.cover.get(end, []):
                    a, b = find(i), find(j)
                    if a != b:
                        parent[a] = b
        self.comp = [find(i) for i in range(len(wires))]

    def comps_at(self, n: Node) -> set[int]:
        return {self.comp[i] for i in self.cover.get(n, [])}

    def comp_nodes(self, comps: set[int]) -> set[Node]:
        out: set[Node] = set()
        for i, s in enumerate(self.segs):
            if s is not None and self.comp[i] in comps:
                out.update(_nodes(s))
        return out

    def _is_end(self, i: int, n: Node) -> bool:
        s = self.segs[i]
        return s is not None and ((s[0], s[1]) == n or (s[2], s[3]) == n)

    def ends_at(self, n: Node) -> int:
        return sum(1 for i in self.cover.get(n, []) if self._is_end(i, n))

    def through(self, n: Node) -> list[int]:
        """从 n 中间穿过（n 不是端点）的线。"""
        return [i for i in self.cover.get(n, []) if not self._is_end(i, n)]


def hard_nodes(prims: Prims, extra: Sequence[Box] = ()) -> set[Node]:
    """哪条网络的线都不能走的网格点。

    包括符号、文字、NC 叉、引脚端点、额外的障碍（如根页上的图纸框）。
    """
    out: set[Node] = set()
    for s in prims.symbols:
        out.update(box_nodes(s.box, 0.6))
        for tb in s.text_boxes():
            out.update(box_nodes(tb, 0.3))
        for p in s.pins.values():
            n = _node(p)
            if n is not None:
                out.add(n)
    for x, y in prims.ncs:
        out.update(box_nodes((x - 1.3, y - 1.3, x + 1.3, y + 1.3), 0.0))
    for note in prims.notes:
        out.update(box_nodes(note.box, 0.3))
    for b in extra:
        out.update(box_nodes(b, 0.6))
    return out


def search(
    start: Node,
    targets: set[Node],
    blocked: Callable[[Node], bool],
    near: set[Node],
    bounds: tuple[int, int, int, int],
) -> list[Node] | None:
    """A*：从 start 走到 targets 里任何一点，返回经过的网格点（含两头）；走不到返回 None。"""
    if not targets:
        return None
    tx0 = min(n[0] for n in targets)
    tx1 = max(n[0] for n in targets)
    ty0 = min(n[1] for n in targets)
    ty1 = max(n[1] for n in targets)

    def h(n: Node) -> float:
        dx = tx0 - n[0] if n[0] < tx0 else n[0] - tx1 if n[0] > tx1 else 0
        dy = ty0 - n[1] if n[1] < ty0 else n[1] - ty1 if n[1] > ty1 else 0
        return float(dx + dy)

    bx0, by0, bx1, by1 = bounds
    heap: list[tuple[float, float, int, Node, int]] = [(h(start), 0.0, 0, start, -1)]
    best: dict[tuple[Node, int], float] = {(start, -1): 0.0}
    parent: dict[tuple[Node, int], tuple[Node, int]] = {}
    tie = 0
    expanded = 0
    while heap:
        _, g, _, n, d = heapq.heappop(heap)
        if g > best.get((n, d), math.inf) + 1e-9:
            continue
        if n != start and n in targets:
            path = [n]
            key = (n, d)
            while key in parent:
                key = parent[key]
                path.append(key[0])
            return path[::-1]
        expanded += 1
        if expanded > MAX_EXPAND:
            return None
        for k, (dx, dy) in enumerate(_DIRS):
            if d != -1 and k == d ^ 1:  # 不走回头路
                continue
            m = (n[0] + dx, n[1] + dy)
            if not (bx0 <= m[0] <= bx1 and by0 <= m[1] <= by1) or blocked(m):
                continue
            ng = g + 1.0 + (NEAR if m in near else 0.0) + (BEND if d not in (-1, k) else 0.0)
            key = (m, k)
            if ng < best.get(key, math.inf) - 1e-9:
                best[key] = ng
                parent[key] = (n, d)
                tie += 1
                heapq.heappush(heap, (ng + h(m), ng, tie, m, k))
    return None


def corners(path: list[Node]) -> list[Node]:
    """路径上的拐点（含两头）。"""
    out = [path[0]]
    for a, b, c in zip(path, path[1:], path[2:], strict=False):
        if (b[0] - a[0], b[1] - a[1]) != (c[0] - b[0], c[1] - b[1]):
            out.append(b)
    out.append(path[-1])
    return out


def bounds_of(prims: Prims, extra: Sequence[Box] = ()) -> tuple[int, int, int, int]:
    boxes = [prims.bbox(), *extra]
    return (
        math.floor(min(b[0] for b in boxes) / GRID) - MARGIN,
        math.floor(min(b[1] for b in boxes) / GRID) - MARGIN,
        math.ceil(max(b[2] for b in boxes) / GRID) + MARGIN,
        math.ceil(max(b[3] for b in boxes) / GRID) + MARGIN,
    )


def _neighbors(nodes: set[Node]) -> set[Node]:
    return {(n[0] + dx, n[1] + dy) for n in nodes for dx, dy in _DIRS} - nodes


def _commit(prims: Prims, path: list[Node]) -> int:
    """把路径画成导线。终点落在别的线中间时把那根线断开；三根以上的线在一点相会时加连接点。"""
    info = _Wires(prims.wires)
    for n in (path[0], path[-1]):
        cut = info.through(n)
        meet = info.ends_at(n) + 2 * len(cut) + 1
        for i in sorted(cut, reverse=True):
            w = prims.wires.pop(i)
            prims.wires += [Wire(w.a, _pt(n)), Wire(_pt(n), w.b)]
        if cut:
            info = _Wires(prims.wires)
        if meet >= 3 and _pt(n) not in prims.junctions:
            prims.junctions.append(_pt(n))
    pts = corners(path)
    for a, b in zip(pts, pts[1:], strict=False):
        prims.add_wire(_pt(a), _pt(b))
    return len(pts) - 1


def acceptable(path: list[Node]) -> bool:
    """不要太绕：拐弯不超过 MAX_BENDS 次，总长不超过 MAX_LEN_MM，也不比两头的直角距离长出太多。"""
    direct = abs(path[0][0] - path[-1][0]) + abs(path[0][1] - path[-1][1])
    length = (len(path) - 1) * GRID
    bends = len(corners(path)) - 2
    # 2026-11-01 第一次出图时 i2c_temp_sensor 的 SDA 绕到连接器左边再折回来，这种宁可用标签
    return (
        bends <= MAX_BENDS and length <= MAX_LEN_MM and length <= DETOUR * direct * GRID + DETOUR_MM
    )


def _spread(nodes: list[Node]) -> int:
    """端点之间大概要多长的线（每个点到前面最近一点的直角距离之和）。"""
    total = 0
    for k in range(1, len(nodes)):
        total += min(abs(nodes[k][0] - p[0]) + abs(nodes[k][1] - p[1]) for p in nodes[:k])
    return total


def _route_net(
    prims: Prims,
    text: str,
    hard: set[Node],
    hard_near: set[Node],
    bounds: tuple[int, int, int, int],
    keep_name: bool = True,
) -> tuple[str, int]:
    terms = [
        (lb, n) for lb in prims.labels if lb.text == text and (n := _node((lb.x, lb.y))) is not None
    ]
    if len(terms) < 2:
        return "failed", 0
    snapshot = (list(prims.wires), list(prims.junctions), list(prims.labels))
    # 层次标签排最前（它要留下来）；其余从上到下、从左到右
    terms.sort(key=lambda t: (not t[0].shape, t[1][1], t[1][0]))
    root = terms[0][1]
    # 不留名字时（根页：图纸框的引脚本身就写着网络名）第一个端点的标签也不挡路，连全了就去掉；
    # 连不全就整个网络退回标签，免得留下的标签压在新画的线上
    drop_root = not keep_name and not terms[0][0].shape
    if drop_root:
        prims.labels = [lb for lb in prims.labels if lb is not terms[0][0]]
    connected = {0}
    failed: set[int] = set()
    added = 0
    while True:
        info = _Wires(prims.wires)
        term_comps = [info.comps_at(n) for _, n in terms]
        tree_comps = term_comps[0]
        for k in range(1, len(terms)):
            if k not in connected and term_comps[k] & tree_comps:
                connected.add(k)
        pending = [k for k in range(len(terms)) if k not in connected and k not in failed]
        if not pending:
            break
        tree = info.comp_nodes(tree_comps) | {root}
        # 只被这个网络已连好的部分盖住的点可以接上去；其余被导线盖住的点（别的网络、
        # 这个网络还没连上的那些端点自己的线、交叉点）一律不能走
        wired = {
            n for n, idx in info.cover.items() if not {info.comp[i] for i in idx} <= tree_comps
        }

        def dist(k: int, tree: set[Node] = tree) -> int:
            n = terms[k][1]
            return min(abs(n[0] - t[0]) + abs(n[1] - t[1]) for t in tree)

        k = min(pending, key=lambda j: (dist(j), j))
        start_label, start = terms[k]
        texts: set[Node] = set()
        for lb in prims.labels:
            if lb is not start_label:
                texts.update(box_nodes(lb.box, 0.3))
        base = hard | wired | texts
        targets = tree - base
        near = hard_near | _neighbors(wired | texts)

        def blocked(m: Node, base: set[Node] = base) -> bool:
            return m in base

        path = search(start, targets, blocked, near, bounds)
        if path is None or not acceptable(path):
            failed.add(k)
            continue
        added += _commit(prims, path)
        connected.add(k)
        prims.labels = [lb for lb in prims.labels if lb is not start_label]

    if len(connected) < 2 or (drop_root and failed):
        prims.wires, prims.junctions, prims.labels = snapshot
        return "failed", 0
    return ("partial" if failed else "wired"), added


def _route_all(
    prims: Prims,
    order: list[str],
    hard: set[Node],
    hard_near: set[Node],
    bounds: tuple[int, int, int, int],
    keep_name: bool,
) -> tuple[RouteStats, list[str]]:
    stats = RouteStats()
    unfinished: list[str] = []
    for text in order:
        stats.nets += 1
        result, added = _route_net(prims, text, hard, hard_near, bounds, keep_name)
        stats.wires += added
        if result == "wired":
            stats.wired += 1
        else:
            unfinished.append(text)
            if result == "partial":
                stats.partial += 1
            else:
                stats.failed += 1
    return stats, unfinished


def connect_labels(
    prims: Prims, *, obstacles: Sequence[Box] = (), keep_name: bool = True
) -> RouteStats:
    """把 prims 里同名标签的端点用导线连起来（就地修改 prims）。obstacles：额外不能穿过的区域。

    keep_name：连好的网络留一个标签当名字（平常的页）；根页上图纸框的引脚已经写着名字，不留。
    先按「端点离得近的网络先连」走一遍；有没连全的，就全部拆掉，让没连全的先走，再来一遍，
    两遍里取连全的网络多的那一遍（一样多取导线少的）。
    """
    names: dict[str, list[Node]] = {}
    for lb in prims.labels:
        n = _node((lb.x, lb.y))
        if n is not None:
            names.setdefault(lb.text, []).append(n)
    multi = sorted(
        (t for t, ns in names.items() if len(ns) >= 2), key=lambda t: (_spread(names[t]), t)
    )
    if not multi:
        return RouteStats()
    hard = hard_nodes(prims, obstacles)
    hard_near = _neighbors(hard)
    bounds = bounds_of(prims, obstacles)
    start = (list(prims.wires), list(prims.junctions), list(prims.labels))
    stats, unfinished = _route_all(prims, multi, hard, hard_near, bounds, keep_name)
    if not unfinished:
        return stats
    first = (list(prims.wires), list(prims.junctions), list(prims.labels))
    prims.wires, prims.junctions, prims.labels = (list(x) for x in start)  # type: ignore[assignment]
    retry = unfinished + [t for t in multi if t not in unfinished]
    stats2, _ = _route_all(prims, retry, hard, hard_near, bounds, keep_name)
    better = (stats2.wired, stats2.partial, -stats2.wires) > (
        stats.wired,
        stats.partial,
        -stats.wires,
    )
    if better:
        return stats2
    prims.wires, prims.junctions, prims.labels = first
    return stats


def route_free(
    prims: Prims,
    start: Node,
    targets: set[Node],
    *,
    obstacles: Sequence[Box] = (),
    avoid: set[Node] | None = None,
) -> list[Node] | None:
    """不带电气意义的线（根页的电源流向箭头）找路：不穿过符号、文字、导线和 avoid 里的点。"""
    hard = hard_nodes(prims, obstacles)
    for lb in prims.labels:
        hard.update(box_nodes(lb.box, 0.3))
    base = hard | set(_Wires(prims.wires).cover) | (avoid or set())
    near = _neighbors(base)

    def blocked(m: Node) -> bool:
        return m in base

    path = search(start, targets - base, blocked, near, bounds_of(prims, obstacles))
    if path is None or len(corners(path)) - 2 > MAX_BENDS:
        return None
    return path
