"""项目任务入口，替代 make（ADR-0006）。先激活 venv（venv\\Scripts\\activate.bat），再运行：

    python tasks.py              列出所有任务
    python tasks.py lint         ruff format --check、ruff check、mypy（核心包严格模式）
    python tasks.py test         单元测试：不需要外部工具，不调用真实 LLM
    python tasks.py test-tools   需要 KiCad / ngspice / 布线器的测试（标了 @pytest.mark.tools）
    python tasks.py smoke        手写 LED 电路走完整链路，任何改动后都必须保持通过

这里调用的是开发工具（pytest、ruff、mypy），不是 EDA 工具；EDA 工具一律走 eda_agent/tools。
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable

PY = sys.executable


def _run(*args: str) -> int:
    print("$", " ".join(args), flush=True)
    return subprocess.run(args, check=False).returncode


def lint() -> int:
    for args in (
        (PY, "-m", "ruff", "format", "--check", "."),
        (PY, "-m", "ruff", "check", "."),
        (PY, "-m", "mypy"),
    ):
        if code := _run(*args):
            return code
    return 0


def test() -> int:
    return _run(PY, "-m", "pytest", "-m", "not tools")


def test_tools() -> int:
    return _run(PY, "-m", "pytest", "-m", "tools")


def smoke() -> int:
    from eda_agent.smoke import main as smoke_main

    return smoke_main([])


TASKS: dict[str, tuple[Callable[[], int], str]] = {
    "lint": (lint, "ruff format --check、ruff check、mypy"),
    "test": (test, "单元测试（不需要外部工具）"),
    "test-tools": (test_tools, "需要 KiCad / ngspice / 布线器的测试"),
    "smoke": (smoke, "LED 电路走完整链路"),
}


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in TASKS:
        print("用法：python tasks.py <任务>\n")
        for name, (_, help_text) in TASKS.items():
            print(f"  {name:<11} {help_text}")
        return 0 if not argv else 2
    return TASKS[argv[0]][0]()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
