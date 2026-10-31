"""把 sch_layout 排好的图元写成 KiCad 10 的 .kicad_sch 文本。

所有 UUID 由工程名和图元顺序推出来，同样的输入写出同样的文件。
符号的定义直接嵌入库里的原文，不重新生成。
"""

from __future__ import annotations

import uuid

from eda_agent.emit.sch_layout import Drawing, Field, SymbolInst

FONT = "(font (size 1.27 1.27))"


def _f(v: float) -> str:
    text = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _q(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


class _Ids:
    def __init__(self, project: str) -> None:
        self._ns = uuid.uuid5(uuid.NAMESPACE_URL, f"eda-agent/{project}")
        self._n = 0

    def next(self) -> str:
        self._n += 1
        return str(uuid.uuid5(self._ns, str(self._n)))


def _property(
    name: str, text: str, x: float, y: float, angle: int = 0, justify: str = "", hide: bool = False
) -> str:
    effects = FONT
    if justify:
        effects += f" (justify {justify})"
    if hide:
        effects += " (hide yes)"
    at = f"(at {_f(x)} {_f(y)} {angle})"
    return f"\t\t(property {_q(name)} {_q(text)} {at} (effects {effects}))"


def _symbol(inst: SymbolInst, ids: _Ids, root: str, project: str, power_no: int) -> str:
    ref = f"{inst.prefix}{power_no:03d}" if inst.power else inst.ref
    lines = ["\t(symbol", f"\t\t(lib_id {_q(inst.lib_id)})"]
    lines.append(f"\t\t(at {_f(inst.x)} {_f(inst.y)} {inst.rot})")
    if inst.mirror:
        lines.append("\t\t(mirror y)")
    lines += [
        "\t\t(unit 1)",
        "\t\t(exclude_from_sim no)",
        "\t\t(in_bom yes)",
        "\t\t(on_board yes)",
        "\t\t(dnp no)",
        f"\t\t(uuid {_q(ids.next())})",
    ]
    by_name: dict[str, Field] = {f.name: f for f in inst.fields}
    shown = {"Reference": ref, "Value": inst.value}
    # KiCad 里字段的角度是相对符号方向的：符号转了 90/270 度，字段写 90 才是横着的字。
    angle = 90 if inst.rot in (90, 270) else 0
    for name, text in shown.items():
        f = by_name.get(name)
        if f is None:
            lines.append(_property(name, text, inst.x, inst.y, angle, hide=True))
        else:
            lines.append(_property(name, text, f.x, f.y, angle, f.justify, f.hide))
    lines.append(_property("Footprint", inst.footprint, inst.x, inst.y, angle, hide=True))
    lines.append(_property("Datasheet", "~", inst.x, inst.y, angle, hide=True))
    lines.append(_property("Description", "", inst.x, inst.y, angle, hide=True))
    for number in inst.pins:
        lines.append(f"\t\t(pin {_q(number)} (uuid {_q(ids.next())}))")
    lines.append(
        f"\t\t(instances (project {_q(project)} (path {_q('/' + root)}"
        f" (reference {_q(ref)}) (unit 1))))"
    )
    lines.append("\t)")
    return "\n".join(lines)


def render_schematic(drawing: Drawing, *, project: str, title: str, date: str) -> str:
    ids = _Ids(project)
    root = str(uuid.uuid5(uuid.NAMESPACE_URL, f"eda-agent/{project}/root"))
    p = drawing.prims
    out = [
        "(kicad_sch",
        "\t(version 20250114)",
        '\t(generator "eda_agent")',
        '\t(generator_version "0.1")',
        f"\t(uuid {_q(root)})",
        f"\t(paper {_q(drawing.paper)})",
        f"\t(title_block (title {_q(title)}) (date {_q(date)}))",
        "\t(lib_symbols",
    ]
    for raw in drawing.libs.values():
        out.append("\t\t" + raw.strip().replace("\n", "\n\t\t"))
    out.append("\t)")
    for w in p.wires:
        out.append(
            f"\t(wire (pts (xy {_f(w.a[0])} {_f(w.a[1])}) (xy {_f(w.b[0])} {_f(w.b[1])}))"
            f" (stroke (width 0) (type default)) (uuid {_q(ids.next())}))"
        )
    for x, y in p.junctions:
        out.append(
            f"\t(junction (at {_f(x)} {_f(y)}) (diameter 0) (color 0 0 0 0)"
            f" (uuid {_q(ids.next())}))"
        )
    for x, y in p.ncs:
        out.append(f"\t(no_connect (at {_f(x)} {_f(y)}) (uuid {_q(ids.next())}))")
    for lb in p.labels:
        justify = "right bottom" if lb.rot == 180 else "left bottom"
        out.append(
            f"\t(label {_q(lb.text)} (at {_f(lb.x)} {_f(lb.y)} {lb.rot})"
            f" (effects {FONT} (justify {justify})) (uuid {_q(ids.next())}))"
        )
    power_no = 0
    for inst in p.symbols:
        if inst.power:
            power_no += 1
        out.append(_symbol(inst, ids, root, project, power_no))
    out.append(")")
    return "\n".join(out) + "\n"
