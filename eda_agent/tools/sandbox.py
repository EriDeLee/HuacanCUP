"""外部工具的运行环境（ADR-0001）。

- 设置、缓存、临时文件全部指到 tools.sandbox_home，不写到项目以外。
- 只透传运行所需的系统变量；密钥等其他环境变量一律不传给外部工具。
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

from eda_agent.config import REPO_ROOT, ToolsConfig

_WINDOWS_PASSTHROUGH = (
    "SystemRoot",
    "SystemDrive",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_IDENTIFIER",
    "OS",
)
# DISPLAY / XAUTHORITY：KiCad 的 3D 渲染在没有屏幕的服务器上要靠 xvfb 提供一个显示。
_POSIX_PASSTHROUGH = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "DISPLAY", "XAUTHORITY")


def sandbox_env(tools: ToolsConfig, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    home = tools.sandbox_home
    temp = home / "Temp"
    kicad_dirs = {
        "KICAD_CONFIG_HOME": home / "kicad" / "config",
        "KICAD_DOCUMENTS_HOME": home / "kicad" / "documents",
        "KICAD_CACHE_HOME": home / "kicad" / "cache",
    }
    names = _WINDOWS_PASSTHROUGH if sys.platform == "win32" else _POSIX_PASSTHROUGH
    env = {name: os.environ[name] for name in names if name in os.environ}

    made: list[Path] = [home, temp, home / "pycache", *kicad_dirs.values()]
    if sys.platform == "win32":
        roaming = home / "AppData" / "Roaming"
        local = home / "AppData" / "Local"
        made += [roaming, local]
        system_root = env.get("SystemRoot", r"C:\Windows")
        env.update(
            APPDATA=str(roaming),
            LOCALAPPDATA=str(local),
            USERPROFILE=str(home),
            PATH=os.pathsep.join(
                [
                    str(tools.kicad_cli.parent),
                    rf"{system_root}\System32",
                    system_root,
                    rf"{system_root}\System32\WindowsPowerShell\v1.0",
                ]
            ),
        )
    else:
        xdg = {
            "XDG_CONFIG_HOME": home / ".config",
            "XDG_CACHE_HOME": home / ".cache",
            "XDG_DATA_HOME": home / ".local" / "share",
        }
        made += list(xdg.values())
        env.update({name: str(path) for name, path in xdg.items()})

    share = tools.kicad_share
    env.update(
        HOME=str(home),
        TEMP=str(temp),
        TMP=str(temp),
        # opencode 等外壳会设 TMPDIR，KiCad 用的界面库优先读它（ADR-0001 污染检查）。
        TMPDIR=str(temp),
        TEMPDIR=str(temp),
        KICAD_CLI=str(tools.kicad_cli),
        KICAD10_SYMBOL_DIR=str(share / "symbols"),
        KICAD10_FOOTPRINT_DIR=str(share / "footprints"),
        KICAD10_3DMODEL_DIR=str(share / "3dmodels"),
        KICAD10_TEMPLATE_DIR=str(share / "template"),
        PYTHONNOUSERSITE="1",
        PYTHONUTF8="1",
        PYTHONPYCACHEPREFIX=str(home / "pycache"),
        # 子进程里的 python -m eda_agent.… 需要找到本仓库的包。
        PYTHONPATH=str(REPO_ROOT),
        **{name: str(path) for name, path in kicad_dirs.items()},
    )
    for path in made:
        path.mkdir(parents=True, exist_ok=True)
    _ensure_global_lib_tables(tools, kicad_dirs["KICAD_CONFIG_HOME"])
    if extra:
        env.update(extra)
    return env


KICAD_SERIES = "10.0"


def _ensure_global_lib_tables(tools: ToolsConfig, config_home: Path) -> None:
    """沙箱里的 KiCad 配置是全新的。

    照 KiCad 首次启动的默认做法，复制它自带的全局符号库表和封装库表。
    """
    target = config_home / KICAD_SERIES
    target.mkdir(parents=True, exist_ok=True)
    for name in ("sym-lib-table", "fp-lib-table"):
        dst = target / name
        src = tools.kicad_share / "template" / name
        if not dst.exists() and src.exists():
            dst.write_bytes(src.read_bytes())
