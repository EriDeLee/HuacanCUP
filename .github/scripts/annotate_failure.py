"""CI 失败时，把出错日志的尾部写成 GitHub 注释（::error::）。

公开仓库的 Actions 日志要登录才能看，注释和步骤状态不用。有了注释，没登录的人也能读到出错原因。
只用标准库；由工作流在 `if: failure()` 的步骤里调用：python3 .github/scripts/annotate_failure.py
"""

from __future__ import annotations

import sys
from pathlib import Path

MAX_ANNOTATIONS = 8
TAIL_LINES = 40
MAX_CHARS = 3000


def escape_data(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_prop(text: str) -> str:
    return escape_data(text).replace(":", "%3A").replace(",", "%2C")


def tail(path: Path) -> str:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-TAIL_LINES:]
    return "\n".join(lines)[-MAX_CHARS:]


def _newest(paths: list[Path]) -> list[Path]:
    return sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)


def annotations(root: Path) -> list[str]:
    """最新的一份步骤日志（最后跑的那一步就是失败的那一步）加最新几份非空的工具错误输出。"""
    step_logs = _newest(list(root.glob("ci-logs/*.log")))[:1]
    tool_logs = _newest(
        [
            p
            for pattern in ("runs/*/logs/*.stderr.txt", ".pytest_tmp/**/logs/*.stderr.txt")
            for p in root.glob(pattern)
            if p.stat().st_size > 0
        ]
    )
    out = []
    for path in [*step_logs, *tool_logs][:MAX_ANNOTATIONS]:
        title = escape_prop(path.relative_to(root).as_posix())
        out.append(f"::error title={title}::{escape_data(tail(path))}")
    return out or ["::error::没有找到任何日志文件"]


def main() -> int:
    for line in annotations(Path.cwd()):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
