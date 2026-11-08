"""G1 static（PLAN §7）：顺序执行，任一 error 即失败。

1. IR 校验（gates/ir_checks.py）：引用唯一、引脚在符号里存在、符号引脚数和封装焊盘数一致、
   spec 接口对得上 design 的网络。这一步有 error 就停：后面的规则要读符号，ERC 要画原理图。
2. 自写电气规则（gates/rules/）：悬空引脚、电源脚和地脚、两个输出驱动同一网络、去耦电容、
   额定值、封装可装配性。
3. KiCad ERC：沿用 kicad_reports.erc_gate，由调用方跑出 ERC 报告交进来（要先画出原理图）。

finding 的 id 带上来自哪一步：ir.…、rule.…、erc.…。判定只由这里的代码给出（AGENTS.md 原则 1）。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from eda_agent.gates.ir_checks import check_design, validate_document
from eda_agent.gates.report import Finding, GateReport, make_report
from eda_agent.gates.rules import RULES, RuleContext
from eda_agent.ir.models import Design
from eda_agent.ir.spec import Spec


def _prefixed(findings: list[Finding], prefix: str) -> list[Finding]:
    return [
        f if f.id.startswith(prefix) else f.model_copy(update={"id": f"{prefix}{f.id}"})
        for f in findings
    ]


def static_gate(
    design: Design,
    spec: Spec | None,
    *,
    symbol_dirs: tuple[Path, ...],
    footprint_dir: Path,
    derating: float = 0.8,
    sim: GateReport | None = None,
    erc: GateReport | None = None,
) -> GateReport:
    """G1。sim：G2 的报告，给了就走额定值检查的第二轮。
    erc：kicad-cli 的 ERC 结果（erc_gate 的返回）。"""
    ir = check_design(design, spec, symbol_dirs=symbol_dirs, footprint_dir=footprint_dir)
    findings = list(ir.findings)
    counts = {"ir_errors": sum(1 for f in ir.findings if f.severity == "error")}
    if not ir.ok:
        counts.update(rule_errors=0, rule_warnings=0, erc_errors=0, erc_warnings=0)
        return make_report("static", findings, counts)

    context = RuleContext(design=design, spec=spec, symbols=ir.symbols, derating=derating, sim=sim)
    rule_findings: list[Finding] = []
    for _, rule in RULES:
        rule_findings += rule(context)
    findings += _prefixed(rule_findings, "rule.")
    counts["rule_errors"] = sum(1 for f in rule_findings if f.severity == "error")
    counts["rule_warnings"] = len(rule_findings) - counts["rule_errors"]

    if erc is not None:
        findings += _prefixed(erc.findings, "erc.")
        counts["erc_errors"] = erc.counts.get("errors", len(erc.findings))
        counts["erc_warnings"] = erc.counts.get("warnings", 0)
    else:
        counts.update(erc_errors=0, erc_warnings=0)
    return make_report("static", findings, counts)


def static_gate_from_document(
    doc: Mapping[str, Any],
    spec: Spec | None,
    *,
    symbol_dirs: tuple[Path, ...],
    footprint_dir: Path,
    derating: float = 0.8,
    sim: GateReport | None = None,
    erc: GateReport | None = None,
) -> GateReport:
    """设计以原始 JSON 给进来（智能体的补丁应用之后、还没确认合法的时候）：读不进来也是一份报告。"""
    design, problems = validate_document(doc)
    if design is None:
        counts = {"ir_errors": len(problems), "rule_errors": 0, "rule_warnings": 0}
        return make_report("static", problems, counts | {"erc_errors": 0, "erc_warnings": 0})
    return static_gate(
        design,
        spec,
        symbol_dirs=symbol_dirs,
        footprint_dir=footprint_dir,
        derating=derating,
        sim=sim,
        erc=erc,
    )
