"""测试台生成器（PLAN §7 G2）：design.json + spec.acceptance[].testbench → ngspice 网表 .cir。

激励源、负载、电流探针、`.meas` 全部由这里的代码拼接，模型不参与。
同样的输入每次生成完全一样的文本（没有时间戳，路径都是相对的），有快照测试。

**器件模型的优先级**（PLAN §7，任务书 §4）：
1. ngspice 原生元件：Device 库的电阻、电容、电感按 value 直接写成 R、C、L；
2. `parts[].spice` 指向 `models/` 下的厂商模型：子电路接 X 元件，`.model` 卡按类型接 D、Q、M 元件；
   网表里有厂商模型就加 `set ngbehavior=ltpsa`（让 ngspice 按 LTspice / PSpice 的习惯读）；
3. 智能体拟合的行为模型是 M4 的事。
找不到模型的器件，这条验收标准是 sim_error（模型缺失），不会悄悄跳过
（BenchError.kind == "model_missing"）。

**连接器的处理**（review-queue D23）：连接器、测试点、安装孔（ir/parts.py 的 is_connector）
不进网表，它们接的网络照样存在；接口上的电源、信号源、负载直接接在网络上，
等于由接口激励代替了连接器。

**参考地**：spec 里 kind 为 ground 的接口，否则设计里按名字认出的地网络
（GND、AGND、VSS……，要唯一，多个时有名叫 GND 的就用它）。参考地是节点 0。

**电流探针**：每个进网表的元件的每个已接网络的引脚，串一个 0 V 电压源（VI_位号_引脚），
电流方向是网络 → 器件。有了它们就能量任何器件的引脚电流、峰值功耗（sim/stress.py），
也能支持 I(R1)、I(Q1.2)。接口上的源和负载也各串一个探针（VX_接口名、VL_load），
I(接口名) 取自它们。

**恒流负载**：电流源的电流乘一个平滑的开关 0.5·(1 + tanh((V−1)/0.1))，
两端电压低于约 1 V 时自己退出（像电子负载的欠压保护），
否则保护电路把开关断开以后，理想电流源会把节点拉到无穷（review-queue D24）。

**测量**：每条验收标准的测量写成 `.meas` 语句（名字 m_<编号小写>），
结果由 sim/measure.py 从 ngspice 的输出里读（review-queue D28）。
直流工作点（op）没有 `.meas`，取工作点向量；ac 里 freq_at_db 要对复数做比值再找交点，
由 sim/measure.py 在量出的复数向量上找。

网表里只用 ASCII（ngspice 对非 ASCII 字符的处理不可靠），网络名里的非 ASCII 字符换成下划线。
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from eda_agent.emit.sch_layout import infer_kind
from eda_agent.ir.models import Design, Part
from eda_agent.ir.parts import edge_refs, is_connector, passive_kind
from eda_agent.ir.spec import (
    Acceptance,
    AcSource,
    AcSweep,
    CurrentDcLoad,
    CurrentStepLoad,
    DcSource,
    DcSweep,
    Interface,
    Load,
    Measure,
    PulseSource,
    Quantity,
    ResistorLoad,
    SineSource,
    Source,
    Spec,
    StepSource,
    parse_condition,
)
from eda_agent.sim.spice_lib import MODEL_ELEMENTS, LibEntry, scan_library
from eda_agent.units import format_spice, parse_si

# 恒流负载欠压退出的参数：两端电压低于 LOAD_VMIN 时电流被压到零，过渡宽度 LOAD_VSOFT
LOAD_VMIN = 1.0
LOAD_VSOFT = 0.1
LTPSA = "ngbehavior=ltpsa"


class BenchError(Exception):
    """没法生成测试台。kind：model_missing = 器件没有可用的模型；
    spec = spec 写得不对或和设计对不上；design = 设计本身读不出来（元件值不是数）。"""

    def __init__(
        self,
        message: str,
        *,
        kind: Literal["model_missing", "spec", "design"] = "design",
        refs: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.refs = tuple(refs)


# ---------------------------------------------------------------- 收敛重试的各次设置


@dataclass(frozen=True)
class AttemptPlan:
    """一次仿真尝试的设置（PLAN §7 G2「收敛手段按顺序自动尝试」）。后一次在前一次的基础上再加码。"""

    name: Literal["default", "relaxed", "gear", "short_step"]
    options: tuple[str, ...] = ()  # 附加的 .options 内容
    uic: bool = False  # 瞬态加 uic，跳过直流工作点
    step_divisor: int = 1  # 瞬态最大步长缩短到几分之一


_RELAXED = ("gmin=1e-9", "reltol=0.01", "abstol=1e-9", "vntol=0.0001")
_GEAR = (*_RELAXED, "method=gear")
ATTEMPTS: tuple[AttemptPlan, ...] = (
    AttemptPlan("default"),
    AttemptPlan("relaxed", _RELAXED),
    AttemptPlan("gear", _GEAR),
    AttemptPlan("short_step", (*_GEAR, "itl1=1000", "itl2=500", "itl4=100"), True, 10),
)


# ---------------------------------------------------------------- 结果的描述


@dataclass(frozen=True)
class Probe:
    """网表里的一个电流探针（0 V 电压源）。

    电流方向：pin = 网络 → 器件；source = 电源 → 电路；load = 甲网络 → 负载 → 乙网络。
    """

    name: str  # 网表里的元件名，如 VI_R1_1
    kind: Literal["pin", "source", "load"]
    net: str  # 设计里的网络名（load：甲）
    ref: str = ""  # kind == pin：位号
    pin: str = ""
    interface: str = ""  # kind == source：接口名
    other_net: str = ""  # kind == load：乙


@dataclass(frozen=True)
class Signal:
    """被测信号：电压（两个节点之差）或电流（一个探针）。"""

    text: str  # spec 里的原文，如 V(OUT)
    kind: Literal["v", "i"]
    nodes: tuple[str, ...] = ()  # kind == v：一个或两个 SPICE 节点
    probe: str = ""  # kind == i：探针的元件名

    @property
    def spice(self) -> str:
        return f"v({','.join(self.nodes)})" if self.kind == "v" else f"i({self.probe})"


@dataclass(frozen=True)
class MeasurePlan:
    """这条验收标准要测什么，以及 .meas 语句的名字，供 sim/measure.py 解释结果。"""

    acceptance: str
    analysis: str
    measure: Measure
    signal: Signal
    ref_signal: Signal | None
    meas_names: tuple[str, ...]  # .meas 语句的名字（小写），按生成顺序
    condition: tuple[str, float] | None = None  # time_until 的 (比较符号, 数值)
    after: float = 0.0  # time_until：起算时刻（秒）
    sim_time: float = 0.0


@dataclass(frozen=True)
class Bench:
    acceptance_id: str
    analysis: str
    text: str
    includes: tuple[str, ...]  # 要放到 .cir 旁边的模型文件（相对仓库根目录的路径）
    settings: tuple[str, ...]  # 加载电路前的 ngspice 设置
    plan: MeasurePlan
    probes: tuple[Probe, ...]
    nodes: Mapping[str, str]  # 网络名 → SPICE 节点
    attempt: AttemptPlan
    # 每个进网表的元件：位号 → 它的引脚探针 {引脚: 探针名}
    part_probes: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    sweep_source: str = ""  # dc 扫描的电压源名
    ground: str = ""  # 参考地的网络名（节点 0）


# ---------------------------------------------------------------- 命名


def _ascii_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", lambda m: {"+": "p", "-": "m"}.get(m.group(), "_"), text)


class _Names:
    """网络名 → SPICE 节点名：保持可读，不重名（ngspice 不分大小写），不和地、gnd 混。"""

    def __init__(self) -> None:
        self.used: set[str] = {"0", "gnd"}

    def unique(self, base: str) -> str:
        name = base if base[:1].isalpha() else f"n_{base}"
        candidate, n = name, 1
        while candidate.lower() in self.used:
            n += 1
            candidate = f"{name}_{n}"
        self.used.add(candidate.lower())
        return candidate


def _fmt(value: float | str) -> str:
    return format_spice(parse_si(value))


# ---------------------------------------------------------------- 激励


def _step_pwl(before: float, after: float, at: Quantity, rise: Quantity) -> str:
    """PWL(…)：到 at 时刻为止是 before，at 起用 rise 的时间升（降）到 after。时刻必须严格递增。"""
    t, r = parse_si(at), parse_si(rise)
    if r <= 0:
        raise BenchError(f"上升时间必须为正：{rise}", kind="spec")
    pts = ([0.0, before] if t > 0 else []) + [t, before, t + r, after]
    return "PWL(" + " ".join(_fmt(p) for p in pts) + ")"


def _source_text(source: Source) -> str:
    """电压源的波形部分（V 元件行里节点后面的内容）。"""
    if isinstance(source, DcSource):
        return f"DC {_fmt(source.value)}"
    if isinstance(source, StepSource):
        return _step_pwl(source.from_, source.to, source.at, source.rise)
    if isinstance(source, PulseSource):
        fields = [
            source.low,
            source.high,
            parse_si(source.delay),
            parse_si(source.rise),
            parse_si(source.fall),
            parse_si(source.width),
            parse_si(source.period),
        ]
        return "PULSE(" + " ".join(_fmt(f) for f in fields) + ")"
    if isinstance(source, SineSource):
        return f"SIN({_fmt(source.offset)} {_fmt(source.amplitude)} {_fmt(parse_si(source.freq))})"
    assert isinstance(source, AcSource)
    return f"DC {_fmt(source.dc)} AC {_fmt(source.magnitude)}"


def _clamp(across: str) -> str:
    return f"0.5*(1+tanh(({across}-{_fmt(LOAD_VMIN)})/{_fmt(LOAD_VSOFT)}))"


# ---------------------------------------------------------------- 生成


@dataclass
class _Builder:
    design: Design
    spec: Spec
    acc: Acceptance
    models_root: Path
    attempt: AttemptPlan
    lines: list[str] = field(default_factory=list)
    includes: list[str] = field(default_factory=list)
    probes: list[Probe] = field(default_factory=list)
    nodes: dict[str, str] = field(default_factory=dict)
    part_probes: dict[str, dict[str, str]] = field(default_factory=dict)
    # 接口名 → 它的电流探针
    interface_probe: dict[str, str] = field(default_factory=dict)
    source_name: dict[str, str] = field(default_factory=dict)  # 接口名 → 电压源元件名
    missing: list[tuple[str, str]] = field(default_factory=list)  # (位号, 原因)
    ground: str = ""
    names: _Names = field(default_factory=lambda: _Names())

    # ---- 地和节点
    def find_ground(self) -> str:
        nets = [n.name for n in self.design.nets]
        declared = [i.name for i in self.spec.interfaces if i.kind == "ground"]
        if declared:
            if declared[0] not in nets:
                raise BenchError(f"spec 的地接口 {declared[0]} 在设计里没有同名的网络", kind="spec")
            return declared[0]
        grounds = [n for n in nets if infer_kind(n) == "ground"]
        if not grounds:
            raise BenchError(
                "设计里找不到地网络（名字像 GND、AGND、VSS……），"
                "spec 里也没有 kind 为 ground 的接口",
                kind="spec",
            )
        if len(grounds) > 1:
            named = [n for n in grounds if n.upper() == "GND"]
            if not named:
                raise BenchError(
                    f"设计里有多个地网络 {grounds}，请在 spec 里用 kind 为 ground 的接口指定参考地",
                    kind="spec",
                )
            return named[0]
        return grounds[0]

    def assign_nodes(self) -> None:
        self.ground = self.find_ground()
        for net in self.design.nets:
            self.nodes[net.name] = (
                "0" if net.name == self.ground else self.names.unique(_ascii_name(net.name))
            )

    def node(self, net: str, where: str) -> str:
        if net not in self.nodes:
            raise BenchError(f"{where}：设计里没有网络 {net}", kind="spec")
        return self.nodes[net]

    # ---- 元件
    def add_parts(self) -> None:
        edge = edge_refs(self.design)
        pin_net = {(r, p): n.name for n in self.design.nets for r, p in n.nodes}
        for part in self.design.parts:
            if is_connector(part, edge):
                continue
            pins = {p: n for (r, p), n in pin_net.items() if r == part.ref}
            try:
                self._add_part(part, pins)
            except _Missing as exc:
                self.missing.append((part.ref, str(exc)))

    def _pin_node(self, part: Part, pin: str, pins: Mapping[str, str]) -> str:
        """元件的一个引脚在网表里的节点：接了网络就串一个电流探针，没接的是悬空节点。"""
        safe = _ascii_name(f"{part.ref}_{pin}")
        if pin not in pins:
            return self.names.unique(f"nc_{safe}")
        node = self.names.unique(safe)
        probe = f"VI_{safe}"
        net = pins[pin]
        self.lines.append(f"{probe} {self.nodes[net]} {node} DC 0")
        self.probes.append(Probe(probe, "pin", net, part.ref, pin))
        self.part_probes.setdefault(part.ref, {})[pin] = probe
        return node

    def _add_part(self, part: Part, pins: Mapping[str, str]) -> None:
        if part.spice is not None:
            self._add_model_part(part, pins)
            return
        kind = passive_kind(part)
        if kind is None:
            raise _Missing(
                f"{part.symbol} 没有 spice 字段（模型），也不是 Device 库的电阻、电容、电感"
            )
        try:
            value = _fmt(part.value)
        except ValueError as exc:
            raise BenchError(
                f"元件 {part.ref} 的值 {part.value!r} 读不出来：{exc}",
                kind="design",
                refs=[part.ref],
            ) from exc
        if len(pins) != 2 or set(pins) != {"1", "2"}:
            raise BenchError(
                f"元件 {part.ref}（{part.symbol}）的 1、2 脚必须都接了网络才能当成 {kind}，"
                f"现在接了 {sorted(pins)}",
                kind="design",
                refs=[part.ref],
            )
        n1, n2 = (self._pin_node(part, p, pins) for p in ("1", "2"))
        self.lines.append(f"{_element_name(part.ref, kind)} {n1} {n2} {value}")

    def _add_model_part(self, part: Part, pins: Mapping[str, str]) -> None:
        spice = part.spice
        assert spice is not None
        if spice.lib is None:
            raise _Missing(f"spice.model {spice.model} 没有写 lib（模型文件）")
        lib_path = Path(spice.lib)
        full = (self.models_root / lib_path).resolve()
        if (
            lib_path.is_absolute()
            or ".." in lib_path.parts
            or not full.is_relative_to(self.models_root.resolve())
        ):
            raise _Missing(f"模型文件 {spice.lib} 必须是仓库里的相对路径")
        if not full.is_file():
            raise _Missing(f"模型文件 {spice.lib} 不存在")
        entry = scan_library(full).get(spice.model.lower())
        if entry is None:
            raise _Missing(f"{spice.lib} 里没有叫 {spice.model} 的 .subckt 或 .model")
        if spice.lib not in self.includes:
            self.includes.append(spice.lib)
        port_pin = {term.lower(): pin for pin, term in spice.pin_map.items()}
        mapped = set(spice.pin_map)
        stray = sorted(set(pins) - mapped)
        if stray:
            raise _Missing(f"引脚 {stray} 接了网络，但 spice.pin_map 里没有它们（模型表示不了）")
        terminals = self._terminals(entry, spice.model)
        unknown = sorted(t for t in port_pin if t not in {x.lower() for x in terminals})
        if unknown:
            raise _Missing(
                f"spice.pin_map 里的 {unknown} 不是模型 {spice.model} 的端子 {terminals}"
            )
        nodes: list[str] = []
        for term in terminals:
            pin = port_pin.get(term.lower())
            if pin is None:
                raise _Missing(f"模型端子 {term} 在 spice.pin_map 里没有对应的符号引脚")
            nodes.append(self._pin_node(part, pin, pins))
        if entry.kind == "subckt":
            self.lines.append(f"{_element_name(part.ref, 'X')} {' '.join(nodes)} {entry.name}")
            return
        letter = MODEL_ELEMENTS[entry.model_type][0]
        if entry.model_type in ("nmos", "pmos"):
            nodes.append(nodes[2])  # 衬底接源极
        self.lines.append(f"{_element_name(part.ref, letter)} {' '.join(nodes)} {entry.name}")

    @staticmethod
    def _terminals(entry: LibEntry, model: str) -> tuple[str, ...]:
        if entry.kind == "subckt":
            return entry.ports
        if entry.model_type not in MODEL_ELEMENTS:
            raise _Missing(f".model {model} 的类型 {entry.model_type} 还不支持")
        return MODEL_ELEMENTS[entry.model_type][1]

    # ---- 接口：电源、信号源、负载
    def add_interfaces(self) -> None:
        stim = self.acc.testbench.stimulus
        for iface in self.spec.interfaces:
            if iface.kind == "ground":
                continue
            node = self.node(iface.name, f"接口 {iface.name}")
            drive = stim.drive.get(iface.name)
            if drive is None and iface.kind == "power_in":
                volts = iface.nominal_v
                if volts is None:
                    raise BenchError(
                        f"接口 {iface.name} 是 power_in，可 spec 里没有电压"
                        "（v_nom / v_min / v_max）",
                        kind="spec",
                    )
                drive = DcSource(kind="dc", value=volts)
            if drive is None:
                continue
            if node == "0":
                raise BenchError(f"接口 {iface.name} 是参考地，不能再接源", kind="spec")
            self._add_source(iface, node, drive)
        if stim.load is not None:
            self._add_load(stim.load)

    def _add_source(self, iface: Interface, node: str, drive: Source) -> None:
        safe = _ascii_name(iface.name)
        ext = self.names.unique(f"x_{safe}")
        source, probe = f"VS_{safe}", f"VX_{safe}"
        self.lines.append(f"{source} {ext} 0 {_source_text(drive)}")
        self.lines.append(f"{probe} {ext} {node} DC 0")  # 电流：电源 → 电路
        self.probes.append(Probe(probe, "source", iface.name, interface=iface.name))
        self.interface_probe[iface.name] = probe
        self.source_name[iface.name] = source

    def _add_load(self, load: Load) -> None:
        outputs = [i.name for i in self.spec.interfaces if i.kind == "power_out"]
        between = load.between or (outputs[0], self.ground)
        a, b = (self.node(n, "负载 between") for n in between)
        mid = self.names.unique("load_mid")
        probe = "VL_load"
        self.lines.append(f"{probe} {a} {mid} DC 0")  # 电流：甲 → 负载 → 乙
        across = f"V({mid},{b})"
        if isinstance(load, CurrentDcLoad):
            self.lines.append(f"BL_load {mid} {b} I = {_fmt(load.value)}*{_clamp(across)}")
        elif isinstance(load, CurrentStepLoad):
            setpoint = self.names.unique("load_set")  # 1 V = 1 A
            wave = _step_pwl(load.from_, load.to, load.at, load.rise)
            self.lines.append(f"VSET_load {setpoint} 0 {wave}")
            self.lines.append(f"BL_load {mid} {b} I = V({setpoint})*{_clamp(across)}")
        else:
            assert isinstance(load, ResistorLoad)
            self.lines.append(f"RL_load {mid} {b} {_fmt(load.value)}")
        self.probes.append(Probe(probe, "load", between[0], other_net=between[1]))
        # I(接口名)：负载电流，方向按 between（甲 → 乙）。
        # 用在负载涉及的 power_out / signal_out 接口上
        for iface in self.spec.interfaces:
            if iface.kind in ("power_out", "signal_out") and iface.name in between:
                self.interface_probe[iface.name] = probe
        if not outputs and load.between is None:  # spec 校验已经拦过，保险
            raise BenchError("负载没写 between，又没有 power_out 接口", kind="spec")

    # ---- 信号
    def signal(self, text: str) -> Signal:
        kind, names = text[0].lower(), text[2:-1].split(",")
        if kind == "v":
            nodes = tuple(self.node(n, f"信号 {text}") for n in names)
            return Signal(text, "v", nodes=nodes)
        name = names[0]
        if name in self.interface_probe:
            return Signal(text, "i", probe=self.interface_probe[name])
        if any(i.name == name for i in self.spec.interfaces):
            raise BenchError(
                f"{text}：接口 {name} 上没有源也没有负载，测不了它的电流（可以改用 I(位号.引脚)）",
                kind="spec",
            )
        ref, _, pin = name.partition(".")
        pin = pin or "1"
        probe = self.part_probes.get(ref, {}).get(pin)
        if probe is None:
            part = next((p for p in self.design.parts if p.ref == ref), None)
            if part is not None and is_connector(part, edge_refs(self.design)):
                reason = "连接器不进网表，请改用接口名"
            elif part is None:
                reason = "设计里没有这个元件"
            else:
                reason = f"{ref} 的 {pin} 脚没接网络，或者元件没进网表"
            raise BenchError(f"{text}：没有电流探针（{reason}）", kind="spec")
        return Signal(text, "i", probe=probe)


class _Missing(Exception):
    """某个元件没有可用的模型（内部用，汇总成 BenchError）。"""


def _element_name(ref: str, letter: str) -> str:
    """SPICE 元件名的第一个字母决定类型。位号本来就以这个字母开头就直接用，否则前面加上。"""
    return ref if ref.upper().startswith(letter) else f"{letter}{ref}"


# ---------------------------------------------------------------- 分析和测量


def _analysis_lines(b: _Builder) -> list[str]:
    tb = b.acc.testbench
    if tb.type == "op":
        return [".op"]
    if tb.type == "transient":
        stop = parse_si(tb.sim_time) if tb.sim_time is not None else 0.0
        step = parse_si(tb.max_step) if tb.max_step is not None else stop / 1000
        step /= b.attempt.step_divisor
        uic = " uic" if b.attempt.uic else ""
        return [f".tran {_fmt(step)} {_fmt(stop)} 0 {_fmt(step)}{uic}"]
    if tb.type == "ac":
        sweep = tb.sweep
        assert isinstance(sweep, AcSweep)
        return [
            f".ac dec {sweep.points_per_decade} {_fmt(sweep.start)} {_fmt(sweep.stop)}",
        ]
    sweep = tb.sweep
    assert isinstance(sweep, DcSweep)
    source = b.source_name.get(sweep.source)
    if source is None:
        raise BenchError(f"dc 扫描的接口 {sweep.source} 上没有电压源", kind="spec")
    return [f".dc {source} {_fmt(sweep.start)} {_fmt(sweep.stop)} {_fmt(sweep.step)}"]


def _window(m: Measure) -> str:
    out = ""
    if m.from_ is not None:
        out += f" FROM={_fmt(m.from_)}"
    if m.to is not None:
        out += f" TO={_fmt(m.to)}"
    return out


def _meas_lines(b: _Builder, sig: Signal, ref: Signal | None) -> tuple[list[str], MeasurePlan]:
    acc, m = b.acc, b.acc.measure
    kind = m.kind
    analysis = {"transient": "tran", "ac": "ac", "dc": "dc", "op": ""}[acc.testbench.type]
    name = f"m_{acc.id.lower()}"
    lines: list[str] = []
    names: list[str] = []
    condition = None
    after = 0.0
    sim_time = parse_si(acc.testbench.sim_time) if acc.testbench.sim_time is not None else 0.0

    def add(label: str, body: str) -> None:
        lines.append(f".meas {analysis} {label} {body}")
        names.append(label)

    # 交流分析里信号是复数，取幅值
    expr = f"vm({','.join(sig.nodes)})" if analysis == "ac" and sig.kind == "v" else sig.spice
    if analysis == "ac" and sig.kind == "i":
        expr = f"im({sig.probe})"

    if analysis == "":  # op：没有 .meas，取工作点向量
        pass
    elif kind == "value_at":
        add(name, f"FIND {expr} AT={_fmt(m.at) if m.at is not None else 0}")
    elif kind in ("max", "min", "avg"):
        add(name, f"{kind.upper()} {expr}{_window(m)}")
    elif kind == "time_until":
        assert m.condition is not None
        op, level = parse_condition(m.condition)
        condition = (op, level)
        after = parse_si(m.after) if m.after is not None else 0.0
        edge = "FALL" if op in ("<", "<=") else "RISE"
        add(
            name,
            f"TRIG AT={_fmt(after)} TARG {sig.spice} VAL={_fmt(level)} {edge}=1 TD={_fmt(after)}",
        )
    elif kind == "frequency":
        assert m.level is not None
        td = f" TD={_fmt(m.from_)}" if m.from_ is not None else ""
        add(
            name,
            f"TRIG {sig.spice} VAL={_fmt(m.level)} RISE=1{td} "
            f"TARG {sig.spice} VAL={_fmt(m.level)} RISE={m.cycles + 1}{td}",
        )
    elif kind == "gain_db_at":
        assert m.at is not None
        add(f"{name}_out", f"FIND vdb({_vname(sig)}) AT={_fmt(m.at)}")
        if ref is not None:
            add(f"{name}_ref", f"FIND vdb({_vname(ref)}) AT={_fmt(m.at)}")
    elif kind == "freq_at_db":
        # 要先把输出除以参考信号再找交点（还可能相对通带），.meas 做不了复数的比值，
        # 这一种由 sim/measure.py 在复数向量上找交点；这里只检查信号写得对
        _vname(sig)
        if ref is not None:
            _vname(ref)
    plan = MeasurePlan(
        acc.id, acc.testbench.type, m, sig, ref, tuple(names), condition, after, sim_time
    )
    return lines, plan


def _vname(sig: Signal) -> str:
    """交流分析里 vdb() 要的是节点（或两点之差）。"""
    if sig.kind != "v":
        raise BenchError(f"{sig.text}：交流增益要量电压信号", kind="spec")
    return ",".join(sig.nodes)


def _default_ref(b: _Builder) -> Signal | None:
    """gain_db_at、freq_at_db 没写 ref_signal 时：
    唯一的 signal_in 接口的电压（有 ac 源的那个优先）。"""
    drive = b.acc.testbench.stimulus.drive
    ac_inputs = [
        i.name for i in b.spec.interfaces if i.name in drive and isinstance(drive[i.name], AcSource)
    ]
    inputs = [i.name for i in b.spec.interfaces if i.kind == "signal_in"]
    pick = ac_inputs if len(ac_inputs) == 1 else inputs if len(inputs) == 1 else []
    if len(pick) != 1:
        return None
    return Signal(f"V({pick[0]})", "v", nodes=(b.nodes[pick[0]],))


def build_bench(
    design: Design,
    spec: Spec,
    acceptance: Acceptance,
    *,
    models_root: Path,
    attempt: AttemptPlan = ATTEMPTS[0],
) -> Bench:
    """生成第 attempt 次尝试用的网表。
    models_root：parts[].spice.lib 的相对路径以它为准（仓库根目录）。"""
    b = _Builder(design, spec, acceptance, models_root, attempt)
    b.assign_nodes()
    b.add_parts()
    if b.missing:
        detail = "；".join(f"{ref}：{why}" for ref, why in b.missing)
        raise BenchError(
            f"模型缺失，仿真不能进行——{detail}",
            kind="model_missing",
            refs=[ref for ref, _ in b.missing],
        )
    b.add_interfaces()
    m = acceptance.measure
    sig = b.signal(m.signal)
    ref: Signal | None = None
    if m.kind in ("gain_db_at", "freq_at_db"):
        ref = b.signal(m.ref_signal) if m.ref_signal else _default_ref(b)
    analysis = _analysis_lines(b)
    meas, plan = _meas_lines(b, sig, ref)

    includes = tuple(sorted(b.includes))
    kind = acceptance.testbench.type
    header = [
        f"* eda_agent testbench {acceptance.id} ({kind}), attempt {attempt.name}",
        "* generated by eda_agent.sim.testbench - do not edit by hand",
    ]
    header += [f".include {path}" for path in includes]
    net_notes = [f"* net {n} = node {node}" for n, node in b.nodes.items() if n.isascii()]
    options = [f".options {' '.join(attempt.options)}"] if attempt.options else []
    text = "\n".join([*header, *net_notes, *b.lines, *options, *analysis, *meas, ".end"]) + "\n"
    return Bench(
        acceptance_id=acceptance.id,
        analysis=acceptance.testbench.type,
        text=text,
        includes=includes,
        settings=(LTPSA,) if includes else (),
        plan=plan,
        probes=tuple(b.probes),
        nodes=dict(b.nodes),
        attempt=attempt,
        part_probes={r: dict(p) for r, p in b.part_probes.items()},
        ground=b.ground,
        sweep_source=(
            b.source_name.get(acceptance.testbench.sweep.source, "")
            if isinstance(acceptance.testbench.sweep, DcSweep)
            else ""
        ),
    )


def is_finite(value: float) -> bool:
    return math.isfinite(value)
