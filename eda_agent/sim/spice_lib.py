"""扫描 SPICE 模型文件：里面有哪些 .subckt（端子顺序）和 .model（类型）。

测试台生成器要靠它决定一个元件怎么写进网表：子电路按端子顺序接线，模型卡按类型选 D / Q / M 元件。
只认文件最外层的定义；写在 .subckt 里面的 .model 是子电路自己的，电路里看不见。
大小写不敏感（SPICE 本来就不分），续行（行首 +）、整行注释（行首 *）、行内注释（; 和 $）都处理。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal

# ngspice 的器件模型类型 → 元件首字母和端子（端子名就是 pin_map 里要写的名字）
MODEL_ELEMENTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "d": ("D", ("A", "K")),
    "npn": ("Q", ("C", "B", "E")),
    "pnp": ("Q", ("C", "B", "E")),
    "nmos": ("M", ("D", "G", "S")),
    "pmos": ("M", ("D", "G", "S")),
    "vdmos": ("M", ("D", "G", "S")),
}

_SUBCKT = re.compile(r"^\.subckt\s+(\S+)(.*)$", re.IGNORECASE)
_MODEL = re.compile(r"^\.model\s+(\S+)\s+([A-Za-z0-9_]+)", re.IGNORECASE)
_ENDS = re.compile(r"^\.ends\b", re.IGNORECASE)


@dataclass(frozen=True)
class LibEntry:
    name: str
    kind: Literal["subckt", "model"]
    ports: tuple[str, ...] = ()  # subckt 的端子，按声明顺序，原样（大小写不动）
    model_type: str = ""  # model 的类型，小写：d、npn、nmos、vdmos……


def _logical_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("*"):
            continue
        line = re.split(r"\s;|\s\$|;", line, maxsplit=1)[0].strip()
        if line.startswith("+") and lines:
            lines[-1] += " " + line[1:].strip()
        elif line:
            lines.append(line)
    return lines


def parse_library(text: str) -> dict[str, LibEntry]:
    """名字（小写）→ 条目。同名的取先出现的。"""
    entries: dict[str, LibEntry] = {}
    depth = 0
    for line in _logical_lines(text):
        sub = _SUBCKT.match(line)
        if sub is not None:
            if depth == 0:
                ports = []
                for token in sub.group(2).split():
                    if "=" in token or token.lower().startswith("params"):
                        break
                    ports.append(token)
                entries.setdefault(
                    sub.group(1).lower(), LibEntry(sub.group(1), "subckt", tuple(ports))
                )
            depth += 1
        elif _ENDS.match(line):
            depth = max(0, depth - 1)
        elif depth == 0:
            model = _MODEL.match(line)
            if model is not None:
                entries.setdefault(
                    model.group(1).lower(),
                    LibEntry(model.group(1), "model", model_type=model.group(2).lower()),
                )
    return entries


@cache
def _scan(path: str, mtime_ns: int) -> dict[str, LibEntry]:
    return parse_library(Path(path).read_text(encoding="utf-8", errors="replace"))


def scan_library(path: Path) -> dict[str, LibEntry]:
    return _scan(str(path), path.stat().st_mtime_ns)
