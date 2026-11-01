"""把 sch_layout 排好的图元写成 KiCad 10 的 .kicad_sch 文本。

- 所有 UUID 由工程名、页名和图元顺序推出来，同样的输入写出同样的文件。
- 符号的定义直接嵌入库里的原文（派生符号先展平，见 kicad_sym），不重新生成。
- 多页工程（ADR-0013）：根页放每个功能块的图纸框（sheet），框上的引脚通过短线和同名标签互相连接；
  每个功能块一页，跨页的信号在子页里画层次标签。电源符号、PWR_FLAG 的编号在整个工程里统一往下数。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from eda_agent.emit.sch_layout import Drawing, Field, SymbolInst, flip_justify

FONT = "(font (size 1.27 1.27))"


def _f(v: float) -> str:
    text = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _q(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def root_uuid(project: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"eda-agent/{project}/root"))


def sheet_uuid(project: str, page: str) -> str:
    """根页上这一页的图纸框的 UUID；子页里元件的实例路径是 /根页/图纸框。"""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"eda-agent/{project}/sheet/{page}"))


class _Ids:
    def __init__(self, project: str, page: str | None) -> None:
        name = f"eda-agent/{project}" if page is None else f"eda-agent/{project}/page/{page}"
        self._ns = uuid.uuid5(uuid.NAMESPACE_URL, name)
        self._n = 0

    def next(self) -> str:
        self._n += 1
        return str(uuid.uuid5(self._ns, str(self._n)))


@dataclass
class Counter:
    """电源符号和 PWR_FLAG 的编号（#PWR001、#FLG002……）在整个工程里不能重复。"""

    n: int = 0

    def take(self) -> int:
        self.n += 1
        return self.n


@dataclass(frozen=True)
class SheetPin:
    name: str
    shape: str  # input / output / bidirectional / passive
    side: str  # left / right
    y: float  # 引脚的绝对 y


@dataclass(frozen=True)
class SheetSym:
    name: str
    file: str
    x: float
    y: float
    w: float
    h: float
    page: str  # 页码，根页是 "1"
    pins: tuple[SheetPin, ...] = ()


@dataclass(frozen=True)
class Text:
    text: str
    x: float
    y: float


@dataclass
class RootExtras:
    sheets: list[SheetSym] = field(default_factory=list)
    texts: list[Text] = field(default_factory=list)


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


