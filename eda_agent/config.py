"""读取配置：EDA_AGENT_CONFIG 指定的文件，否则 config.yaml，再否则 config.example.yaml。

配置里的相对路径一律以仓库根目录为准。
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

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
    freerouting: Path
    krt_python: Path
    krt_route: Path
    timeouts: Timeouts = Timeouts()


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tools: ToolsConfig


def default_config_path() -> Path:
    env = os.environ.get("EDA_AGENT_CONFIG")
    if env:
        return Path(env)
    local = REPO_ROOT / "config.yaml"
    return local if local.exists() else REPO_ROOT / "config.example.yaml"


def _absolute(path: Path) -> Path:
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def load_config(path: Path | None = None) -> Config:
    source = path or default_config_path()
    raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    cfg = Config.model_validate(raw)
    tools = cfg.tools
    resolved = tools.model_copy(
        update={
            name: _absolute(getattr(tools, name))
            for name, field in ToolsConfig.model_fields.items()
            if field.annotation is Path
        }
    )
    return cfg.model_copy(update={"tools": resolved})
