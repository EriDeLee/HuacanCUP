"""G3 drc（PLAN §7）：KiCad DRC（含原理图一致性）加三项检查。

1. 未布通的网络数 = 0：数 DRC 报告里「缺连接」条目涉及多少个网络（条目描述里 [方括号] 是网络名）。
2. 板尺寸 ≤ spec.constraints.board_max_mm：量 PCB 里 Edge.Cuts 的外包框，可以转 90° 放。
3. 电源网络线宽 ≥ 按电流估算的最小值（IPC-2221，eda_agent/pcb/trace_width.py）。
   网络电流 = max(spec 接口的 i_max, G2 仿真里这个网络的电流上界)；没有电流数据的网络不查，
   只在 counts.width_unchecked 里计数。外层（F.Cu、B.Cu）和内层（In*.Cu）各按各的 k 值查。

判定只由这里的代码给出（AGENTS.md 原则 1）。
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from eda_agent.gates.kicad_reports import drc_gate
from eda_agent.gates.report import GateReport, error_finding, make_report, margin_fields
from eda_agent.ir.models import Design
from eda_agent.ir.spec import Spec
from eda_agent.kicad_side import sexpr
from eda_agent.pcb.trace_width import layer_kind, min_width_mm

_NET_IN_BRACKETS = re.compile(r"\[([^\[\]]*)\]")


@dataclass(frozen=True)
class PcbTrack:
    net: str
    layer: str
    width_mm: float
    length_mm: float


@dataclass
class PcbInfo:
    tracks: list[PcbTrack] = field(default_factory=list)
    outline: tuple[float, float, float, float] | None = None  # x0, y0, x1, y1（mm）

    @property
    def size_mm(self) -> tuple[float, float] | None:
        if self.outline is None:
            return None
        x0, y0, x1, y1 = self.outline
        return (x1 - x0, y1 - y0)


def _xy(node: sexpr.Node | None) -> tuple[float, float] | None:
    if node is None or len(node) < 3:
        return None
    return (float(node[1]), float(node[2]))


def _outline_points(item: sexpr.Node) -> list[tuple[float, float]]:
    tag = item[0]
    if tag in ("gr_line", "gr_rect"):
        pts = [_xy(sexpr.child(item, "start")), _xy(sexpr.child(item, "end"))]
    elif tag == "gr_arc":
        pts = [_xy(sexpr.child(item, k)) for k in ("start", "mid", "end")]
    elif tag == "gr_circle":
        center, edge = _xy(sexpr.child(item, "center")), _xy(sexpr.child(item, "end"))
        if center is None or edge is None:
            return []
        r = math.dist(center, edge)
        pts = [(center[0] - r, center[1] - r), (center[0] + r, center[1] + r)]
    elif tag == "gr_poly":
        pts = [_xy(p) for p in sexpr.children(sexpr.child(item, "pts"), "xy")]
    else:
        return []
    return [p for p in pts if p is not None]


def read_pcb(pcb: Path) -> PcbInfo:
    """.kicad_pcb 里的走线（网络、层、线宽、长度）和板框外包框。KiCad 10 把网络写成名字，
    早期版本写成编号并在文件开头声明，两种都认。"""
    top = sexpr.parse(pcb.read_text(encoding="utf-8"))[0]
    names = {str(n[1]): str(n[2]) for n in sexpr.children(top, "net") if len(n) >= 3}
    info = PcbInfo()
    for seg in sexpr.children(top, "segment"):
        start, end = _xy(sexpr.child(seg, "start")), _xy(sexpr.child(seg, "end"))
        if start is None or end is None:
            continue
        net = sexpr.value(seg, "net")
        info.tracks.append(
            PcbTrack(
                net=names.get(net, net),
                layer=sexpr.value(seg, "layer"),
                width_mm=float(sexpr.value(seg, "width", "0")),
                length_mm=math.dist(start, end),
            )
        )
    points: list[tuple[float, float]] = []
    for item in top[1:]:
        is_graphic = isinstance(item, list) and item and str(item[0]).startswith("gr_")
        if is_graphic and sexpr.value(item, "layer") == "Edge.Cuts":
            points += _outline_points(item)
    if points:
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        info.outline = (min(xs), min(ys), max(xs), max(ys))
    return info


def unrouted_nets(report: Path) -> dict[str, int]:
    """DRC 报告里「缺连接」涉及的网络 → 缺几处连接。描述里没有网络名的算到 "?" 上。"""
    data = json.loads(report.read_text(encoding="utf-8"))
    out: dict[str, int] = {}
    for item in data.get("unconnected_items", []):
        if item.get("severity") != "error":
            continue
        names = {
            m.group(1)
            for sub in item.get("items", [])
            for m in [_NET_IN_BRACKETS.search(str(sub.get("description", "")))]
            if m is not None
        }
        for name in names or {"?"}:
            out[name] = out.get(name, 0) + 1
    return dict(sorted(out.items()))


def pcb_net_names(design: Design, actual: Mapping[tuple[str, str], str]) -> dict[str, str]:
    """设计里的网络名 → KiCad 网表 / PCB 里的网络名。actual 是 exported_nets(netlist) 的结果。

    没有标签的网络 KiCad 会自己起名（如 Net-(D1-A)），多页时本页网络带页名前缀，所以按引脚找。
    网表里拆成多个网络的（net_split）不进映射，那是网表一致检查报的。
    """
    out: dict[str, str] = {}
    for net in design.nets:
        found = {actual[(r, p)] for r, p in net.nodes if (r, p) in actual}
        if len(found) == 1:
            out[net.name] = found.pop()
    return out


def estimate_net_currents(spec: Spec | None, sim: GateReport | None) -> dict[str, float]:
    """每个网络的电流估计（A）：spec 接口的 i_max 和 G2 仿真的电流上界，取大的（review-queue D21）。

    G2 的上界是「各引脚电流绝对值之和的一半」在时间上的最大值，任何一段走线上的电流都不会超过它。
    """
    out: dict[str, float] = {}
    if spec is not None:
        out.update({i.name: i.i_max for i in spec.interfaces if i.i_max is not None})
    if sim is not None:
        for net, amps in sim.net_current_peak.items():
            out[net] = max(out.get(net, 0.0), amps)
    return dict(sorted(out.items()))


def drc_checks(
    *,
    drc_report: Path,
    pcb: Path,
    spec: Spec | None = None,
    net_names: Mapping[str, str] | None = None,
    currents: Mapping[str, float] | None = None,
    copper_um: float = 35.0,
    temp_rise_c: float = 10.0,
    min_segment_mm: float = 0.0,
) -> GateReport:
    """G3。net_names 是设计网络名 → PCB 网络名（pcb_net_names）；不给就当两边同名。

    min_segment_mm：比它短的走线段不参加线宽检查（布线器在焊盘旁边会把线收窄一小段）；
    默认 0 = 都查。
    """
    base = drc_gate(drc_report)
    # KiCad 的「缺连接」条目由下面的 unrouted_nets 汇总成网络数，不重复报
    findings = [f for f in base.findings if f.id != "unconnected_items"]
    counts = dict(base.counts)

    missing = unrouted_nets(drc_report)
    counts["unrouted_nets"] = len(missing)
    if missing:
        findings.append(
            error_finding(
                "unrouted_nets",
                f"{len(missing)} 个网络没布通：{', '.join(missing)}",
                **margin_fields(float(len(missing)), "<=", 0.0, unit="个"),
                detail={"nets": missing},
            )
        )

    info = read_pcb(pcb)
    limit = spec.constraints.board_max_mm if spec is not None else None
    size = info.size_mm
    counts["board_oversize"] = 0
    if limit is not None and size is not None:
        long_, short = sorted(size, reverse=True)
        max_long, max_short = sorted(limit, reverse=True)
        for label, value, allowed in (("长边", long_, max_long), ("短边", short, max_short)):
            if value > allowed + 1e-6:
                counts["board_oversize"] += 1
                findings.append(
                    error_finding(
                        "board_size",
                        f"板子{label} {value:.2f} mm，超过 spec 允许的 {allowed:g} mm",
                        **margin_fields(value, "<=", allowed, unit="mm"),
                        detail={"board_mm": list(size), "board_max_mm": list(limit)},
                    )
                )

    # width_unchecked：有电流数据，但在网表里找不到这个网络，或者板上没有这个网络的走线可量
    counts.update(width_checked=0, width_failed=0, width_unchecked=0)
    by_net: dict[str, list[PcbTrack]] = {}
    for track in info.tracks:
        by_net.setdefault(track.net, []).append(track)
    for net, amps in sorted((currents or {}).items()):
        if net_names is not None and net not in net_names:
            counts["width_unchecked"] += 1
            continue
        pcb_net = net_names[net] if net_names is not None else net
        measured_any = False
        for kind in ("external", "internal"):
            tracks = [
                t
                for t in by_net.get(pcb_net, [])
                if layer_kind(t.layer) == kind and t.length_mm >= min_segment_mm
            ]
            if not tracks:
                continue
            measured_any = True
            need = min_width_mm(amps, copper_um=copper_um, temp_rise_c=temp_rise_c, layer=kind)
            narrowest = min(tracks, key=lambda t: t.width_mm)
            counts["width_checked"] += 1
            if narrowest.width_mm + 1e-9 < need:
                counts["width_failed"] += 1
                where = "外层" if kind == "external" else "内层"
                findings.append(
                    error_finding(
                        "trace_width",
                        f"网络 {net}（按 {amps:g} A 估计）在{where}"
                        f"最窄的线宽 {narrowest.width_mm:g} mm，IPC-2221 要求至少 {need:.3f} mm"
                        f"（铜厚 {copper_um:g} µm、温升 {temp_rise_c:g} °C）",
                        **margin_fields(narrowest.width_mm, ">=", need, unit="mm"),
                        detail={
                            "net": net,
                            "pcb_net": pcb_net,
                            "layer": narrowest.layer,
                            "current_a": amps,
                        },
                    )
                )
        if not measured_any:
            counts["width_unchecked"] += 1
    return make_report("drc", findings, counts)