def _symbol(inst: SymbolInst, ids: _Ids, instance_path: str, project: str, ref: str) -> str:
    lines = ["\t(symbol", f"\t\t(lib_id {_q(inst.lib_id)})"]
    lines.append(f"\t\t(at {_f(inst.x)} {_f(inst.y)} {inst.rot})")
    if inst.mirror:
        lines.append("\t\t(mirror y)")
    lines += [
        f"\t\t(unit {inst.unit})",
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
            # 版面里镜像符号的对齐已经按显示效果对调过；文件里写的是相对符号的对齐，要对调回去
            justify = flip_justify(f.justify) if inst.mirror else f.justify
            lines.append(_property(name, text, f.x, f.y, angle, justify, f.hide))
    lines.append(_property("Footprint", inst.footprint, inst.x, inst.y, angle, hide=True))
    lines.append(_property("Datasheet", "~", inst.x, inst.y, angle, hide=True))
    lines.append(_property("Description", "", inst.x, inst.y, angle, hide=True))
    for number in inst.pins:
        lines.append(f"\t\t(pin {_q(number)} (uuid {_q(ids.next())}))")
    lines.append(
        f"\t\t(instances (project {_q(project)} (path {_q(instance_path)}"
        f" (reference {_q(ref)}) (unit {inst.unit}))))"
    )
    lines.append("\t)")
    return "\n".join(lines)


def _sheet(sheet: SheetSym, ids: _Ids, project: str) -> str:
    sid = sheet_uuid(project, sheet.name)
    lines = [
        "\t(sheet",
        f"\t\t(at {_f(sheet.x)} {_f(sheet.y)}) (size {_f(sheet.w)} {_f(sheet.h)})",
        "\t\t(exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no)",
        "\t\t(stroke (width 0.1524) (type solid)) (fill (color 0 0 0 0.0000))",
        f"\t\t(uuid {_q(sid)})",
        f'\t\t(property "Sheetname" {_q(sheet.name)} (at {_f(sheet.x)} {_f(sheet.y - 0.7)} 0)'
        f" (effects {FONT} (justify left bottom)))",
        f'\t\t(property "Sheetfile" {_q(sheet.file)}'
        f" (at {_f(sheet.x)} {_f(sheet.y + sheet.h + 0.6)} 0)"
        f" (effects {FONT} (justify left top)))",
    ]
    for pin in sheet.pins:
        on_left = pin.side == "left"
        px = sheet.x if on_left else sheet.x + sheet.w
        lines.append(
            f"\t\t(pin {_q(pin.name)} {pin.shape} (at {_f(px)} {_f(pin.y)} {180 if on_left else 0})"
            f" (uuid {_q(ids.next())}) (effects {FONT} (justify {'left' if on_left else 'right'})))"
        )
    lines.append(
        f"\t\t(instances (project {_q(project)} (path {_q('/' + root_uuid(project))}"
        f" (page {_q(sheet.page)}))))"
    )
    lines.append("\t)")
    return "\n".join(lines)


def render_page(
    drawing: Drawing,
    *,
    project: str,
    title: str,
    date: str,
    page: str | None = None,
    counter: Counter | None = None,
    extras: RootExtras | None = None,
    comment: str = "",
) -> str:
    """page 为 None 写根页（可带 extras 里的图纸框和文字）；否则是功能块子页的名字。

    comment 写进标题栏的注释 1（功能块是做什么的），标题本身保持短，免得超出标题栏。
    """
    counter = counter or Counter()
    ids = _Ids(project, page)
    root = root_uuid(project)
    is_root = page is None
    if page is None:
        file_uuid, instance_path = root, "/" + root
    else:
        file_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"eda-agent/{project}/file/{page}"))
        instance_path = f"/{root}/{sheet_uuid(project, page)}"
    p = drawing.prims
    out = [
        "(kicad_sch",
        "\t(version 20250114)",
        '\t(generator "eda_agent")',
        '\t(generator_version "0.1")',
        f"\t(uuid {_q(file_uuid)})",
        f"\t(paper {_q(drawing.paper)})",
        f"\t(title_block (title {_q(title)}) (date {_q(date)})"
        + (f" (comment 1 {_q(comment)})" if comment else "")
        + ")",
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
        at = f"(at {_f(lb.x)} {_f(lb.y)} {lb.rot})"
        if lb.shape:
            justify = "right" if lb.rot == 180 else "left"
            out.append(
                f"\t(hierarchical_label {_q(lb.text)} (shape {lb.shape}) {at}"
                f" (effects {FONT} (justify {justify})) (uuid {_q(ids.next())}))"
            )
        else:
            justify = "right bottom" if lb.rot == 180 else "left bottom"
            out.append(
                f"\t(label {_q(lb.text)} {at}"
                f" (effects {FONT} (justify {justify})) (uuid {_q(ids.next())}))"
            )
    if extras is not None:
        for t in extras.texts:
            out.append(
                f"\t(text {_q(t.text)} (exclude_from_sim no) (at {_f(t.x)} {_f(t.y)} 0)"
                f" (effects {FONT} (justify left top)) (uuid {_q(ids.next())}))"
            )
    for inst in p.symbols:
        ref = f"{inst.prefix}{counter.take():03d}" if inst.power else inst.ref
        out.append(_symbol(inst, ids, instance_path, project, ref))
    if extras is not None:
        for sheet in extras.sheets:
            out.append(_sheet(sheet, ids, project))
    if is_root:
        out.append('\t(sheet_instances (path "/" (page "1")))')
    out.append(")")
    return "\n".join(out) + "\n"


def render_schematic(drawing: Drawing, *, project: str, title: str, date: str) -> str:
    """单页工程：所有元件都在根页上。"""
    return render_page(drawing, project=project, title=title, date=date)
