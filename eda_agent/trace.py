"""运行事件流 runs/<id>/trace.jsonl：每行一个 JSON 事件，格式见 schemas/trace.schema.json。

网页回放完全依赖这个文件；改字段时要同步改 schema 和回放代码。
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class TraceWriter:
    """只追加写；每写一行立即 flush，进程中途退出也不会丢已发生的事件。"""

    def __init__(self, path: Path, run_id: str) -> None:
        self.path = path
        self.run_id = run_id
        self._seq = 0
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event_type: str, **fields: Any) -> dict[str, Any]:
        with self._lock:
            event: dict[str, Any] = {
                "ts": datetime.now(UTC).isoformat(),
                "run_id": self.run_id,
                "seq": self._seq,
                "type": event_type,
                **fields,
            }
            self._seq += 1
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
                f.flush()
        return event


def read_trace(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                events.append(json.loads(line))
    return events
