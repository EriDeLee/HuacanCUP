"""JSON Patch 应用器：RFC 6902 附录 A 的全部可表示示例，加上设计层面的校验。"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from eda_agent.ir.models import load_design
from eda_agent.ir.patch import (
    PatchError,
    apply_design_patch,
    apply_patch,
    design_document,
    json_equal,
    parse_pointer,
)

# RFC 6902 附录 A：（编号, 文档, 补丁, 期望结果）；期望结果为 None 表示应当报错。
# A.13（同一条操作里重复的 "op" 成员）在 Python 的 dict 里表示不出来，略去。
RFC_EXAMPLES: list[tuple[str, Any, list[dict[str, Any]], Any]] = [
    (
        "A.1",
        {"foo": "bar"},
        [{"op": "add", "path": "/baz", "value": "qux"}],
        {"baz": "qux", "foo": "bar"},
    ),
    (
        "A.2",
        {"foo": ["bar", "baz"]},
        [{"op": "add", "path": "/foo/1", "value": "qux"}],
        {"foo": ["bar", "qux", "baz"]},
    ),
    ("A.3", {"baz": "qux", "foo": "bar"}, [{"op": "remove", "path": "/baz"}], {"foo": "bar"}),
    (
        "A.4",
        {"foo": ["bar", "qux", "baz"]},
        [{"op": "remove", "path": "/foo/1"}],
        {"foo": ["bar", "baz"]},
    ),
    (
        "A.5",
        {"baz": "qux", "foo": "bar"},
        [{"op": "replace", "path": "/baz", "value": "boo"}],
        {"baz": "boo", "foo": "bar"},
    ),
    (
        "A.6",
        {"foo": {"bar": "baz", "waldo": "fred"}, "qux": {"corge": "grault"}},
        [{"op": "move", "from": "/foo/waldo", "path": "/qux/thud"}],
        {"foo": {"bar": "baz"}, "qux": {"corge": "grault", "thud": "fred"}},
    ),
    (
        "A.7",
        {"foo": ["all", "grass", "cows", "eat"]},
        [{"op": "move", "from": "/foo/1", "path": "/foo/3"}],
        {"foo": ["all", "cows", "eat", "grass"]},
    ),
    (
        "A.8",
        {"baz": "qux", "foo": ["a", 2, "c"]},
        [
            {"op": "test", "path": "/baz", "value": "qux"},
            {"op": "test", "path": "/foo/1", "value": 2},
        ],
        {"baz": "qux", "foo": ["a", 2, "c"]},
    ),
    ("A.9", {"baz": "qux"}, [{"op": "test", "path": "/baz", "value": "bar"}], None),
    (
        "A.10",
        {"foo": "bar"},
        [{"op": "add", "path": "/child", "value": {"grandchild": {}}}],
        {"foo": "bar", "child": {"grandchild": {}}},
    ),
    (
        "A.11",
        {"foo": "bar"},
        [{"op": "add", "path": "/baz", "value": "qux", "xyz": 123}],
        {"foo": "bar", "baz": "qux"},
    ),
    ("A.12", {"foo": "bar"}, [{"op": "add", "path": "/baz/bat", "value": "qux"}], None),
    ("A.14", {"/": 9, "~1": 10}, [{"op": "test", "path": "/~01", "value": 10}], {"/": 9, "~1": 10}),
    ("A.15", {"/": 9, "~1": 10}, [{"op": "test", "path": "/~01", "value": "10"}], None),
    (
        "A.16",
        {"foo": ["bar"]},
        [{"op": "add", "path": "/foo/-", "value": ["abc", "def"]}],
        {"foo": ["bar", ["abc", "def"]]},
    ),
]


@pytest.mark.parametrize(
    "name, doc, patch, expected", RFC_EXAMPLES, ids=[e[0] for e in RFC_EXAMPLES]
)
def test_rfc6902_appendix_a(
    name: str, doc: Any, patch: list[dict[str, Any]], expected: Any
) -> None:
    original = copy.deepcopy(doc)
    if expected is None:
        with pytest.raises(PatchError):
            apply_patch(doc, patch)
    else:
        assert apply_patch(doc, patch) == expected
    assert doc == original, "原文档不能被改动"


def test_pointer_unescaping_order() -> None:
    assert parse_pointer("") == []
    assert parse_pointer("/a~1b/m~0n/~01") == ["a/b", "m~n", "~1"]
    with pytest.raises(PatchError):
        parse_pointer("no-slash")


@pytest.mark.parametrize(
    "patch",
    [
        [{"op": "add", "path": "/foo/01", "value": 1}],  # 前导 0
        [{"op": "add", "path": "/foo/5", "value": 1}],  # 超出范围
        [{"op": "remove", "path": "/foo/-"}],  # - 只能用在 add
        [{"op": "replace", "path": "/nope", "value": 1}],  # 目标必须存在
        [{"op": "move", "from": "/foo", "path": "/foo/0"}],  # 不能移到自己里面
        [{"op": "copy", "from": "/nope", "path": "/x"}],
        [{"op": "jump", "path": "/foo"}],
        [{"op": "add", "path": "/x"}],  # 缺 value
        [{"op": "remove", "path": ""}],
        [{"path": "/foo"}],
        ["not an object"],
    ],
)
def test_invalid_operations_are_rejected(patch: list[Any]) -> None:
    with pytest.raises(PatchError):
        apply_patch({"foo": [1, 2]}, patch)


def test_a_failing_operation_leaves_the_document_untouched_and_names_the_index() -> None:
    doc = {"a": 1}
    with pytest.raises(PatchError) as info:
        apply_patch(doc, [{"op": "add", "path": "/b", "value": 2}, {"op": "remove", "path": "/zz"}])
    assert info.value.index == 1
    assert doc == {"a": 1}


def test_copy_is_deep_and_replace_keeps_member_order() -> None:
    out = apply_patch({"a": {"x": [1]}, "b": 0}, [{"op": "copy", "from": "/a", "path": "/c"}])
    out["c"]["x"].append(2)
    assert out["a"]["x"] == [1]
    assert list(apply_patch({"a": 1, "b": 2}, [{"op": "replace", "path": "/a", "value": 9}])) == [
        "a",
        "b",
    ]


def test_json_equality_keeps_booleans_and_numbers_apart() -> None:
    assert json_equal(1, 1.0)
    assert not json_equal(True, 1)
    assert not json_equal({"a": [True]}, {"a": [1]})
    with pytest.raises(PatchError):
        apply_patch({"flag": True}, [{"op": "test", "path": "/flag", "value": 1}])


# ---------------------------------------------------------------- 设计层面


def test_a_valid_patch_gives_a_new_design(led_design_path: Path) -> None:
    design = load_design(led_design_path)
    result = apply_design_patch(
        design,
        [
            {"op": "replace", "path": "/parts/1/value", "value": "470"},
            {"op": "add", "path": "/parts/1/lcsc", "value": "C17710"},
            {"op": "test", "path": "/nets/0/name", "value": "VIN"},
        ],
    )
    assert result.ok and result.design is not None
    assert result.design.parts[1].value == "470"
    assert result.design.parts[1].lcsc == "C17710"
    assert design.parts[1].value == "330", "原设计不变"


def test_empty_optional_fields_need_add_not_replace(led_design_path: Path) -> None:
    result = apply_design_patch(
        load_design(led_design_path), [{"op": "replace", "path": "/parts/1/lcsc", "value": "C1"}]
    )
    assert not result.ok
    assert result.errors[0]["kind"] == "patch"
    assert "add" in result.errors[0]["message"]


def test_a_patch_that_breaks_the_design_is_reported_with_its_location(
    led_design_path: Path,
) -> None:
    result = apply_design_patch(
        load_design(led_design_path),
        [{"op": "add", "path": "/nets/1/nodes/-", "value": ["U9", "1"]}],
    )
    assert not result.ok
    assert result.errors[0]["kind"] == "design"
    assert "U9" in result.errors[0]["message"]


def test_field_level_errors_name_the_field(led_design_path: Path) -> None:
    result = apply_design_patch(
        load_design(led_design_path), [{"op": "add", "path": "/parts/0/lcsc", "value": "12345"}]
    )
    assert not result.ok
    assert result.errors[0]["loc"] == "parts.0.lcsc"


def test_the_document_round_trips(led_design_path: Path) -> None:
    design = load_design(led_design_path)
    document = design_document(design)
    assert "class" in document["nets"][0] and "mpn" not in document["parts"][0]
    assert apply_design_patch(design, []).design == design
    json.dumps(document)  # 能直接写成 JSON
