# ADR-0013 M1：完整的 IR、JSON Patch、运行目录、自写原理图引擎接进主流程

- 状态：M1 完成标准已达到（2026-11-01）：10 个手写样例都生成了 KiCad 自己检查通过的工程。
  用户 2026-10-31 选了「开始 M1」和「M1 原理图用自写引擎」；当时用户外出，细节由我按 docs/review-queue.md 里的规则先做，等用户确认。
- 影响：PLAN §2「原理图生成」、§5.2、§12 M1、§14；`eda_agent/ir/`、`eda_agent/emit/`、`schemas/`

## 用户的决定

- M1 按 PLAN §12 做：IR（pydantic + JSON Schema）、JSON Patch 应用器、IR → KiCad 发射器、运行目录与 trace；
  完成标准是 10 个手写 IR 样例全部生成可打开的工程。
- 原理图不用 SKiDL，用自写版面引擎（ADR-0011）的完整版：给元件加角色、支持多单元符号、按信号流向排、按功能块分页。
  SKiDL 发射器保留，只做对照。

## IR 补全（`eda_agent/ir/models.py`，导出 `schemas/design.schema.json`）

按 PLAN §5.2 补上：`mpn`、`lcsc`、`source`（inventory / catalog）、`spice`（model、lib、pin_map）、`ratings`、
`ratings_source`（doc_id、page）、`board.layers`（2 或 4）、`board.mount_holes`（如 `M3x4`）、`board.net_classes`
（track_mm、clearance_mm，可选 via_diameter_mm、via_drill_mm）、`nets[].class`。

新增两个 PLAN 里没有的字段，给原理图引擎用：

- `parts[].role`：`hub`（主芯片，自成一团）、`connector`（连接器，画在页边、引脚朝里）、`passive`（两脚元件，串在引脚链上）。
  不填就按引脚数推断：两个引脚的是 passive，其余是 hub。
  只定这三个值，因为引擎只需要知道「怎么画」；去耦电容、上拉电阻、串联电阻这些，引擎从连接关系就能看出来。
- `parts[].anchor`：不在任何引脚链上的元件（典型的是电源和地之间的去耦电容）画在哪个元件旁边。
  不填就贴在同一功能块里第一个和它共用网络的主芯片旁边。

新增的检查：功能块 id、网络名不能重复；元件的 `block`、`anchor` 必须存在；网络类必须在 `board.net_classes` 里定义
（`default` 总是有）；过孔钻孔小于外径；网络名不能含空格和斜杠（KiCad 用斜杠表示图页路径）。

JSON Schema 由模型导出（`python tasks.py schemas`），`tests/test_ir.py` 检查文件与模型一致，并用 jsonschema 验证所有样例。

**spec.json 的模型推迟到 M2。** 它的 `testbench` / `measure` 字段要和 M2 的测试台生成器一起定，现在定很可能要返工。

## JSON Patch 应用器（`eda_agent/ir/patch.py`）

- 自己按 RFC 6902（含 RFC 6901 的 JSON Pointer）实现，只用标准库，不引第三方 jsonpatch 包（ADR-0005：依赖越少越好）。
  测试用 RFC 6902 附录 A 的全部可表示示例（A.13 是同一条操作里重复的键，Python 的 dict 表示不出来）。
- 整个补丁要么全部生效，要么完全不生效。出错时报出是第几条操作、为什么。
- `apply_design_patch`：补丁 → pydantic 校验。错误分两类，都能原样回给智能体：
  补丁本身错（`kind: patch`，带 `op_index`）；补丁能用但结果不是合法设计（`kind: design`，带字段位置如 `parts.3.lcsc`）。
- 补丁作用在设计的标准 JSON 上：字段名用 JSON 里的写法（如 `class`），值为空的可选字段不出现。
  所以给空字段赋值要用 `add`；用 `replace` 会报「路径不存在」，错误信息里写明了该用 add。
- JSON 的相等比较里 `true` 和 `1` 不相等（Python 里 `True == 1`），`test` 操作按 JSON 的规则比。

## 运行目录（`eda_agent/orchestrator/rundir.py`）

- 按 PLAN §5.4 建目录：`request.md`、`iterations/NN/`（design.json、gates/、notes.md）、`sim/`、`kicad/`、`final/`、
  `trace.jsonl`、`state.json`。同一秒建两次会得到两个不同的目录。
