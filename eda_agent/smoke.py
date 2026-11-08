"""冒烟：手写 LED 电路走完整链路。AGENTS.md：任何改动后都必须保持通过。

IR → 原理图（自写版面引擎，ADR-0013；--schematic skidl 换回 SKiDL 做对照）→ 版面检查 → ERC
→ 网表（与 IR 逐网络核对）→ G1 静态检查 → G2 仿真 → G1 额定值检查（带仿真峰值）
→ PCB（含网络类）→ 自动摆放 → 布线 → G3（DRC 含原理图一致性，加未布通、板尺寸、线宽）
→ Gerber、钻孔、坐标、STEP、GLB、渲染图、原理图 SVG。

G1、G2 是前后相接的关：前一关没过就停在那里，不往下做板子（PLAN §4）。
G2 要设计旁边有 spec.json 才有东西可查；没有就跳过，并在 summary.skipped 里写明，不是悄悄略过。
主布线器 fastroute 和备用布线器 KiCadRoutingTools 都跑（ADR-0004），制造文件用主布线器的结果；
主布线器的 G3 不通过，冒烟就算失败。摆放方式取 config.yaml 的 placement.mode（默认 auto）。

    python tasks.py smoke
    python -m eda_agent.smoke --design examples/led/design.json --primary krt
    python -m eda_agent.smoke --design examples/attiny85_blink/design.json
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from eda_agent.config import REPO_ROOT, load_config
from eda_agent.emit.kicad_project import emit_schematic, write_net_classes
from eda_agent.emit.sch_layout import NetSpec
from eda_agent.emit.sch_project import SchematicProject
from eda_agent.flow import ROUTERS, copy_project, route_board
from eda_agent.gates.drc import drc_checks, estimate_net_currents, pcb_net_names
from eda_agent.gates.kicad_reports import erc_gate
from eda_agent.gates.report import GateReport
from eda_agent.gates.schematic_layout import layout_gate
from eda_agent.gates.schematic_quality import exported_nets, layout_quality, netlist_gate
from eda_agent.gates.sim import SimEnv, sim_gate
from eda_agent.gates.static import static_gate
from eda_agent.ir.models import load_design
from eda_agent.ir.spec import Spec, load_spec
from eda_agent.placement.run import PlacementReport, place_board
from eda_agent.tools.kicad_cli import KicadCli
from eda_agent.tools.kicad_python import run_kicad_script
from eda_agent.tools.runner import ToolResult, run_tool
from eda_agent.tools.sandbox import sandbox_env
from eda_agent.trace import TraceWriter

SCHEMATIC_MODES = ("engine", "skidl")


class Smoke:
    def __init__(self, design_path: Path, runs_dir: Path, schematic: str = "engine") -> None:
        self.design_path = design_path.resolve()
        self.design = load_design(self.design_path)
        spec_path = self.design_path.with_name("spec.json")
        self.spec: Spec | None = load_spec(spec_path) if spec_path.exists() else None
        self.schematic_mode = schematic
        self.project: SchematicProject | None = None
        self.name = self.design_path.parent.name
        self.run_id = f"smoke-{self.name}-{datetime.now():%Y%m%d-%H%M%S}"
        self.run_dir = runs_dir / self.run_id
        self.trace = TraceWriter(self.run_dir / "trace.jsonl", self.run_id)
        self.cfg = load_config()
        self.tools = self.cfg.tools
        self.logs = self.run_dir / "logs"
        self.net_names: dict[str, str] = {}  # 设计里的网络名 → KiCad 网表 / PCB 里的网络名
        self.gates_dir = self.run_dir / "gates"
        self.gates_dir.mkdir(parents=True, exist_ok=True)
        self.kicad = KicadCli(self.tools, log_dir=self.logs, trace=self.trace)
        self.gates: dict[str, GateReport] = {}

    @contextmanager
    def step(self, name: str) -> Iterator[dict[str, Any]]:
        detail: dict[str, Any] = {}
        self.trace.emit("step", name=name, status="start")
        try:
            yield detail
        except Exception as exc:
            detail["error"] = str(exc)[-2000:]
            self.trace.emit("step", name=name, status="fail", detail=detail)
            raise
        self.trace.emit("step", name=name, status="ok", detail=detail)

    def _record(self, key: str, report: GateReport) -> GateReport:
        self.gates[key] = report
        (self.gates_dir / f"{key}.gate.json").write_text(
            json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return report

    def _kicad_script(self, script: str, args: list[str | Path], cwd: Path) -> ToolResult:
        return run_kicad_script(
            self.tools, script, args, cwd=cwd, log_dir=self.logs, trace=self.trace
        )

    def schematic(self) -> Path:
        out_dir = self.run_dir / "kicad"
        if self.schematic_mode == "engine":
            with self.step("schematic") as detail:
                self.project, root = emit_schematic(
                    self.design, out_dir, self.name, self.tools, date=f"{datetime.now():%Y-%m-%d}"
                )
                detail.update(
                    file=str(root), pages=[p.key for p in self.project.pages], engine="sch_layout"
                )
            return root
        with self.step("schematic") as detail:
            run_tool(
                "skidl",
                [
                    sys.executable,
                    "-m",
                    "eda_agent.emit.skidl_emit",
                    "--design",
                    self.design_path,
                    "--out-dir",
                    out_dir,
                    "--name",
                    self.name,
                ],
                cwd=out_dir,
                env=sandbox_env(self.tools),
                timeout_s=self.tools.timeouts.default_s,
                log_dir=self.logs,
                trace=self.trace,
            ).check()
            sch = out_dir / f"{self.name}.kicad_sch"
            detail["file"] = str(sch)
        return sch

    def erc(self, sch: Path) -> GateReport:
        with self.step("erc") as detail:
            report = self.gates_dir / "erc.json"
            self.kicad.sch_erc(sch, report, severity="all")
            gate = self._record("erc", erc_gate(report))
            detail.update(passed=gate.passed, **gate.counts)
        return gate

    def layout(self, sch: Path) -> GateReport:
        """符号都在图纸内（每张有元件的页）；自写引擎画的图还要过版面质量检查。合成一个结果。"""
        with self.step("schematic-layout") as detail:
            reports = []
            if self.project is None:
                reports.append(layout_gate(sch))
            else:
                for page in self.project.pages:
                    reports.append(layout_gate(sch.parent / page.file))
                    quality = layout_quality(page.drawing)
                    self._record(f"schematic_quality-{page.key}", quality)
                    reports.append(quality)
                if self.project.root is not None:
                    # 分页时根页的框图也查：框之间的导线、电源说明、虚线箭头（ADR-0014）
                    quality = layout_quality(self.project.root)
                    self._record("schematic_quality-root", quality)
                    reports.append(quality)
            passed = all(r.passed for r in reports)
            counts: dict[str, int] = {}
            for r in reports:
                for key, value in r.counts.items():
                    counts[key] = counts.get(key, 0) + value
            findings = [f for r in reports for f in r.findings]
            gate = self._record(
                "schematic_layout",
                GateReport(
                    gate="schematic_layout", passed=passed, counts=counts, findings=findings
                ),
            )
            detail.update(passed=gate.passed, **gate.counts)
        return gate

    def netlist_check(self, netlist: Path) -> GateReport:
        """KiCad 读出来的网表和 IR 逐网络一致：没拆开、没短路、没缺脚。"""
        with self.step("netlist-equivalence") as detail:
            nets = [NetSpec(n.name, tuple(n.nodes)) for n in self.design.nets]
            gate = self._record("netlist_equivalence", netlist_gate(nets, netlist))
            detail.update(passed=gate.passed, **gate.counts)
        return gate

    def export_netlist(self, sch: Path) -> Path:
        netlist = sch.with_suffix(".net")
        self.kicad.sch_export_netlist(sch, netlist)
        self.net_names = pcb_net_names(self.design, exported_nets(netlist))
        return netlist

    def static(self, erc: GateReport, sim: GateReport | None = None) -> GateReport:
        """G1。sim 给了就是第二轮：额定值检查加上 G2 量到的峰值电压、电流、功耗。"""
        key = "static" if sim is None else "static_with_sim"
        with self.step(key) as detail:
            gate = static_gate(
                self.design,
                self.spec,
                symbol_dirs=(self.tools.kicad_share / "symbols",),
                footprint_dir=self.tools.kicad_share / "footprints",
                derating=self.cfg.gates.derating,
                sim=sim,
                erc=erc,
            )
            self._record(key, gate)
            detail.update(passed=gate.passed, **gate.counts)
        return gate

    def simulate(self, spec: Spec) -> GateReport:
        """G2。文件放在 branches/0/sim/（ADR-0016，单路就是 0）。"""
        sim_dir = self.run_dir / "branches" / "0" / "sim"
        sim_dir.mkdir(parents=True, exist_ok=True)
        with self.step("sim") as detail:
            env = SimEnv(
                tools=self.tools,
                sim_dir=sim_dir,
                run_dir=self.run_dir,
                trace=self.trace,
                branch=0,
                log_dir=self.logs,
            )
            gate = self._record("sim", sim_gate(self.design, spec, env))
            detail.update(passed=gate.passed, **gate.counts)
        return gate

    def board(self, sch: Path, netlist: Path) -> Path:
        with self.step("pcb") as detail:
            pcb = sch.with_suffix(".kicad_pcb")
            built = self._kicad_script(
                "board_build.py",
                [
                    "--netlist",
                    netlist,
                    "--design",
                    self.design_path,
                    "--footprints",
                    self.tools.kicad_share / "footprints",
                    "--out",
                    pcb,
                ],
                cwd=sch.parent,
            )
            detail.update(built.last_json())
            # 网络类（线宽、间距）写进工程文件，DRC 和布线器读的都是它
            write_net_classes(pcb.with_suffix(".kicad_pro"), self.design, netlist)
            detail["net_classes"] = sorted(self.design.board.net_classes)
        return pcb

    def place(self, pcb: Path) -> tuple[Path, PlacementReport]:
        """摆放在副本上做，两个布线器都从摆好的这块板出发。步骤记录由 place_board 自己写。"""
        work = self.run_dir / "placement"
        target = copy_project(pcb, work, self.name)
        report = place_board(
            self.tools,
            target,
            self.design,
            self.cfg.placement,
            work_dir=work,
            log_dir=self.logs,
            trace=self.trace,
            branch=0,
        )
        (work / "placement.json").write_text(
            json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return target, report

    def route(self, pcb: Path, router: str) -> Path:
        """布线在副本上做。多页原理图的每一页都要一起复制，DRC 的原理图一致性检查会读整个原理图。"""
        with self.step(f"route-{router}") as detail:
            target = copy_project(pcb, self.run_dir / f"route-{router}", self.name)
            detail["seconds"] = round(
                route_board(self.tools, target, router, log_dir=self.logs, trace=self.trace), 1
            )
        return target

    def drc(self, pcb: Path, router: str, sim: GateReport | None) -> GateReport:
        """G3：KiCad DRC，加未布通网络数、板尺寸、电源网络线宽。"""
        with self.step(f"drc-{router}") as detail:
            report = self.gates_dir / f"drc-{router}.json"
            self.kicad.pcb_drc(pcb, report, severity="all")
            gate = drc_checks(
                drc_report=report,
                pcb=pcb,
                spec=self.spec,
                net_names=self.net_names,
                currents=estimate_net_currents(self.spec, sim),
                copper_um=self.cfg.pcb.copper_thickness_um,
                temp_rise_c=self.cfg.pcb.temp_rise_c,
            )
            self._record(f"drc-{router}", gate)
            detail.update(passed=gate.passed, **gate.counts)
        return gate

    def export(self, pcb: Path, sch: Path) -> dict[str, str]:
        final = self.run_dir / "final"
        with self.step("export") as detail:
            fab = final / "gerbers"
            self.kicad.pcb_export_gerbers(pcb, fab)
            self.kicad.pcb_export_drill(pcb, fab)
            fab_zip = final / "gerbers.zip"
            with zipfile.ZipFile(fab_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in sorted(fab.iterdir()):
                    zf.write(f, f.name)
            outputs = {
                "gerbers_zip": fab_zip,
                "pos": final / "pos.csv",
                "step": final / "board.step",
                "glb": final / "board.glb",
                "render_top": final / "renders" / "top.png",
                "render_iso": final / "renders" / "iso.png",
                "schematic_svg": final / "schematic",
            }
            outputs["render_top"].parent.mkdir(parents=True, exist_ok=True)
            self.kicad.pcb_export_pos(pcb, outputs["pos"])
            self.kicad.pcb_export_step(pcb, outputs["step"])
            self.kicad.pcb_export_glb(pcb, outputs["glb"])
            self.kicad.pcb_render(pcb, outputs["render_top"])
            self.kicad.pcb_render(pcb, outputs["render_iso"], rotate="-45,0,45")
            self.kicad.sch_export_svg(sch, outputs["schematic_svg"])
            missing = [k for k, p in outputs.items() if not p.exists()]
            if missing:
                raise RuntimeError(f"没有生成：{missing}")
            detail.update({k: str(p.relative_to(self.run_dir)) for k, p in outputs.items()})
        return detail

    def gate_chain(
        self, erc: GateReport, summary: dict[str, Any]
    ) -> tuple[list[GateReport], GateReport | None]:
        """G1 → G2 → G1（带仿真峰值）。前一关没过就停，summary.stopped_at 写停在哪一关。
        返回（跑过的关，G2 的报告）；没有 spec.json 时 G2 跳过，summary.skipped 里写明。"""
        reports = [self.static(erc)]
        if not reports[-1].passed:
            summary["stopped_at"] = "static"
            return reports, None
        if self.spec is None:
            summary["skipped"] = {"sim": "设计旁边没有 spec.json，G2 没有验收标准可查"}
            return reports, None
        sim = self.simulate(self.spec)
        reports.append(sim)
        if not sim.passed:
            summary["stopped_at"] = "sim"
            return reports, sim
        reports.append(self.static(erc, sim))
        if not reports[-1].passed:
            summary["stopped_at"] = "static_with_sim"
        return reports, sim

    def run(self, routers: tuple[str, ...], primary: str) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "run_id": self.run_id,
            "design": str(self.design_path),
            "spec": None if self.spec is None else str(self.design_path.with_name("spec.json")),
        }
        routed: dict[str, Path] = {}
        placement_ok = False
        primary_gate: GateReport | None = None
        try:
            sch = self.schematic()
            layout = self.layout(sch)
            erc = self.erc(sch)
            netlist = self.export_netlist(sch)
            equivalence = self.netlist_check(netlist)
            chain, sim = self.gate_chain(erc, summary)
            checks = [layout, erc, equivalence, *chain]
            if "stopped_at" not in summary:
                pcb = self.board(sch, netlist)
                placed, placement = self.place(pcb)
                summary["placement"] = {
                    k: v
                    for k, v in asdict(placement).items()
                    if k in ("mode", "seed", "method", "hpwl_mm", "hpwl_rows_mm", "violations")
                }
                # rows 是原来按行排的板子，不由摆放这一步担保合法；它的问题 G3 的 DRC 会报
                placement_ok = placement.ok or placement.mode == "rows"
                for router in routers:
                    try:
                        routed[router] = self.route(placed, router)
                        self.drc(routed[router], router, sim)
                    except Exception as exc:
                        summary.setdefault("router_errors", {})[router] = str(exc)[-1500:]
                primary_gate = self.gates.get(f"drc-{primary}")
                if primary_gate is not None and primary_gate.passed:
                    summary["exports"] = self.export(routed[primary], sch)
            summary["passed"] = (
                bool(
                    all(g.passed for g in checks)
                    and placement_ok
                    and primary_gate is not None
                    and primary_gate.passed
                )
                and "exports" in summary
            )
        except Exception as exc:
            summary["passed"] = False
            summary["error"] = str(exc)[-2000:]
        summary["gates"] = {
            k: v.model_dump(mode="json") | {"findings": len(v.findings)}
            for k, v in self.gates.items()
        }
        summary["primary_router"] = primary
        summary["schematic"] = self.schematic_mode
        (self.run_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return summary


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--design", type=Path, default=REPO_ROOT / "examples" / "led" / "design.json"
    )
    parser.add_argument("--runs", type=Path, default=REPO_ROOT / "runs")
    parser.add_argument("--primary", choices=ROUTERS, default="fastroute")
    parser.add_argument("--routers", nargs="+", choices=ROUTERS, default=list(ROUTERS))
    parser.add_argument("--schematic", choices=SCHEMATIC_MODES, default="engine")
    args = parser.parse_args(argv)
    routers = tuple(dict.fromkeys([args.primary, *args.routers]))
    # 换成绝对路径：KiCad 自带 Python 的步骤在运行目录里跑，相对路径会指到不存在的文件，
    # pcbnew 读不到板子时直接崩溃（退出码 0xC0000005），看不出原因（2026-11-01 实测）
    summary = Smoke(args.design.resolve(), args.runs.resolve(), args.schematic).run(
        routers, args.primary
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n冒烟{'通过' if summary['passed'] else '失败'}：{summary['run_id']}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
