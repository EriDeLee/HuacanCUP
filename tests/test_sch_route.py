"""同名标签连成导线（eda_agent/emit/sch_route.py，ADR-0014）：在手搭的小图上检查，不需要 KiCad。"""

from __future__ import annotations

import copy

from eda_agent.emit.sch_layout import GRID, Box, Drawing, Label, Prims, SymbolInst, Wire
from eda_agent.emit.sch_route import _label_nodes, _node, _Wires, connect_labels
from eda_agent.gates.schematic_quality import layout_quality

G = GRID


def _lane(p: Prims, text: str, x: float, y: float, d: int, shape: str = "") -> None:
    """一段从左（d=1）或从右（d=-1）伸过来的线，线头上一个标签，像引擎画的链末端。"""
    p.add_wire((x - d * 2 * G, y), (x, y))
    p.labels.append(Label(text, x, y, 0 if d > 0 else 180, shape))


def _block(ref: str, box: Box) -> SymbolInst:
    return SymbolInst(ref, "X:Y", "", "", 0.0, 0.0, 0, False, [], box, box, {})


def _same_wire_group(p: Prims, *points: tuple[float, float]) -> bool:
    info = _Wires(p.wires)
    groups = []
    for pt in points:
        n = _node(pt)
        assert n is not None
        groups.append(info.comps_at(n))
    return all(g and g == groups[0] for g in groups)


def _quality(p: Prims) -> dict[str, int]:
    return layout_quality(Drawing(p, "A4", {})).counts


def test_two_labels_facing_each_other_become_one_wire_and_keep_one_name() -> None:
    p = Prims()
    _lane(p, "SIG", 5 * G, 0.0, 1)
    _lane(p, "SIG", 25 * G, 0.0, -1)

    stats = connect_labels(p)

    assert (stats.nets, stats.wired) == (1, 1)
    assert [lb.text for lb in p.labels] == ["SIG"], "连好后只留一个标签当网络名"
    assert _same_wire_group(p, (5 * G, 0.0), (25 * G, 0.0))


def test_the_wire_runs_straight_under_the_name_it_keeps() -> None:
    """留下来当名字的标签就坐在新画的线上，线不用为了躲它先拐一格（uno_like_board 的 BUS_A）。"""
    p = Prims()
    _lane(p, "BUS_A", 5 * G, 0.0, 1)
    _lane(p, "BUS_A", 25 * G, 0.0, -1)

    assert connect_labels(p).wired == 1
    assert all(w.a[1] == w.b[1] == 0.0 for w in p.wires), "不该有拐弯"
    assert [(lb.x, lb.y) for lb in p.labels] == [(5 * G, 0.0)]
    assert _quality(p)["failures"] == 0


def test_only_the_own_net_may_run_under_a_label() -> None:
    """别的网络的线贴着标签底下走，看起来像这个名字是它的；层次标签的旗形框把线包在中间。"""
    local = Label("SDA", 5 * G, 0.0, 0)
    under = {n for n in _label_nodes(local, same_net=False) if n[1] == 0}
    assert under, "别的网络：字底下那条线上的点要挡住"
    assert not {n for n in _label_nodes(local, same_net=True) if n[1] == 0}
    port = Label("SDA", 5 * G, 0.0, 0, "output")
    assert {n for n in _label_nodes(port, same_net=True) if n[1] == 0}


def test_the_wire_goes_around_a_symbol_instead_of_through_it() -> None:
    p = Prims(symbols=[_block("U1", (12 * G, -4 * G, 18 * G, 4 * G))])
    _lane(p, "SIG", 5 * G, 0.0, 1)
    _lane(p, "SIG", 25 * G, 0.0, -1)

    assert connect_labels(p).wired == 1
    assert _quality(p)["failures"] == 0, "不能穿过符号本体"
    assert _same_wire_group(p, (5 * G, 0.0), (25 * G, 0.0))


