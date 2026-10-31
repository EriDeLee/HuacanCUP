"""kicad-cli 封装。每条命令的参数都按 KiCad 10.0.8 的 --help 输出核对过（ADR-0002）。"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from pathlib import Path
from typing import Literal

from eda_agent.config import ToolsConfig
from eda_agent.tools.runner import ToolResult, run_tool
from eda_agent.tools.sandbox import sandbox_env
from eda_agent.trace import TraceWriter

# --exit-code-violations 打开时，有违规返回 5，报告照常写出。
VIOLATIONS_EXIT_CODE = 5
Severity = Literal["error", "all"]


class KicadCli:
    def __init__(
        self, tools: ToolsConfig, *, log_dir: Path, trace: TraceWriter | None = None
    ) -> None:
        self.tools = tools
        self.log_dir = log_dir
        self.trace = trace
        self.env = sandbox_env(tools)

    def run(
        self,
        args: Sequence[str | Path],
        *,
        cwd: Path,
        ok_codes: Collection[int] = (0,),
        timeout_s: float | None = None,
    ) -> ToolResult:
        result = run_tool(
            "kicad-cli",
            [self.tools.kicad_cli, *args],
            cwd=cwd,
            env=self.env,
            timeout_s=timeout_s or self.tools.timeouts.default_s,
            log_dir=self.log_dir,
            trace=self.trace,
        )
        return result.check(ok_codes)

    def version(self) -> str:
        return self.run(["version"], cwd=self.log_dir).stdout.strip()

    def sch_erc(self, sch: Path, report: Path, *, severity: Severity = "error") -> ToolResult:
        return self.run(
            [
                "sch",
                "erc",
                "--format",
                "json",
                f"--severity-{severity}",
                "--exit-code-violations",
                "-o",
                report,
                sch,
            ],
            cwd=sch.parent,
            ok_codes=(0, VIOLATIONS_EXIT_CODE),
        )

    def sch_export_netlist(self, sch: Path, out: Path) -> ToolResult:
        return self.run(
            ["sch", "export", "netlist", "--format", "kicadsexpr", "-o", out, sch], cwd=sch.parent
        )

    def sch_export_svg(self, sch: Path, out_dir: Path) -> ToolResult:
        out_dir.mkdir(parents=True, exist_ok=True)
        return self.run(["sch", "export", "svg", "-o", out_dir, sch], cwd=sch.parent)

    def sch_export_pdf(self, sch: Path, out: Path) -> ToolResult:
        return self.run(["sch", "export", "pdf", "-o", out, sch], cwd=sch.parent)

    def pcb_drc(
        self,
        pcb: Path,
        report: Path,
        *,
        schematic_parity: bool = True,
        severity: Severity = "error",
    ) -> ToolResult:
        parity = ["--schematic-parity"] if schematic_parity else []
        return self.run(
            [
                "pcb",
                "drc",
                "--format",
                "json",
                *parity,
                f"--severity-{severity}",
                "--exit-code-violations",
                "-o",
                report,
                pcb,
            ],
            cwd=pcb.parent,
            ok_codes=(0, VIOLATIONS_EXIT_CODE),
        )

    def pcb_export_gerbers(self, pcb: Path, out_dir: Path) -> ToolResult:
        out_dir.mkdir(parents=True, exist_ok=True)
        return self.run(["pcb", "export", "gerbers", "-o", out_dir, pcb], cwd=pcb.parent)

    def pcb_export_drill(self, pcb: Path, out_dir: Path) -> ToolResult:
        out_dir.mkdir(parents=True, exist_ok=True)
        return self.run(["pcb", "export", "drill", "-o", out_dir, pcb], cwd=pcb.parent)

    def pcb_export_pos(self, pcb: Path, out: Path) -> ToolResult:
        return self.run(
            [
                "pcb",
                "export",
                "pos",
                "--format",
                "csv",
                "--units",
                "mm",
                "--side",
                "both",
                "-o",
                out,
                pcb,
            ],
            cwd=pcb.parent,
        )

    def pcb_export_step(self, pcb: Path, out: Path) -> ToolResult:
        return self.run(["pcb", "export", "step", "--force", "-o", out, pcb], cwd=pcb.parent)

    def pcb_export_glb(self, pcb: Path, out: Path) -> ToolResult:
        return self.run(["pcb", "export", "glb", "--force", "-o", out, pcb], cwd=pcb.parent)

    def pcb_render(
        self, pcb: Path, out: Path, *, side: str = "top", rotate: str | None = None
    ) -> ToolResult:
        extra = ["--rotate", rotate] if rotate else []
        return self.run(["pcb", "render", "--side", side, *extra, "-o", out, pcb], cwd=pcb.parent)
