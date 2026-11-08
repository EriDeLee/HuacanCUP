# SPICE 模型库

每个模型文件都要写明来源和许可。测试台（`eda_agent/sim/testbench.py`）把用到的模型文件复制到运行目录的
`branches/<k>/sim/models/`，网表里 `.include` 的是那份副本，并打开 `set ngbehavior=ltpsa`。

在设计里这样用（`parts[].spice`）：`model` 是文件里的 `.subckt` 或 `.model` 的名字，`lib` 是本目录下的路径，
`pin_map` 是「符号引脚编号 → 模型端子名」。子电路的端子名就是 `.subckt` 那一行里的名字；
二极管的端子叫 `A`、`K`，三极管 `C`、`B`、`E`，MOSFET `D`、`G`、`S`。

| 文件 | 内容 | 来源 | 许可 | 备注 |
|---|---|---|---|---|
| `led_red_generic.lib` | `LED_RED_GENERIC`（二极管卡） | 本仓库自写，参数按 Vf≈1.85 V @ 10 mA 手算得出，不是厂商模型 | 同本仓库 | 仅用于测试 |
| `lm358.lib` | `LM358`（单运放）、`LM358_DUAL`（封装里的两个） | National Semiconductor（现 TI）的 LM358 宏模型，取自 github.com/rene-dev/stmbl 的 `hw/spice/LM358.lib` | 原文件头：「可以复制、分发，不得转售」，原文保留在文件里 | 三处 `POLY(1)` 源改写成等价的 B 源（ngspice 的 POLY 要 XSPICE 代码模型 `spice2poly.cm`，Ubuntu 的 `libngspice0` 不带它），其余原文；`LM358_DUAL` 是本仓库加的 |
| `ua555.lib` | `UA555`（555 晶体管级模型）、`NE555`（按 KiCad 符号 `Timer:NE555P` 的引脚顺序包一层） | ngspice 项目 `examples/p-to-n-examples/555-timer-2.cir`，原文注明取自 Fairchild 1982 年数据手册第 9-3 页 | ngspice 源码是 BSD 3-clause | 5 V 下下阈值约 0.37 VCC、上阈值约 0.675 VCC，周期比理想公式短约 8 %（文件头有说明）。地用全局节点 0，`GND` 脚必须接在电路的地网络上 |
| `lm393.lib` | `LM393`（单比较器）、`LM393_DUAL`（封装里的两个） | github.com/semianalog/mod12003 的 `sim/LM393.sub`，文件头是 1990 年 PARTS 4.03 生成的 National Semiconductor / TI LM393 宏模型 | **文件里没有许可文字，没能确认**（review-queue D26） | 输出是开集，电路里要有上拉电阻 |
| `irlz44n.lib` | `IRLZ44N`（ngspice VDMOS 模型卡） | **本仓库自写，不是厂商模型**。参数来自 Infineon IRLZ44NPbF 数据手册：RDS(on) ≤ 25 mΩ @ 5 V/25 A、VGS(th) 1–2 V、gfs ≥ 21 S、Ciss 1700 pF；RG、CGD、Coss 是通用值（文件头逐项写明） | 同本仓库 | 没用厂商模型是因为没核实它的再分发条款。M4 起由智能体按数据手册拟合并过核对检查，取代这类手写模型 |

找不到模型的器件，G2 的结果是 `sim_error`（模型缺失），不会静默跳过。
