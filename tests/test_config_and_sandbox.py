from __future__ import annotations

import sys
from pathlib import Path

import pytest

from eda_agent.config import REPO_ROOT, _absolute, load_config
from eda_agent.tools.sandbox import sandbox_env


def test_example_config_paths_are_absolute_and_inside_repo() -> None:
    tools = load_config(REPO_ROOT / "config.example.yaml").tools
    for path in (tools.sandbox_home, tools.kicad_cli, tools.kicad_python, tools.fastroute):
        assert path.is_absolute()
        assert REPO_ROOT in path.parents


def test_linux_sandbox_passes_the_display_but_still_drops_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setenv("GITHUB_TOKEN", "should-not-leak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-not-leak")
    tools = load_config(REPO_ROOT / "config.example.yaml").tools

    env = sandbox_env(tools)

    assert env["DISPLAY"] == ":99"
    assert "GITHUB_TOKEN" not in env
    assert "ANTHROPIC_API_KEY" not in env


def test_linux_config_loads_and_points_at_the_ci_install_locations() -> None:
    tools = load_config(REPO_ROOT / "config.linux.yaml").tools
    assert str(tools.kicad_cli).replace("\\", "/").endswith("/usr/bin/kicad-cli")
    assert tools.fastroute.name == "fastroute"
    assert REPO_ROOT in tools.krt_python.parents


def test_relative_tool_paths_do_not_follow_symlinks(tmp_path: Path) -> None:
    """venv 的 bin/python 在 Linux 上是符号链接；配置里的路径必须停在链接本身。"""
    real = tmp_path / "real-python"
    real.write_text("", encoding="utf-8")
    link = tmp_path / "venv-python"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("这台机器上不能建符号链接（Windows 需要开发者模式）")
    assert _absolute(Path("venv-python"), root=tmp_path) == link


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
