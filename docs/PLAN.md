# 基于AI智能体的电子电路全流程自动化设计系统 — 实现方案

> 本文件是整个仓库的设计依据，交给编码智能体实现。实现时遇到与本文冲突的情况，先改本文再改代码。
> 截止：2027-03-05 17:00（华灿奖·主题十五·方向一）。今天：2026-10-31。

---

## 0. 一句话

用户用一句话许愿（例："在电池和 ZVS 装置之间接一个过流保护器"），智能体团队把它变成**可制造的电路设计**：需求规格 → 方案 → 原理图 → 仿真验证 → PCB → BOM/采购申请 → 3D 渲染 → 文档；实物做出来后还能陪用户调试（bring-up）并出修订版。

**硬原则：所有"通过/不通过"的判定只能来自确定性程序（KiCad ERC/DRC、ngspice 仿真、自写规则检查）。模型只负责提方案、读报告、改设计，永远不能宣布"设计没问题"。**

---

## 1. 范围

### 1.1 输入
- 自然语言需求（中文/英文），可附图片/草图/已有原理图。
- 用户长期上下文：手头元器件库存、工具条件（是否能焊 0402/QFN、有无回流焊）、偏好的板厂、历史设计。

### 1.2 输出（每次运行 `runs/<run_id>/final/`）
| 产物 | 格式 | 生成方式 |
|---|---|---|
| 需求规格（冻结） | `spec.json` + `spec.md` | 需求智能体起草，用户确认后冻结 |
| 设计源 | `design.json`（本系统 IR） | 各智能体以补丁方式修改 |
| 可编辑 EDA 工程 | `.kicad_pro` / `.kicad_sch` / `.kicad_pcb` | SKiDL 生成原理图，kinet2pcb 生成 PCB，Freerouting 布线 |
| 制造文件 | Gerber + 钻孔 zip、坐标文件 | `kicad-cli pcb export gerbers` / `drill` / `pos` |
| BOM | `bom.csv`（含库存/采购来源、LCSC 编号、单价） | 确定性脚本 |
| 采购申请 | `purchase_request.json` + 可上传的购物车 CSV | 采购智能体起草，用户在飞书/网页审批 |
| 3D | `board.step`、渲染图 `renders/*.png`、网页预览 `board.glb`（若可导出） | `kicad-cli pcb export step` / `pcb render` |
| 仿真报告 | 波形图 + 每条验收标准的数值与结论 | ngspice + 自写测量脚本 |
| 文档 | `README.md`（原理说明、设计取舍、风险）、`bringup.md`（上电调试步骤与期望读数） | 文档智能体 |
| 全过程记录 | `trace.jsonl`（每次模型调用、工具调用、费用、判定） | 编排器自动写 |

### 1.3 不做
- 不自动下单付款。采购最多生成购物车文件 + 审批通过记录。
- 不承诺高速/射频/高密度板的布线质量（自动布线器能力边界），文档里如实标注。
- 不针对某一类电路做特化。ZVS 只是演示用例之一。

---

## 2. 技术选型（已定）

