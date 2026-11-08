"""波形图（eda_agent/sim/plots.py）：用假的仿真结果画图，只检查文件有没有生成、边界情况。"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from eda_agent.config import REPO_ROOT, load_config
from eda_agent.gates.sim import SimEnv, sim_gate
from eda_agent.ir.models import load_design
from eda_agent.ir.spec import Spec, load_spec
from eda_agent.sim.measure import Measurement
from eda_agent.sim.plots import plot_acceptance
from eda_agent.sim.testbench import build_bench
from tests.helpers import fake_sim
from tests.test_sim_gate import Scripted, divider, divider_vectors

CIRCUITS = REPO_ROOT / "tests" / "circuits"
EXAMPLES = REPO_ROOT / "examples"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _bench(folder: Path, acceptance: str):  # type: ignore[no-untyped-def]
    design, spec = load_design(folder / "design.json"), load_spec(folder / "spec.json")
    acc = next(a for a in spec.acceptance if a.id == acceptance)
    return build_bench(design, spec, acc, models_root=REPO_ROOT)


def _config(tmp_path: Path) -> Path:
    return tmp_path / "mpl"


def test_a_transient_plot_is_a_png(tmp_path: Path) -> None:
    b = _bench(EXAMPLES / "overcurrent_cutoff", "A1")
    t = np.linspace(0, 0.006, 600)
    current = np.where(t < 0.002, 1.0, np.where(t < 0.00207, 10.0, 0.0))
    sim = fake_sim({"time": t.tolist(), "vl_load#branch": current.tolist()})
    out = plot_acceptance(
        b,
        sim,
        Measurement("ok", 7.2e-5),
        "pass",
        tmp_path / "plots" / "A1.png",
        config_dir=_config(tmp_path),
    )
    assert out == tmp_path / "plots" / "A1.png"
    assert out.read_bytes().startswith(PNG_MAGIC) and out.stat().st_size > 2000


def test_an_ac_plot_has_gain_and_phase(tmp_path: Path) -> None:
    b = _bench(CIRCUITS / "rc_lowpass", "A1")
    f = np.logspace(1, 5, 120)
    h = 1 / (1 + 1j * f / 1591.5)
    zeros = [0.0] * len(f)
    sim = fake_sim(
        complex_vectors={
            "frequency": (f.tolist(), zeros),
            "out": (h.real.tolist(), h.imag.tolist()),
            "in": ([1.0] * len(f), zeros),
        }
    )
    out = plot_acceptance(
        b, sim, Measurement("ok", 1587.7), "fail", tmp_path / "A1.png", config_dir=_config(tmp_path)
    )
    assert out is not None and out.read_bytes().startswith(PNG_MAGIC)


def test_a_dc_operating_point_has_no_waveform_to_draw(tmp_path: Path) -> None:
    b = _bench(CIRCUITS / "voltage_divider", "A1")
    out = plot_acceptance(
        b,
        fake_sim({"mid": [3.0]}),
        Measurement("ok", 3.0),
        "pass",
        tmp_path / "A1.png",
        config_dir=_config(tmp_path),
    )
    assert out is None and not (tmp_path / "A1.png").exists()


def test_missing_vectors_mean_no_plot_instead_of_a_crash(tmp_path: Path) -> None:
    b = _bench(EXAMPLES / "overcurrent_cutoff", "A1")
    out = plot_acceptance(
        b,
        fake_sim({"time": [0.0, 1.0]}),
        Measurement("error"),
        "sim_error",
        tmp_path / "A1.png",
        config_dir=_config(tmp_path),
    )
    assert out is None


def test_the_gate_adds_the_plot_to_the_artifacts_of_a_transient_criterion(tmp_path: Path) -> None:
    design = load_design(EXAMPLES / "overcurrent_cutoff" / "design.json")
    spec = load_spec(EXAMPLES / "overcurrent_cutoff" / "spec.json")
    spec = Spec.model_validate(
        spec.model_dump(by_alias=True)
        | {"acceptance": [spec.model_dump(by_alias=True)["acceptance"][0]]}
    )
    t = np.linspace(0, 0.006, 300)
    sim = Scripted(
        fake_sim({"time": t.tolist(), "vl_load#branch": [1.0] * len(t)}, m_a1=7.2e-5),
    )
    env = SimEnv(
        tools=load_config().tools,
        sim_dir=tmp_path / "branches" / "0" / "sim",
        run_dir=tmp_path,
        simulate=sim,
        plot=True,
    )
    report = sim_gate(design, spec, env)
    finding = report.findings[0]
    assert "branches/0/sim/plots/A1.png" in finding.artifacts
    assert (tmp_path / "branches" / "0" / "sim" / "plots" / "A1.png").exists()


def test_an_op_criterion_has_no_plot_in_its_artifacts(tmp_path: Path) -> None:
    design, spec = divider()
    env = SimEnv(
        tools=load_config().tools,
        sim_dir=tmp_path / "branches" / "0" / "sim",
        run_dir=tmp_path,
        simulate=Scripted(fake_sim(divider_vectors())),
        plot=True,
    )
    report = sim_gate(design, spec, env)
    assert all(not a.endswith(".png") for f in report.findings for a in f.artifacts)
