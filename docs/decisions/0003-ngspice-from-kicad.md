# ADR-0003 仿真用 KiCad 自带的 ngspice，自写对接代码

- 状态：已采纳（2026-10-31，用户决定）
- 影响：PLAN §2「仿真」、§7 G2；M0 不再单独安装 ngspice，也不用 PySpice

## 决定

- 引擎：KiCad 10.0.8 自带的 `bin/ngspice.dll`（ngspice-46，KLU 求解器）。
- 对接：`eda_agent/tools/ngspice_worker.py` 用 ctypes 调用 `ngSpice_Init`、`ngSpice_Circ`、`ngSpice_Command`、
  `ngGet_Vec_Info` 等函数，在单独的子进程里运行。由 `eda_agent/tools/ngspice.py` 经统一的 `run_tool` 启动，
  所以超时、日志、trace 和其他外部工具一致；引擎崩溃或卡死不会拖垮主程序。
- 不用 PySpice（PyPI 最后一版 1.5，2021-05）。InSpice 会随 SKiDL 3.0 一起装上，但我们不调用它。

## 验证（2026-10-31）

`tests/tools/test_toolchain.py::test_ngspice_led_current_matches_hand_calculation`：
5 V 经 330 Ω 接 `models/led_red_generic.lib` 的红色 LED，工作点电流 9.494 mA；
手算 5 V = 332 Ω × I + 2·Vt·ln(I/IS) 得 9.49 mA，一致。

## 留到 M2

- 共享库启动时提示找不到 `spinit`。XSPICE 代码模型（`lib/ngspice/*.cm`）要在加载电路前用 `codemodel` 命令手动加载。
- 没有设兼容模式。PLAN 要求厂商模型用 `set ngbehavior=ltpsa`。