| 方面 | 选择 | 理由 |
|---|---|---|
| 编排 | **自写薄编排层（Python 3.11+）**，不用 LangGraph/CrewAI/OpenClaw | 流程是固定状态机 + 回退边，自写最可控、最易复现 |
| 模型 | **全部走 API**；`llm/` 做供应商抽象（Anthropic + OpenAI 兼容接口） | 用户决定 |
| 向量模型 | **跑在用户的云 GPU 上，只通过一个 API 端口调用**（数据手册与历史设计检索）；开发机没有可用的 GPU；具体模型 M4 用真实数据对比后再定 | 用户决定，ADR-0010 |
| 存储 | **只用文件**：JSON / JSONL / Markdown / `.npy`。不建数据库服务 | 用户不想碰数据库；可审计、可 git 追踪 |
| 电路 IR | 自定义 `design.json`（见 §5） | 便于确定性校验、diff、补丁；与具体 EDA 解耦 |
| 原理图生成 | **SKiDL** `generate_schematic()`（SKiDL 3.0 把布局布线交给 schematizer；层次化子电路→多页） | ADR-0009：大电路要开 `auto_stub`，不连线的元件会让它崩溃 |
| PCB 生成 | **自写 `eda_agent/kicad_side/board_build.py`**（kicad-cli 导出的网表→`.kicad_pcb`，在 KiCad 自带 Python 里调 `pcbnew`）；kinet2pcb 在 Windows 上不可用 | ADR-0007 |
| 布线 | **Freerouting 2.5.0（Java 版）与 KiCadRoutingTools v0.23.7**，M0 实测后二选一；DSN 导出 / SES 导回走 `pcbnew.ExportSpecctraDSN` / `ImportSpecctraSES` | ADR-0004 |
| ERC/DRC/导出 | **kicad-cli**（KiCad 10.0.8，解压版）：`sch erc --format json --exit-code-violations`、`pcb drc --format json --schematic-parity`、`pcb export gerbers/drill/pos/step/glb`、`pcb render` | ADR-0002 |
| 仿真 | **KiCad 自带的 ngspice-46**（`ngspice.dll`），自写 ctypes 对接、子进程隔离，`.meas` 测量 | ADR-0003 |
| 元器件目录 | JLCPCB/LCSC 公开目录：`cdfer/jlcpcb-parts-database` 的基础/优选件 CSV（约 3MB）为默认；可选下载其在库件 SQLite 文件（约 1GB）**作为只读数据文件**查询 | 无需凭证 |
| 聊天入口 | **飞书**（`lark-oapi`，长连接 WebSocket，卡片按钮回调做审批）；Discord 作为第二入口（可选） | 已核实：长连接仅企业自建应用；回调需 3 秒内响应 |
| 网页 | FastAPI 后端 + 单页前端（运行时间线、产物下载、原理图 SVG、3D 预览） | 比赛要求"供评审交互体验的网址" |
| 开发环境 | 本机 Windows 原生运行，工具只解压不安装，放在项目内 `.tools/`，外部工具的设置、缓存、临时文件都指到 `.tools/home`；Linux 验证用 GitHub Actions | ADR-0001 |
| 依赖与任务 | 只用 pip（直接依赖写死版本）；主程序 Python 3.14，用到 pcbnew 的步骤用 KiCad 自带 Python 3.11；`python tasks.py <任务>` 代替 make | ADR-0005、ADR-0006 |
| 部署 | M6 再装箱：Docker 镜像（KiCad + 布线器 + Python）；部署到协创者号提供的云服务器或自有 VPS | 可复现 |

---

## 3. 总体架构

```
           ┌──────────────┐     ┌──────────────┐
  用户 ───►│ 飞书 / Discord│     │  网页 (FastAPI)│◄── 评委
           └──────┬───────┘     └──────┬───────┘
                  └──────────┬─────────┘
                       ┌─────▼─────┐
                       │  Gateway  │  统一消息/审批事件 → RunManager
                       └─────┬─────┘
                       ┌─────▼──────────────────────────────┐
                       │ Orchestrator（状态机 + 回退边 + 预算）│
                       └─────┬──────────────────────────────┘
        ┌─────────────┬──────┼─────────┬────────────┬────────────┐
   ┌────▼───┐   ┌─────▼──┐ ┌─▼──────┐ ┌▼────────┐ ┌─▼──────┐ ┌───▼────┐
   │ Agents │   │ Gates  │ │ Tools  │ │ Memory  │ │ Parts  │ │  LLM   │
   │(角色)  │   │(确定性)│ │(EDA/仿真)│ │(文件)   │ │(目录/库存)│ │(API抽象)│
   └────────┘   └────────┘ └────────┘ └─────────┘ └────────┘ └────────┘
                                         │
                                  ┌──────▼──────┐
                                  │ 本地向量服务 │ (GPU, embeddings only)
                                  └─────────────┘
```

- **Agent** = 系统提示词 + 允许使用的工具列表 + 输出 JSON Schema。不直接写文件，只返回结构化结果（多为 `design.json` 的 JSON Patch）。
- **Gate** = 纯函数：`(design, spec, artifacts) -> GateReport{passed: bool, findings: [...]}`。不调用模型。
- **Orchestrator** 只根据 Gate 结果决定下一步，不根据模型的自我评价。

