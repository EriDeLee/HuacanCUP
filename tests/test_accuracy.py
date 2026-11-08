"""峰值的复核（eda_agent/sim/accuracy.py，review-queue D39）：相邻两轮一致才采用。

不需要 ngspice：每一轮的「极值」用准备好的数代替。数据取自过流切断样例的真实实验
（Q1 的最高电压，最大步长缩小以后）：13.77 → 12.52 → 12.407 → 12.404 V，往 12.40 V 收敛。
"""

from __future__ import annotations

from typing import Any

import pytest

from eda_agent.sim.accuracy import (
    MAX_ROUNDS,
    STRICT_SETTINGS,
    agree,
    confirm,
    strict_plan,
)
from eda_agent.sim.testbench import AttemptPlan

Extremes = dict[str, tuple[float, ...]]


def scripted(by_divisor: dict[int, Extremes], fail: set[int] | None = None) -> Any:
    """run(设置, 标签)：返回 (设置, 标签)；extract 按步长缩小的倍数查准备好的数。
    fail 里的倍数代表这一轮没跑通。"""
    calls: list[AttemptPlan] = []

    def run(plan: AttemptPlan, tag: str) -> tuple[AttemptPlan, str] | None:
        calls.append(plan)
        return None if plan.step_divisor in (fail or set()) else (plan, tag)

    def extract(plan: AttemptPlan, tag: str) -> Extremes:
        return by_divisor[plan.step_divisor]

    return run, extract, calls


def q1(v: float, i: float = 10.0, p: float = 106.0) -> Extremes:
    return {"dev:Q1": (v, i, p)}


# ---------------------------------------------------------------- agree


def test_values_within_three_percent_agree() -> None:
    assert agree("dev:Q1", (12.4, 10.0, 106.0), (12.5, 10.1, 105.0))
    assert not agree("dev:Q1", (12.4, 10.0, 106.0), (13.0, 10.0, 106.0))  # 电压差 4.8 %


def test_every_number_of_an_entry_has_to_agree() -> None:
    assert not agree("dev:Q1", (12.4, 10.0, 106.0), (12.4, 10.0, 80.0))  # 功耗差得多


def test_numbers_near_zero_are_compared_by_a_floor_not_by_percent() -> None:
    assert agree("dev:C3", (5.0, 7e-15, 1e-16), (5.0, 4e-13, 1e-12))
    assert not agree("dev:C3", (5.0, 1e-3, 1e-6), (5.0, 2e-3, 1e-6))
    assert agree("net:GND", (1e-9,), (5e-7,))
    assert not agree("net:GND", (0.001,), (0.002,))


# ---------------------------------------------------------------- 收敛的几种走法


def test_it_stops_as_soon_as_two_neighbouring_rounds_agree() -> None:
    run, extract, calls = scripted({1: q1(12.362), 2: q1(12.376), 4: q1(99.0)})
    got = confirm(run, extract)
    assert got.devices["Q1"].v_peak == pytest.approx(12.376), "采用较细的那一轮"
    assert got.unconfirmed == [] and got.error == ""
    assert [c.step_divisor for c in calls] == [1, 2], "第 2 轮就一致了，不再往下算"
    assert got.runs == [{"trtol": 1.0, "step_divisor": 1}, {"trtol": 0.5, "step_divisor": 2}]
    assert got.settings == STRICT_SETTINGS[0][0]


def test_a_coarse_start_needs_more_rounds_and_the_real_value_is_found() -> None:
    """步子粗时 Q1 的最高电压是 18.7 V、13.9 V，往下才收敛到 12.4 V。"""
    run, extract, calls = scripted({1: q1(18.7), 2: q1(13.9), 4: q1(12.52), 8: q1(12.407)})
    got = confirm(run, extract)
    assert got.devices["Q1"].v_peak == pytest.approx(12.407)
    assert [c.step_divisor for c in calls] == [1, 2, 4, 8]
    assert got.unconfirmed == []


def test_values_that_never_settle_are_not_reported() -> None:
    drifting = {1: q1(55.4), 2: q1(40.0), 4: q1(28.0), 8: q1(19.0)}
    run, extract, calls = scripted(drifting)
    got = confirm(run, extract)
    assert got.devices == {} and got.nets == {}
    assert got.unconfirmed == ["dev:Q1"]
    assert len(calls) == MAX_ROUNDS + 1, "最多 4 轮"


