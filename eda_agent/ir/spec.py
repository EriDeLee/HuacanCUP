"""spec.json：需求规格（PLAN §5.1）。JSON Schema 由这里导出到 schemas/spec.schema.json。

需求智能体起草、用户在 S1 确认后冻结（记 sha256），之后任何智能体都不能改。验收标准必须是仿真能测的
数值条件：`acceptance[]` 的 testbench 由 `eda_agent/sim/testbench.py` 确定性地拼成 ngspice 网表，
measure 的结果和 pass 比较，判定只由 `eda_agent/gates/sim.py` 给出（AGENTS.md 原则 1）。

多余字段一律报错，不静默忽略。字段说明写在 Field(description=...) 里，会进 JSON Schema。

和 design.json 的对应规则（review-queue D13）：接口名 = design 里的网络名，对不上就在 G1 报错。
时间、频率、电阻这类量可以写数字（国际单位制）或带前缀的字符串（"2ms"、"10k"），
见 eda_agent/units.py。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from eda_agent.units import UnitError, parse_si

NET_NAME = r"^[^\s/]+$"
# V(OUT)、V(OUT,FB)、I(VOUT)、I(R1)、I(Q1.2)
SIGNAL = r"^[VI]\([^()\s,]+(,[^()\s,]+)?\)$"
CONDITION = r"^\s*(<=|>=|<|>)\s*\S+\s*$"
_CONDITION_OP = re.compile(r"^\s*(<=|>=|<|>)\s*")

Quantity = float | str

# 每种仿真能给出的测量
KINDS_BY_ANALYSIS: dict[str, tuple[str, ...]] = {
    "op": ("value_at",),
    "dc": ("value_at", "max", "min", "avg"),
    "transient": ("value_at", "max", "min", "avg", "time_until", "frequency"),
    "ac": ("value_at", "gain_db_at", "freq_at_db", "max", "min"),
}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


def _check_quantities(**fields: Quantity | None) -> None:
    for name, value in fields.items():
        if value is None:
            continue
        try:
            parse_si(value)
        except UnitError as exc:
            raise ValueError(f"{name}：{exc}") from exc


def parse_condition(text: str) -> tuple[str, float]:
    """ "< 0.1" → ("<", 0.1)。"""
    m = _CONDITION_OP.match(text)
    if m is None:
        raise ValueError(f"条件要以 <、<=、>、>= 开头：{text!r}")
    return m.group(1), parse_si(text[m.end() :].strip())


# ---------------------------------------------------------------- 接口


class Interface(_Model):
    name: str = Field(
        pattern=NET_NAME,
        description="接口名。规则：接口名 = design.json 里的网络名，对不上就在 G1 报错",
    )
    kind: Literal["power_in", "power_out", "signal_in", "signal_out", "ground"] = Field(
        description=(
            "power_in = 板外给电的输入；power_out = 板子给负载供电的输出（或负载接在它上面）；"
            "signal_in / signal_out = 信号输入 / 输出；"
            "ground = 参考地（不写则按网络名认 GND、AGND……）"
        )
    )
    v_min: float | None = Field(default=None, description="电压下限（V）")
    v_max: float | None = Field(default=None, description="电压上限（V）")
    v_nom: float | None = Field(
        default=None,
        description=(
            "标称电压（V）。测试台给 power_in 接口接多少伏：写了用它，否则取 v_min 和 v_max 的中点"
        ),
    )
    i_max: float | None = Field(default=None, gt=0, description="最大电流（A）；G3 用它估计线宽")
    notes: str = ""

    @model_validator(mode="after")
    def _voltages(self) -> Interface:
        known = [v for v in (self.v_min, self.v_nom, self.v_max) if v is not None]
        if known != sorted(known):
            raise ValueError(f"接口 {self.name}：电压要满足 v_min ≤ v_nom ≤ v_max")
        if self.kind == "ground" and known:
            raise ValueError(f"接口 {self.name}：ground 不写电压")
        return self

    @property
    def nominal_v(self) -> float | None:
        """测试台和额定值检查（第一轮）用的标称电压；没有电压信息返回 None。"""
        if self.v_nom is not None:
            return self.v_nom
        if self.v_min is not None and self.v_max is not None:
            return (self.v_min + self.v_max) / 2
        return self.v_max if self.v_max is not None else self.v_min


# ---------------------------------------------------------------- 激励


class DcSource(_Model):
    kind: Literal["dc"]
    value: float = Field(description="直流电压（V）")


class StepSource(_Model):
    kind: Literal["step"]
    from_: float = Field(alias="from", description="阶跃前的电压（V）")
    to: float = Field(description="阶跃后的电压（V）")
    at: Quantity = Field(description="阶跃发生的时刻")
    rise: Quantity = Field(default="1us", description="上升时间")

    @model_validator(mode="after")
    def _times(self) -> StepSource:
        _check_quantities(at=self.at, rise=self.rise)
        return self


class PulseSource(_Model):
    kind: Literal["pulse"]
    low: float = Field(description="低电平（V）")
    high: float = Field(description="高电平（V）")
    delay: Quantity = Field(default=0.0, description="第一个脉冲前的延时")
    rise: Quantity = Field(default="1us")
    fall: Quantity = Field(default="1us")
    width: Quantity = Field(description="高电平持续时间")
    period: Quantity = Field(description="周期")

    @model_validator(mode="after")
    def _times(self) -> PulseSource:
        _check_quantities(
            delay=self.delay, rise=self.rise, fall=self.fall, width=self.width, period=self.period
        )
        return self


class SineSource(_Model):
    kind: Literal["sine"]
    offset: float = Field(default=0.0, description="直流偏置（V）")
    amplitude: float = Field(description="幅值（V）")
    freq: Quantity = Field(description="频率")

    @model_validator(mode="after")
    def _times(self) -> SineSource:
        _check_quantities(freq=self.freq)
        return self


class AcSource(_Model):
    kind: Literal["ac"]
    dc: float = Field(
        default=0.0, description="直流偏置（V）：放大器要先偏置在线性区，交流小信号才有意义"
    )
    magnitude: float = Field(
        default=1.0, gt=0, description="交流幅值（V）；小信号分析里只是数值的缩放"
    )


Source = Annotated[
    DcSource | StepSource | PulseSource | SineSource | AcSource, Field(discriminator="kind")
]


class CurrentDcLoad(_Model):
    kind: Literal["current_dc"]
    value: float = Field(description="负载电流（A）")
    between: tuple[str, str] | None = None


class CurrentStepLoad(_Model):
    kind: Literal["current_step"]
    from_: float = Field(alias="from", description="阶跃前的电流（A）")
    to: float = Field(description="阶跃后的电流（A）")
    at: Quantity = Field(description="阶跃发生的时刻")
    rise: Quantity = Field(default="1us", description="上升时间")
    between: tuple[str, str] | None = Field(
        default=None,
        description=(
            "[网络甲, 网络乙]：电流从甲经过负载流向乙。不写则 [唯一的 power_out 接口, 地]。"
            "负载接在两个电源网络之间（如低边开关的负载接在电池正极和开关漏极之间）时必须写"
        ),
    )

    @model_validator(mode="after")
    def _times(self) -> CurrentStepLoad:
        _check_quantities(at=self.at, rise=self.rise)
        return self


class ResistorLoad(_Model):
    kind: Literal["resistor"]
    value: Quantity = Field(description='电阻（Ω），如 10 或 "4k7"')
    between: tuple[str, str] | None = None

    @model_validator(mode="after")
    def _value(self) -> ResistorLoad:
        _check_quantities(value=self.value)
        if parse_si(self.value) <= 0:
            raise ValueError("负载电阻必须为正")
        return self


Load = Annotated[CurrentDcLoad | CurrentStepLoad | ResistorLoad, Field(discriminator="kind")]


class Stimulus(_Model):
    drive: dict[str, Source] = Field(
        default_factory=dict,
        description=(
            "接口名 → 电压源。不写的 power_in 接口按标称电压接直流源；"
            "signal_in 接口没写就不驱动（悬空，只靠电路自己的偏置）"
        ),
    )
    load: Load | None = Field(
        default=None,
        description=(
            "接在输出上的负载。恒流负载两端电压低于约 1 V 时自己退出"
            "（像电子负载的欠压保护），不会把节点拉到无穷"
        ),
    )


class AcSweep(_Model):
    start: Quantity = Field(description='起始频率，如 10 或 "10Hz"')
    stop: Quantity = Field(description='终止频率，如 "1MEG"')
    points_per_decade: int = Field(default=20, ge=1)

    @model_validator(mode="after")
    def _range(self) -> AcSweep:
        _check_quantities(start=self.start, stop=self.stop)
        if not 0 < parse_si(self.start) < parse_si(self.stop):
            raise ValueError("频率范围要满足 0 < start < stop")
        return self


class DcSweep(_Model):
    source: str = Field(description="被扫描的接口名（它的电压源）")
    start: float
    stop: float
    step: float = Field(gt=0)

    @model_validator(mode="after")
    def _range(self) -> DcSweep:
        if self.start == self.stop:
            raise ValueError("扫描的起点和终点不能相同")
        return self


class Testbench(_Model):
    type: Literal["op", "dc", "transient", "ac"] = Field(
        description="op = 直流工作点；dc = 直流扫描；transient = 瞬态；ac = 交流小信号"
    )
    stimulus: Stimulus = Field(default_factory=Stimulus)
    sim_time: Quantity | None = Field(default=None, description="transient 的仿真总时长")
    max_step: Quantity | None = Field(
        default=None, description="transient 的最大步长；不写取 sim_time / 1000"
    )
    sweep: AcSweep | DcSweep | None = Field(default=None, description="ac / dc 的扫描范围")

    @model_validator(mode="after")
    def _by_type(self) -> Testbench:
        _check_quantities(sim_time=self.sim_time, max_step=self.max_step)
        if self.type == "transient":
            if self.sim_time is None or parse_si(self.sim_time) <= 0:
                raise ValueError("transient 要写 sim_time（正数）")
        elif self.sim_time is not None or self.max_step is not None:
            raise ValueError(f"{self.type} 不用 sim_time / max_step")
        if self.type == "ac":
            if not isinstance(self.sweep, AcSweep):
                raise ValueError("ac 要写 sweep: {start, stop, points_per_decade}")
            if not any(s.kind == "ac" for s in self.stimulus.drive.values()):
                raise ValueError('ac 要在 stimulus.drive 里至少给一个接口 kind="ac" 的源')
        if self.type == "dc" and not isinstance(self.sweep, DcSweep):
            raise ValueError("dc 要写 sweep: {source, start, stop, step}")
        if self.type in ("op", "transient") and self.sweep is not None:
            raise ValueError(f"{self.type} 不用 sweep")
        return self


# ---------------------------------------------------------------- 测量和判定


class Measure(_Model):
    kind: Literal[
        "value_at", "max", "min", "avg", "time_until", "gain_db_at", "freq_at_db", "frequency"
    ] = Field(
        description=(
            "value_at = 某时刻（transient）/ 某频率（ac）/ 某扫描值（dc）处的值，op 不写 at；"
            "max、min、avg = 窗口里的最大、最小、平均；"
            "time_until = 从 after 起算，到条件首次成立的时间间隔"
            "（after 缺省为 0，这时就是条件首次成立的时刻）；"
            "gain_db_at = 交流增益（dB）在某频率处的值；"
            "freq_at_db = 交流增益第一次降到某个 dB 值的频率（-3 dB 点这类）；"
            "frequency = 周期信号的频率，取 level 处连续上升沿的间隔"
        )
    )
    signal: str = Field(
        pattern=SIGNAL,
        description=(
            "被测信号。V(网络或接口名) = 节点电压；V(甲,乙) = 两点之差；"
            "I(接口名) = 电流（power_in 接口：从电源流进电路为正；power_out：从电路流进负载为正）；"
            "I(位号) = 两脚元件从 1 脚流向 2 脚的电流；I(位号.引脚) = 流进该引脚的电流"
        ),
    )
    at: Quantity | None = Field(default=None, description="value_at、gain_db_at：测量点")
    from_: Quantity | None = Field(
        default=None, alias="from", description="max、min、avg、frequency：窗口起点，缺省从头"
    )
    to: Quantity | None = Field(default=None, description="窗口终点，缺省到尾")
    condition: str | None = Field(
        default=None,
        pattern=CONDITION,
        description='time_until：条件，如 "< 0.1"、">= 4.5"（数值用国际单位制）',
    )
    after: Quantity | None = Field(default=None, description="time_until：从这一刻起算")
    ref_signal: str | None = Field(
        default=None,
        pattern=SIGNAL,
        description=(
            "gain_db_at、freq_at_db：增益的参考（输入）信号；不写则取唯一的 signal_in 接口的电压"
        ),
    )
    db: float | None = Field(
        default=None, description="freq_at_db：目标增益（dB）；有 ref_at 时是相对 ref_at 处增益的差"
    )
    ref_at: Quantity | None = Field(
        default=None,
        description='freq_at_db：写了就按"比 ref_at 处低多少 dB"算，如 db=-3、ref_at="100"',
    )
    level: float | None = Field(default=None, description="frequency：判上升沿的电平")
    cycles: int = Field(default=1, ge=1, description="frequency：测几个周期取平均")

    @field_validator("condition")
    @classmethod
    def _condition(cls, value: str | None) -> str | None:
        if value is not None:
            parse_condition(value)
        return value

    @model_validator(mode="after")
    def _by_kind(self) -> Measure:
        _check_quantities(
            at=self.at, from_=self.from_, to=self.to, after=self.after, ref_at=self.ref_at
        )
        required = {
            "gain_db_at": "at",
            "time_until": "condition",
            "freq_at_db": "db",
            "frequency": "level",
        }
        field = required.get(self.kind)
        if field is not None and getattr(self, field) is None:
            raise ValueError(f"{self.kind} 要写 {field}")
        if self.at is not None and self.kind not in ("value_at", "gain_db_at"):
            raise ValueError(f"{self.kind} 不用 at")
        if (self.from_ is not None or self.to is not None) and self.kind not in (
            "max",
            "min",
            "avg",
            "frequency",
        ):
            raise ValueError(f"{self.kind} 不用 from / to")
        return self


class Pass(_Model):
    op: Literal["<=", ">=", "<", ">", "between"]
    value: float | None = Field(default=None, description="阈值；between 不写")
    low: float | None = Field(default=None, description="between 的下限")
    high: float | None = Field(default=None, description="between 的上限")
    unit: str = Field(default="", description="单位，只用于显示，如 s、V、dB、Hz")
    scale: float | None = Field(
        default=None,
        gt=0,
        description=(
            "阈值为 0 时余量的分母（量级）。不写则余量只表示方向：满足 +1、刚好 0、不满足 -1"
        ),
    )

    @model_validator(mode="after")
    def _by_op(self) -> Pass:
        if self.op == "between":
            if self.low is None or self.high is None or self.value is not None:
                raise ValueError("between 要写 low 和 high，不写 value")
            if self.low >= self.high:
                raise ValueError("between 要满足 low < high")
        elif self.value is None or self.low is not None or self.high is not None:
            raise ValueError(f"{self.op} 要写 value，不写 low / high")
        return self


class Acceptance(_Model):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$", description="验收标准编号，如 A1")
    text: str = Field(min_length=1, description="一句话，说给用户听的")
    testbench: Testbench
    measure: Measure
    pass_: Pass = Field(alias="pass")

    @model_validator(mode="after")
    def _measure_fits_the_analysis(self) -> Acceptance:
        analysis, kind = self.testbench.type, self.measure.kind
        if kind not in KINDS_BY_ANALYSIS[analysis]:
            usable = list(KINDS_BY_ANALYSIS[analysis])
            raise ValueError(f"{self.id}：{analysis} 分析测不了 {kind}，可用的有 {usable}")
        if kind == "value_at" and analysis != "op" and self.measure.at is None:
            raise ValueError(f"{self.id}：{analysis} 分析的 value_at 要写 at")
        if kind == "value_at" and analysis == "op" and self.measure.at is not None:
            raise ValueError(f"{self.id}：op 分析的 value_at 不写 at")
        return self


# ---------------------------------------------------------------- 约束和整份 spec


class Constraints(_Model):
    board_max_mm: tuple[float, float] | None = Field(
        default=None, description="板子外形的最大宽、高；可以转 90° 放"
    )
    layers_max: Literal[2, 4] | None = None
    assembly: Literal["hand_solder", "reflow", "smt_service"] | None = Field(
        default=None,
        description=(
            "hand_solder = 手焊，不能用 QFN、DFN、BGA、LGA、WLCSP 这类焊盘在底下的封装；"
            "reflow、smt_service 不限制封装类型"
        ),
    )
    min_package: str | None = Field(
        default=None,
        pattern=r"^\d{4}$",
        description='贴片阻容的最小英制尺寸，如 "0805"；比它小的（0603、0402……）不允许',
    )
    prefer_inventory: bool = False
    budget_cny: float | None = Field(default=None, gt=0)


class Spec(_Model):
    id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    interfaces: list[Interface] = Field(default_factory=list)
    acceptance: list[Acceptance] = Field(default_factory=list)
    constraints: Constraints = Field(default_factory=Constraints)
    assumptions: list[str] = Field(default_factory=list)
    untestable: list[str] = Field(
        default_factory=list, description="写不出可测条件的需求，在 S1 让用户看到"
    )
    frozen_sha256: str | None = Field(default=None, description="冻结时的 sha256；冻结前为 null")

    @model_validator(mode="after")
    def _references(self) -> Spec:
        names = [i.name for i in self.interfaces]
        if len(set(names)) != len(names):
            raise ValueError(f"接口名重复：{sorted({n for n in names if names.count(n) > 1})}")
        ids = [a.id for a in self.acceptance]
        if len(set(ids)) != len(ids):
            raise ValueError(f"验收标准编号重复：{sorted({n for n in ids if ids.count(n) > 1})}")
        outputs = [i.name for i in self.interfaces if i.kind == "power_out"]
        for a in self.acceptance:
            stim = a.testbench.stimulus
            for key in stim.drive:
                if key not in names:
                    raise ValueError(f"{a.id} 的 drive 里有不是接口的名字：{key}")
            load = stim.load
            if load is not None and load.between is None and len(outputs) != 1:
                raise ValueError(
                    f"{a.id} 的负载没写 between，而 power_out 接口有 {len(outputs)} 个，"
                    "不知道接在哪"
                )
            sweep = a.testbench.sweep
            if isinstance(sweep, DcSweep) and sweep.source not in names:
                raise ValueError(f"{a.id} 的扫描对象 {sweep.source} 不是接口")
        return self

    def interface(self, name: str) -> Interface | None:
        return next((i for i in self.interfaces if i.name == name), None)


def load_spec(path: Path) -> Spec:
    return Spec.model_validate_json(path.read_text(encoding="utf-8"))


def spec_json_schema() -> dict[str, object]:
    """导出到 schemas/spec.schema.json 的内容（字段名用 JSON 里的写法，如 from、pass）。"""
    schema = Spec.model_json_schema(by_alias=True, mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "spec.schema.json"
    return schema