---

## 4. 流程（状态机）

```
S0 intake        需求智能体：一句话 → spec 草案（含可测验收标准）
S1 confirm       ★人工：用户在飞书/网页确认或修改 spec → 冻结（记录 sha256）
S2 architect     方案智能体：拆功能块、选拓扑、给 2~3 个备选并说明取舍 → 选定一个
S3 select        选型智能体：每个功能块选具体器件（先查库存，再查目录；读数据手册检索结果）
S4 schematic     原理图智能体：生成/修改 design.json 的 parts + nets
G1 static        Gate：IR 结构校验 + 自写电气规则 + KiCad ERC
S5 testbench     由 spec 的验收标准**确定性生成**仿真测试台（不经模型）
G2 sim           Gate：ngspice 仿真，逐条验收标准判定
   ↺ 失败 → S6 diagnose（诊断智能体读波形/报告）→ 回到 S3/S4（或 S2，若拓扑本身不行）
S7 layout        PCB：kinet2pcb 摆放 → 板框/安装孔/连接器位置按 board 约束 → Freerouting
G3 drc           Gate：kicad-cli DRC（含原理图一致性）
   ↺ 失败 → PCB 智能体调整板尺寸/线宽规则/层数/摆放提示 → 重布
S8 procure       采购智能体：BOM 对比库存，缺件生成采购申请
S9 approve       ★人工：飞书卡片「批准 / 修改 / 拒绝」
S10 package      导出 Gerber/STEP/渲染图/BOM/文档；总审智能体写"风险与未验证项"清单
S11 bringup      （板子回来后）生成上电步骤 + 每步期望读数（来自仿真）；用户报读数 → 比对 → 偏差诊断 → 修订版
```

**回退与预算**
- 每条回退边有次数上限（默认：S4↔G1 5 次，G2 失败回退 6 次，G3 回退 4 次），超限就停止并把当前最好设计 + 未通过项交给用户，不得隐瞒。
- 每次运行有费用上限（美元，默认可配置），`trace.jsonl` 累计；超限暂停并询问。
- 每个状态结束都把 `design.json` 快照写到 `iterations/NN/`，任何时候可回滚。

**人工节点只有两个必经**（S1、S9）。其余节点在飞书里只推送进度卡片，不打扰。

---

## 5. 数据格式

所有格式在 `schemas/` 里用 JSON Schema 定义，并用 pydantic 模型实现。

### 5.1 `spec.json`
```json
{
  "id": "spec-20261031-001",
  "summary": "电池与ZVS之间的过流保护器",
  "interfaces": [
    {"name": "VIN", "kind": "power_in", "v_min": 10.0, "v_max": 14.8, "notes": "3S/4S 锂电"},
    {"name": "VOUT", "kind": "power_out", "i_max": 10.0}
  ],
  "acceptance": [
    {
      "id": "A1",
      "text": "负载电流超过 12A 时，在 1ms 内切断输出",
      "testbench": {
        "type": "transient",
        "stimulus": {"load": {"kind": "current_step", "from": 5, "to": 15, "at": "2ms"}},
        "sim_time": "10ms"
      },
      "measure": {"kind": "time_until", "signal": "I(VOUT)", "condition": "< 0.1", "after": "2ms"},
      "pass": {"op": "<=", "value": 0.001, "unit": "s"}
    }
  ],
  "constraints": {
    "board_max_mm": [60, 40], "layers_max": 2,
    "assembly": "hand_solder", "min_package": "0805",
    "prefer_inventory": true, "budget_cny": 80
  },
  "assumptions": ["ZVS 装置启动峰值电流按 20A 估计（用户未提供，需确认）"],
  "frozen_sha256": null
}
```
要点：
- 验收标准必须是**仿真能测的数值条件**。需求智能体如果写不出可测条件，就把它放进 `assumptions` 或 `untestable`，在 S1 让用户看到。
- 冻结后任何智能体不得修改 spec；若确实需要改，必须回到 S1 让用户重新确认。

