"""G1 的第一步：IR 校验（PLAN §7 G1）。

1. 引用唯一：位号、网络名不重复。设计文件交给 pydantic 读之前先在原始 JSON 上查一遍，
   这样报错说的是「哪个位号重复」，而不是 pydantic 的一大串。
2. 每个 node 的引脚在这个元件的符号里存在（用 KiCad 符号库解析，emit/kicad_sym.py）。
3. 符号的引脚数和封装的焊盘数一致（封装用 emit/kicad_mod.py 读）；每个 node 的引脚在封装上有焊盘。
4. spec 和 design 的对应：接口名 = 网络名，对不上就报错（review-queue D13）；
   负载、测量里提到的网络、位号、引脚也要在 design 里找得到。

这一步有 error，后面的自写规则和 ERC 都不跑：符号读不出来，规则没法判断引脚的电气类型，
发射器也画不出原理图。
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from eda_agent.emit.kicad_mod import FootprintNotFound, pad_numbers
from eda_agent.emit.kicad_sym import SymbolDef, load_symbol
from eda_agent.gates.report import Finding, error_finding
from eda_agent.ir.models import Design
from eda_agent.ir.spec import SIGNAL, Spec

_SIGNAL = re.compile(SIGNAL)


@dataclass
class IrCheck:
    findings: list[Finding] = field(default_factory=list)
    symbols: dict[str, SymbolDef] = field(default_factory=dict)  # 位号 → 符号（读出来的）

    @property
    def ok(self) -> bool:
        return not any(f.severity == "error" for f in self.findings)


def validate_document(doc: Mapping[str, Any]) -> tuple[Design | None, list[Finding]]:
    """原始 JSON → Design。读不进来就返回 (None, 说明问题的 finding)。"""
    findings: list[Finding] = []
    for key, label in (("parts", "位号"), ("nets", "网络名")):
        field_name = "ref" if key == "parts" else "name"
        items = doc.get(key)
        if not isinstance(items, list):
            continue
        names = [i.get(field_name) for i in items if isinstance(i, dict)]
        for name, n in sorted(Counter(names).items(), key=lambda kv: str(kv[0])):
            if name is not None and n > 1:
                findings.append(
                    error_finding(
                        "ir.duplicate_ref" if key == "parts" else "ir.duplicate_net",
                        f"{label} {name} 出现了 {n} 次，引用必须唯一",
                        detail={"name": name, "count": n},
                    )
                )
    if findings:
        return None, findings
    try:
        return Design.model_validate(doc), []
    except ValidationError as exc:
        return None, [
            error_finding(
                "ir.invalid",
                f"设计文件不合法：{'.'.join(str(p) for p in e['loc'])}：{e['msg']}",
                detail={"loc": [str(p) for p in e["loc"]], "type": e["type"]},
            )
            for e in exc.errors()
        ]


def _numbers(pins: Sequence[Any]) -> set[str]:
    return {p.number for p in pins}


def check_design(
    design: Design,
    spec: Spec | None,
    *,
    symbol_dirs: tuple[Path, ...],
    footprint_dir: Path,
) -> IrCheck:
    result = IrCheck()
    add = result.findings.append
    pins_of: dict[str, set[str]] = {}
    pads_of: dict[str, set[str]] = {}
    for part in design.parts:
        try:
            symbol = load_symbol(part.symbol, symbol_dirs)
        except (KeyError, ValueError, OSError) as exc:
            reason = exc.args[0] if exc.args else exc
            add(
                error_finding(
                    "ir.symbol_missing",
                    f"{part.ref} 的符号 {part.symbol} 读不出来：{reason}",
                    detail={"ref": part.ref, "symbol": part.symbol},
                )
            )
        else:
            result.symbols[part.ref] = symbol
            pins_of[part.ref] = {
                p.number for unit in symbol.units.values() for p in unit.pins
            } | _numbers(symbol.pins)
        try:
            pads_of[part.ref] = set(pad_numbers(part.footprint, footprint_dir))
        except (FootprintNotFound, ValueError, OSError) as exc:
            add(
                error_finding(
                    "ir.footprint_missing",
                    f"{part.ref} 的封装 {part.footprint} 读不出来：{exc}",
                    detail={"ref": part.ref, "footprint": part.footprint},
                )
            )

    for part in design.parts:
        pins, pads = pins_of.get(part.ref), pads_of.get(part.ref)
        if pins is not None and pads is not None and len(pins) != len(pads):
            only_pins, only_pads = sorted(pins - pads), sorted(pads - pins)
            add(
                error_finding(
                    "ir.pin_pad_count",
                    f"{part.ref}：符号 {part.symbol} 有 {len(pins)} 个引脚，"
                    f"封装 {part.footprint} 有 {len(pads)} 个焊盘"
                    f"（只在符号里：{only_pins}；只在封装里：{only_pads}）",
                    detail={
                        "ref": part.ref,
                        "pins": len(pins),
                        "pads": len(pads),
                        "only_in_symbol": only_pins,
                        "only_in_footprint": only_pads,
                    },
                )
            )
        if pins is not None:
            for pin in part.nc_pins:
                if pin not in pins:
                    add(
                        error_finding(
                            "ir.nc_pin_unknown",
                            f"{part.ref} 标了 NC 的引脚 {pin} 在符号 {part.symbol} 里不存在",
                            detail={"ref": part.ref, "pin": pin},
                        )
                    )

    for net in design.nets:
        for ref, pin in net.nodes:
            pins, pads = pins_of.get(ref), pads_of.get(ref)
            if pins is not None and pin not in pins:
                symbol_name = _symbol_of(design, ref)
                add(
                    error_finding(
                        "ir.pin_missing",
                        f"网络 {net.name} 里的 {ref}.{pin}：符号 {symbol_name} 没有这个引脚",
                        detail={"net": net.name, "ref": ref, "pin": pin},
                    )
                )
            elif pads is not None and pin not in pads:
                footprint = _footprint_of(design, ref)
                add(
                    error_finding(
                        "ir.pad_missing",
                        f"网络 {net.name} 里的 {ref}.{pin}：封装 {footprint} 没有这个焊盘",
                        detail={"net": net.name, "ref": ref, "pin": pin},
                    )
                )
    if spec is not None:
        result.findings.extend(check_spec_mapping(design, spec))
    return result


def _symbol_of(design: Design, ref: str) -> str:
    return next(p.symbol for p in design.parts if p.ref == ref)


def _footprint_of(design: Design, ref: str) -> str:
    return next(p.footprint for p in design.parts if p.ref == ref)


# ---------------------------------------------------------------- spec 和 design 的对应


def signal_names(signal: str) -> tuple[str, list[str]]:
    """ "V(OUT,FB)" → ("V", ["OUT", "FB"])；"I(Q1.2)" → ("I", ["Q1.2"])。"""
    m = _SIGNAL.match(signal)
    if m is None:
        raise ValueError(f"不是合法的信号写法：{signal!r}")
    return signal[0], signal[2:-1].split(",")


def check_spec_mapping(design: Design, spec: Spec) -> list[Finding]:
    nets = {n.name for n in design.nets}
    refs = {p.ref for p in design.parts}
    wired = {(r, p) for n in design.nets for r, p in n.nodes}
    interfaces = {i.name for i in spec.interfaces}
    findings: list[Finding] = []

    for iface in spec.interfaces:
        if iface.name not in nets:
            findings.append(
                error_finding(
                    "ir.spec_interface_net_missing",
                    f"spec 的接口 {iface.name} 在 design 里没有同名的网络（规则：接口名 = 网络名）",
                    detail={"interface": iface.name, "nets": sorted(nets)},
                )
            )

    def unknown(where: str, name: str, detail: dict[str, Any]) -> None:
        findings.append(
            error_finding(
                "ir.spec_signal_unknown",
                f"spec 的 {where} 提到的 {name} 在 design 里既不是网络也不是接口",
                detail=detail | {"name": name},
            )
        )

    for acc in spec.acceptance:
        load = acc.testbench.stimulus.load
        if load is not None and load.between is not None:
            for net in load.between:
                if net not in nets:
                    unknown(f"{acc.id} 的负载 between", net, {"acceptance": acc.id})
        for label, signal in (
            ("signal", acc.measure.signal),
            ("ref_signal", acc.measure.ref_signal),
        ):
            if signal is None:
                continue
            kind, names = signal_names(signal)
            for name in names:
                if kind == "V" and name not in nets and name not in interfaces:
                    unknown(f"{acc.id} 的 measure.{label}", name, {"acceptance": acc.id})
                if kind == "I" and not _current_target_exists(name, refs, wired, interfaces):
                    unknown(f"{acc.id} 的 measure.{label}", name, {"acceptance": acc.id})
    return findings


def _current_target_exists(
    name: str, refs: set[str], wired: set[tuple[str, str]], interfaces: set[str]
) -> bool:
    """I(…) 里的名字：接口名、两脚元件的位号、或 位号.引脚（引脚要接了网络）。"""
    if name in interfaces:
        return True
    ref, _, pin = name.partition(".")
    return ref in refs and (not pin or (ref, pin) in wired)