- 设计只能通过 `snapshot`（存一版）或 `apply_patch`（补丁成功才存新版）改动，每次改动都留快照。
  补丁不论成败都记进事件流，失败时把错误原样记下。
- `state.json`：当前状态、最新快照编号、每条回退边已用次数、费用累计；先写临时文件再改名，进程中途被打断也不会写坏。
  `RunDir.open` 用来接着跑（M3 的断点续跑用它）。
- 事件流新增三类：`snapshot`、`patch`、`gate`（`schemas/trace.schema.json`），测试把每条事件都对 schema 校验一遍。
- 这里只负责存和记；「通过不通过」只由 `eda_agent/gates/` 的代码给出（AGENTS.md 原则 1）。

## IR → KiCad 发射器（`eda_agent/emit/kicad_project.py`）

一次调用出完整工程：原理图（自写引擎，见下）→ kicad-cli 导出网表 → `kicad_side/board_build.py` 建板 →
把 `board.net_classes` 写进 `.kicad_pro`。网表里的网络名不一定和 IR 一样（多页时本页网络带页名前缀），
所以网络类按引脚找到网表里对应的网络再写，写成精确的名字。冒烟（`python tasks.py smoke`）默认走这条路；
`--schematic skidl` 换回 SKiDL 做对照。

### 原理图引擎的完整版（在 ADR-0011 样张的基础上）

- **按功能块分页**：两个以上功能块时，每块一页；根页是框图，每块一个图纸框，框上的引脚是和别的块共用的信号，
  引脚之间用短线加同名标签连起来，框下写这一块做什么；框按信号流向从左往右排。
  子页里跨页的信号画层次标签，箭头方向看驱动它的引脚在哪页：在本页 = output，在别页 = input，都有 = bidirectional，都没有 = passive。
  电源和地各页都只画电源符号，不占图纸框的引脚。板外送电的网络只在第一张用到它的页上放 PWR_FLAG。
- **多单元符号**：每个单元单独画（LM358 的 A、B 两个运放和电源单元 C），全部单元都放上，免得 ERC 报「有单元没放」。
- **派生符号**（`extends`，KiCad 库里很常见，如 AMS1117-3.3 派生自 AP1117-15）：图形和引脚取最上面的父符号，
  属性逐层合并（和 KiCad 自己一样：父符号独有的字段保留，同名的用子符号的）。可以多层派生（MAX485E → MAX481E → LTC2850xS8）。
- **按信号流向排**：页内每个主芯片（的每个单元）连同它的引脚链是一团；从左边的板边连接器（没有就从任一连接器）出发，
  沿共用的信号网络一列列往右排，右边的板边连接器放最后一列。
- **不在引脚链上的元件**：贴在共用网络的主芯片旁边（正下方排一行）。优先级：经电源输入脚共用 > 经别的脚共用 > 只共用地。
  所以去耦电容会贴到多单元芯片的电源单元旁边。`anchor` 可以指定。
- **只有两脚元件的页**（如「电阻 + 发光二极管」指示灯页）：从通到别页的信号、或从电源起头，把两脚元件串成一条链画，
  不再是一个个孤立的元件。
- **电源符号要有凭据**：地按名字认（GND、AGND、VSS……）；电源要么是板外送电（external_supply）、要么有电源输出脚驱动它、
  要么名字像电源（VCC、+5V……）且有电源输入脚接在上面。只看名字时，基准信号 VREF 被画成了电源符号，ERC 报错（实测）。
- **连接器**：板边连接器、或符号库名以 Connector 开头的元件，自动当连接器（引脚朝向电路）。
- 主芯片的位号和数值照库里的位置和对齐写。

### 建板（`kicad_side/board_build.py`）

- 板边连接器贴着指定的一边竖排；其余元件按 IR 顺序一行行排，放满一行换下一行，行内和行间的空隙都均匀分开。
  原来只排一行，十几个元件就放不下。
- `board.mount_holes`（如 `M3x4`）：四角（或左上、右下两角）放安装孔，标成「只在板上」，DRC 的原理图一致性检查不会把它当成多出来的元件；
  四角留出空地，元件不往那里放。
- 层数按 `board.layers` 设置。

### 顺带修好的问题

- **kicad-cli 的相对路径**：多页原理图用相对路径时 kicad-cli 报「加载原理图失败」（它在别的工作目录里找子页）。
  现在 `KicadCli` 统一先把路径变成绝对路径（有测试）。
