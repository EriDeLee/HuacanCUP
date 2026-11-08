"""测试用的小符号库和封装库：G1 的测试不需要装 KiCad，自己写几个符号和封装就行。

符号库 Device、T、Connector_T 里的符号（引脚编号：名字，电气类型）：
    Device:R、Device:C   1、2：passive（名字和 KiCad 自带的一样，规则按这个名字认电阻、电容）
    T:IC8             1 VCC power_in；2 GND power_in；3 IN input；4 OUT output；5 IO bidirectional；
                      6 OC open_collector；7 V- power_in；8 NC no_connect
    T:REG             1 VI power_in；2 VO power_out；3 GND power_in
    T:OPA             1 OUT output；2 IN- input；3 IN+ input；4 V- power_in；5 V+ power_in
    Connector_T:CONN2 1、2：passive
封装库 FP：R2（焊盘 1、2）、IC8（1–8）、IC5（1–5）、REG（1、2、3，另有一个同号为 2 的散热片焊盘）、
CONN2（1、2）、以及几个只用来测封装名规则的：R_0402_1005Metric、R_0603_1608Metric、
R_0805_2012Metric、R_1206_3216Metric、QFN-8-1EP_3x3mm、SOIC-8、TQFP-8、BGA-8。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from eda_agent.emit.kicad_sym import SymbolDef, load_symbol
from eda_agent.ir.models import Design
from eda_agent.tools.ngspice import SimResult
from eda_agent.tools.runner import ToolResult

SYMBOLS: dict[str, dict[str, list[tuple[str, str, str]]]] = {
    "Device": {
        "R": [("1", "~", "passive"), ("2", "~", "passive")],
        "C": [("1", "~", "passive"), ("2", "~", "passive")],
    },
    "T": {
        "IC8": [
            ("1", "VCC", "power_in"),
            ("2", "GND", "power_in"),
            ("3", "IN", "input"),
            ("4", "OUT", "output"),
            ("5", "IO", "bidirectional"),
            ("6", "OC", "open_collector"),
            ("7", "V-", "power_in"),
            ("8", "NC", "no_connect"),
        ],
        "REG": [("1", "VI", "power_in"), ("2", "VO", "power_out"), ("3", "GND", "power_in")],
        "OPA": [
            ("1", "OUT", "output"),
            ("2", "IN-", "input"),
            ("3", "IN+", "input"),
            ("4", "V-", "power_in"),
            ("5", "V+", "power_in"),
        ],
    },
    "Connector_T": {"CONN2": [("1", "1", "passive"), ("2", "2", "passive")]},
}

FOOTPRINTS: dict[str, list[str]] = {
    "R2": ["1", "2"],
    "IC8": [str(n) for n in range(1, 9)],
    "IC5": [str(n) for n in range(1, 6)],
    "REG": ["1", "2", "3", "2"],
    "CONN2": ["1", "2"],
    "R_0402_1005Metric": ["1", "2"],
    "R_0603_1608Metric": ["1", "2"],
    "R_0805_2012Metric": ["1", "2"],
    "R_1206_3216Metric": ["1", "2"],
    "C_0805_2012Metric": ["1", "2"],
    "QFN-8-1EP_3x3mm": [str(n) for n in range(1, 9)],
    "SOIC-8": [str(n) for n in range(1, 9)],
    "TQFP-8": [str(n) for n in range(1, 9)],
    "BGA-8": [str(n) for n in range(1, 9)],
}


def _symbol_text(name: str, pins: list[tuple[str, str, str]]) -> str:
    body = []
    for i, (number, pin_name, etype) in enumerate(pins):
        body.append(
            f"(pin {etype} line (at -5.08 {-2.54 * i} 0) (length 2.54) "
            f'(name "{pin_name}" (effects (font (size 1.27 1.27)))) '
            f'(number "{number}" (effects (font (size 1.27 1.27)))))'
        )
    return (
        f'(symbol "{name}" (property "Reference" "U" (at 0 0 0))'
        f' (property "Value" "{name}" (at 0 0 0))'
        f' (symbol "{name}_1_1" (rectangle (start -2.54 1.27) (end 2.54 -20)) {" ".join(body)}))'
    )


def write_libs(root: Path) -> tuple[tuple[Path, ...], Path]:
    """在 root 下写好符号库和封装库，返回 (符号库目录们, 封装库目录)。"""
    symbols = root / "symbols"
    symbols.mkdir(parents=True, exist_ok=True)
    for lib, table in SYMBOLS.items():
        text = "(kicad_symbol_lib (version 20260101) (generator test) " + " ".join(
            _symbol_text(name, pins) for name, pins in table.items()
        )
        (symbols / f"{lib}.kicad_sym").write_text(text + ")\n", encoding="utf-8")
    footprints = root / "footprints"
    pretty = footprints / "FP.pretty"
    pretty.mkdir(parents=True, exist_ok=True)
    for name, pads in FOOTPRINTS.items():
        pad_text = " ".join(
            f'(pad "{n}" smd rect (at {i} 0) (size 1 1) (layers "F.Cu"))'
            for i, n in enumerate(pads)
        )
        (pretty / f"{name}.kicad_mod").write_text(
            f'(footprint "{name}" (layer "F.Cu") {pad_text})\n', encoding="utf-8"
        )
    return (symbols,), footprints


def part(ref: str, symbol: str, footprint: str, **extra: Any) -> dict[str, Any]:
    return {"ref": ref, "symbol": symbol, "footprint": f"FP:{footprint}", "value": symbol} | extra


def design(parts: list[dict[str, Any]], nets: list[dict[str, Any]], **board: Any) -> Design:
    return Design.model_validate(
        {"parts": parts, "nets": nets, "board": {"size_mm": [20, 20]} | board}
    )


def net(name: str, *nodes: str, **extra: Any) -> dict[str, Any]:
    """net("VCC", "U1.1", "C1.1")"""
    return {"name": name, "nodes": [n.split(".", 1) for n in nodes]} | extra


def symbols_for(d: Design, lib_dirs: tuple[Path, ...]) -> dict[str, SymbolDef]:
    return {p.ref: load_symbol(p.symbol, lib_dirs) for p in d.parts}


def meas_output(**values: float | None) -> list[str]:
    """ngspice 打印 .meas 结果的样子；None 是测不出来的那种。"""
    lines = ["stdout Measurements for Transient Analysis"]
    for name, value in values.items():
        if value is None:
            lines.append(f"stderr .meas tran {name} when v(x)=1 rise=1 failed!")
        else:
            lines.append(f"stdout {name:<20}=  {value:.6e}")
    return lines


def fake_sim(
    vectors: dict[str, list[float]] | None = None,
    *,
    complex_vectors: dict[str, tuple[list[float], list[float]]] | None = None,
    output: list[str] | None = None,
    ok: bool = True,
    errors: list[str] | None = None,
    timed_out: bool = False,
    exit_code: int | None = 0,
    **meas: float | None,
) -> SimResult:
    """不用 ngspice 的假结果。meas 里的键值会变成 .meas 的输出行。"""
    tool = ToolResult(
        tool="ngspice",
        argv=("fake",),
        cwd=Path("."),
        exit_code=exit_code,
        timed_out=timed_out,
        termination="none",
        duration_s=0.0,
        stdout="",
        stderr="",
        stdout_path=None,
        stderr_path=None,
    )
    lines = list(output or []) + (meas_output(**meas) if meas else [])
    return SimResult(
        ok=ok,
        vectors=vectors or {},
        errors=errors or [],
        output=lines,
        result_json=Path("fake.json"),
        tool=tool,
        complex_vectors=complex_vectors or {},
    )
