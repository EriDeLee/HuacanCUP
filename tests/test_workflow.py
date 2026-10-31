"""GitHub Actions 工作流的安全底线（ADR-0012）：公开仓库里，改工作流时不能悄悄放松。"""

from __future__ import annotations

import re
from typing import Any

import pytest
import yaml

from eda_agent.config import REPO_ROOT

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"


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


def test_downloads_are_checked_against_a_hash() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.count("curl ") == text.count("sha256sum -c"), "每次 curl 下载后都要校验哈希"
