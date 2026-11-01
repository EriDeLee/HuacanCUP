"""design.json → 完整的 KiCad 工程（ADR-0013）。

1. 原理图：自写版面引擎（sch_project），一个功能块一页；只有一个功能块时就一页。
2. 网表：kicad-cli 从原理图导出（以 KiCad 自己读出来的连接为准）。
3. PCB：kicad_side/board_build.py 在 KiCad 自带的 Python 里建板、放封装、画板框和安装孔。
4. 网络类：把 board.net_classes 写进 .kicad_pro（线宽、间距、过孔），并按网表里的网络名逐个指定。
   网表里的网络名不一定和 IR 一样（多页时本页网络会带页名前缀），所以按引脚找到对应的网络再写。
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eda_agent.config import ToolsConfig
from eda_agent.emit.sch_project import SchematicProject, build_project, write_project
from eda_agent.gates.schematic_quality import exported_nets
from eda_agent.ir.models import DEFAULT_CLASS, Design, NetClass, load_design
from eda_agent.tools.kicad_cli import KicadCli
from eda_agent.tools.kicad_python import run_kicad_script
from eda_agent.trace import TraceWriter


@dataclass(frozen=True)
class EmittedProject:
    schematic: SchematicProject
    root: Path  # 根页 .kicad_sch
    sheets: tuple[Path, ...]  # 全部 .kicad_sch（含根页）
    netlist: Path
    pcb: Path
    project_file: Path


def _apply(target: dict[str, Any], nc: NetClass) -> None:
    target["track_width"] = nc.track_mm
    target["clearance"] = nc.clearance_mm
    if nc.via_diameter_mm is not None:
        target["via_diameter"] = nc.via_diameter_mm
    if nc.via_drill_mm is not None:
        target["via_drill"] = nc.via_drill_mm


def net_class_settings(
    net_settings: dict[str, Any], design: Design, actual: dict[tuple[str, str], str]
) -> dict[str, Any]:
    """在 .kicad_pro 的 net_settings 上加上设计的网络类，返回新的 net_settings（不改传入的）。

    actual 是网表里「引脚 → 网络名」；每个用了自定义网络类的 IR 网络，
    按它的引脚找到网表里的名字，写成一条精确的指定。
    """
    settings = copy.deepcopy(net_settings)
    classes: list[dict[str, Any]] = settings.setdefault("classes", [])
    default = next((c for c in classes if c.get("name") == "Default"), None)
    if default is None:
        raise ValueError(".kicad_pro 里没有 Default 网络类（工程文件不是 KiCad 写的？）")
    custom = {k: v for k, v in design.board.net_classes.items() if k != DEFAULT_CLASS}
    if DEFAULT_CLASS in design.board.net_classes:
        _apply(default, design.board.net_classes[DEFAULT_CLASS])
    classes[:] = [c for c in classes if c.get("name") == "Default" or c.get("name") not in custom]
    for priority, (name, nc) in enumerate(sorted(custom.items())):
        entry = copy.deepcopy(default)
        entry["name"] = name
        entry["priority"] = priority
        _apply(entry, nc)
        classes.append(entry)
    patterns = []
    for net in design.nets:
        if net.net_class == DEFAULT_CLASS:
            continue
        names = {actual[(r, p)] for r, p in net.nodes if (r, p) in actual}
        if len(names) != 1:
            raise ValueError(
                f"网络 {net.name} 在网表里对应 {sorted(names) or '没有'} 个网络，没法指定网络类"
            )
        patterns.append({"netclass": net.net_class, "pattern": names.pop()})
    settings["netclass_patterns"] = patterns
    return settings


def write_net_classes(project_file: Path, design: Design, netlist: Path) -> None:
    data: dict[str, Any] = json.loads(project_file.read_text(encoding="utf-8"))
    data["net_settings"] = net_class_settings(
        data.get("net_settings", {}), design, exported_nets(netlist)
    )
    project_file.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def emit_schematic(
    design: Design, out_dir: Path, name: str, tools: ToolsConfig, *, date: str
) -> tuple[SchematicProject, Path]:
    project = build_project(design, name, (tools.kicad_share / "symbols",))
    root = write_project(project, out_dir.absolute(), title=name, date=date)
    return project, root


def emit_kicad_project(
    design_path: Path,
    out_dir: Path,
    name: str,
    tools: ToolsConfig,
    *,
    log_dir: Path,
    date: str,
    trace: TraceWriter | None = None,
) -> EmittedProject:
    design = load_design(design_path)
    out_dir = out_dir.absolute()
    schematic, root = emit_schematic(design, out_dir, name, tools, date=date)
    kicad = KicadCli(tools, log_dir=log_dir, trace=trace)
    netlist = out_dir / f"{name}.net"
    kicad.sch_export_netlist(root, netlist)
    pcb = out_dir / f"{name}.kicad_pcb"
    run_kicad_script(
        tools,
        "board_build.py",
        [
            "--netlist",
            netlist,
            "--design",
            design_path.absolute(),
            "--footprints",
            tools.kicad_share / "footprints",
            "--out",
            pcb,
        ],
        cwd=out_dir,
        log_dir=log_dir,
        trace=trace,
    )
    project_file = out_dir / f"{name}.kicad_pro"
    write_net_classes(project_file, design, netlist)
    sheets = (root, *(out_dir / p.file for p in schematic.pages if schematic.root is not None))
    return EmittedProject(schematic, root, sheets, netlist, pcb, project_file)
