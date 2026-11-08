"""GateReport：G1、G2、G3 共用的检查报告（PLAN §5.3）。
JSON Schema 导出到 schemas/gate_report.schema.json。

AGENTS.md 原则 1：`passed=True` 只能由 eda_agent/gates/ 里的代码写出。
这里的 make_report 是写出 passed 的统一做法：没有一条 severity 为 error 的 finding 才通过。

每条 finding 都有 id、severity、status（pass / fail / sim_error）、message、artifacts，
有数值的还有 measured、threshold、unit、margin。
G2 对每条验收标准都写一条，通过的也写（ADR-0016），汇合选优按余量排名。

余量（margin）：有符号的相对余量，正数表示有余量，负数表示超标。
    <=、<     (threshold - measured) / |threshold|
    >=、>     (measured - threshold) / |threshold|
    between   两端各算一个（下端按 >=，上端按 <=），取较小的那个，也就是离失败更近的那一端
    threshold 为 0 时没有量级可除：pass.scale 写了就拿它当分母；没写，余量只表示方向——
              满足 +1，刚好等于阈值 0，不满足 -1
status 由比较结果决定，不由余量的符号决定：严格不等式（<、>）刚好等于阈值时余量是 0，但判不通过。
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Severity = Literal["error", "warning", "info"]
Status = Literal["pass", "fail", "sim_error"]
Op = Literal["<=", ">=", "<", ">", "between"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Attempt(_Model):
    """G2 的一次仿真尝试（收敛重试要按顺序记下每一次，PLAN §7 G2）。"""

    name: Literal["default", "relaxed", "gear", "short_step"] = Field(
        description="default = 默认设置；relaxed = 放宽 gmin / reltol；gear = method=gear；"
        "short_step = 缩短步长并加 uic（瞬态），或加大迭代次数（其他分析）"
    )
    options: list[str] = Field(default_factory=list, description="这一次附加的 .options / 设置")
    outcome: Literal["ok", "error", "timeout"]
    error: str = ""
    netlist: str = Field(default="", description="这一次的网表，相对运行目录")


class Finding(_Model):
    id: str = Field(description="G2：验收标准编号（A1…）；G1、G3：规则或检查项名")
    severity: Severity
    status: Status = Field(
        description=(
            "pass = 通过；fail = 不满足；"
            "sim_error = 没能得出结果（不收敛、超时、模型缺失），不算通过，和 fail 分开统计"
        )
    )
    message: str
    measured: float | None = None
    threshold: float | None = Field(default=None, description="阈值；between 时是下限")
    threshold_high: float | None = Field(default=None, description="between 的上限")
    op: Op | None = None
    unit: str = ""
    margin: float | None = Field(
        default=None, description="有符号的相对余量，正数有余量、负数超标；定义见模块说明"
    )
    artifacts: list[str] = Field(
        default_factory=list, description="相关文件，相对运行目录，如 sim/A1.cir、sim/plots/A1.png"
    )
    attempts: list[Attempt] = Field(default_factory=list, description="G2：每次仿真尝试")
    detail: dict[str, Any] = Field(
        default_factory=dict, description="工具给的原始条目（KiCad 的违规项等），供回放和诊断用"
    )


class DeviceStress(_Model):
    """G2 的额外测量：一个器件在所有仿真里的峰值，给 G1 额定值检查的第二轮用。"""

    v_peak: float = Field(description="V，各引脚所在网络对地电压的最大绝对值")
    i_peak: float = Field(description="A，各引脚电流的最大绝对值")
    p_peak: float = Field(description="W，各引脚电压乘电流之和的最大值（瞬时功耗）")


class GateReport(_Model):
    gate: str
    passed: bool
    counts: dict[str, int] = Field(default_factory=dict)
    findings: list[Finding] = Field(default_factory=list)
    device_stress: dict[str, DeviceStress] = Field(
        default_factory=dict, description="G2：位号 → 峰值电压、电流、功耗"
    )
    net_current_peak: dict[str, float] = Field(
        default_factory=dict,
        description=(
            "G2：网络名 → 流过它的电流上界（A）：各引脚电流绝对值之和的一半，取时间上的最大值"
        ),
    )


def make_report(
    gate: str,
    findings: list[Finding],
    counts: dict[str, int] | None = None,
    **extra: Any,
) -> GateReport:
    """没有一条 severity 为 error 的 finding 才通过。"""
    return GateReport(
        gate=gate,
        passed=not any(f.severity == "error" for f in findings),
        counts=counts or {},
        findings=findings,
        **extra,
    )


def error_finding(id: str, message: str, **fields: Any) -> Finding:
    return Finding(id=id, severity="error", status="fail", message=message, **fields)


def warning_finding(id: str, message: str, **fields: Any) -> Finding:
    return Finding(id=id, severity="warning", status="fail", message=message, **fields)


# ---------------------------------------------------------------- 余量


def satisfies(
    measured: float, op: Op, threshold: float, threshold_high: float | None = None
) -> bool:
    """测量值是否满足判定条件。between 的两端都包含。"""
    if op == "<=":
        return measured <= threshold
    if op == "<":
        return measured < threshold
    if op == ">=":
        return measured >= threshold
    if op == ">":
        return measured > threshold
    if threshold_high is None:
        raise ValueError("between 需要 threshold_high")
    return threshold <= measured <= threshold_high


def _relative(distance: float, limit: float, scale: float | None) -> float:
    """distance：到这一端阈值的有符号距离（正 = 在允许的一侧）。"""
    denominator = abs(limit) if limit != 0 else scale
    if denominator is None:
        return float((distance > 0) - (distance < 0))
    return distance / denominator


def margin(
    measured: float,
    op: Op,
    threshold: float,
    threshold_high: float | None = None,
    scale: float | None = None,
) -> float:
    """有符号的相对余量，定义见模块说明。measured 必须是有限数。"""
    if not math.isfinite(measured):
        raise ValueError(f"测量值不是有限数：{measured}")
    if op in ("<=", "<"):
        return _relative(threshold - measured, threshold, scale)
    if op in (">=", ">"):
        return _relative(measured - threshold, threshold, scale)
    if threshold_high is None:
        raise ValueError("between 需要 threshold_high")
    return min(
        _relative(measured - threshold, threshold, scale),
        _relative(threshold_high - measured, threshold_high, scale),
    )


def margin_fields(
    measured: float,
    op: Op,
    threshold: float,
    threshold_high: float | None = None,
    *,
    unit: str = "",
    scale: float | None = None,
) -> dict[str, Any]:
    """Finding 里和数值有关的字段（measured、threshold、op、unit、margin）一次填好。"""
    return {
        "measured": measured,
        "threshold": threshold,
        "threshold_high": threshold_high,
        "op": op,
        "unit": unit,
        "margin": margin(measured, op, threshold, threshold_high, scale),
    }


def report_json_schema() -> dict[str, object]:
    """导出到 schemas/gate_report.schema.json 的内容。"""
    schema = GateReport.model_json_schema(mode="serialization")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "gate_report.schema.json"
    return schema
