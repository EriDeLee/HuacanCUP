"""GitHub Actions 工作流的安全底线（ADR-0012）：公开仓库里，改工作流时不能悄悄放松。"""

from __future__ import annotations

import re
from typing import Any

import pytest
import yaml

from eda_agent.config import REPO_ROOT

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
SCRIPTS = sorted((REPO_ROOT / ".github" / "scripts").glob("*.sh"))


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return data


def _steps(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    return [step for job in workflow["jobs"].values() for step in job["steps"]]


def test_every_third_party_action_is_pinned_to_a_commit_hash(workflow: dict[str, Any]) -> None:
    uses = [step["uses"] for step in _steps(workflow) if "uses" in step]
    assert uses, "工作流里应该至少用到 checkout"
    for ref in uses:
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", ref), f"没有钉在 40 位提交哈希上：{ref}"


def test_token_is_read_only_and_no_secrets_are_used() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    assert data["permissions"] == {"contents": "read"}
    assert "secrets." not in text
    assert "pull_request_target" not in text


def test_triggers_are_the_expected_ones(workflow: dict[str, Any]) -> None:
    triggers = workflow.get(True) or workflow.get("on")  # 裸的 on 会被 YAML 解析成 True
    assert set(triggers) == {"push", "pull_request", "workflow_dispatch"}


def test_every_download_is_checked_against_a_hash_right_after() -> None:
    for path in [WORKFLOW, *SCRIPTS]:
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if "curl " in line and not line.lstrip().startswith("#"):
                assert "sha256sum -c" in "\n".join(lines[i : i + 4]), (
                    f"{path.name}:{i + 1} 下载后没有马上校验哈希"
                )


def test_scripts_named_in_the_workflow_exist() -> None:
    named = set(re.findall(r"\.github/scripts/[\w.-]+", WORKFLOW.read_text(encoding="utf-8")))
    assert named
    for rel in named:
        assert (REPO_ROOT / rel).is_file(), rel


def test_cache_keys_change_with_the_python_version_and_dependency_files(
    workflow: dict[str, Any],
) -> None:
    caches = [s for s in _steps(workflow) if str(s.get("uses", "")).startswith("actions/cache@")]
    assert caches
    for step in caches:
        key = step["with"]["key"]
        assert "steps.python.outputs.python-version" in key, key
        groups = re.findall(r"hashFiles\(([^)]*)\)", key)
        assert groups, f"缓存钥匙里没有依赖清单的哈希：{key}"
        for group in groups:
            for rel in re.findall(r"'([^']+)'", group):
                # 文件名写错时 hashFiles 返回空串，钥匙就不再随依赖变化，会一直用旧缓存
                assert (REPO_ROOT / rel).is_file(), f"hashFiles 指向不存在的文件：{rel}"


def test_every_background_install_is_waited_for_before_the_tool_tests(
    workflow: dict[str, Any],
) -> None:
    steps = _steps(workflow)
    runs = [str(s.get("run", "")) for s in steps]
    tools_at = next(i for i, r in enumerate(runs) if "tasks.py test-tools" in r)
    starts = {
        m.group(1): i for i, r in enumerate(runs) for m in re.finditer(r"bg\.sh start (\S+)", r)
    }
    assert "kicad" in starts
    for name, at in starts.items():
        waits = [i for i, r in enumerate(runs) if re.search(rf"bg\.sh wait {re.escape(name)}\b", r)]
        assert len(waits) == 1, f"{name} 要恰好等一次"
        assert at < waits[0] < tools_at, f"{name} 要在工具测试之前等它装完"
        assert steps[waits[0]].get("if") == steps[at].get("if"), f"{name} 的启动和等待条件要一致"
