# ADR-0006 用 `python tasks.py <任务>` 代替 make

- 状态：已采纳（2026-10-31，用户决定）
- 影响：AGENTS.md「测试」里的 `make test` / `make smoke`；PLAN §12 M0、M7 的 `make smoke` / `make eval`；PLAN §14 Makefile

## 背景

Windows 上没有 make，用户不想为此多装工具。

## 决定

激活 venv 后，在仓库根目录运行：

| 原命令 | 新命令 | 内容 |
|---|---|---|
| — | `python tasks.py` | 列出所有任务 |
| — | `python tasks.py lint` | `ruff format --check`、`ruff check`、`mypy`（核心包严格模式） |
| `make test` | `python tasks.py test` | 单元测试：不需要外部工具，不调用真实 LLM |
| — | `python tasks.py test-tools` | 标了 `@pytest.mark.tools` 的测试，需要 KiCad / ngspice / 布线器 |
| `make smoke` | `python tasks.py smoke` | LED 电路走完整链路 |

`make eval`、`demo`、`serve` 到对应里程碑再加成同名任务。Windows 和 Linux 上写法相同。
