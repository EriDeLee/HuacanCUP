"""运行目录 runs/<run_id>/（PLAN §5.4）和断点续跑用的 state.json。

    runs/<run_id>/
      request.md                      用户原话
      spec.json / spec.md             冻结的需求规格（M2 起）
      iterations/NN/design.json       每次设计改动后的快照，NN 从 00 开始
      iterations/NN/gates/<gate>.json 这一版设计的检查报告
      iterations/NN/notes.md          这一版是谁、为什么改的
      sim/  kicad/  final/            仿真、KiCad 工程、交付物
      trace.jsonl                     事件流（schemas/trace.schema.json）
      state.json                      当前状态、回退计数、费用累计

设计只能通过 apply_patch（JSON Patch，PLAN §5.2）或 snapshot 改动，每次改动都留快照，随时可以回滚。
这里只负责把东西存好、记进事件流；「通过不通过」只由 eda_agent/gates/ 的代码给出
（AGENTS.md 原则 1）。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from eda_agent.gates.kicad_reports import GateReport
from eda_agent.ir.models import Design
from eda_agent.ir.patch import PatchResult, apply_design_patch, design_document
from eda_agent.trace import TraceWriter


class RunState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    state: str = Field(default="S0", description="当前所在的状态（PLAN §4 的 S0…S11、G1…G3）")
    iteration: int = Field(default=-1, description="最新快照的编号；-1 = 还没有快照")
    retries: dict[str, int] = Field(default_factory=dict, description="每条回退边已用的次数")
    cost_usd: float = Field(default=0.0, ge=0)
    updated: str = ""


def _write_atomic(path: Path, text: str) -> None:
    """先写临时文件再改名：进程中途被打断时，旧文件要么完整保留，要么已被完整替换。"""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


class RunDir:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.run_id = path.name
        self.trace = TraceWriter(path / "trace.jsonl", self.run_id)

    # ---------------------------------------------------------------- 建立、打开

    @classmethod
    def create(
        cls, runs_dir: Path, request: str, *, prefix: str = "run", now: datetime | None = None
    ) -> RunDir:
        stamp = f"{prefix}-{(now or datetime.now()):%Y%m%d-%H%M%S}"
        path = runs_dir / stamp
        n = 2
        while path.exists():
            path = runs_dir / f"{stamp}-{n}"
            n += 1
        path.mkdir(parents=True)
        run = cls(path)
        for sub in ("iterations", "sim", "kicad", "final"):
            (path / sub).mkdir()
        _write_atomic(path / "request.md", request.rstrip("\n") + "\n")
        run.save_state(RunState(run_id=run.run_id))
        return run

    @classmethod
    def open(cls, path: Path) -> RunDir:
        """接着跑一个已有的运行（断点续跑）。"""
        if not (path / "state.json").exists():
            raise FileNotFoundError(f"{path} 不是运行目录（没有 state.json）")
        return cls(path)

    @property
    def kicad_dir(self) -> Path:
        return self.path / "kicad"

    @property
    def sim_dir(self) -> Path:
        return self.path / "sim"

    @property
    def final_dir(self) -> Path:
        return self.path / "final"

    # ---------------------------------------------------------------- 状态

    def state(self) -> RunState:
        return RunState.model_validate_json((self.path / "state.json").read_text(encoding="utf-8"))

    def save_state(self, state: RunState) -> None:
        stamped = state.model_copy(update={"updated": datetime.now(UTC).isoformat()})
        _write_atomic(self.path / "state.json", stamped.model_dump_json(indent=2) + "\n")

    # ---------------------------------------------------------------- 设计快照

    def iteration_dir(self, n: int) -> Path:
        return self.path / "iterations" / f"{n:02d}"

    def snapshot(self, design: Design, *, by: str, note: str = "") -> int:
        state = self.state()
        n = state.iteration + 1
        folder = self.iteration_dir(n)
        folder.mkdir(parents=True)
        _write_atomic(folder / "design.json", _dump(design_document(design)))
        if note:
            _write_atomic(folder / "notes.md", note.rstrip("\n") + "\n")
        self.save_state(state.model_copy(update={"iteration": n}))
        self.trace.emit(
            "snapshot",
            iteration=n,
            by=by,
            path=str((folder / "design.json").relative_to(self.path).as_posix()),
        )
        return n

    def latest_design(self) -> Design | None:
        n = self.state().iteration
        if n < 0:
            return None
        text = (self.iteration_dir(n) / "design.json").read_text(encoding="utf-8")
        return Design.model_validate_json(text)

    def apply_patch(self, patch: Any, *, by: str, note: str = "") -> PatchResult:
        """把补丁应用到最新快照上；成功就存成新快照。不论成败都记进事件流，失败的错误原样记下。"""
        design = self.latest_design()
        if design is None:
            raise RuntimeError("还没有设计快照，先用 snapshot 存第一版")
        result = apply_design_patch(design, patch)
        ops = len(patch) if isinstance(patch, list) else 0
        self.trace.emit("patch", by=by, ok=result.ok, ops=ops, errors=result.errors)
        if result.design is not None:
            self.snapshot(result.design, by=by, note=note)
        return result

    # ---------------------------------------------------------------- 检查报告

    def record_gate(self, report: GateReport, iteration: int | None = None) -> Path:
        n = self.state().iteration if iteration is None else iteration
        if n < 0:
            raise RuntimeError("还没有设计快照，检查报告没有地方放")
        folder = self.iteration_dir(n) / "gates"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{report.gate}.json"
        _write_atomic(path, _dump(asdict(report)))
        self.trace.emit(
            "gate",
            gate=report.gate,
            passed=report.passed,
            counts=report.counts,
            iteration=n,
            path=str(path.relative_to(self.path).as_posix()),
        )
        return path
