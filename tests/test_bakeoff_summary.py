from __future__ import annotations

import importlib.util
from types import ModuleType

import pytest

from eda_agent.config import REPO_ROOT


@pytest.fixture(scope="module")
def summary() -> ModuleType:
    path = REPO_ROOT / ".github" / "scripts" / "bakeoff_summary.py"
    spec = importlib.util.spec_from_file_location("bakeoff_summary", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ROUTED = {
    "seconds": 12.5,
    "violations_errors": 2,
    "unconnected_errors": 1,
    "parity_errors": 0,
    "segments": 84,
    "vias": 6,
    "exit_code": 3,
    "error_types": {"clearance": 2},
    "router_summary": {"routed": 41, "connections": 42},
}
RESULTS = {
    "run_id": "bakeoff-tracemaker",
    "routers": ["tracemaker"],
    "boards": {
        "ecc83/ecc83-pp": {
            "original": {
                "violations_errors": 0,
                "unconnected_errors": 0,
                "parity_errors": 0,
                "footprints": 15,
            },
            "tracemaker": ROUTED,
        },
        "interf_u/interf_u": {
            "original": {
                "violations_errors": 3,
                "unconnected_errors": 0,
                "parity_errors": 0,
                "footprints": 25,
            },
            "tracemaker": {"seconds": 900.1, "error": "tracemaker 退出码 None\n超时"},
        },
    },
}


def test_errors_are_drc_plus_unconnected_plus_parity(summary: ModuleType) -> None:
    assert summary.total_errors(ROUTED) == 3
    assert summary.cell(ROUTED) == "3 / 12.5 s"


def test_a_failed_run_is_shown_as_failed_not_as_zero_errors(summary: ModuleType) -> None:
    failed = {"seconds": 900.1, "error": "boom"}
    assert summary.total_errors(failed) is None
    assert summary.cell(failed) == "出错 / 900.1 s"


def test_render_gives_one_table_row_and_one_notice_per_board_and_router(
    summary: ModuleType,
) -> None:
    markdown, notices = summary.render(RESULTS)

    assert "| ecc83/ecc83-pp（15） | 0 | 3 / 12.5 s |" in markdown
    assert "| interf_u/interf_u（25） | 3 | 出错 / 900.1 s |" in markdown
    assert len(notices) == 2
    assert all(n.startswith("::notice title=tracemaker ") for n in notices)
    assert all("\n" not in n for n in notices)
    assert "router.routed=41" in notices[0]
    assert "超时" in notices[1]


def test_escaping_follows_the_github_workflow_command_rules(summary: ModuleType) -> None:
    assert summary.escape_data("50%\nok") == "50%25%0Aok"
    assert summary.escape_prop("krt a/b:c,d") == "krt a/b%3Ac%2Cd"
