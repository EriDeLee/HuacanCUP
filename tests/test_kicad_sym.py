"""派生符号展平时属性怎么合并（eda_agent/emit/kicad_sym.py）。

照 KiCad 10 的 LIB_SYMBOL::Flatten()（eeschema/lib_symbol.cpp）。
"""

from __future__ import annotations

from eda_agent.emit.kicad_sym import merge_properties


def _sym(*props: tuple[str, str]) -> str:
    body = "\n\t\t".join(f'(property "{k}" "{v}" (at 0 0 0))' for k, v in props)
    return f'(symbol "X"\n\t\t{body}\n\t)'


def _merged(*levels: str) -> dict[str, str]:
    out = {}
    for p in merge_properties(list(levels)):
        name, value = p.split('"')[1], p.split('"')[3]
        out[name] = value
    return out


def test_an_empty_footprint_in_the_child_keeps_the_parent_footprint() -> None:
    root = _sym(("Reference", "U"), ("Value", "LTC2850xS8"), ("Footprint", "Package_SO:SOIC-8"))
    middle = _sym(("Reference", "U"), ("Value", "MAX481E"), ("Footprint", ""))
    child = _sym(("Reference", "U"), ("Value", "MAX485E"), ("Footprint", ""))
    merged = _merged(root, middle, child)
    assert merged["Footprint"] == "Package_SO:SOIC-8"
    assert merged["Value"] == "MAX485E"


def test_a_non_empty_child_field_replaces_the_parent_one() -> None:
    root = _sym(("Value", "AP1117-15"), ("Footprint", "SOT-223"), ("Datasheet", "a.pdf"))
    child = _sym(("Value", "AMS1117-3.3"), ("Footprint", "SOT-223-3"), ("Datasheet", "b.pdf"))
    assert _merged(root, child) == {
        "Value": "AMS1117-3.3",
        "Footprint": "SOT-223-3",
        "Datasheet": "b.pdf",
    }


def test_custom_fields_are_kept_and_an_empty_custom_field_still_overrides() -> None:
    """只有 KiCad 的固定字段和关键字、封装过滤空了才不盖；自定义字段照盖。

    父符号独有的字段（LM358 的父符号 LM2904 的 ki_locked）要留着。
    """
    root = _sym(("Value", "LM2904"), ("ki_locked", ""), ("Note", "parent"))
    child = _sym(("Value", "LM358"), ("Note", ""))
    assert _merged(root, child) == {"Value": "LM358", "ki_locked": "", "Note": ""}
