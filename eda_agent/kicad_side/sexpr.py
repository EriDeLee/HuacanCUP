"""最小的 S 表达式解析器，够读 kicad-cli 导出的 kicadsexpr 网表。只用标准库。"""

from __future__ import annotations

import re
from typing import Any

Node = list[Any]

_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))')
_ESCAPE = re.compile(r"\\(.)")


def _unescape(text: str) -> str:
    return _ESCAPE.sub(lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), text)


def parse(text: str) -> Node:
    stack: list[Node] = [[]]
    pos = 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if m is None:
            if text[pos:].strip():
                raise ValueError(f"无法解析的位置 {pos}: {text[pos : pos + 40]!r}")
            break
        pos = m.end()
        if m.group(1):
            stack.append([])
        elif m.group(2):
            if len(stack) < 2:
                raise ValueError(f"多余的右括号，位置 {pos}")
            done = stack.pop()
            stack[-1].append(done)
        elif m.group(3) is not None:
            stack[-1].append(_unescape(m.group(3)))
        else:
            stack[-1].append(m.group(4))
    if len(stack) != 1:
        raise ValueError("括号不配对")
    return stack[0]


def children(node: Node | None, tag: str) -> list[Node]:
    if not node:
        return []
    return [c for c in node[1:] if isinstance(c, list) and c and c[0] == tag]


def child(node: Node | None, tag: str) -> Node | None:
    found = children(node, tag)
    return found[0] if found else None


def value(node: Node | None, tag: str, default: str = "") -> str:
    found = child(node, tag)
    if found and len(found) > 1 and isinstance(found[1], str):
        return found[1]
    return default
