# ADR-0013 M1：完整的 IR、JSON Patch、运行目录、自写原理图引擎接进主流程

- 状态：进行中（2026-11-01）。用户 2026-10-31 选了「开始 M1」和「M1 原理图用自写引擎」，
  当时用户外出，细节由我按 docs/review-queue.md 里的规则先做，等用户确认。
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
