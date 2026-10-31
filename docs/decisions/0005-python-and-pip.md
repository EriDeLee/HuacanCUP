# ADR-0005 只用 pip；主程序 Python 3.14，用到 pcbnew 的步骤用 KiCad 自带的 Python 3.11

- 状态：已采纳（2026-10-31，用户决定）
- 影响：AGENTS.md「依赖用 uv 管理」；PLAN §14 `pyproject.toml uv 管理依赖`

## 决定

1. 不用 uv，只用 pip。直接依赖在 `requirements.txt`（运行）和 `requirements-dev.txt`（开发、测试）里写死版本号；
   间接依赖不锁。`pyproject.toml` 只放 ruff、mypy、pytest 的配置。
2. 主程序用便携 Python 3.14（`C:\Users\Lee\opencode\config\python-3.14`），环境建在仓库里的 `venv\`。
   pip 缓存放在 `venv\.pipcache`（`venv\pip.ini`）。
3. 需要 `import pcbnew` 的步骤（建板、Specctra 导出导回）放在 `eda_agent/kicad_side/`，
   用 KiCad 自带的 `python.exe` 单独起进程运行，只用标准库和 pcbnew，不往 KiCad 的 Python 里装任何包。
   不用 kinet2pcb 之后（ADR-0007），原先为它准备的 `venv-kicad\` 已经用不上。

## 原因与后果

- KiCad 的 Python 版本跟着 KiCad 走（10.0.8 是 3.11.5）；分开进程后，主程序的 Python 版本不受 KiCad 牵制，
  KiCad 那一侧崩溃或卡住也只影响那一步，并能按统一超时结束。
- SKiDL 3.0 依赖的 InSpice 要求 Python ≥ 3.12，所以主程序最低版本是 3.12（PLAN 原写 3.11+）。
- 只锁直接依赖：间接依赖过一阵子可能装出不同版本；GitHub Actions 出现「本机好、云端坏」时先查这里。
- KiCad 自带的 pip 23.2.1 按系统编码（GBK）读 requirements 文件，所以 requirements 文件里只写英文注释；
  外部工具统一开 `PYTHONUTF8=1`。
