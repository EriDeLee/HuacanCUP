# ADR-0007 网表转电路板用自写脚本，不用 kinet2pcb

- 状态：已采纳（2026-10-31，用户决定）
- 已被 ADR-0018 扩展的部分（2026-11-08）：下面「决定」第 4 条的摆放。S7 布线之前加独立的自动摆放步骤（按功能块分区、按连接关系靠拢、
  程序打分迭代优化）；其余内容不变
- 影响：PLAN §2「PCB 生成」、§16 第 4 项

## 现象（2026-10-31 实测）

1. kinet2pcb 1.1.4 在 Windows 上一 import 就抛异常：它只在 `C:\Program Files\KiCad\` 下找 9.0/8.0/7.0/6.0/5.0，
   找不到就 `raise Exception`，即使 `pcbnew` 本来就能直接 import（我们的 KiCad 10 解压在项目里）。Linux 上没有这个检查。
2. 它读网表时不读符号 UUID，生成的封装没有「原理图 ↔ 电路板」对应路径。
   对照实验（`board_build.py --omit-paths`）：DRC 的原理图一致性检查仍然 0 错误——KiCad 会退到按位号匹配。
   所以第 2 点不是阻碍，第 1 点才是。

## 决定

用 `eda_agent/kicad_side/board_build.py`（在 KiCad 自带 Python 里运行，只依赖标准库和 pcbnew）：

1. 网表来自 `kicad-cli sch export netlist`（kicadsexpr 格式），所以和原理图里的 UUID、网络名完全一致；
2. 逐个加载封装，写入位号、值、库 ID、UUID 路径、所在页，并把符号字段（Datasheet、Description 等）抄到封装上
   （不抄的话一致性检查会对每个字段报警告）；
3. 按网表连网络（包括只有一个引脚的网络），在 Edge.Cuts 上画矩形板框；
4. 通用摆放规则：板边连接器靠 IR 里指定的一侧，其余元件按 IR 顺序从左到右等间距排成一排、竖直居中；
   排不下或放不下就直接报错，不会把元件放到板外。

不再需要 kinet2pcb、hierplace、simp_sexp。SKiDL 3.0 仍会把 kinet2pcb 作为依赖装进 `venv\`，但不调用。

## 后果

- 多维护约 150 行代码。
- M0 的摆放只适合单排的小板；PLAN S7 的摆放（安装孔、按功能块分区）在后续里程碑扩展。
