"""design.json 的 M0 子集：只含全链路冒烟用到的字段（PLAN §5.2）。

M1 按 PLAN 补全其余字段（mpn、lcsc、spice、ratings、net_classes 等）并导出 JSON Schema。
多余字段一律报错，不静默忽略。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

LIB_ITEM = r"^[^:\s]+:[^:\s]+$"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class Block(_Model):
    id: str
    purpose: str


class Part(_Model):
    ref: str = Field(pattern=r"^[A-Z]+[0-9]+$")
    block: str | None = None
    symbol: str = Field(pattern=LIB_ITEM, description="KiCad 符号，库名:符号名")
    footprint: str = Field(pattern=LIB_ITEM, description="KiCad 封装，库名:封装名")
    value: str


class Net(_Model):
    name: str = Field(min_length=1)
    # M0 只有默认网络类；自定义网络类（线宽、间距）在 M1 加入。
    net_class: Literal["default"] = Field(default="default", alias="class")
    nodes: list[tuple[str, str]] = Field(min_length=1)
    # 这个网络的电由板外经连接器送进来。原理图上只给这样的网络放 PWR_FLAG（ADR-0008），
    # 没标的电源网络若没有电源输出来驱动，ERC 照样报错。
    external_supply: bool = False


class EdgeConnector(_Model):
    ref: str
    side: Literal["left", "right"]


class Board(_Model):
    size_mm: tuple[float, float]
    layers: Literal[2] = 2
    edge_connectors: list[EdgeConnector] = []


class Design(_Model):
    spec_sha256: str | None = None
    blocks: list[Block] = []
    parts: list[Part] = Field(min_length=1)
    nets: list[Net] = Field(min_length=1)
    board: Board
    notes: list[str] = []

    @model_validator(mode="after")
    def _check_references(self) -> Design:
        refs = [p.ref for p in self.parts]
        duplicates = sorted({r for r in refs if refs.count(r) > 1})
        if duplicates:
            raise ValueError(f"元件位号重复：{duplicates}")
        known = set(refs)
        seen: dict[tuple[str, str], str] = {}
        for net in self.nets:
            for node in net.nodes:
                if node[0] not in known:
                    raise ValueError(f"网络 {net.name} 引用了不存在的元件 {node[0]}")
                if node in seen:
                    raise ValueError(f"引脚 {node[0]}.{node[1]} 同时在 {seen[node]} 和 {net.name}")
                seen[node] = net.name
        for conn in self.board.edge_connectors:
            if conn.ref not in known:
                raise ValueError(f"板边连接器 {conn.ref} 不在元件列表里")
        return self


def load_design(path: Path) -> Design:
    return Design.model_validate_json(path.read_text(encoding="utf-8"))