### 5.2 `design.json`（电路 IR，系统唯一的设计源）
```json
{
  "spec_sha256": "...",
  "blocks": [{"id": "sense", "purpose": "电流检测"}, {"id": "switch", "purpose": "高边开关"}],
  "parts": [
    {
      "ref": "Q1", "block": "switch",
      "symbol": "Device:Q_NMOS_GDS", "footprint": "Package_TO_SOT_THT:TO-220-3_Vertical",
      "value": "IRFZ44N", "mpn": "IRFZ44NPBF", "lcsc": "C2836",
      "source": "inventory",
      "spice": {"model": "IRFZ44N", "lib": "models/irfz44n.lib", "pin_map": {"1": "G", "2": "D", "3": "S"}},
      "ratings": {"vds_max": 55, "id_max": 49}
    }
  ],
  "nets": [
    {"name": "VIN", "class": "power", "nodes": [["J1", "1"], ["R1", "1"]]}
  ],
  "board": {"size_mm": [50, 30], "layers": 2, "mount_holes": "M3x4",
            "edge_connectors": [{"ref": "J1", "side": "left"}, {"ref": "J2", "side": "right"}],
            "net_classes": {"power": {"track_mm": 2.0, "clearance_mm": 0.3}}},
  "notes": []
}
```
- 智能体只能提交 **RFC 6902 JSON Patch**；编排器应用补丁 → pydantic 校验 → 失败则把错误原样返回给该智能体重试（最多 3 次）。
- `nets[].external_supply`（默认 false）：这个网络的电由板外经连接器送进来；原理图只在这类网络上放 PWR_FLAG（ADR-0008）。
- `ratings` 字段来自数据手册检索结果，并记录出处（`ratings_source: {doc_id, page}`），供规则检查使用。

### 5.3 `GateReport`
```json
{"gate": "sim", "passed": false,
 "findings": [{"id": "A1", "severity": "error", "measured": 0.0042, "threshold": 0.001,
               "message": "切断时间 4.2ms 超过 1ms", "artifacts": ["plots/A1.png", "raw/A1.raw"]}]}
```

### 5.4 运行目录
```
runs/<run_id>/
  request.md            用户原话 + 附件
  spec.json / spec.md
  iterations/NN/design.json, gates/*.json, notes.md
  sim/                  测试台 .cir、.raw、plots/
  kicad/                工程文件（每次 layout 重生成）
  final/                交付物（§1.2）
  trace.jsonl           事件流：llm_call / tool_call / gate / human / cost
  state.json            当前状态、回退计数、费用累计（用于断点续跑）
```

---

## 6. 智能体（角色）

每个角色一个文件 `eda_agent/agents/<role>.py` + 提示词 `prompts/<role>.md`。所有角色共享：只能通过工具读取信息；输出必须符合 Schema；必须写 `rationale`（会进文档）。

| 角色 | 输入 | 输出 | 可用工具 | 模型档位 |
|---|---|---|---|---|
| 需求 RequirementAgent | 用户原话、用户记忆 | spec 草案 + 需要用户确认的问题（≤3 个） | memory.read | 强 |
| 方案 ArchitectAgent | spec | 功能块划分 + 拓扑备选（2~3）+ 选择理由 | datasheet.search, design_memory.search | 强 |
| 选型 SelectorAgent | spec + 方案 | 每块器件清单（优先库存）与额定参数出处 | inventory.query, catalog.search, datasheet.search | 中 |
| 原理图 SchematicAgent | 方案 + 器件 + 上一轮 GateReport | design.json 补丁 | datasheet.search（引脚/典型应用电路） | 强 |
| 诊断 DiagnoseAgent | 仿真/ERC 报告、波形摘要（数值） | 根因假设 + 返回到哪个阶段 + 建议修改 | sim.probe（追加探针重仿真） | 强 |
| 版图 LayoutAgent | DRC 报告、板约束 | board 字段补丁（尺寸、规则、摆放提示） | — | 中 |
| 采购 ProcurementAgent | BOM、库存、目录 | 采购申请（替代料理由、数量含损耗） | catalog.search | 便宜 |
| 文档 DocAgent | 全部产物与 trace | README.md、bringup.md | — | 中 |
| 总审 ReviewAgent | 全部产物 | **只能新增**"风险/未验证项"，无权判定通过 | 全部只读工具 | 强 |
| 调试 BringupAgent | 用户测量值、期望值 | 偏差解释 + 下一步测量或修订补丁 | sim.probe | 强 |

