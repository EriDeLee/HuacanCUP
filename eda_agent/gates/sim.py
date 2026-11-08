"""G2 sim（PLAN §7）：对 spec 的每条验收标准各生成一个测试台、用 ngspice 仿真、和 pass 比较。

结果三态，分开统计：pass、fail（电路没做到）、sim_error（没得出结果：不收敛、超时、
模型缺失、测量没出来）。sim_error 不算通过，走诊断。
**每条验收标准都写一条 finding，通过的也写**（severity 是 info），
带实测值、阈值、余量：汇合选优按余量排名（ADR-0016）。

收敛重试：默认设置不行就依次放宽 gmin / reltol → method=gear → 缩短步长并加 uic
（sim/testbench.ATTEMPTS），每次尝试的设置、结果、网表都记进 finding.attempts。
只有不收敛类的错误才重试；模型缺失、网表有语法错误、超时都不会因为换设置而变好，直接停。

额外测量：每个器件的峰值电压、电流、功耗，每个网络的电流上界，写进报告的 device_stress 和
net_current_peak（G1 额定值检查第二轮、G3 线宽检查用）。取所有仿真里的最大值。
瞬态分析的峰值是时间上的极值，对步长很敏感，所以不直接用验收标准那次仿真的数：
另跑一串严格设置（网表和结果在 sim/check/），相邻两轮一致才采用，没收敛的不报，
detail["stress_check"] 里写明（sim/accuracy.py，review-queue D39）。

文件放在运行目录的 branches/<k>/sim/ 下（ADR-0016）：<编号>.cir（第一次尝试；后面几次是
<编号>.<设置名>.cir）、<编号>.json（ngspice 结果）、<编号>.raw、plots/<编号>.png、
models/（用到的模型文件的副本，.include 的相对路径指向它）。
判定只由这里的代码给出（AGENTS.md 原则 1）。
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eda_agent.config import REPO_ROOT, ToolsConfig
from eda_agent.gates.report import (
    Attempt,
    DeviceStress,
    Finding,
    GateReport,
    Status,
    error_finding,
    make_report,
    margin_fields,
    satisfies,
    sim_error_finding,
)
from eda_agent.ir.models import Design
from eda_agent.ir.spec import Acceptance, Spec
from eda_agent.sim.accuracy import Confirmed, confirm
from eda_agent.sim.measure import Measurement, evaluate
from eda_agent.sim.plots import plot_acceptance
from eda_agent.sim.stress import device_stress, net_current_peaks
from eda_agent.sim.testbench import ATTEMPTS, AttemptPlan, Bench, BenchError, build_bench
from eda_agent.tools.ngspice import SimResult, run_netlist
from eda_agent.trace import TraceWriter

Simulate = Callable[..., SimResult]

# 不收敛一类的错误：换一组设置有可能好；别的错误（语法、找不到模型）换设置也没用
_CONVERGENCE = re.compile(
    r"timestep too small|no convergence|singular matrix|iteration limit|too many iterations|"
    r"convergence problem|run simulation\(s\) aborted|transient op failed",
    re.IGNORECASE,
)
_DETAIL_LINES = 6


@dataclass
class SimEnv:
    tools: ToolsConfig
    sim_dir: Path  # branches/<k>/sim
    run_dir: Path  # finding.artifacts 里的路径相对它
    models_root: Path = REPO_ROOT
    trace: TraceWriter | None = None
    branch: int | None = None  # 写进 trace 事件的路（单路是 0）
    simulate: Simulate = run_netlist  # 测试里换成假的，不用真的 ngspice
    plot: bool = True
    timeout_s: float | None = None
    log_dir: Path | None = None
    confirm: bool = True  # 瞬态的峰值另跑严格设置复核（sim/accuracy.py，D39）


@dataclass
class _Outcome:
    finding: Finding
    stress: dict[str, DeviceStress] = field(default_factory=dict)
    net_currents: dict[str, float] = field(default_factory=dict)
    attempts_used: int = 0


def _rel(path: Path, env: SimEnv) -> str:
    return path.relative_to(env.run_dir).as_posix()


def _emit(env: SimEnv, name: str, state: str, **detail: Any) -> None:
    """trace 里的一条 step 事件：state 是 start / ok / fail。"""
    if env.trace is not None:
        extra: dict[str, Any] = {} if env.branch is None else {"branch": env.branch}
        env.trace.emit("step", name=name, status=state, detail=detail, **extra)


def _problems(sim: SimResult) -> list[str]:
    """ngspice 输出里和失败有关的行，给人看。"""
    lines = [
        line.strip()
        for line in sim.output
        if line.startswith("stderr")
        and "spinit" not in line
        and re.search(r"rror|abort|fail|convergence|singular|too small", line, re.IGNORECASE)
    ]
    return (lines or sim.errors)[:_DETAIL_LINES]


def _install_models(bench: Bench, env: SimEnv, folder: Path | None = None) -> None:
    """模型文件的副本放在网表旁边（.include 的相对路径指向它）。"""
    for lib in bench.includes:
        target = (folder or env.sim_dir) / lib
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(env.models_root / lib, target)


def _confirmed_stress(
    design: Design, spec: Spec, acc: Acceptance, env: SimEnv, artifacts: list[str]
) -> Confirmed:
    """瞬态的峰值：另跑一串严格设置，相邻两轮一致的才采用（sim/accuracy.py）。
    网表和结果放在 sim/check/ 下；跑通的网表也列进 artifacts。"""
    folder = env.sim_dir / "check"

    def run(plan: AttemptPlan, tag: str) -> tuple[Bench, SimResult] | None:
        try:
            bench = build_bench(design, spec, acc, models_root=env.models_root, attempt=plan)
        except BenchError:
            return None
        folder.mkdir(parents=True, exist_ok=True)
        cir = folder / f"{acc.id}.{tag}.cir"
        cir.write_text(bench.text, encoding="utf-8", newline="\n")
        _install_models(bench, env, folder)
        sim = env.simulate(
            env.tools,
            cir,
            folder / f"{acc.id}.{tag}.json",
            log_dir=env.log_dir or env.sim_dir / "logs",
            trace=env.trace,
            settings=bench.settings,
            timeout_s=env.timeout_s,
        )
        stuck = any(_CONVERGENCE.search(line) for line in sim.output)
        converged = sim.ok and not sim.tool.timed_out and not stuck
        _emit(env, "sim-check", "ok" if converged else "fail", acceptance=acc.id, run=tag)
        if not converged:
            return None
        artifacts.append(_rel(cir, env))
        return bench, sim

    return confirm(run)


def _describe(acc: Acceptance, value: float, margin: float) -> str:
    p = acc.pass_
    want = f"{p.low:g} 到 {p.high:g}" if p.op == "between" else f"{p.op} {p.value:g}"
    return f"{acc.text}：测得 {value:.6g} {p.unit}，要求 {want} {p.unit}，余量 {margin:+.1%}"


def _verdict(acc: Acceptance, m: Measurement) -> tuple[Status, str, dict[str, Any]]:
    """测量结果 → (状态, 说明, 数值字段)。"""
    p = acc.pass_
    if m.status == "ok" and m.value is not None:
        threshold = p.low if p.op == "between" else p.value
        assert threshold is not None
        numbers = margin_fields(m.value, p.op, threshold, p.high, unit=p.unit, scale=p.scale)
        status: Status = "pass" if satisfies(m.value, p.op, threshold, p.high) else "fail"
        return status, _describe(acc, m.value, numbers["margin"]), numbers
    status = "fail" if m.status == "unmet" else "sim_error"
    return status, f"{acc.text}：{m.message}", {"unit": p.unit}


def _stem(bench: Bench) -> str:
    first = bench.attempt is ATTEMPTS[0]
    return bench.acceptance_id if first else f"{bench.acceptance_id}.{bench.attempt.name}"


def run_acceptance(design: Design, spec: Spec, acc: Acceptance, env: SimEnv) -> _Outcome:
    _emit(env, f"sim:{acc.id}", "start", analysis=acc.testbench.type)
    attempts: list[Attempt] = []
    bench: Bench | None = None
    sim: SimResult | None = None
    why = ""
    for plan in ATTEMPTS:
        try:
            bench = build_bench(design, spec, acc, models_root=env.models_root, attempt=plan)
        except BenchError as exc:
            _emit(env, f"sim:{acc.id}", "fail", reason=exc.kind, message=str(exc))
            return _Outcome(
                sim_error_finding(
                    acc.id,
                    f"{acc.text}：{exc}",
                    detail={"reason": exc.kind, "refs": list(exc.refs)},
                ),
                attempts_used=len(attempts),
            )
        env.sim_dir.mkdir(parents=True, exist_ok=True)
        cir = env.sim_dir / f"{_stem(bench)}.cir"
        cir.write_text(bench.text, encoding="utf-8", newline="\n")
        _install_models(bench, env)
        sim = env.simulate(
            env.tools,
            cir,
            env.sim_dir / f"{_stem(bench)}.json",
            log_dir=env.log_dir or env.sim_dir / "logs",
            trace=env.trace,
            settings=bench.settings,
            raw=env.sim_dir / f"{_stem(bench)}.raw",
            timeout_s=env.timeout_s,
        )
        record: dict[str, Any] = {
            "name": plan.name,
            "options": list(plan.options),
            "netlist": _rel(cir, env),
        }
        convergence = [line for line in sim.output if _CONVERGENCE.search(line)]
        if sim.tool.timed_out:
            limit = f"{env.timeout_s or env.tools.timeouts.sim_s:g} s"
            attempts.append(Attempt(outcome="timeout", error=f"超过 {limit}", **record))
            why = f"仿真超时（超过 {limit}）"
            break
        if sim.ok and not convergence:
            attempts.append(Attempt(outcome="ok", **record))
            why = ""
            break
        problems = _problems(sim) or [f"退出码 {sim.tool.exit_code}"]
        attempts.append(Attempt(outcome="error", error="；".join(problems), **record))
        _emit(env, "sim-attempt", "fail", acceptance=acc.id, attempt=plan.name, error=problems[:2])
        if not convergence:
            why = f"ngspice 报错：{'；'.join(problems)}"
            break
        why = f"试了 {len(attempts)} 组设置都不收敛：{'；'.join(problems[:2])}"
    assert bench is not None and sim is not None

    artifacts = [_rel(env.sim_dir / f"{_stem(bench)}.cir", env)]
    raw = env.sim_dir / f"{_stem(bench)}.raw"
    if raw.exists():
        artifacts.append(_rel(raw, env))

    if attempts[-1].outcome != "ok":
        _emit(env, f"sim:{acc.id}", "fail", reason="sim_error", message=why)
        reason = (
            "timeout"
            if attempts[-1].outcome == "timeout"
            else ("no_convergence" if "不收敛" in why else "ngspice_error")
        )
        return _Outcome(
            sim_error_finding(
                acc.id,
                f"{acc.text}：{why}",
                artifacts=artifacts,
                attempts=attempts,
                detail={"reason": reason},
            ),
            attempts_used=len(attempts),
        )

    measurement = evaluate(bench, sim)
    status, message, numbers = _verdict(acc, measurement)
    if env.plot:
        try:
            png = plot_acceptance(
                bench,
                sim,
                measurement,
                status,
                env.sim_dir / "plots" / f"{acc.id}.png",
                config_dir=env.tools.sandbox_home / "matplotlib",
            )
        except Exception as exc:  # 画图失败不影响判定，在 detail 里记一笔
            png = None
            measurement.detail["plot_error"] = str(exc)[-300:]
        if png is not None:
            artifacts.append(_rel(png, env))
    detail: dict[str, Any] = {
        "analysis": acc.testbench.type,
        "measure": acc.measure.kind,
        **measurement.detail,
    }
    stress: dict[str, DeviceStress] = {}
    nets: dict[str, float] = {}
    if acc.testbench.type == "transient" and env.confirm:
        # 时间上的极值对步长敏感（D39）：验收标准的判定用上面的结果，峰值要复核过才报
        checked = _confirmed_stress(design, spec, acc, env, artifacts)
        stress, nets = checked.devices, checked.nets
        detail["stress_check"] = checked.summary()
    else:
        try:
            stress, nets = device_stress(bench, sim), net_current_peaks(bench, sim)
        except (
            KeyError
        ) as exc:  # 结果里缺探针的向量（仿真中途出了问题）：判定照给，只是没有额外测量
            detail["stress_error"] = str(exc.args[0] if exc.args else exc)
    finding = Finding(
        id=acc.id,
        severity="info" if status == "pass" else "error",
        status=status,
        message=message,
        artifacts=artifacts,
        attempts=attempts,
        detail=detail,
        **numbers,
    )
    _emit(env, f"sim:{acc.id}", "ok" if status == "pass" else "fail", verdict=status)
    return _Outcome(finding, stress, nets, len(attempts))


def _merge_stress(old: DeviceStress | None, new: DeviceStress) -> DeviceStress:
    if old is None:
        return new
    return DeviceStress(
        v_peak=max(old.v_peak, new.v_peak),
        i_peak=max(old.i_peak, new.i_peak),
        p_peak=max(old.p_peak, new.p_peak),
    )


def sim_gate(design: Design, spec: Spec, env: SimEnv) -> GateReport:
    if not spec.acceptance:
        return make_report(
            "sim",
            [error_finding("no_acceptance", "spec 里没有一条验收标准，G2 没有东西可以判定")],
            {"pass": 0, "fail": 0, "sim_error": 0, "total": 0, "attempts": 0},
        )
    findings: list[Finding] = []
    stress: dict[str, DeviceStress] = {}
    nets: dict[str, float] = {}
    attempts = 0
    for acc in spec.acceptance:
        result = run_acceptance(design, spec, acc, env)
        findings.append(result.finding)
        attempts += result.attempts_used
        for ref, s in result.stress.items():
            stress[ref] = _merge_stress(stress.get(ref), s)
        for net, amps in result.net_currents.items():
            nets[net] = max(nets.get(net, 0.0), amps)
    counts = {s: sum(1 for f in findings if f.status == s) for s in ("pass", "fail", "sim_error")}
    counts |= {"total": len(findings), "attempts": attempts}
    return make_report(
        "sim",
        findings,
        counts,
        device_stress=dict(sorted(stress.items())),
        net_current_peak=dict(sorted(nets.items())),
    )
