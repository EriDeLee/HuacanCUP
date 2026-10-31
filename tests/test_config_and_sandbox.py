from __future__ import annotations

import pytest

from eda_agent.config import REPO_ROOT, load_config
from eda_agent.tools.sandbox import sandbox_env


def test_example_config_paths_are_absolute_and_inside_repo() -> None:
    tools = load_config(REPO_ROOT / "config.example.yaml").tools
    for path in (tools.sandbox_home, tools.kicad_cli, tools.kicad_python, tools.fastroute):
        assert path.is_absolute()
        assert REPO_ROOT in path.parents


def test_sandbox_redirects_tool_state_and_drops_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-not-leak")
    monkeypatch.setenv("TMPDIR", r"C:\somewhere\else")
    tools = load_config(REPO_ROOT / "config.example.yaml").tools

    env = sandbox_env(tools)

    assert "ANTHROPIC_API_KEY" not in env
    home = str(tools.sandbox_home)
    for name in ("HOME", "TEMP", "TMP", "TMPDIR", "KICAD_CONFIG_HOME", "KICAD_CACHE_HOME"):
        assert env[name].startswith(home), name
    assert env["PYTHONUTF8"] == "1"
