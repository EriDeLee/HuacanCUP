"""子进程入口：用 ctypes 调 ngspice 共享库（KiCad 自带的 ngspice.dll）跑一个网表，结果写成 JSON。

只由 eda_agent.tools.ngspice 经 run_tool 启动：超时、日志、trace 都走统一封装，
引擎崩溃或卡死也不会拖垮主程序（ADR-0003）。用法：

    python -m eda_agent.tools.ngspice_worker --lib ngspice.dll --netlist x.cir --out x.json
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
from pathlib import Path
from typing import Any


class _VectorInfo(ctypes.Structure):
    _fields_ = [
        ("v_name", ctypes.c_char_p),
        ("v_type", ctypes.c_int),
        ("v_flags", ctypes.c_short),
        ("v_realdata", ctypes.POINTER(ctypes.c_double)),
        ("v_compdata", ctypes.c_void_p),
        ("v_length", ctypes.c_int),
    ]


_VF_REAL = 1
_SendChar = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p)
_SendStat = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p)
_ControlledExit = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.c_int, ctypes.c_bool, ctypes.c_bool, ctypes.c_int, ctypes.c_void_p
)


def _load(lib_path: Path) -> ctypes.CDLL:
    if sys.platform == "win32":
        # ngspice.dll 依赖同目录下 KiCad 自带的其他 DLL。
        os.add_dll_directory(str(lib_path.parent))
    lib = ctypes.CDLL(str(lib_path))
    lib.ngSpice_Init.argtypes = [
        _SendChar,
        _SendStat,
        _ControlledExit,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    lib.ngSpice_Init.restype = ctypes.c_int
    lib.ngSpice_Command.argtypes = [ctypes.c_char_p]
    lib.ngSpice_Command.restype = ctypes.c_int
    lib.ngSpice_Circ.argtypes = [ctypes.POINTER(ctypes.c_char_p)]
    lib.ngSpice_Circ.restype = ctypes.c_int
    lib.ngSpice_CurPlot.argtypes = []
    lib.ngSpice_CurPlot.restype = ctypes.c_char_p
    lib.ngSpice_AllVecs.argtypes = [ctypes.c_char_p]
    lib.ngSpice_AllVecs.restype = ctypes.POINTER(ctypes.c_char_p)
    lib.ngGet_Vec_Info.argtypes = [ctypes.c_char_p]
    lib.ngGet_Vec_Info.restype = ctypes.POINTER(_VectorInfo)
    return lib


def simulate(lib_path: Path, netlist: Path) -> dict[str, Any]:
    lib = _load(lib_path)
    output: list[str] = []
    exit_info: dict[str, Any] = {}

    def on_char(text: bytes | None, _ident: int, _user: int | None) -> int:
        output.append((text or b"").decode("utf-8", errors="replace"))
        return 0

    def on_stat(_text: bytes | None, _ident: int, _user: int | None) -> int:
        return 0

    def on_exit(status: int, immediate: bool, quit_: bool, _ident: int, _user: int | None) -> int:
        exit_info.update(status=status, immediate=bool(immediate), quit=bool(quit_))
        return 0

    # 回调对象要一直持有引用，否则会被回收，ngspice 回调时进程崩溃。
    callbacks = (_SendChar(on_char), _SendStat(on_stat), _ControlledExit(on_exit))
    lib.ngSpice_Init(*callbacks, None, None, None, None)
    lib.ngSpice_Command(b"version")

    lines = [line.encode("utf-8") for line in netlist.read_text(encoding="utf-8").splitlines()]
    circ = (ctypes.c_char_p * (len(lines) + 1))(*lines, None)
    rc_circ = lib.ngSpice_Circ(circ)
    rc_run = lib.ngSpice_Command(b"run") if rc_circ == 0 else None

    plot = (lib.ngSpice_CurPlot() or b"").decode("utf-8")
    vectors: dict[str, list[float]] = {}
    names = lib.ngSpice_AllVecs(plot.encode("utf-8"))
    i = 0
    while names and names[i]:
        name = names[i].decode("utf-8")
        i += 1
        info = lib.ngGet_Vec_Info(f"{plot}.{name}".encode())
        if not info:
            continue
        vec = info.contents
        if vec.v_flags & _VF_REAL and vec.v_realdata:
            vectors[name] = [vec.v_realdata[k] for k in range(vec.v_length)]

    errors = [line for line in output if line.lower().startswith("stderr error")]
    return {
        "ok": rc_circ == 0 and rc_run == 0 and not errors,
        "rc": {"circ": rc_circ, "run": rc_run},
        "errors": errors,
        "plot": plot,
        "vectors": vectors,
        "output": output,
        "exit": exit_info,
        "_keepalive": len(callbacks),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lib", type=Path, required=True)
    parser.add_argument("--netlist", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    result = simulate(args.lib, args.netlist)
    result.pop("_keepalive")
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
