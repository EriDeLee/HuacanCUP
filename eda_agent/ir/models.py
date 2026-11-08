"""design.json：系统唯一的设计源（PLAN §5.2）。JSON Schema 由这里导出到 schemas/design.schema.json。

多余字段一律报错，不静默忽略。字段说明写在 Field(description=...) 里，会进 JSON Schema，
智能体写补丁时看的就是它。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

LIB_ITEM = r"^[^:\s]+:[^:\s]+$"
DEFAULT_CLASS = "default"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class Block(_Model):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$", description="功能块标识，小写字母开头")
    purpose: str = Field(min_length=1, description="这一块做什么，会写进原理图页名旁边和文档")


class Spice(_Model):
    model: str = Field(min_length=1, description="SPICE 模型或子电路名")
    lib: str | None = Field(
        default=None,
        description="模型文件（相对仓库根目录，如 models/irfz44n.lib）；ngspice 原生元件留空",
    )
    pin_map: dict[str, str] = Field(
        default_factory=dict,
        description="符号引脚编号 → 模型端子名，如 {'1': 'G', '2': 'D', '3': 'S'}",
    )


class RatingsSource(_Model):
    doc_id: str = Field(min_length=1, description="数据手册标识（检索库里的文档 id）")
    page: int = Field(ge=1, description="页码，从 1 开始")


class Part(_Model):
    ref: str = Field(pattern=r"^[A-Z]+[0-9]+$", description="位号，如 R1、U3")
    block: str | None = Field(default=None, description="所属功能块（blocks[].id）")
    symbol: str = Field(pattern=LIB_ITEM, description="KiCad 符号，库名:符号名")
    footprint: str = Field(pattern=LIB_ITEM, description="KiCad 封装，库名:封装名")
    value: str = Field(min_length=1)
    mpn: str | None = Field(default=None, description="厂商料号")
    lcsc: str | None = Field(default=None, pattern=r"^C[0-9]+$", description="立创商城编号")
    source: Literal["inventory", "catalog"] | None = Field(
        default=None, description="inventory = 用户库存；catalog = 元器件目录"
    )
    spice: Spice | None = None
    ratings: dict[str, float] = Field(
        default_factory=dict,
        description="额定值，如 {'vds_max': 55, 'id_max': 49}，单位用国际单位制",
    )
    ratings_source: RatingsSource | None = Field(default=None, description="ratings 的出处")
    role: Literal["hub", "connector", "passive"] | None = Field(
        default=None,
        description=(
            "画原理图时的角色（ADR-0013）。hub = 主芯片，自成一团，其余元件沿它的引脚排开；"
            "connector = 连接器，画在页边、引脚朝里；passive = 两脚元件，串在引脚链上。"
            "不填则按引脚数推断：两个引脚的是 passive，其余是 hub"
        ),
    )
    anchor: str | None = Field(
        default=None,
        description=(
            "画原理图时贴在哪个元件旁边（位号）。用于不在任何引脚链上的元件，如电源和地之间的去耦电容；"
            "不填则贴在同一功能块里第一个和它共用网络的主芯片旁边"
        ),
    )
    nc_pins: list[str] = Field(
        default_factory=list,
        description=(
            "明确不接的引脚编号（NC）。G1 的悬空引脚检查不报这些引脚；"
            "没写的输入脚、电源脚不接东西就报错。这里的引脚不能同时出现在 nets 里"
        ),
    )


class Net(_Model):
    name: str = Field(min_length=1, pattern=r"^[^\s/]+$", description="网络名，不含空格和斜杠")
    net_class: str = Field(
        default=DEFAULT_CLASS,
        alias="class",
        description="网络类：default 或 board.net_classes 里的名字（决定线宽、间距）",
    )
    nodes: list[tuple[str, str]] = Field(min_length=1, description="[[位号, 引脚编号], ...]")
    external_supply: bool = Field(
        default=False,
        description=(
            "这个网络的电由板外经连接器送进来。原理图上只给这样的网络放 PWR_FLAG（ADR-0008）；"
            "没标的电源网络若没有电源输出来驱动，ERC 照样报错"
        ),
    )


class NetClass(_Model):
    track_mm: float = Field(gt=0, description="线宽")
    clearance_mm: float = Field(gt=0, description="与其他铜的最小间距")
    via_diameter_mm: float | None = Field(
        default=None, gt=0, description="过孔外径；不填用 KiCad 默认"
    )
    via_drill_mm: float | None = Field(
        default=None, gt=0, description="过孔钻孔；不填用 KiCad 默认"
    )

    @model_validator(mode="after")
    def _via(self) -> NetClass:
        if self.via_diameter_mm and self.via_drill_mm and self.via_drill_mm >= self.via_diameter_mm:
            raise ValueError("过孔钻孔必须小于外径")
        return self


class EdgeConnector(_Model):
    ref: str
    side: Literal["left", "right"]


class Board(_Model):
    size_mm: tuple[float, float] = Field(description="板子宽、高")
    layers: Literal[2, 4] = 2
    mount_holes: str | None = Field(
        default=None,
        pattern=r"^M(2|2\.5|3)x(2|4)$",
        description="安装孔，如 M3x4 = 四角各一个 M3 孔；x2 = 左上、右下两个",
    )
    edge_connectors: list[EdgeConnector] = []
    net_classes: dict[str, NetClass] = Field(
        default_factory=dict, description="自定义网络类；default 也可以在这里改"
    )

    @model_validator(mode="after")
    def _size(self) -> Board:
        if min(self.size_mm) <= 0:
            raise ValueError("板子尺寸必须为正")
        return self


class Design(_Model):
    spec_sha256: str | None = Field(default=None, description="对应的冻结 spec 的 sha256")
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
        block_ids = [b.id for b in self.blocks]
        if len(set(block_ids)) != len(block_ids):
            raise ValueError(
                f"功能块 id 重复：{sorted({b for b in block_ids if block_ids.count(b) > 1})}"
            )
        for part in self.parts:
            if part.block is not None and part.block not in block_ids:
                raise ValueError(f"{part.ref} 的功能块 {part.block} 不在 blocks 里")
            if part.anchor is not None and (part.anchor not in known or part.anchor == part.ref):
                raise ValueError(f"{part.ref} 的 anchor {part.anchor} 不是另一个元件的位号")
        names = [n.name for n in self.nets]
        if len(set(names)) != len(names):
            raise ValueError(f"网络名重复：{sorted({n for n in names if names.count(n) > 1})}")
        classes = {DEFAULT_CLASS, *self.board.net_classes}
        seen: dict[tuple[str, str], str] = {}
        for net in self.nets:
            if net.net_class not in classes:
                raise ValueError(
                    f"网络 {net.name} 的网络类 {net.net_class} 没有在 board.net_classes 里定义"
                )
            for node in net.nodes:
                if node[0] not in known:
                    raise ValueError(f"网络 {net.name} 引用了不存在的元件 {node[0]}")
                if node in seen:
                    raise ValueError(f"引脚 {node[0]}.{node[1]} 同时在 {seen[node]} 和 {net.name}")
                seen[node] = net.name
        for conn in self.board.edge_connectors:
            if conn.ref not in known:
                raise ValueError(f"板边连接器 {conn.ref} 不在元件列表里")
        for part in self.parts:
            if len(set(part.nc_pins)) != len(part.nc_pins):
                raise ValueError(f"{part.ref} 的 nc_pins 有重复的引脚")
            for pin in part.nc_pins:
                if (part.ref, pin) in seen:
                    raise ValueError(
                        f"引脚 {part.ref}.{pin} 标了 NC，却又在网络 {seen[(part.ref, pin)]} 里"
                    )
        return self


def load_design(path: Path) -> Design:
    return Design.model_validate_json(path.read_text(encoding="utf-8"))


def design_json_schema() -> dict[str, object]:
    """导出到 schemas/design.schema.json 的内容（字段名用 JSON 里的写法，如 class）。"""
    schema = Design.model_json_schema(by_alias=True, mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "design.schema.json"
    return schema
