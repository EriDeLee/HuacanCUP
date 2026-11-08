"""流水线里几步共用的做法：布线。冒烟（smoke.py）和摆放对比（eval/m2_placement.py）都走这里。

布线器按 ADR-0004、ADR-0017：本机 Windows 用 fastroute（备用 KiCadRoutingTools），
TraceMaker 只有 Linux 版，接进流水线是 M3 的事。
每个布线器都是在传进来的那块板上原地改，调用方先复制好工程。
"""

from __future__ import annotations

import time
from pathlib import Path

from eda_agent.config import ToolsConfig
from eda_agent.tools.kicad_python import run_kicad_script
from eda_agent.tools.routers import (
    board_copper_edge_um,
    board_min_track_um,
    fastroute_route,
    krt_route,
)
from eda_agent.trace import TraceWriter

ROUTERS = ("fastroute", "krt")


def copy_project(pcb: Path, dest: Path, name: str) -> Path:
    """复制工程到 dest：工程文件、板子、原理图的每一页、网表。
    DRC 的原理图一致性检查要读整个原理图，所以每一页都要带上。
    板子和工程在 dest 里改成统一的名字 name。返回新板子的路径。"""
    import shutil

    dest.mkdir(parents=True, exist_ok=True)
    for suffix in (".kicad_pro", ".kicad_pcb"):
        shutil.copy2(pcb.with_suffix(suffix), dest / f"{name}{suffix}")
    for sheet in pcb.parent.glob("*.kicad_sch"):
        shutil.copy2(sheet, dest / sheet.name)
    for net in pcb.parent.glob("*.net"):
        shutil.copy2(net, dest / net.name)
    return dest / f"{name}.kicad_pcb"


def route_board(
    tools: ToolsConfig,
    pcb: Path,
    router: str,
    *,
    log_dir: Path,
    trace: TraceWriter | None = None,
) -> float:
    """在板子 pcb 上原地布线，返回用的秒数。"""
    start = time.monotonic()
    if router == "fastroute":
        dsn, ses = pcb.with_suffix(".dsn"), pcb.with_suffix(".ses")
        run_kicad_script(
            tools,
            "specctra.py",
            ["export", "--pcb", pcb, "--dsn", dsn],
            cwd=pcb.parent,
            log_dir=log_dir,
            trace=trace,
        )
        project = pcb.with_suffix(".kicad_pro")
        fastroute_route(
            tools,
            dsn,
            ses,
            log_dir=log_dir,
            trace=trace,
            min_track_um=board_min_track_um(project),
            edge_clearance_um=board_copper_edge_um(project),
        )
        run_kicad_script(
            tools,
            "specctra.py",
            ["import", "--pcb", pcb, "--ses", ses],
            cwd=pcb.parent,
            log_dir=log_dir,
            trace=trace,
        )
    elif router == "krt":
        routed = pcb.with_name(f"{pcb.stem}_routed.kicad_pcb")
        krt_route(
            tools,
            pcb,
            routed,
            summary_json=pcb.with_name("krt_summary.json"),
            log_dir=log_dir,
            trace=trace,
        )
        # DRC 的原理图一致性检查要求板、原理图、工程同名；工程文件保留布线前的原样
        routed.replace(pcb)
        routed.with_suffix(".kicad_pro").unlink(missing_ok=True)
    else:
        raise ValueError(f"不认识的布线器：{router}（可选 {ROUTERS}）")
    return time.monotonic() - start
