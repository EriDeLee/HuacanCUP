"""RFC 6902 JSON Patch 应用器（含 RFC 6901 JSON Pointer），以及「补丁 + design.json 校验」。

PLAN §5.2。

智能体只能提交 JSON Patch。编排器调用 apply_design_patch：补丁整体应用 → pydantic 校验 →
失败时把错误原样返回，交给同一个智能体重试（重试次数由编排器管，PLAN 规定最多 3 次）。

- 整个补丁要么全部生效，要么完全不生效：先在副本上逐条应用，任何一条出错都不改原设计。
- 补丁作用在设计的标准 JSON 上（design_document()）：字段名用 JSON 里的写法（如 class），
  值为空（null）的可选字段不出现。所以给空字段赋值要用 add，用 replace 会报「路径不存在」。
- 只用标准库，不引第三方包（ADR-0005：依赖越少越好）。
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from eda_agent.ir.models import Design

JSON = Any
_INDEX = re.compile(r"^(0|[1-9][0-9]*)$")
OPS = ("add", "remove", "replace", "move", "copy", "test")


class PatchError(ValueError):
    """补丁本身不能应用（格式错、路径不存在、test 不相等……）。index 是出错的那一条（从 0 数）。"""

    def __init__(self, message: str, index: int | None = None) -> None:
        self.index = index
        prefix = f"第 {index} 条操作：" if index is not None else ""
        super().__init__(prefix + message)
        self.message = message


def parse_pointer(pointer: str) -> list[str]:
    """RFC 6901：先把 ~1 换回 /，再把 ~0 换回 ~（顺序不能反）。"""
    if not isinstance(pointer, str):
        raise PatchError(f"路径必须是字符串，收到 {pointer!r}")
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise PatchError(f"路径 {pointer!r} 必须以 / 开头（整个文档用空字符串）")
    return [t.replace("~1", "/").replace("~0", "~") for t in pointer[1:].split("/")]


def _array_index(token: str, length: int, *, allow_end: bool, pointer: str) -> int:
    if allow_end and token == "-":
        return length
    if not _INDEX.match(token):
        raise PatchError(f"{pointer}：数组下标 {token!r} 不是非负整数（不能有前导 0）")
    index = int(token)
    limit = length if allow_end else length - 1
    if index > limit:
        raise PatchError(f"{pointer}：数组下标 {index} 超出范围（数组长度 {length}）")
    return index


def _resolve(doc: JSON, tokens: list[str], pointer: str) -> JSON:
    node = doc
    for i, token in enumerate(tokens):
        here = "/" + "/".join(tokens[: i + 1])
        if isinstance(node, dict):
            if token not in node:
                raise PatchError(f"路径 {pointer} 不存在（{here} 没有这个成员）")
            node = node[token]
        elif isinstance(node, list):
            node = node[_array_index(token, len(node), allow_end=False, pointer=pointer)]
        else:
            raise PatchError(f"路径 {pointer} 不存在（{here} 的上一级不是对象也不是数组）")
    return node


def get_value(doc: JSON, pointer: str) -> JSON:
    return _resolve(doc, parse_pointer(pointer), pointer)


def _parent(doc: JSON, pointer: str) -> tuple[JSON, str]:
    tokens = parse_pointer(pointer)
    parent = _resolve(doc, tokens[:-1], pointer)
    if not isinstance(parent, (dict, list)):
        raise PatchError(f"路径 {pointer} 的上一级不是对象也不是数组")
    return parent, tokens[-1]


def _add(doc: JSON, pointer: str, value: JSON) -> JSON:
    if pointer == "":
        return value
    parent, last = _parent(doc, pointer)
    if isinstance(parent, dict):
        parent[last] = value
    else:
        parent.insert(_array_index(last, len(parent), allow_end=True, pointer=pointer), value)
    return doc


def _remove(doc: JSON, pointer: str) -> tuple[JSON, JSON]:
    if pointer == "":
        raise PatchError("不能删除整个文档")
    parent, last = _parent(doc, pointer)
    if isinstance(parent, dict):
        if last not in parent:
            raise PatchError(f"路径 {pointer} 不存在，没法删除")
        return doc, parent.pop(last)
    return doc, parent.pop(_array_index(last, len(parent), allow_end=False, pointer=pointer))


def json_equal(a: JSON, b: JSON) -> bool:
    """JSON 意义上的相等：1 和 1.0 相等，但 true 和 1 不相等（Python 里 True == 1）。"""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(json_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(json_equal(x, y) for x, y in zip(a, b, strict=True))
    return type(a) is type(b) and a == b


def _member(op: dict[str, Any], name: str) -> Any:
    if name not in op:
        raise PatchError(f"{op.get('op')} 操作缺少 {name!r}")
    return op[name]


def _apply_one(doc: JSON, op: Any) -> JSON:
    if not isinstance(op, dict):
        raise PatchError(f"每条操作必须是对象，收到 {type(op).__name__}")
    kind = op.get("op")
    if kind not in OPS:
        raise PatchError(f"不认识的 op {kind!r}，只能是 {', '.join(OPS)}")
    path = _member(op, "path")
    if kind == "add":
        return _add(doc, path, copy.deepcopy(_member(op, "value")))
    if kind == "remove":
        return _remove(doc, path)[0]
    if kind == "replace":
        value = copy.deepcopy(_member(op, "value"))
        if path == "":
            return value
        parent, last = _parent(doc, path)
        if isinstance(parent, dict):
            if last not in parent:
                raise PatchError(f"路径 {path} 不存在，没法替换（给空字段赋值要用 add）")
            parent[last] = value  # 原地替换，不改变成员的先后顺序
        else:
            parent[_array_index(last, len(parent), allow_end=False, pointer=path)] = value
        return doc
    if kind == "test":
        actual = get_value(doc, path)
        expected = _member(op, "value")
        if not json_equal(actual, expected):
            raise PatchError(f"test 不通过：{path} 的值是 {actual!r}，不是 {expected!r}")
        return doc
    source = _member(op, "from")
    if kind == "move":
        if source == path:
            return doc
        if path.startswith(source + "/"):
            raise PatchError(f"不能把 {source} 移到它自己里面（{path}）")
        doc, value = _remove(doc, source)
        return _add(doc, path, value)
    return _add(doc, path, copy.deepcopy(get_value(doc, source)))  # copy


def apply_patch(document: JSON, patch: Any) -> JSON:
    """把整个补丁应用到文档的副本上，返回新文档；原文档不变。出错抛 PatchError。"""
    if not isinstance(patch, list):
        raise PatchError(f"补丁必须是操作的数组，收到 {type(patch).__name__}")
    doc = copy.deepcopy(document)
    for index, op in enumerate(patch):
        try:
            doc = _apply_one(doc, op)
        except PatchError as exc:
            raise PatchError(exc.message, index) from None
    return doc


def design_document(design: Design) -> dict[str, Any]:
    """补丁作用的对象，也是写进 iterations/NN/design.json 的内容。"""
    data: dict[str, Any] = design.model_dump(mode="json", by_alias=True, exclude_none=True)
    return data


@dataclass(frozen=True)
class PatchResult:
    design: Design | None
    errors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.design is not None


def apply_design_patch(design: Design, patch: Any) -> PatchResult:
    """补丁 + 校验。失败时 errors 里每一项都能直接回给智能体：

    - 补丁本身错了：{"kind": "patch", "op_index": 2, "message": "..."}
    - 补丁能应用，但结果不是合法的设计：{"kind": "design", "loc": "parts.3.lcsc", "message": "..."}
    """
    try:
        document = apply_patch(design_document(design), patch)
    except PatchError as exc:
        return PatchResult(None, [{"kind": "patch", "op_index": exc.index, "message": exc.message}])
    try:
        return PatchResult(Design.model_validate(document))
    except ValidationError as exc:
        errors = [
            {
                "kind": "design",
                "loc": ".".join(str(part) for part in err["loc"]),
                "message": err["msg"],
            }
            for err in exc.errors(include_url=False)
        ]
        return PatchResult(None, errors)
