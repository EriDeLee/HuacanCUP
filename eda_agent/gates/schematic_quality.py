"""版面引擎的两道确定性检查（AGENTS.md 原则 1：通过与否只由这里的代码决定）。

- layout_quality：画出来的图有没有符号互相压住、文字压住符号或别的文字、导线穿过元件本体、
  导线从文字上穿过（ADR-0014 加的）。
  导线交叉只计数，不判失败（交叉本身不是错，但数量会写进报告，方便比较版面好坏）。
- netlist_gate：KiCad 重新导出的网表，和输入的网络逐个对得上；没有拆开的网络，也没有短路。
"""

from __future__ import annotations

from pathlib import Path

from eda_agent.emit.sch_layout import Box, Drawing, NetSpec, Point, SymbolInst
from eda_agent.gates.kicad_reports import GateReport
from eda_agent.kicad_side import sexpr


def _overlap(a: Box, b: Box, shrink: float = 0.0) -> bool:
    return (
        a[0] + shrink < b[2] - shrink
        and b[0] + shrink < a[2] - shrink
        and a[1] + shrink < b[3] - shrink
        and b[1] + shrink < a[3] - shrink
    )


def _leaves_a_pin(a: Point, b: Point, s: SymbolInst) -> bool:
    """这根线是不是从符号的某个引脚直接往外拉出去的。

    符号的「本体外框」可能比引脚还长（比如发光二极管的光线箭头），
    从引脚往外的那一小段不可能穿过它自己的本体，这种情况不算穿过。
    """
    for number, (px, py) in s.pins.items():
        out = s.pin_dirs.get(number)
        if out is None:
            continue
        for start, end in ((a, b), (b, a)):
            if abs(start[0] - px) < 1e-6 and abs(start[1] - py) < 1e-6:
                dx, dy = end[0] - start[0], end[1] - start[1]
                along = dx * out[0] + dy * out[1]
                across = abs(dx * out[1]) + abs(dy * out[0])
                if along > 1e-6 and across < 1e-6:
                    return True
    return False


def _count_crossings(drawing: Drawing) -> int:
    wires = drawing.prims.wires
    crossings = 0
    for i, a in enumerate(wires):
        for b in wires[i + 1 :]:
            a_vertical = abs(a.a[0] - a.b[0]) < 1e-6
            if a_vertical == (abs(b.a[0] - b.b[0]) < 1e-6):
                continue
            v, h = (a, b) if a_vertical else (b, a)
            if (
                min(h.a[0], h.b[0]) + 1e-6 < v.a[0] < max(h.a[0], h.b[0]) - 1e-6
                and min(v.a[1], v.b[1]) + 1e-6 < h.a[1] < max(v.a[1], v.b[1]) - 1e-6
            ):
                crossings += 1
    return crossings


def layout_quality(drawing: Drawing) -> GateReport:
    p = drawing.prims
    parts = [s for s in p.symbols if not s.power]
    findings: list[dict[str, object]] = []
    for i, a in enumerate(parts):
        for b in parts[i + 1 :]:
            if _overlap(a.box, b.box, 0.1):
                findings.append({"type": "symbol_overlap", "items": [a.ref, b.ref]})
    texts = [(s.ref, tb) for s in p.symbols for tb in s.text_boxes()]
    texts += [(lb.text, lb.box) for lb in p.labels]
    texts += [(n.text, n.box) for n in p.notes]
    for name, tb in texts:
        for s in parts:
            if s.ref != name and _overlap(tb, s.box, 0.15):
                findings.append({"type": "text_over_symbol", "items": [name, s.ref]})
    for i, (n1, t1) in enumerate(texts):
        for n2, t2 in texts[i + 1 :]:
            if _overlap(t1, t2, 0.15):
                findings.append({"type": "text_overlap", "items": [n1, n2]})
    for w in p.wires:
        wb = (min(w.a[0], w.b[0]), min(w.a[1], w.b[1]), max(w.a[0], w.b[0]), max(w.a[1], w.b[1]))
        for s in p.symbols:
            if s.core and _overlap(wb, s.core, 0.2) and not _leaves_a_pin(w.a, w.b, s):
                findings.append({"type": "wire_through_symbol", "items": [s.ref]})
        # 线从文字上穿过（ADR-0014，用户 2026-11-01 指出 ldo_3v3 的 +3V3 线穿过 U1 的型号）。
        # 标签的锚点就在线头上，线只碰到它的边，不算穿过。
        for name, tb in texts:
            if _overlap(wb, tb, 0.15):
                findings.append({"type": "wire_through_text", "items": [name]})
    counts = {
        "parts": len(parts),
        "wires": len(p.wires),
        "labels": len(p.labels),
        "wire_crossings": _count_crossings(drawing),
        "failures": len(findings),
    }
    return GateReport(
        gate="schematic_quality", passed=not findings, counts=counts, findings=findings
    )


def exported_nets(netlist: Path) -> dict[tuple[str, str], str]:
    """kicad-cli 导出的 kicadsexpr 网表 → {(位号, 引脚): 网络名}。"""
    export = sexpr.parse(netlist.read_text(encoding="utf-8"))[0]
    out: dict[tuple[str, str], str] = {}
    for net in sexpr.children(sexpr.child(export, "nets"), "net"):
        name = sexpr.value(net, "name")
        for node in sexpr.children(net, "node"):
            out[(sexpr.value(node, "ref"), sexpr.value(node, "pin"))] = name
    return out


def netlist_gate(nets: list[NetSpec], netlist: Path) -> GateReport:
    actual = exported_nets(netlist)
    findings: list[dict[str, object]] = []
    owner: dict[str, str] = {}
    for net in nets:
        missing = sorted(f"{r}.{p}" for r, p in net.pins if (r, p) not in actual)
        if missing:
            findings.append({"type": "pin_missing", "net": net.name, "pins": missing})
        names = {actual[pin] for pin in net.pins if pin in actual}
        if len(names) > 1:
            findings.append({"type": "net_split", "net": net.name, "into": sorted(names)})
        for name in names:
            if owner.setdefault(name, net.name) != net.name:
                findings.append({"type": "short", "nets": [owner[name], net.name], "in": name})
    counts = {"nets": len(nets), "failures": len(findings)}
    return GateReport(
        gate="netlist_equivalence", passed=not findings, counts=counts, findings=findings
    )