"模型档位"在 `config.yaml` 里映射到具体模型名，不在代码里写死。

---

## 7. 确定性 Gate 细则

### G1 static（顺序执行，任一 error 即失败）
1. **IR 校验**：引用唯一；每个 node 的引脚存在于该 symbol（用 KiCad 符号库解析，`kicad-sch-api` 或 SKiDL 读取）；symbol 引脚数与 footprint 焊盘数一致。
2. **自写电气规则**（`gates/rules/*.py`，每条规则一个函数，单测覆盖）：
   - 悬空引脚（未标 NC 的输入/电源脚）
   - 电源脚未接电源网络、地脚未接地
   - 两个输出驱动同一网络
   - 每个 IC 电源脚附近有去耦电容（按网络拓扑判断，不按距离）
   - **额定值检查**：网络标称电压（来自 spec 接口 + 仿真 DC 工作点）≤ 器件 `ratings` × 降额系数（默认 0.8）
   - 封装可装配性：与 spec.constraints.assembly / min_package 冲突即报错
3. **KiCad ERC**：SKiDL 生成 `.kicad_sch` → `kicad-cli sch erc --format json --severity-error --exit-code-violations`。

### G2 sim
- 测试台由 `sim/testbench.py` 根据 spec.acceptance 的 `testbench` 字段**模板化生成**：激励源、负载、测量语句（`.meas`）都由代码拼接，模型不参与。
- 器件模型来源优先级：ngspice 原生元件（R/L/C/D/BJT/MOSFET 通用模型）→ 厂商 SPICE 模型（`models/`，开启 `set ngbehavior=ltpsa` 兼容）→ 行为级宏模型（由智能体编写，**标记为 approximate**，报告里单列）。
- 结果三态：`pass` / `fail` / `sim_error`（不收敛、超时、模型缺失）。`sim_error` 不算通过，走诊断，但与 `fail` 区分统计。
- 收敛手段按顺序自动尝试：默认 → `.options gmin/reltol 放宽` → `method=gear` → 缩短步长 / 加 `uic`；每次尝试记录在报告里。
- 额外自动测量：所有器件的峰值电压/电流/功耗，回填给 G1 的额定值检查（第二轮起生效）。

### G3 drc
- `kicad-cli pcb drc --format json --schematic-parity --severity-error --exit-code-violations`
- 额外检查：未布通网络数 = 0；板尺寸 ≤ spec 约束；电源网络线宽 ≥ 按电流估算的最小值（IPC-2221 公式，代码计算）。

---

## 8. 元器件、库存、采购

- `data/catalog/`：启动脚本下载 JLC 基础/优选件 CSV；可选下载在库件 SQLite（只读文件，用 Python 自带 sqlite3 打开，不起数据库服务）。`parts/catalog.py` 提供 `search(category, params, package, in_stock=True)`。
- `memory/users/<user_id>/inventory.yaml`：用户库存（MPN 或描述 + 数量 + 位置）。可通过飞书/网页发照片或文字添加，智能体解析后**用户确认才写入**。
- 选型规则：库存满足额定值且封装可装配 → 优先；否则目录件，优先 JLC 基础件 → 优选件 → 扩展件。
- 采购申请：缺件列表、数量（含损耗）、单价、总价、替代料说明；导出 LCSC/JLC 可上传的 BOM CSV。**审批通过只生成文件和记录，不下单。**

---

## 9. 数据手册检索（本地 GPU 唯一用途）

- 来源：目录 CSV 里的 datasheet URL；下载到 `data/datasheets/<mpn>.pdf`。
- 解析：PyMuPDF 抽文本和表格 → 按页/段切块；保留页码。
- 向量：本地 embedding 模型（多语种，如 bge-m3 或 Qwen3-Embedding 系列；具体型号在 M0 按显存与效果选），服务形式为本机 HTTP 小服务 `embed_server.py`（GPU）。
- 索引：`data/index/<mpn>.npy` + `<mpn>.jsonl`（块文本、页码），检索用 numpy 余弦相似度；全库规模不大，不需要向量数据库。
- 关键参数抽取：选型时由模型从检索到的段落里抽 `ratings`，**必须附页码**；抽不到的参数标为 unknown，G1 对 unknown 的额定值检查报 warning。
- 同一机制索引历史设计（`memory/designs/*/README.md`），用于"类似需求以前怎么做的"。
- 云端部署时 GPU 不可用 → 退化为：预先在本地建好索引随镜像发布 + 查询向量走 API embedding（配置开关），保证评委环境能跑。

