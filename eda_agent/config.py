"""读取配置：EDA_AGENT_CONFIG 指定的文件，否则 config.yaml，再否则 config.example.yaml。

配置里的相对路径一律以仓库根目录为准。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

REPO_ROOT = Path(__file__).resolve().parent.parent


class Timeouts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    default_s: float = 300
    route_s: float = 900
    sim_s: float = 120


class ToolsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sandbox_home: Path
    kicad_cli: Path
    kicad_python: Path
    kicad_share: Path
    ngspice_lib: Path
    fastroute: Path
    krt_python: Path
    krt_route: Path
    # 只在 Linux 的布线器对比里用（ADR-0004），没装就留空。
    tracemaker: Path | None = None
    timeouts: Timeouts = Timeouts()


class GatesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # G1 额定值检查：网络电压（电流、功耗）≤ 器件额定值 × 降额系数（PLAN §7）
    derating: float = Field(default=0.8, gt=0, le=1)


class PcbConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # G3 线宽检查和版图智能体定线宽用的铜厚、温升（IPC-2221，PLAN §7 G3）
    copper_thickness_um: float = Field(default=35.0, gt=0)  # 1 oz/ft²
    temp_rise_c: float = Field(default=10.0, gt=0)


class PlacementWeights(BaseModel):
    """自动摆放的打分权重（ADR-0018）。各项换算成 mm 量级，hpwl 是 1。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    hpwl: float = Field(default=1.0, ge=0, description="总线长 HPWL（mm）")
    block: float = Field(
        default=0.5, ge=0, description="同一功能块的元件聚在一起：块内元件中心外包框的半周长"
    )
    flow: float = Field(
        default=0.3, ge=0, description="信号按流向从左往右排：元件中心离它的流向位置的距离"
    )
    edge: float = Field(
        default=1.0, ge=0, description="离板边太近（比 edge_preferred_mm 近）的距离"
    )
    overlap: float = Field(default=40.0, gt=0, description="courtyard 重叠、出区域：每 mm 的惩罚")


class PlacementConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["auto", "rows"] = "auto"  # rows = 原来的按行排，用来对比
    seed: int = 1  # 同样的输入和种子得到同样的摆放
    restarts: int = Field(default=1, ge=1)  # 用 seed、seed+1… 各摆一次，取分数最好的
    iterations_per_part: int = Field(default=1500, ge=1)
    min_gap_mm: float = Field(default=1.0, ge=0)  # 元件 courtyard 之间留的最小间隙（给走线留通道）
    # courtyard 离板边的最小距离，板边连接器就贴在这条线上
    edge_margin_mm: float = Field(default=1.0, ge=0)
    edge_preferred_mm: float = Field(default=2.5, ge=0)  # 非连接器元件离板边最好不要比这更近
    weights: PlacementWeights = PlacementWeights()


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tools: ToolsConfig
    gates: GatesConfig = GatesConfig()
    pcb: PcbConfig = PcbConfig()
    placement: PlacementConfig = PlacementConfig()


def default_config_path() -> Path:
    env = os.environ.get("EDA_AGENT_CONFIG")
    if env:
        return Path(env)
    local = REPO_ROOT / "config.yaml"
    return local if local.exists() else REPO_ROOT / "config.example.yaml"


def _absolute(path: Path, root: Path = REPO_ROOT) -> Path:
    # 不能用 resolve()：Linux 上 venv 里的 bin/python 是指向系统 Python 的符号链接，
    # 跟过去就跑到了不带 venv 包的那个解释器（ADR-0012 第一次 CI 运行发现）。
    return path if path.is_absolute() else Path(os.path.abspath(root / path))


def load_config(path: Path | None = None) -> Config:
    source = path or default_config_path()
    raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    cfg = Config.model_validate(raw)
    tools = cfg.tools
    resolved = tools.model_copy(
        update={
            name: _absolute(value)
            for name in ToolsConfig.model_fields
            if isinstance(value := getattr(tools, name), Path)
        }
    )
    return cfg.model_copy(update={"tools": resolved})
