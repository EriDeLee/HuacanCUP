"""GitHub Actions 工作流的安全底线（ADR-0012）：公开仓库里，改工作流时不能悄悄放松。

每条规则对 .github/workflows/ 下的所有工作流都适用。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from eda_agent.config import REPO_ROOT

WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
WORKFLOWS = sorted(WORKFLOW_DIR.glob("*.yml"))
SCRIPTS = sorted((REPO_ROOT / ".github" / "scripts").glob("*.sh"))
ALLOWED_TRIGGERS = {"push", "pull_request", "workflow_dispatch"}


def _load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data


def _triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    triggers: dict[str, Any] = (
        workflow.get(True) or workflow.get("on") or {}
    )  # 裸的 on 会被解析成 True
    return triggers


def _steps(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    return [step for job in workflow["jobs"].values() for step in job["steps"]]


def test_the_expected_workflows_exist() -> None:
    assert {p.name for p in WORKFLOWS} >= {"ci.yml", "bakeoff.yml"}


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_third_party_action_is_pinned_to_a_commit_hash(path: Path) -> None:
    uses = [step["uses"] for step in _steps(_load(path)) if "uses" in step]
    assert uses, "工作流里应该至少用到 checkout"
    for ref in uses:
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", ref), f"没有钉在 40 位提交哈希上：{ref}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_token_is_read_only_and_no_secrets_are_used(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    workflow = _load(path)
    assert workflow["permissions"] == {"contents": "read"}
    for job in workflow["jobs"].values():
        assert "permissions" not in job, "不能在单个任务里放宽令牌权限"
    assert "secrets." not in text
    assert "pull_request_target" not in text


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_only_low_risk_triggers_are_used(path: Path) -> None:
    triggers = _triggers(_load(path))
    assert triggers
    assert set(triggers) <= ALLOWED_TRIGGERS, set(triggers) - ALLOWED_TRIGGERS


def test_ci_runs_on_every_push_and_pull_request() -> None:
    assert set(_triggers(_load(WORKFLOW_DIR / "ci.yml"))) == ALLOWED_TRIGGERS


def test_bakeoff_only_runs_when_its_own_files_change() -> None:
    """布线器对比要跑几十分钟：只在改了它自己的工作流或脚本时自动跑，外人的拉取请求不能触发。"""
    triggers = _triggers(_load(WORKFLOW_DIR / "bakeoff.yml"))
    assert set(triggers) == {"push", "workflow_dispatch"}
    paths = triggers["push"]["paths"]
    assert ".github/workflows/bakeoff.yml" in paths
    for rel in paths:
        assert (REPO_ROOT / rel).is_file(), f"paths 指向不存在的文件：{rel}"


def test_every_download_is_checked_against_a_hash_right_after() -> None:
    for path in [*WORKFLOWS, *SCRIPTS]:
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if "curl " in line and not line.lstrip().startswith("#"):
                assert "sha256sum -c" in "\n".join(lines[i : i + 4]), (
                    f"{path.name}:{i + 1} 下载后没有马上校验哈希"
                )


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_scripts_named_in_the_workflow_exist(path: Path) -> None:
    named = set(re.findall(r"\.github/scripts/[\w.-]+", path.read_text(encoding="utf-8")))
    for rel in named:
        assert (REPO_ROOT / rel).is_file(), rel


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_cache_keys_change_with_the_python_version_and_dependency_files(path: Path) -> None:
    caches = [s for s in _steps(_load(path)) if str(s.get("uses", "")).startswith("actions/cache@")]
    for step in caches:
        key = step["with"]["key"]
        assert "steps.python.outputs.python-version" in key, key
        groups = re.findall(r"hashFiles\(([^)]*)\)", key)
        assert groups, f"缓存钥匙里没有依赖清单的哈希：{key}"
        for group in groups:
            for rel in re.findall(r"'([^']+)'", group):
                # 文件名写错时 hashFiles 返回空串，钥匙就不再随依赖变化，会一直用旧缓存
                assert (REPO_ROOT / rel).is_file(), f"hashFiles 指向不存在的文件：{rel}"


def test_both_workflows_share_the_same_python_cache_key() -> None:
    def venv_keys(path: Path) -> set[str]:
        return {
            s["with"]["key"]
            for s in _steps(_load(path))
            if str(s.get("uses", "")).startswith("actions/cache@") and s["with"]["path"] == "venv"
        }

    assert venv_keys(WORKFLOW_DIR / "ci.yml") == venv_keys(WORKFLOW_DIR / "bakeoff.yml")


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_background_install_is_waited_for_before_kicad_is_used(path: Path) -> None:
    """需要 KiCad 的步骤都在 xvfb-run 下跑；后台安装必须在第一个这样的步骤之前等完。"""
    steps = _steps(_load(path))
    runs = [str(s.get("run", "")) for s in steps]
    first_use = next(i for i, r in enumerate(runs) if "xvfb-run" in r)
    starts = {
        m.group(1): i for i, r in enumerate(runs) for m in re.finditer(r"bg\.sh start (\S+)", r)
    }
    assert "kicad" in starts
    for name, at in starts.items():
        waits = [i for i, r in enumerate(runs) if re.search(rf"bg\.sh wait {re.escape(name)}\b", r)]
        assert len(waits) == 1, f"{name} 要恰好等一次"
        assert at < waits[0] < first_use, f"{name} 要在用到 KiCad 之前等它装完"
        assert steps[waits[0]].get("if") == steps[at].get("if"), f"{name} 的启动和等待条件要一致"