---

## 10. 记忆与个性化（文件）

```
memory/users/<user_id>/
  profile.md        工具条件、焊接能力、常用板厂、偏好（用户说过的才写）
  inventory.yaml
  history.jsonl     每次运行摘要：需求、结果、失败原因、用户反馈
memory/designs/<run_id>/   通过的设计（README + design.json），供检索复用
```
- 每次运行开始，需求智能体读取 profile + inventory + 最近 history 摘要。
- 运行结束，编排器追加 history；用户在飞书里说"以后都用 xx"时写入 profile（需用户确认）。
- 这对应赛题第 3 点"长记忆与个性化演进"：演示时展示第二次许愿时系统主动用上库存与焊接偏好。

---

## 11. 交互入口

### 11.1 飞书机器人（主入口，对应赛题第 4 点）
- `lark-oapi` 长连接（企业自建应用；无需公网 IP）。
- 事件：接收消息 → 新建/继续运行；卡片按钮回调 → 确认 spec、审批采购、选择方案备选。
- **回调 3 秒内必须返回**：处理函数只把事件放入队列并立即回 toast，实际工作在后台线程/进程执行，完成后主动发新卡片或更新卡片。
- 卡片类型：spec 确认卡（可编辑字段）、进度卡（状态 + 当前回退次数 + 费用）、方案对比卡、采购审批卡、交付卡（链接到网页产物页）。

### 11.2 Discord（第二入口，可选）
- discord.py，按钮组件对应同样的审批事件。Gateway 层统一事件格式，飞书/Discord/网页共用 RunManager。

### 11.3 网页（评委入口）
- 页面：新建许愿 / 运行列表 / 运行详情（时间线：每个智能体发言、每个 Gate 报告、回退箭头）/ 产物（原理图 SVG：`kicad-cli sch export svg`；3D：渲染图 + 可旋转模型）/ 采购审批。
- **回放模式**：读取已完成运行的 `trace.jsonl` 按时间重放，无需 API 费用；评委可直接看完整过程。
- **实时模式**：限流（每 IP 每日次数、每次费用上限），防止被刷费用。
- 技术：FastAPI + SSE 推送事件；前端用原生 JS 或轻量框架，3D 用 three.js 加载 GLB（若 KiCad 无法导出 GLB，退回 STEP→GLB 转换或只展示渲染图，见 M0）。

---

## 12. 里程碑（共约 18 周）

每个里程碑结束都要有可运行的演示与测试，且更新本文件的"已验证"标记。

