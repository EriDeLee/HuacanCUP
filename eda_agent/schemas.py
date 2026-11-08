"""schemas/ 下由 pydantic 模型导出的 JSON Schema。

PLAN §5：格式用 JSON Schema 定义，用 pydantic 实现。

改了模型后运行 python tasks.py schemas；tests/test_ir.py 会检查文件和模型是否一致。
trace.schema.json 是手写的（事件流不是 pydantic 模型），不在这里生成。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from eda_agent.config import REPO_ROOT
from eda_agent.gates.report import report_json_schema
from eda_agent.ir.models import design_json_schema
from eda_agent.ir.spec import spec_json_schema

SCHEMA_DIR = REPO_ROOT / "schemas"
GENERATED: dict[str, Callable[[], dict[str, object]]] = {
    "design.schema.json": design_json_schema,
    "spec.schema.json": spec_json_schema,
    "gate_report.schema.json": report_json_schema,
}


def render(name: str) -> str:
    return json.dumps(GENERATED[name](), ensure_ascii=False, indent=2) + "\n"


def write_schemas(directory: Path = SCHEMA_DIR) -> list[Path]:
    written = []
    for name in GENERATED:
        path = directory / name
        path.write_text(render(name), encoding="utf-8", newline="\n")
        written.append(path)
    return written
