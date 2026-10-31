# ADR-0009 SKiDL 自动画原理图：30 个以上元件的实测（M0 待验证项③）

- 状态：实测完成；结论是目前不能直接用于中大型电路，后续做法等用户定（2026-10-31）
- 影响：PLAN §2「原理图生成」、§15 第 1 条风险、§16

## 实测（SKiDL 3.0.0 + schematizer 0.2.0，KiCad 10.0.8）

素材：KiCad 示例 pic_programmer（63 个元件、2 页）。示例原理图 → kicad-cli 网表 → design.json → `eda_agent/emit/skidl_emit.py`。
运行目录 `runs/m0-pic-20261031-161138/`。「在图纸内」是 `eda_agent/gates/schematic_layout.py` 的检查结果。

| 输入 | 选项 | 生成器退出 | 符号在图纸内 |
|---|---|---|---|
| 全部 63 个元件，单页（含 6 个完全不连线的安装孔） | 默认 | 20.6 秒后崩溃：`AttributeError: 'NoneType' object has no attribute 'tx'` | — |
| 同上 | `auto_stub` | 8.6 秒后同样崩溃 | — |
| 去掉 6 个不连线的元件，57 个，单页 | 默认 | 900 秒超时（统一超时先发 CTRL_BREAK，进程正常结束） | — |
| 只取 pic_sockets 页的 7 个元件 | 默认 | 14 秒后 `RoutingFailure` | — |
| 同上 | `auto_stub` | 26.8 秒，报成功 | 20/20（A4），只有 1 根连线，其余是标签 |
| 只取根页的 50 个元件 | `auto_stub` | 31.9 秒，报成功 | **11/103（A0）**：其余符号被摆到图纸上下 2.7 m、3.5 m 以外，PDF 上几乎是空的 |
| 全部 63 个元件 | labels 模式（每个引脚只挂网络标签） | 6.3 秒成功 | 130/130（A2） |
| LED，3 个元件 | 默认 | 成功 | 8/8（A4） |

更正：最初把「根页 50 个元件 auto_stub 成功」当成了能用的结果，是只看了退出码。用户打开 PDF 发现是空的，
查出符号都在图纸外。之后加了 `schematic_layout` 检查（冒烟也会跑），生成器报成功但图不对时判为失败。

## 结论

1. 默认模式（摆放 + 连线）只适合很小的电路。
2. `auto_stub` 能让 7 个元件的小块画在图纸内，但 50 个元件的单页会把大部分符号甩出图纸。
3. 只有 labels 模式能把 63 个元件全部放进图纸，代价是图上基本没有连线。
4. 完全不连线的元件（安装孔这类）会让 schematizer 崩溃，属于上游 bug。
5. schematizer 的文档写明摆放从随机位置开始，同一网表每次画出来都不一样，没法固定；这和我们「可复现」的要求冲突。
6. 重画后 ERC 的错误主要来自网表转 IR 时丢掉的信息（不接的引脚没有 NC 标记、电源网络没有标 external_supply），不是画图本身的问题。

## 分块分页实验（`eval/m0_schematic_blocks.py 12`，运行目录 `runs/m0-blocks12-20261031-182659/`）

PLAN §15 对「原理图可读性差」的对策是按功能块分页。把 57 个有连线的元件按连接关系切成 11 块（每块不超过 12 个），每块一张子页，打开 `auto_stub`。
70 秒画完，12 张图纸的符号全部在图纸内，但读图不及格：

- 切块规则（忽略电源网络，按连接关系广度优先，攒满 12 个换块）切出了只有 1–2 个元件的碎块（B01 只有 C1，B07 只有 D12，B08 只有 D8），这些页只有一两个小符号放在空白 A4 中间。
- 最大的一页（B06，35 个符号，A3）：符号成簇散落，簇与簇之间隔着大片空白，簇内几乎没有连线，靠 `Net-(D2-A)` 这类网络名标签连接。
- 总页是 11 个空框纵向排列，框上没有端口，看不出块与块怎么连；子页名与相邻框的文字重叠。
- ERC 仍报 85 个错误、66 个警告。
- 用户要求「只能比 original 好看」，这个结果达不到。切块规则是随手写的，换成按功能切会少些空页（推测），但页内没有连线这一点不会变。

## 为什么别人的图好看（2026-10-31 查证）

网表只写「哪个引脚连哪个引脚」，没有画图意图：坐标、信号流向、分页、分组、符号摆法
（elektroda 上有一句话把这个说得很清楚，来源见下）。好看的工具都从别处拿到这份意图，来源有四种：

1. **作者声明。** schemagen 的 `Bus` 接口要作者给出分页（`set_sheets`）、电源轨（`rail`）、总览图的节点与箭头（`overview`）；
   Schematex 有「functional structure」；tscircuit 要给 `schX/schY`、`pinLabels`。
2. **按电路块类型手写的摆法。** schemagen 的 `schematic.py`（121 KB，我读了文件头说明和函数清单，没有通读）有
   `place_half_bridge`、`place_rail_chain`、`place_regulators`、`place_ic_block`、`place_comms`、`place_connector_block`、`place_satellite`、`place_flag`；
   块的归属由位号、网络名、元件值推断，样例是电机驱动板。符号也是它自己画的（`sym_passive_v`、`sym_ic`……），不用 KiCad 标准库，几何完全可控。
   文件头写明的规则：块内短连接画真导线，跨块一律用标签，电源用轨符号、地用地符号，所有引脚坐标落在 1.27 mm 网格，
   每个引脚必须有连线或 NC 标记，否则拒绝输出。
3. **看图修正。** EEschematic（arXiv 2510.17002，ISCAS 2026，针对模拟 IC）用 6 个模拟子结构做少样本摆放示例，
   再用「视觉思维链」看渲染图，迭代修摆放和连线。
4. **通用分层算法。** Weave（SPICE→LTspice）把网络分成地、电源、信号，信号元件交给 elkjs 的分层（Sugiyama）布局，得到从左到右的信号流和直角走线；
   反馈环、分压支路、悬挂的并联元件、电源拐角当作摆放模式处理；最后解析自己的输出，逐网络核对连通性。

schematizer（SKiDL 3.0 用的）只拿连接关系，靠力导向摆放猜位置，没有意图，所以我们的 63 个元件实测散成一团。

schemagen 不能直接拿来用：MIT 许可，但 2026-08-12 创建、只有一天的提交、0 星，样例偏电机驱动；
输出的 `.kicad_sch` 自带符号，封装名是 `LQFP48` 这种简写而不是 KiCad 库 ID，接不上我们的建板步骤。它的做法值得借鉴。

**我们的优势。** IR 已经有 `parts[].block`、`blocks[].purpose`、`nets[].external_supply`，相当于「作者声明」那一半；
缺的是每个元件的角色（主芯片、去耦电容、上拉、串联电阻、指示灯、连接器……）。智能体按功能块设计，本来就知道这些。

来源（都是作者自述或论文，我没有逐项复现，只读了 schemagen 的源码结构）：
`github.com/TheEnergyNerd/schemagen`、`arxiv.org/abs/2510.17002`、`github.com/senolgulgonul/weave`、
`docs.tscircuit.com/guides/tscircuit-essentials/automatic-schematic-layout`、
`schematex.js.org/docs/circuit`、`elektroda.com/qa,netlist-to-schematic-converter.html`、
SPAR（1993，academia.edu/25434139：以「功能识别」和「可追踪」两条可读性原则做划分、摆放、全局与局部布线）。