| # | 周 | 内容 | 完成标准 |
|---|---|---|---|
| M0 | 11/02–11/08 | **工具链冒烟（本机 `.tools/` + GitHub Actions，ADR-0001）**：KiCad 10.0.8（自带 ngspice）、Freerouting / KiCadRoutingTools、SKiDL、lark-oapi 跑通；手写一个 LED+电阻电路走完：IR→原理图→ERC→PCB→布线（DSN→SES 回导，或直接读写 .kicad_pcb）→DRC→Gerber/STEP/GLB/render | `python tasks.py smoke` 全绿。**必须验证并记录**：① 无界面下 DSN 导出与 SES 导入的方法（pcbnew Python API 的 Specctra 导出/导入函数在所选 KiCad 版本是否可用）；② `kicad-cli` 是否支持导出 GLB；③ SKiDL `generate_schematic` 对 30+ 器件电路的可读性；④ 选定 embedding 模型（已推迟到 M4，ADR-0010） |
| M1 | 11/09–11/22 | IR（pydantic + JSON Schema）、JSON Patch 应用器、IR→SKiDL→KiCad 发射器、运行目录与 trace | 10 个手写 IR 样例全部生成可打开的工程 |
| M2 | 11/23–12/06 | G1/G2/G3 全部 Gate；测试台生成器；收敛重试 | 每条规则有正反单测；5 个手写电路的仿真判定与手算一致 |
| M3 | 12/07–12/27 | LLM 抽象、各智能体、编排状态机、回退与预算、断点续跑；CLI：`eda-agent run "需求"` | 3 个演示需求在 CLI 端到端跑出交付物（允许人工节点用命令行确认） |
| M4 | 12/28–01/10 | 目录/库存/采购；数据手册下载+解析+本地向量检索 | 选型结果都带出处页码；缺件生成采购申请 |
| M5 | 01/11–01/24 | 飞书机器人（卡片审批全流程）、记忆读写；Discord（可选） | 在飞书里从许愿到收到交付卡 |
| M6 | 01/25–02/07 | 网页（实时+回放）、部署到云服务器、限流 | 评委可在网页回放 3 个案例、可实时跑 1 个小案例 |
| M7 | 02/08–02/21 | 评测：自建对照集 + 基线对比 + 报告；bring-up 流程（S11） | `python tasks.py eval` 一键重跑出表格与图 |
| M8 | 02/22–03/04 | 海报、演示视频、提交材料、修 bug；03/05 前一天提交 | 提交完成 |

进度落后时的处理顺序（实现先后，不是砍产品范围）：先保证 S0–S10 主链路在 CLI 与网页可跑；飞书卡片的编辑能力、Discord、bring-up、实物测量仪器接入依次后移。

---

## 13. 评测（证明"质量更高"）

- **对照集**：从 GitHub / OSHWLab 选 20–30 个公开的开源板级项目，覆盖电源、保护、模拟、MCU 外设、电机驱动等；由开发者把每个项目的功能写成需求原话 + 可测验收标准（`eval/tasks/<id>/spec.json`），参考设计只用于写验收标准，不给系统看。
- **对比组**：
  1. 单次生成基线：同一强模型，一次提示直接输出 design.json，不迭代；
  2. 本系统完整流程；
  3. 消融：去掉仿真回退 / 去掉数据手册检索 / 去掉规则检查，各一组。
- **指标**：G1/G2/G3 各自通过率、全部通过率、`sim_error` 比例、平均迭代次数、平均费用与耗时、额定值违规数。
- **防作弊**：评测脚本只读系统输出；系统运行时看不到评测专用的额外测试；验收标准在运行前冻结。全部原始 trace 公开。
- **HWE-Bench**：已去信询问作者数据与评测脚本（若开放，再单独加一组对照；其评测基于 LTspice，需逐题确认能否在 ngspice 下等价运行，结果不直接与论文 8.15% 横比，除非用其原评测环境）。

---

## 14. 仓库结构

```
.
├── AGENTS.md                 给编码智能体的工作规则（CLAUDE.md 软链接到它）
├── docs/PLAN.md              本文件
├── docs/decisions/           每个重要技术决定一页（ADR）
├── pyproject.toml            ruff / mypy / pytest 配置
├── requirements*.txt         pip 直接依赖，版本写死（ADR-0005）
├── tasks.py                  lint / test / test-tools / smoke（代替 Makefile，ADR-0006）
├── Dockerfile / docker-compose.yml   M6 部署时再加（ADR-0001）
├── .tools/                   本地解压的外部工具与沙箱目录（git 忽略，ADR-0001）
├── config.example.yaml       模型档位映射、费用上限、回退上限、路径
├── schemas/                  spec / design / gate_report / trace 的 JSON Schema
├── prompts/                  每个角色一个 .md
├── eda_agent/
│   ├── orchestrator/         state machine, run manager, budget, resume
│   ├── agents/               各角色
│   ├── llm/                  provider 抽象、重试、结构化输出、计费
│   ├── ir/                   pydantic 模型、patch 应用、校验
│   ├── emit/                 IR→SKiDL→kicad_sch / netlist / kicad_pcb；IR→SPICE
│   ├── kicad_side/           在 KiCad 自带 Python 里运行、调用 pcbnew 的脚本（建板、Specctra 导出导回）
│   ├── gates/                static(rules/), sim, drc
│   ├── tools/                kicad_cli, 布线器, ngspice, sandbox（外部工具环境）, runner（统一超时与 trace）
│   ├── parts/                catalog, inventory, procurement
│   ├── retrieval/            datasheet 下载/解析、embed client、numpy 检索
│   ├── memory/               profile / history / design memory 读写
│   ├── gateway/              feishu, discord, web 事件统一
│   └── web/                  FastAPI + static 前端
├── embed_server/             本地 GPU embedding 服务
├── models/                   SPICE 模型库（注明来源与许可）
├── data/                     目录、数据手册、索引（git 忽略，脚本可重建）
├── eval/                     tasks/, baselines/, run_eval.py, report.py
├── examples/                 手写 IR 样例与演示需求
├── runs/                     运行输出（git 忽略；精选案例复制到 showcase/）
└── tests/
```

