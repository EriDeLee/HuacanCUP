"""调用外部程序的唯一入口：统一超时、捕获 stdout/stderr、把完整命令行写进 trace。

超时后先发可被程序处理的中断信号（Windows: CTRL_BREAK，POSIX: SIGTERM），
宽限期内仍不退出才强制结束，trace 里记录用的是哪一种。
"""

from __future__ import annotations

import itertools
import json
import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eda_agent.trace import TraceWriter

GRACE_PERIOD_S = 10.0
_TAIL_CHARS = 2000
_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*]')
_log_counter = itertools.count(1)


@dataclass(frozen=True)
class ToolResult:
    tool: str
    argv: tuple[str, ...]
    cwd: Path
    exit_code: int | None
    timed_out: bool
    termination: str
    duration_s: float
    stdout: str
    stderr: str
    stdout_path: Path | None
    stderr_path: Path | None

    def check(self, ok_codes: Collection[int] = (0,)) -> ToolResult:
        if self.timed_out:
            raise ToolError(self, f"{self.tool} 超时（{self.duration_s:.0f} s）")
        if self.exit_code not in ok_codes:
            raise ToolError(self, f"{self.tool} 退出码 {self.exit_code}")
        return self

    def last_json(self) -> Any:
        """stdout 里最后一行 JSON。

        KiCad 的 Python 退出时还会打印 SWIG 提示，所以不能只看最后一行。
        """
        for line in reversed(self.stdout.splitlines()):
            if line.lstrip().startswith(("{", "[")):
                return json.loads(line)
        raise ToolError(self, f"{self.tool} 的输出里没有 JSON 结果")


class ToolError(RuntimeError):
    def __init__(self, result: ToolResult, message: str) -> None:
        detail = result.stderr.strip()[-800:] or result.stdout.strip()[-800:]
        super().__init__(
            f"{message}\n命令：{' '.join(result.argv)}\n"
            f"日志：{result.stdout_path} / {result.stderr_path}\n{detail}"
        )
        self.result = result


def run_tool(
    tool: str,
    argv: Sequence[str | Path],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout_s: float,
    log_dir: Path | None = None,
    trace: TraceWriter | None = None,
) -> ToolResult:
    args = tuple(str(a) for a in argv)
    cwd.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    if sys.platform == "win32":
        proc = subprocess.Popen(
            args,
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    else:
        proc = subprocess.Popen(
            args,
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    timed_out = False
    termination = "none"
    try:
        out_b, err_b = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        termination, out_b, err_b = _stop(proc)
    duration = time.monotonic() - start

    stdout = out_b.decode("utf-8", errors="replace")
    stderr = err_b.decode("utf-8", errors="replace")
    stdout_path = stderr_path = None
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        # 工具名里可能有 ":"（如 kicad-python:board_build.py），Windows 文件名里不能用。
        stem = f"{next(_log_counter):03d}_{_UNSAFE_FILENAME.sub('_', tool)}"
        stdout_path = log_dir / f"{stem}.stdout.txt"
        stderr_path = log_dir / f"{stem}.stderr.txt"
        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")

    result = ToolResult(
        tool=tool,
        argv=args,
        cwd=cwd,
        exit_code=None if timed_out and proc.returncode is None else proc.returncode,
        timed_out=timed_out,
        termination=termination,
        duration_s=duration,
        stdout=stdout,
        stderr=stderr,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )
    if trace is not None:
        trace.emit(
            "tool_call",
            tool=tool,
            argv=list(args),
            cwd=str(cwd),
            exit_code=result.exit_code,
            timed_out=timed_out,
            termination=termination,
            duration_s=round(duration, 3),
            stdout_path=str(stdout_path) if stdout_path else None,
            stderr_path=str(stderr_path) if stderr_path else None,
            stdout_tail=stdout[-_TAIL_CHARS:],
            stderr_tail=stderr[-_TAIL_CHARS:],
        )
    return result


def _stop(proc: subprocess.Popen[bytes]) -> tuple[str, bytes, bytes]:
    try:
        if sys.platform == "win32":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(proc.pid, signal.SIGTERM)
        out, err = proc.communicate(timeout=GRACE_PERIOD_S)
        return "graceful", out, err
    except (subprocess.TimeoutExpired, OSError):
        if sys.platform == "win32":
            # 连同它启动的子进程一起结束，否则会留下孤儿进程。
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
        out, err = proc.communicate()
        return "forced", out, err