def test_each_entry_is_decided_on_its_own() -> None:
    """一个器件收敛了、另一个没有：收敛的照报，没收敛的单独列出来。"""
    rounds = {
        1: {"dev:Q1": (13.9, 10.0, 106.0), "dev:U1": (5.0, 0.0118, 0.0596), "net:VBAT": (10.0,)},
        2: {"dev:Q1": (12.4, 10.0, 106.0), "dev:U1": (5.0, 0.0091, 0.0495), "net:VBAT": (10.0,)},
        4: {"dev:Q1": (12.4, 10.0, 106.0), "dev:U1": (5.0, 0.0088, 0.0477), "net:VBAT": (10.0,)},
        8: {"dev:Q1": (12.4, 10.0, 106.0), "dev:U1": (5.0, 0.0081, 0.0391), "net:VBAT": (10.0,)},
    }
    run, extract, _ = scripted(rounds)
    got = confirm(run, extract)
    assert sorted(got.devices) == ["Q1"] and got.nets == {"VBAT": 10.0}
    assert got.unconfirmed == ["dev:U1"]
    assert got.summary()["unconfirmed"] == ["dev:U1"]


def test_an_entry_that_only_one_round_has_is_not_confirmed() -> None:
    without_u1 = {"dev:Q1": (12.4, 10.0, 106.0)}
    rounds: dict[int, Extremes] = {
        1: without_u1 | {"dev:U1": (5.0, 0.01, 0.05)},
        2: without_u1,
        4: without_u1,
        8: without_u1,
    }
    run, extract, _ = scripted(rounds)
    got = confirm(run, extract)
    assert sorted(got.devices) == ["Q1"] and got.unconfirmed == ["dev:U1"]


# ---------------------------------------------------------------- 跑不通的情况


def test_when_the_first_strict_setting_fails_the_next_one_is_used() -> None:
    seen: list[tuple[str, ...]] = []

    def run(plan: AttemptPlan, tag: str) -> tuple[AttemptPlan, str] | None:
        seen.append(plan.options)
        return None if plan.options == STRICT_SETTINGS[0][1] else (plan, tag)

    got = confirm(run, lambda plan, tag: q1(12.4))
    assert got.settings == STRICT_SETTINGS[1][0]
    assert seen[0] == STRICT_SETTINGS[0][1] and seen[1] == STRICT_SETTINGS[1][1]
    assert set(seen[2:]) == {STRICT_SETTINGS[1][1]}, "跑通的那一组用到底，不混着用"
    assert got.devices["Q1"].v_peak == pytest.approx(12.4)


def test_when_no_strict_setting_runs_nothing_is_reported_and_the_reason_is_given() -> None:
    got = confirm(lambda plan, tag: None, lambda plan, tag: q1(12.4))
    assert got.devices == {} and got.nets == {} and got.unconfirmed == []
    assert "严格设置" in got.error and got.summary()["error"] == got.error


def test_a_failed_refinement_stops_the_series_and_leaves_the_rest_unconfirmed() -> None:
    run, extract, calls = scripted({1: q1(18.7), 2: q1(13.9), 4: q1(12.5)}, fail={4})
    got = confirm(run, extract)
    assert got.devices == {} and got.unconfirmed == ["dev:Q1"]
    assert [c.step_divisor for c in calls] == [1, 2, 4]


def test_results_without_the_probe_vectors_give_no_peaks_and_say_why() -> None:
    def extract(plan: AttemptPlan, tag: str) -> Extremes:
        raise KeyError("vi_q1_1#branch")

    got = confirm(lambda plan, tag: (plan, tag), extract)
    assert got.devices == {} and "vi_q1_1#branch" in got.error


# ---------------------------------------------------------------- 各轮的设置


def test_each_round_halves_the_error_factor_and_the_step() -> None:
    plans = [strict_plan(("method=gear",), k) for k in range(4)]
    assert [p.trtol for p in plans] == [1.0, 0.5, 0.25, 0.125]
    assert [p.step_divisor for p in plans] == [1, 2, 4, 8]
    assert all(p.name == "accuracy" and not p.uic for p in plans)