---

## 15. 风险与对策

| 风险 | 对策 |
|---|---|
| SKiDL 自动生成的原理图可读性差 | 按功能块分层（每块一页）；M0 评估；必要时用 `kicad-sch-api` 自写布局后处理 |
| 自动布线对功率/大电流板质量一般 | 电源网络先由规则设定线宽与铺铜；文档如实写"建议人工复核的网络" |
| 厂商 SPICE 模型与 ngspice 不兼容 | 兼容模式 + 行为模型兜底并在报告里标"近似"；收集常用器件模型到 `models/` |
| 仿真不收敛（尤其自激振荡电路） | 自动重试策略；`sim_error` 单独统计，不当作通过 |
| 模型自我评价造成"假通过" | 架构上禁止：只有 Gate 能写 passed |
| API 费用失控 | 档位分级、每次运行上限、网页实时模式限流、回放模式 |
| 评委不是电子专家 | 网页时间线把"失败→诊断→修改→通过"可视化；海报只放一张前后对比图与一个数字 |
| 飞书长连接仅限企业自建应用 | 用自己创建的飞书企业/团队创建自建应用；评委主要走网页 |
| 一人开发进度 | 里程碑每两周可演示；所有外部工具在 M0 一次性打通 |

---

## 16. 待验证清单（实现前/中逐项勾掉）

- [x] 所选 KiCad 版本下，无界面 DSN 导出 / SES 导入的可用方法 —— KiCad 10.0.8 用 `pcbnew.ExportSpecctraDSN` / `ImportSpecctraSES`，LED 板实测导回后 DRC 0 错误（ADR-0004）
- [x] `kicad-cli` 是否可导出 GLB（否则 STEP→GLB 转换方案）—— `pcb export glb` 可用，冒烟已生成 board.glb（ADR-0002）
- [x] SKiDL `generate_schematic` 在 KiCad 9/10 下对中等规模电路的效果 —— 替代做法见 ADR-0011（自写版面引擎样张：机器检查全过，导线总长约为原图的 1/8，等用户看图）。SKiDL 本身实测不能直接用：默认模式只适合很小的电路；`auto_stub` 下 50 个元件的单页会把大部分符号甩出图纸；只有 labels 模式能放下 63 个元件；不连线的元件会让它崩溃（ADR-0009）。替代做法待定
- [x] kinet2pcb 与所选 KiCad 版本的 `pcbnew` 模块兼容性 —— Windows 上 import 即报错，改用自写建板脚本（ADR-0007）
- [x] 布线器在 30 个以上元件的板子上的对比 —— 5 块 KiCad 示例板 × 4 个布线器（ADR-0004）：规则一致的前三块上 fastroute 与 Freerouting 错误数相当、快 1.2–7.6 倍；kct 的 C++ 版 4 块板超时；两块大板的错误主要是规则没传进布线器，不能排名。选哪个、是否继续找更强的由用户定
- [ ] embedding 模型与接口格式（用户的云 GPU 只暴露一个 API 端口）—— 推迟到 M4，用真实数据手册对比后再定（ADR-0010）
- [ ] 协创者号平台项目展示网站的提交格式（仓库 + Demo URL 的具体要求）
- [ ] HWE-Bench 作者回复
- [ ] 飞书自建应用创建与卡片回调权限
