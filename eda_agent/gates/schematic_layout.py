"""检查自动生成的原理图有没有把符号摆到图纸外面（M0 实测发现：生成器报成功，图却是空的）。

只看符号的放置点是否落在图纸范围内；可读性本身还要靠人看。
"""

from __future__ import annotations

import re
from pathlib import Path

from eda_agent.gates.report import Finding, GateReport, error_finding, make_report

# KiCad 图纸尺寸（mm，横向）。
PAPER_MM = {
    "A5": (210.0, 148.0),
    "A4": (297.0, 210.0),
    "A3": (420.0, 297.0),
    "A2": (594.0, 420.0),
    "A1": (841.0, 594.0),
    "A0": (1189.0, 841.0),
}
_PAPER = re.compile(r'\(paper\s+"([^"]+)"(\s+portrait)?')
_SYMBOL_AT = re.compile(r'\(symbol\s*\(lib_id\s+"([^"]+)"\)\s*\(at\s+([-\d.]+)\s+([-\d.]+)')
_REF = re.compile(r'\(property\s+"Reference"\s+"([^"]+)"')


def layout_gate(sch: Path) -> GateReport:
    text = sch.read_text(encoding="utf-8")
    paper = _PAPER.search(text)
    if paper is None or paper.group(1) not in PAPER_MM:
        return make_report(
            "schematic_layout",
            [
                error_finding(
                    "paper_unknown", f"不认识的图纸尺寸：{paper.group(1) if paper else '无'}"
                )
            ],
            {"symbols": 0, "outside": 0},
        )
    width, height = PAPER_MM[paper.group(1)]
    if paper.group(2):
        width, height = height, width
    findings: list[Finding] = []
    symbols = list(_SYMBOL_AT.finditer(text))
    if not symbols:
        findings.append(error_finding("no_symbols", "原理图里一个符号都没有"))
    for m in symbols:
        x, y = float(m.group(2)), float(m.group(3))
        if not (0 <= x <= width and 0 <= y <= height):
            ref = _REF.search(text, m.end())
            name = ref.group(1) if ref else "?"
            findings.append(
                error_finding(
                    "symbol_outside",
                    f"{name} 在 {paper.group(1)} 图纸（{width:.0f}×{height:.0f} mm）外面",
                    detail={"ref": name, "lib_id": m.group(1), "at_mm": [x, y]},
                )
            )
    outside = sum(1 for f in findings if f.id == "symbol_outside")
    return make_report("schematic_layout", findings, {"symbols": len(symbols), "outside": outside})