- **fastroute 的线宽下限**：fastroute 默认会在焊盘附近把线收窄到 0.15 mm，低于板子规定的 0.2 mm，
  多页样例 attiny85_blink 的 DRC 报了 10 个线宽错误。现在从 `.kicad_pro` 读出板子的最小线宽，
  用 `--router.min_trace_width_um` 告诉它（参数来自 `fastroute --help`），之后 DRC 0 错误。LED 冒烟不受影响。

### 10 个手写样例（M1 完成标准）

`examples/` 下 10 个 IR，`eval/m1_examples.py` 对每个：发射工程，再让 KiCad 检查 ERC、网表与 IR 逐网络一致、
每页版面质量、符号都在图纸内、PCB 与原理图一致（还没布线）。同样的检查在 `tests/tools/test_examples.py` 里。
最后一次运行 `runs/m1-examples-20261101-091722`，全部通过：

| 样例 | 功能块（页） | 元件 | 覆盖的东西 | ERC 错误 / 警告 |
|---|---|---|---|---|
| led | 1 | 3 | 冒烟用的最小电路 | 0 / 0 |
| attiny85_blink | 4 页 | 7 | 多页、层次标签、ISP 口 | 0 / 0 |
| ldo_3v3 | 2 页 | 7 | 派生符号（AMS1117）、网络类、四角安装孔 | 0 / 0 |
| ne555_astable | 1 | 9 | 一个脚接多个元件的定时网络 | 0 / 0 |
| lm358_amp | 1 | 8（画成 10 个单元） | 多单元运放、闲置单元接成跟随器、anchor | 0 / 0 |
| nmos_switch | 2 页 | 7 | MOSFET、续流二极管、接线端子、网络类 | 0 / 0 |
| usb_c_5v | 3 页 | 7 | 叠放的隐藏引脚（VBUS、GND）、只有两脚元件的页 | 0 / 0 |
| i2c_temp_sensor | 1 | 6 | 上拉、地址脚 | 0 / 0 |
| rs485_transceiver | 1 | 7 | 三层派生符号、4 层板、两边板边连接器 | 0 / 1 |
| overcurrent_cutoff | 3 页 | 13（画成 15 个单元） | 多单元比较器、迟滞、网络类、安装孔 | 0 / 0 |

- 全部页面版面检查通过，导线交叉 0 处；PCB 与原理图一致性 0 个错误；未布线板上除「没连上」外 0 个 DRC 错误。
- 做的过程中修过的：lm358_amp、overcurrent_cutoff 原来各有 3 个「符号与库不一致」警告（派生符号展平时丢了父符号的 ki_locked）；
  usb_c_5v 原来用 16 针 USB-C 座，它的定位孔离焊盘 0.19 mm，小于板子默认的 0.25 mm 孔间距，换成 6 针「仅供电」座后 DRC 0 错误。
- rs485_transceiver 剩 1 个警告：MAX485E 是三层派生符号，展平后 KiCad 仍认为和库里的不完全一样，原因没查明。只是警告，不影响 ERC 判定。
- overcurrent_cutoff 只是画图的样例，电路本身没有仿真验证（M2 才有仿真关），它的说明里写了。
- 冒烟：LED（`smoke-led-20261101-085804`）ERC 0 错误 0 警告（SKiDL 时是 5 个警告），两个布线器 DRC 都 0 错误。
- 比 M1 标准多走一步：10 个样例全部走完冒烟的整条链路（原理图、ERC、网表一致、建板、fastroute 和 KRT 各布一次、DRC、
  导出制造文件），**20 次布线全部 DRC 0 错误、0 条没连上**（`.tools/audit/smoke_all.py`，运行目录 `runs/m1-route-all/`）。
  DRC 只有警告：ldo_3v3、overcurrent_cutoff 各 2 个。
- Linux 上也通过：GitHub Actions 运行 41262079790（提交 4303a50）的工具测试含这 10 个样例，全部通过。

### 已知限制

- 隐藏的引脚不画（叠放在可见引脚上的那种会自动连上，样例 usb_c_5v 就是）；不叠放的隐藏电源脚目前画不出来。
- 两个主芯片之间的信号只用同名标签，不画直连线；跨页只到「每块一页」，块太大时一页可能放不下（会报错，要把块拆小）。
- 建板只是按顺序排开，不考虑连线长短；布线质量由布线器决定。
- 原理图好不好看还是要人看；机器只检查重叠、压字、穿线、出图纸和连接是否正确。
