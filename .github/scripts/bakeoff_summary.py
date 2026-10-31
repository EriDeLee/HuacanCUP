"""把布线器对比的 results.json 写成 GitHub 的运行摘要和注释（ADR-0004）。

    python3 .github/scripts/bakeoff_summary.py runs/bakeoff-<布线器>/results.json

公开仓库的 Actions 日志要登录才能看；运行摘要和注释（::notice::）不用登录，
注释还能从公开接口读到（check-runs 的 annotations）。只用标准库。

「错误数」和 ADR-0004 的表一样：KiCad DRC 的错误 + 没连上的线 + 与原理图不一致的项。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

COUNT_KEYS = ("violations_errors", "unconnected_errors", "parity_errors")


def escape_data(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_prop(text: str) -> str:
    return escape_data(text).replace(":", "%3A").replace(",", "%2C")


def total_errors(entry: dict[str, Any]) -> int | None:
    if "error" in entry or not all(k in entry for k in COUNT_KEYS):
        return None
    return sum(int(entry[k]) for k in COUNT_KEYS)


def cell(entry: dict[str, Any]) -> str:
    seconds = entry.get("seconds", "?")
    errors = total_errors(entry)
    if errors is None:
        return f"出错 / {seconds} s"
    return f"{errors} / {seconds} s"


def detail(entry: dict[str, Any]) -> str:
    if "error" in entry:
        return "出错：" + str(entry["error"]).strip().splitlines()[-1][:300]
    parts = [f"{k}={entry.get(k)}" for k in COUNT_KEYS]
    parts += [f"segments={entry.get('segments')}", f"vias={entry.get('vias')}"]
    if "exit_code" in entry:
        parts.append(f"exit_code={entry['exit_code']}")
    if entry.get("error_types"):
        parts.append("types=" + json.dumps(entry["error_types"], ensure_ascii=False))
    summary = entry.get("router_summary") or {}
    for key in ("routed", "connections", "variant", "tracks", "vias"):
        if key in summary:
            parts.append(f"router.{key}={summary[key]}")
    return ", ".join(parts)


def render(results: dict[str, Any]) -> tuple[str, list[str]]:
    routers: list[str] = list(results.get("routers", []))
    boards: dict[str, dict[str, Any]] = results.get("boards", {})
    head = "| 板子（元件数） | 原板 | " + " | ".join(routers) + " |"
    rule = "|---" * (len(routers) + 2) + "|"
    rows = [
        f"## 布线器对比 {results.get('run_id', '')}",
        "",
        "错误数 / 用时（错误数 = DRC 错误 + 没连上 + 原理图不一致）",
        "",
        head,
        rule,
    ]
    notices: list[str] = []
    for spec, board in boards.items():
        original = board.get("original", {})
        footprints = original.get("footprints", "?")
        cells = [cell(board.get(r, {"error": "没有结果"})) for r in routers]
        base = total_errors(original)
        rows.append(
            f"| {spec}（{footprints}） | {base if base is not None else '?'} | "
            + " | ".join(cells)
            + " |"
        )
        for router in routers:
            entry = board.get(router, {"error": "没有结果"})
            title = escape_prop(f"{router} {spec}")
            notices.append(
                f"::notice title={title}::{escape_data(cell(entry) + ' | ' + detail(entry))}"
            )
    return "\n".join(rows) + "\n", notices


def main(argv: list[str]) -> int:
    results = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    markdown, notices = render(results)
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as handle:
            handle.write(markdown)
    print(markdown)
    for line in notices:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