def test_no_new_wire_crosses_another_net() -> None:
    p = Prims()
    _lane(p, "A", 5 * G, 0.0, 1)
    _lane(p, "A", 25 * G, 8 * G, -1)
    _lane(p, "B", 5 * G, 8 * G, 1)
    _lane(p, "B", 25 * G, 0.0, -1)

    connect_labels(p)

    assert _quality(p)["wire_crossings"] == 0
    for net in ("A", "B"):  # 每个网络要么连通了，要么照旧靠同名标签相连
        labels = [lb for lb in p.labels if lb.text == net]
        wired = _same_wire_group(p, *[(lb.x, lb.y) for lb in labels]) if len(labels) == 1 else True
        assert labels and wired


def test_a_third_end_joins_the_middle_of_a_wire_with_a_junction() -> None:
    p = Prims()
    _lane(p, "N", 5 * G, 0.0, 1)
    _lane(p, "N", 30 * G, 0.0, -1)
    _lane(p, "N", 15 * G, 10 * G, 1)

    assert connect_labels(p).wired == 1
    assert _same_wire_group(p, (5 * G, 0.0), (30 * G, 0.0), (15 * G, 10 * G))
    assert p.junctions, "三根线相会处要有连接点"
    info = _Wires(p.wires)
    for j in p.junctions:
        n = _node(j)
        assert n is not None and not info.through(n), "接在线中间的地方要把线断开"


def test_a_port_net_keeps_exactly_one_hierarchical_label() -> None:
    p = Prims()
    _lane(p, "GATE", 5 * G, 0.0, 1, shape="output")
    _lane(p, "GATE", 25 * G, 4 * G, -1, shape="output")

    assert connect_labels(p).wired == 1
    assert [(lb.text, lb.shape) for lb in p.labels] == [("GATE", "output")]


def test_without_names_a_net_that_cannot_be_finished_keeps_all_its_labels() -> None:
    """根页（keep_name=False）：连不全就整个网络退回标签，不留半截线。"""
    walled = (38 * G, -6 * G, 52 * G, 6 * G)
    p = Prims(symbols=[_block("WALL", walled)])
    _lane(p, "X", 5 * G, 0.0, 1)
    _lane(p, "X", 20 * G, 0.0, -1)
    p.labels.append(Label("X", 45 * G, 0.0, 0))  # 埋在符号里面，走不出来
    before = copy.deepcopy(p)

    stats = connect_labels(p, keep_name=False)

    assert stats.wired == 0
    assert p.wires == before.wires and p.labels == before.labels


def test_without_names_a_finished_net_has_no_labels_left() -> None:
    p = Prims()
    _lane(p, "MISO", 5 * G, 0.0, 1)
    _lane(p, "MISO", 25 * G, 0.0, -1)

    assert connect_labels(p, keep_name=False).wired == 1
    assert p.labels == []


def test_a_detour_much_longer_than_the_gap_is_refused() -> None:
    """i2c_temp_sensor 的 SDA 曾绕到连接器左边再折回来：太绕的宁可留标签。"""
    p = Prims(symbols=[_block("WALL", (8 * G, -30 * G, 10 * G, 30 * G))])
    _lane(p, "SDA", 5 * G, 0.0, 1)
    _lane(p, "SDA", 14 * G, 0.0, -1)

    assert connect_labels(p).wired == 0
    assert len([lb for lb in p.labels if lb.text == "SDA"]) == 2


def test_the_same_input_always_gives_the_same_wires() -> None:
    def build() -> Prims:
        p = Prims(symbols=[_block("U1", (12 * G, -4 * G, 18 * G, 4 * G))])
        _lane(p, "A", 5 * G, 0.0, 1)
        _lane(p, "A", 25 * G, 6 * G, -1)
        _lane(p, "B", 5 * G, 6 * G, 1)
        _lane(p, "B", 25 * G, 12 * G, -1)
        return p

    one, two = build(), build()
    connect_labels(one)
    connect_labels(two)
    assert one.wires == two.wires and one.labels == two.labels and one.junctions == two.junctions


def test_off_grid_wires_are_left_alone() -> None:
    p = Prims(wires=[Wire((0.3, 0.3), (5.0, 0.3))])
    _lane(p, "SIG", 5 * G, 4 * G, 1)
    _lane(p, "SIG", 25 * G, 4 * G, -1)
    connect_labels(p)
    assert Wire((0.3, 0.3), (5.0, 0.3)) in p.wires
