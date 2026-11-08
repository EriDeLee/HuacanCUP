# 基于AI智能体的电子电路全流程自动化设计系统 — 实现方案

> 本文件是整个仓库的设计依据，交给编码智能体实现。实现时遇到与本文冲突的情况，先改本文再改代码。
> 截止：2027-03-05 17:00（华灿奖·主题十五·方向一）。最近修订：2026-11-08（ADR-0016 ~ ADR-0018）。

---

## 0. 一句话

目标用户是电子爱好者、学生和硬件工程师。用户用一句话许愿（例："在电池和 ZVS 装置之间接一个过流保护器"、"4 串锂电池的短路与过流保护板"），智能体团队交付**可打板的工程**：需求规格 → 方案 → 原理图 → 仿真验证 → PCB → BOM/采购申请 → 3D 渲染 → 文档；实物做出来后还能陪用户调试（bring-up）并出修订版。

同一份需求通常有几种做法，成本、精度、面积、焊接难度各不相同（4 串锂电池保护板就有比较器加 MOSFET 的分立方案、专用多串电池保护芯片、电子保险丝或热插拔控制芯片、保险丝加 PTC 的被动保护）。系统在方案阶段分叉，几种做法各自把整条流水线做完（路与路之间也会交流），最后由程序按仿真余量、板面积、BOM 成本、库存件占比给通过全部 Gate 的方案排名，胜出的交给用户确认（ADR-0016，用户 2026-11-08 决定）。

**硬原则：所有"通过/不通过"的判定只能来自确定性程序（KiCad ERC/DRC、ngspice 仿真、自写规则检查）。模型只负责提方案、读报告、改设计，永远不能宣布"设计没问题"。**

---

## 1. 范围

### 1.1 输入
- 自然语言需求（中文/英文），可附图片/草图/已有原理图。
- 用户长期上下文：手头元器件库存、工具条件（是否能焊 0402/QFN、有无回流焊）、偏好的板厂、历史设计。

### 1.2 输出（每次运行 `runs/<run_id>/final/`，内容是胜出方案，用户在 S9 改选时是改选的那一路；其余各路留在 `branches/<k>/`。`ranking.json`、`trace.jsonl` 的原件在运行目录根部，S10 打包时各复制一份进 `final/`，见 §5.4）
| 产物 | 格式 | 生成方式 |
|---|---|---|
| 需求规格（冻结） | `spec.json` + `spec.md` | 需求智能体起草，用户确认后冻结 |
| 设计源 | `design.json`（本系统 IR） | 各智能体以补丁方式修改 |
| 可编辑 EDA 工程 | `.kicad_pro` / `.kicad_sch` / `.kicad_pcb` | 自写原理图版面引擎（放得下就一页、每个功能块一个方框，放不下才分页；同页信号连成导线，ADR-0011、ADR-0013、ADR-0014），自写建板脚本生成 PCB，布线前自动摆放（ADR-0018），TraceMaker 布线（本机 Windows 退回 fastroute，ADR-0017） |
| 制造文件 | Gerber + 钻孔 zip、坐标文件 | `kicad-cli pcb export gerbers` / `drill` / `pos` |
| BOM | `bom.csv`（含库存/采购来源、LCSC 编号、单价） | 确定性脚本 |
| 采购申请 | `purchase_request.json` + 可上传的购物车 CSV | 采购智能体起草，用户在网页对话里审批 |
| 3D | `board.step`、渲染图 `renders/*.png`、网页预览 `board.glb`（若可导出） | `kicad-cli pcb export step` / `pcb render` |
| 仿真报告 | 波形图 + 每条验收标准的数值与结论 | ngspice + 自写测量脚本 |
| 方案对比 | `ranking.json`（每一路的 Gate 结果、各项指标、名次，被淘汰的路附淘汰理由；用户在 S9 改选时另记一笔「用户选了哪一路」，程序算的名次不改） | 名次由汇合程序按配置里的权重算；淘汰理由由总审智能体写（ADR-0016） |
| 文档 | `README.md`（原理说明、设计取舍、风险）、`bringup.md`（上电调试步骤与期望读数） | 文档智能体 |
| 全过程记录 | `trace.jsonl`（每次模型调用、工具调用、费用、判定） | 编排器自动写 |

### 1.3 不做
- 不自动下单付款。采购最多生成购物车文件 + 审批通过记录。
- 不承诺高速/射频/高密度板的布线质量（自动布线器能力边界），文档里如实标注。
- 不针对某一类电路做特化。ZVS 过流保护器和 li4s_protect（4 串锂电池短路与过流保护板，主演示用例之一）都只是演示用例。
- 不接飞书、Discord 等聊天软件：只做网页对话（用户 2026-11-01 决定，ADR-0015）。

### 1.4 比赛与提交物
- 本项目属于华灿奖主题十五的方向（一）「AI 智能体行业应用创新」，不涉及跨境。
- 成果提交三样东西（2026-11-08 用户提供）：产品宣传海报；产品交互演示视频；在协创者号平台上的可体验网址。
  赛题页「03 创作总体要求」同样要求「供评审交互体验产品网址链接」（ADR-0015）。
- 网址就是 §11 的网页（M6 部署）；海报、视频、提交材料排在 M8。具体提交格式的要求还没确认，列在 §16。

### 1.5 竞品与差异
下面是 2026-11-04 核实的公开信息，只写已核实的内容：

| 竞品 | 已有能力 | 没做或没承诺的部分 |
|---|---|---|
| ProtoFlow | 桌面端智能体选型、原理图、布局布线；ERC/DRC；ngspice（工作点、瞬态、AC）；导出 KiCad 工程；有 CLI 和 MCP | 官方博客写明目前没有可靠的"一句话到验证过的可打板"工具，仍需人工审核；仿真由用户触发 |
| Flux | 浏览器端 Copilot，原理图到布局到制造；自动布局适合中低复杂度板 | 不导出 KiCad 工程；高速接口需人工 |
| Quilter / DeepPCB | 强化学习做布局布线，内置 DRC；Quilter 会生成多个候选 | 只做布线，不是 LLM 智能体 |
| Circuit Mind | 框图到原理图和 BOM；降额、FMEA 分析 | 不做布局布线，分析结果交给人看 |
| CELUS | 需求到原理图和 BOM | 到布局之前为止 |
| LG PCBWorld（KDD 2026 workshop） | KiCad DRC 反馈闭环、LLM 工具调用智能体、679 块开源板的公开评测 | 只覆盖布线 |
| 开源 KiCad MCP 类项目 | 让 LLM 直接操作 KiCad | 没有验收门禁 |

来源：
- https://www.protoflow.ai/blog/ai-pcb-design-2026-guide
- https://www.protoflow.ai/compare/best-ai-pcb-design-tools-for-professionals
- https://www.lgresearch.ai/publication/view?seq=183
- https://arxiv.org/abs/2607.05915

**差异**：我们和它们的不同在于「多种方案并行做完，由程序检查结果选优」（§4、ADR-0016）。其他整板生成工具是单条流水线。
降额检查不算差异（Circuit Mind 已经有了）。海报、视频、网页等对外材料照这个写，不用「首创」「唯一」。

---

## 2. 技术选型（已定）

| 方面 | 选择 | 理由 |
|---|---|---|
| 编排 | **自写编排层**，不用 LangGraph/CrewAI/OpenClaw；上下文取舍、工具调用时机、失败回退、重复调用的缓存是单独一项设计工作（§3.1） | 流程是固定状态机 + 回退边（多路并行时每路一份），自写最可控、最易复现；同一个模型能发挥多少，取决于智能体循环本身的质量（用户 2026-11-08 的判断） |
| 模型 | **全部走 API**；`llm/` 做供应商抽象（Anthropic + OpenAI 兼容接口） | 用户决定 |
| 向量模型 | **部署在自有 GPU 服务器上，只通过一个 API 端口调用**（数据手册与历史设计检索）；开发机没有可用的 GPU；具体模型 M4 用真实数据对比后再定 | 用户决定，ADR-0010 |
| 存储 | **只用文件**：JSON / JSONL / Markdown / `.npy`。不建数据库服务 | 用户不想碰数据库；可审计、可 git 追踪 |
| 电路 IR | 自定义 `design.json`（见 §5） | 便于确定性校验、diff、补丁；与具体 EDA 解耦 |
| 原理图生成 | **自写版面引擎**（`eda_agent/emit/sch_*.py`，用户 2026-10-31 选定）：主芯片 + 引脚链，按信号流向排；同一页上的信号用找路程序连成导线（不交叉，连不上才用同名标签）；整个设计在 A4/A3 上放得下就画一页、每个功能块一个虚线方框，放不下才每块一页、根页是框图；支持多单元和派生符号。SKiDL 只留作对照（`smoke --schematic skidl`） | ADR-0009（SKiDL 实测不够好）、ADR-0011、ADR-0013、ADR-0014（用户 2026-11-01 选定） |
| PCB 生成 | **自写 `eda_agent/kicad_side/board_build.py`**（kicad-cli 导出的网表→`.kicad_pcb`，在 KiCad 自带 Python 里调 `pcbnew`）；kinet2pcb 在 Windows 上不可用 | ADR-0007 |
| 布线 | **TraceMaker 0.12.1 主用，fastroute 0.1.14 和 KiCadRoutingTools v0.23.7 备用**（用户 2026-11-08 决定）。TraceMaker 与 KiCadRoutingTools 直接读写 .kicad_pcb；fastroute 走 DSN 导出 / SES 导回（`pcbnew.ExportSpecctraDSN` / `ImportSpecctraSES`）。**TraceMaker 只有 Linux 版**：Linux 上（GitHub Actions、部署镜像）主用；本机 Windows 跑不了，退回 fastroute 主用 + KiCadRoutingTools 备用。布线器按配置里的顺序选第一个可用的，一个不过换下一个 | ADR-0004（实测）、ADR-0017 |
| ERC/DRC/导出 | **kicad-cli**（KiCad 10.0.8，解压版）：`sch erc --format json --exit-code-violations`、`pcb drc --format json --schematic-parity`、`pcb export gerbers/drill/pos/step/glb`、`pcb render` | ADR-0002 |
| 仿真 | **KiCad 自带的 ngspice-46**（`ngspice.dll`），自写 ctypes 对接、子进程隔离，`.meas` 测量 | ADR-0003 |
| 元器件目录 | JLCPCB/LCSC 公开目录：`cdfer/jlcpcb-parts-database` 的基础/优选件 CSV（约 3MB）为默认；可选下载其在库件 SQLite 文件（约 1GB）**作为只读数据文件**查询 | 无需凭证 |
| 交互入口 | **只做网页对话**：用户在对话里许愿、确认、审批；Agent 把功能以卡片推到对话里，自写渲染引擎展示原理图、3D、波形和报告。不接飞书、Discord | 用户 2026-11-01 决定：纯网页「更好自己写渲染引擎来展示模型成果」（ADR-0015） |
| 网页 | FastAPI 后端 + 单页前端（对话、运行时间线、产物下载、原理图 SVG、3D 预览） | 比赛要求"供评审交互体验的网址"；也是唯一的交互入口 |
| 开发环境 | 本机 Windows 原生运行，工具只解压不安装，放在项目内 `.tools/`，外部工具的设置、缓存、临时文件都指到 `.tools/home`；Linux 验证用 GitHub Actions；TraceMaker 只有 Linux 版，只在 Linux 上跑 | ADR-0001、ADR-0017 |
| 依赖与任务 | 只用 pip（直接依赖写死版本）；主程序 Python 3.14，用到 pcbnew 的步骤用 KiCad 自带 Python 3.11；`python tasks.py <任务>` 代替 make | ADR-0005、ADR-0006 |
| 部署 | M6 再装箱：Docker 镜像（KiCad + 布线器 + Python）；部署到协创者号提供的云服务器或自有 VPS | 可复现 |

---

## 3. 总体架构

```
                       ┌───────────────────┐
  用户 / 评委 ────────►│ 网页对话 (FastAPI)│  自写渲染引擎：原理图、3D、波形、审批卡片（ADR-0015）
                       └─────┬─────────────┘
                       ┌─────▼─────┐
                       │  Gateway  │  对话消息/审批事件 → RunManager
                       └─────┬─────┘
                       ┌─────▼─────────────────────────────────────────────┐
                       │ Orchestrator（状态机 + 回退边 + 预算 + 分叉/汇合）│
                       └─────┬─────────────────────────────────────────────┘
        ┌─────────────┬──────┼─────────┬────────────┬────────────┐
   ┌────▼───┐   ┌─────▼──┐ ┌─▼──────┐ ┌▼────────┐ ┌─▼──────┐ ┌───▼────┐
   │ Agents │   │ Gates  │ │ Tools  │ │ Memory  │ │ Parts  │ │  LLM   │
   │(角色)  │   │(确定性)│ │(EDA/仿真)│ │(文件)   │ │(目录/库存)│ │(API抽象)│
   └────────┘   └────────┘ └────────┘ └─────────┘ └────────┘ └────────┘
                                         │
                                  ┌──────▼──────┐
                                  │  向量服务   │ (自有 GPU 服务器，API 端口，只做 embedding)
                                  └─────────────┘
```

- **Agent** = 系统提示词 + 允许使用的工具列表 + 输出 JSON Schema。不直接写文件，只返回结构化结果（多为 `design.json` 的 JSON Patch）。
- **Gate** = 纯函数：`(design, spec, artifacts) -> GateReport{passed: bool, findings: [...]}`。不调用模型。
- **Orchestrator** 只根据 Gate 结果决定下一步，不根据模型的自我评价。方案阶段分叉成多路以后，每一路有自己的状态、回退计数和运行目录，费用上限按全部路合计；路与路之间可以交流（§4）；分叉、指纹去重、汇合选优由它调用确定性程序完成（`orchestrator/swarm.py`，ADR-0016）。

### 3.1 编排层的设计内容

同一个模型能发挥多少，取决于智能体循环本身的质量（用户 2026-11-08 的判断）。所以编排层继续自写（不用 LangGraph / CrewAI / OpenClaw），但不再当成「薄」的一层，下面四点是要专门设计的内容：

1. **上下文怎么取舍**：每次调用只给模型相关的 GateReport 片段和 `design.json` 的差异，不给全量。`docs/review-queue.md` P1 第 5 条「先给摘要和编号，要细节再按编号读」是同一件事。
2. **工具什么时候调用**：Gate、测试台生成、导出这些确定性步骤由编排器按状态机直接调用，不经模型；模型自己可以调用的工具（§6 表里每个角色的工具清单），在提示里写明什么情况下该调、最多调几次。
3. **失败后怎么回退**：回退边、次数上限和预算在 §4。回退时交给下一个智能体的信息（哪个 Gate 的哪几条失败、上一版 `design.json` 的差异）也属于这一项。
4. **重复调用的缓存**：输入完全相同的确定性工具调用（数据手册检索、仿真）和模型调用，结果能复用就不重复付费。多路并行时各路会查同样的数据手册，缓存是否跨路共享，M3 定。

这四点的第一版在 M3 做；好坏用 §13 的指标衡量：每次全部通过所需的 token 数和费用、平均迭代次数。`docs/review-queue.md` P1 里相关的条目（第 5 条、第 9 条「记规则版本」）到 M3 时一并定。

---

## 4. 流程（状态机）

```
S0 intake        需求智能体：一句话 → spec 草案（含可测验收标准）
S1 confirm       ★人工：用户在网页对话里确认或修改 spec → 冻结（记录 sha256）。spec 在分叉前冻结，各路共用同一套验收标准
S2 architect     方案智能体：拆功能块，给出 3 种明显不同的拓扑并说明取舍（不再自己选定一个）
S2b fork         分叉：每种拓扑 × 3 种选型策略（优先用户库存 / 优先 JLC 基础件 / 优先最小面积）= 9 路，数量是配置项
                 此后到 G3，每一路各自运行，回退计数、运行目录都分开；路与路之间可以交流（ADR-0016）
S3 select        选型智能体：按本路的选型策略给每个功能块选具体器件（库存、目录、数据手册检索结果；规则见 §8）
S4 schematic     原理图智能体：生成/修改 design.json 的 parts + nets
G1 static        Gate：IR 结构校验 + 自写电气规则 + KiCad ERC
S5 testbench     由 spec 的验收标准**确定性生成**仿真测试台（不经模型）
G2 sim           Gate：ngspice 仿真，逐条验收标准判定
   ↺ 失败 → S6 diagnose（诊断智能体读波形/报告）→ 回到 S3/S4（拓扑本身不行时：拓扑只有 1 种（单路，或方案智能体只想出 1 种）就回 S2 换一种拓扑，用它的各路一起回去、重新分叉；拓扑有 2 种及以上时这一路终止，别的拓扑在别的路里）
S7 layout        PCB：自动摆放（ADR-0018）→ 板框/安装孔/连接器位置按 board 约束 → 版图智能体布线前设线宽、间距、禁布区、铺铜规则并给摆放提示 → 布线（TraceMaker；本机 Windows 用 fastroute；一个不过换下一个，ADR-0017）
G3 drc           Gate：kicad-cli DRC（含原理图一致性）
   ↺ 失败 → 版图智能体读 DRC 报告和未布通网络，调整规则、摆放提示或板尺寸 → 重新摆放、重布
S7b converge     汇合选优：只在 G1、G2、G3 全部通过的路之间比较，名次由程序算（仿真余量、板面积、BOM 成本、库存件占比，权重在配置里）；总审智能体只为被淘汰的路写淘汰理由，不能改名次
S8 procure       采购智能体：只针对胜出方案（用户在 S9 改选后针对改选的那一路），BOM 对比库存，缺件生成采购申请
S9 approve       ★人工：网页对话里的审批卡片「批准 / 修改 / 拒绝」，展示胜出方案，附其余各路的一行摘要和淘汰理由；
                 用户可以改选另一条 G1、G2、G3 全部通过的路，改选后回 S8 为它生成采购申请，再推一次审批卡片
S10 package      导出 Gerber/STEP/渲染图/BOM/文档；总审智能体写"风险与未验证项"清单
S11 bringup      （板子回来后）生成上电步骤 + 每步期望读数（来自仿真）；用户报读数 → 比对 → 偏差诊断 → 修订版
```

**多方案并行**（ADR-0016，用户 2026-11-08 决定）
- 路数 = 拓扑数 × 策略数，默认 3 × 3，都是配置项。配成 1 × 1 就是单路。拓扑只有 1 种时（配成 1 × 1，或方案智能体只想出 1 种），S6 认为拓扑不行就回 S2，用这种拓扑的各路一起回去；拓扑有 2 种及以上时只终止这一路（用户 2026-11-08 决定）。先跑通单路，再做并行，并行不能挡住单路。
- 防止各路趋同：每路的提示里带明确的差异约束（例如「这一路必须使用专用保护芯片」）；程序计算每路的拓扑指纹和 BOM 指纹（拓扑指纹在分叉时算，BOM 指纹在 S3 选型后算），重复的路合并掉，不经模型判断。
- 路与路之间要能交流，是一个集群，不是各跑各的（用户 2026-11-08 补充，选了轻量版）。先做两种：共享记事板（各路把查到的事实写上去，别的路能读，只共享事实、不共享整份设计）；失败教训广播（某路没过 Gate 时，把「为什么」发给所有路）。交流只影响智能体提什么方案，判定仍只来自 Gate；每条消息记进 `trace.jsonl`（带发送路的 branch id），时间线上能看到。评审、协调员智能体、借用子电路、拼混合路以后再加（ADR-0016）。
- 汇合只在 G1、G2、G3 全部通过的路之间比较；排名由程序按配置里的权重算，权重运行前固定。一路都没有全部通过时，按通过的 Gate 数量和失败项排出「当前最好」交给用户，不得隐瞒未通过项。
- 总审智能体只做两件事：为每条被淘汰的路写淘汰理由（S7b），写"风险与未验证项"（S10）；不能改变名次，不能宣布通过。
- 采购只针对胜出方案，S9 审批卡片展示胜出方案和其余各路的摘要。用户可以在 S9 改选另一条全部通过的路（没全部通过的路不能选）；改选后回 S8 为它生成采购申请，再审批一次。`ranking.json` 里程序算的名次不变，另记一笔用户选了哪一路（用户 2026-11-08 决定）。

**回退与预算**
- 每条回退边有次数上限（默认：S4↔G1 5 次，G2 失败回退 6 次，G3 回退 4 次），超限就停止并把当前最好设计 + 未通过项交给用户，不得隐瞒。多路并行时上限按路单独计算：某一路超限只终止这一路，保留它的最好状态和未通过项。
- 每次运行有费用上限（美元，默认可配置），`trace.jsonl` 累计；超限暂停并询问。多路并行时按全部路合计。
- 共享阶段（S0–S2）只跑一次。并行专门的费用控制先不做（用户 2026-11-08：费用先不管）：分支阶段过 G1 之前用便宜的模型、过了再用强模型，超出预算时按当前排名从末位终止路。这两条的想法记在 ADR-0016，以后再定。
- 每个状态结束都把 `design.json` 快照写到 `branches/<k>/iterations/NN/`（单路运行 k 为 0），任何时候可回滚。

**人工节点只有两个必经**（S1、S9）。其余节点在网页对话里只推送进度卡片，不打扰。

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
- 实现：`eda_agent/ir/spec.py`；完整字段见 `schemas/spec.schema.json`（由模型导出，M2）。接口名对应 design 里的同名网络（review-queue D13）。

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
  实现：`eda_agent/ir/patch.py`；完整字段见 `schemas/design.schema.json`（由模型导出，ADR-0013）。
- `parts[].role`（hub / connector / passive）、`parts[].anchor`：只给原理图引擎用，说明元件怎么画、画在谁旁边；不填就自动推断（ADR-0013）。
- `nets[].external_supply`（默认 false）：这个网络的电由板外经连接器送进来；原理图只在这类网络上放 PWR_FLAG（ADR-0008）。
- `ratings` 字段来自数据手册检索结果，并记录出处（`ratings_source: {doc_id, page}`），供规则检查使用。

### 5.3 `GateReport`
G1、G2、G3 共用同一个报告格式。模型在 `eda_agent/gates/report.py`，JSON Schema 是 `schemas/gate_report.schema.json`（由模型导出，M2 定）。
```json
{"gate": "sim", "passed": false,
 "counts": {"pass": 1, "fail": 1, "sim_error": 0, "total": 2},
 "findings": [
   {"id": "A1", "severity": "error", "status": "fail",
    "measured": 0.0042, "threshold": 0.001, "op": "<=", "unit": "s", "margin": -3.2,
    "message": "切断时间 4.2ms 超过 1ms",
    "artifacts": ["branches/0/sim/A1.cir", "branches/0/sim/plots/A1.png", "branches/0/sim/A1.raw"],
    "attempts": [{"name": "default", "options": [], "outcome": "ok", "error": "", "netlist": "branches/0/sim/A1.cir"}]},
   {"id": "A2", "severity": "info", "status": "pass", "measured": 5.02, "threshold": 4.5,
    "threshold_high": 5.5, "op": "between", "unit": "V", "margin": 0.092, "message": "…", "artifacts": [], "attempts": []}],
 "device_stress": {"Q1": {"v_peak": 12.1, "i_peak": 10.0, "p_peak": 3.2}},
 "net_current_peak": {"VBAT": 10.0}}
```

每条 finding 都有这些字段：

| 字段 | 含义 |
|---|---|
| `id` | G2：验收标准编号（A1…）；G1、G3：规则或检查项名；ERC / DRC 是 KiCad 的违规类型 |
| `severity` | `error` / `warning` / `info`。报告的 `passed` 就是「没有 error」，只由 `gates/` 的代码写出 |
| `status` | `pass` / `fail` / `sim_error`。`sim_error` 是没得出结果（不收敛、超时、模型缺失），不算通过，和 `fail` 分开统计 |
| `measured`、`threshold`、`threshold_high`、`op`、`unit` | 实测值、阈值（`between` 时 `threshold` 是下限、`threshold_high` 是上限）、比较符号、单位 |
| `margin` | 有符号的相对余量，定义见下 |
| `message` | 一句话说明，给人和诊断智能体看 |
| `artifacts` | 相关文件，相对运行目录（网表、波形图、`.raw`） |
| `attempts` | G2：每次仿真尝试（默认设置 → 放宽 gmin / reltol → `method=gear` → 缩短步长加 `uic`）及结果 |
| `detail` | 工具给的原始条目（KiCad 违规项等） |

G2 的报告对每条验收标准都写一条 finding，通过的也写（severity 是 `info`），不只记失败项：汇合选优要按余量排名（ADR-0016）。
`device_stress` 是每个器件在全部仿真里的峰值电压、电流、功耗（G1 额定值检查第二轮用）；`net_current_peak` 是每个网络的电流上界（G3 线宽检查用）。

**余量（margin）**：有符号的相对余量，正数表示有余量，负数表示超标。

| 判定 | 余量 |
|---|---|
| `<=`、`<` | `(threshold - measured) / abs(threshold)` |
| `>=`、`>` | `(measured - threshold) / abs(threshold)` |
| `between` | 下端按 `>=`、上端按 `<=` 各算一个，取较小的那个（离失败更近的一端） |
| `threshold` 为 0 | 没有量级可除。`pass.scale` 写了就拿它当分母；没写，余量只表示方向：满足 +1、刚好等于阈值 0、不满足 −1 |

`status` 由比较结果决定，不由余量的符号决定：严格不等式（`<`、`>`）刚好等于阈值时余量是 0，但判不通过。
把各项余量合成一个总分是汇合选优的事（§16），M2 只记录每一项。

### 5.4 运行目录
```
runs/<run_id>/
  request.md            用户原话 + 附件
  spec.json / spec.md   分叉前冻结，各路共用
  branches/<k>/         每一路一个目录；单路运行只有 branches/0/（ADR-0016）
    iterations/NN/design.json, gates/*.json, notes.md
    sim/                  这一路的测试台 .cir、.raw、plots/
    kicad/                这一路的工程文件（每次 layout 重生成）
  ranking.json          汇合选优的结果：每一路的 Gate 结果、各项指标、名次、淘汰理由；用户在 S9 改选时另记一笔，名次不改
  final/                交付物（§1.2）：胜出方案（用户改选时是改选的那一路）；S10 打包时把 ranking.json、trace.jsonl 各复制一份进来（复制的是打包那一刻的内容）
  trace.jsonl           事件流：llm_call / tool_call / gate / human / cost；每条记录带 branch id（共享阶段为空），路与路之间的交流也记在这里
  state.json            当前状态、每一路的回退计数、费用累计（用于断点续跑）
```
网页回放和时间线按 branch id 把记录分到各路（§11）。

---

## 6. 智能体（角色）

每个角色一个文件 `eda_agent/agents/<role>.py` + 提示词 `prompts/<role>.md`。所有角色共享：只能通过工具读取信息；输出必须符合 Schema；必须写 `rationale`（会进文档）。

| 角色 | 输入 | 输出 | 可用工具 | 模型档位 |
|---|---|---|---|---|
| 需求 RequirementAgent | 用户原话、用户记忆 | spec 草案 + 需要用户确认的问题（≤3 个） | memory.read | 强 |
| 方案 ArchitectAgent | spec | 功能块划分 + 3 种明显不同的拓扑（数量是配置项）+ 每种的取舍说明；不再选定一个，谁胜出由 S7b 的程序决定 | datasheet.search, design_memory.search | 强 |
| 选型 SelectorAgent | spec + 方案（一种拓扑）+ **选型策略**（优先用户库存 / 优先 JLC 基础件 / 优先最小面积） | 每块器件清单（按本路策略排序）与额定参数出处 | inventory.query, catalog.search, datasheet.search | 中 |
| 原理图 SchematicAgent | 方案 + 器件 + 上一轮 GateReport | design.json 补丁 | datasheet.search（引脚/典型应用电路） | 强 |
| 诊断 DiagnoseAgent | 仿真/ERC 报告、波形摘要（数值） | 根因假设 + 返回到哪个阶段 + 建议修改 | sim.probe（追加探针重仿真） | 强 |
| 版图 LayoutAgent | 板约束、各网络的电流、自动摆放的结果；布线后再加 DRC 报告和未布通的网络 | board 字段补丁。布线前：线宽（按电流算）、间距、禁布区、电源网络铺铜规则，以及摆放提示（例如某个连接器靠哪一边、哪个器件需要散热或远离热源）。布线后：调整规则、摆放提示或板尺寸，重新布线，次数受 G3 回退上限约束（ADR-0018） | trace_width（IPC-2221，代码计算） | 中 |
| 采购 ProcurementAgent | 胜出方案（用户在 S9 改选时是改选的那一路）的 BOM、库存、目录 | 采购申请（替代料理由、数量含损耗） | catalog.search | 便宜 |
| 文档 DocAgent | 全部产物与 trace | README.md、bringup.md | — | 中 |
| 总审 ReviewAgent | 全部产物、汇合程序算出的名次 | 只做两件事：① 为每一条被淘汰的路写淘汰理由；② **只能新增**"风险/未验证项"。**不能改变名次，无权判定通过** | 全部只读工具 | 强 |
| 调试 BringupAgent | 用户测量值、期望值 | 偏差解释 + 下一步测量或修订补丁 | sim.probe | 强 |

"模型档位"在 `config.yaml` 里映射到具体模型名，不在代码里写死。

**分支阶段的档位**：先不做特殊处理，各角色照上表（用户 2026-11-08：费用先不管；「过 G1 之前用便宜档、过了换强档」的想法记在 ADR-0016）。汇合选优（S7b）不是智能体，是 `orchestrator/swarm.py` 里的确定性程序。

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
- 器件模型来源优先级：ngspice 原生元件（R/L/C/D/BJT/MOSFET 通用模型）→ 厂商 SPICE 模型（`models/`，开启 `set ngbehavior=ltpsa` 兼容）→ 智能体拟合的行为模型。
  - 厂商没有模型，或模型在 ngspice 里跑不通时，由智能体根据数据手册参数生成行为模型（用户 2026-11-08 决定：缺 SPICE 模型不是难点，模型可以由智能体做）。参数必须附页码，沿用 §9「必须附页码」的规则；抽不到的参数不能用。
  - 拟合模型要过一道确定性检查才能用：程序把模型仿真出的典型曲线或关键点，和数据手册给出的数值（同样附页码）对比，误差在阈值内才通过。阈值写在 `config.yaml`，具体数值等做出第一批拟合模型后定（§16）。没通过的模型不用，该器件按 `sim_error`（模型缺失）处理。模型由智能体写，能不能用由程序比对决定，符合硬原则。
  - 报告里把用了拟合模型的器件标为「近似」，单列。
  - 实物回来后，BringupAgent 的测量值可以用来修正模型。
- 结果三态：`pass` / `fail` / `sim_error`（不收敛、超时、模型缺失）。`sim_error` 不算通过，走诊断，但与 `fail` 区分统计。
- 收敛手段按顺序自动尝试：默认 → `.options gmin/reltol 放宽` → `method=gear` → 缩短步长 / 加 `uic`；每次尝试记录在报告里。
- 额外自动测量：所有器件的峰值电压/电流/功耗，回填给 G1 的额定值检查（第二轮起生效）。
- 每条验收标准的实测值、阈值和余量都写进报告，通过的也写；S7b 汇合选优按余量排名（ADR-0016）。

### G3 drc
- `kicad-cli pcb drc --format json --schematic-parity --severity-error --exit-code-violations`
- 额外检查：未布通网络数 = 0；板尺寸 ≤ spec 约束；电源网络线宽 ≥ 按电流估算的最小值（IPC-2221 公式，代码计算；版图智能体布线前也用这段代码定线宽）。

---

## 8. 元器件、库存、采购

- `data/catalog/`：启动脚本下载 JLC 基础/优选件 CSV；可选下载在库件 SQLite（只读文件，用 Python 自带 sqlite3 打开，不起数据库服务）。`parts/catalog.py` 提供 `search(category, params, package, in_stock=True)`。
- `memory/users/<user_id>/inventory.yaml`：用户库存（MPN 或描述 + 数量 + 位置）。可在网页对话里发照片或文字添加，智能体解析后**用户确认才写入**。
- 选型规则：库存满足额定值且封装可装配 → 优先；否则目录件，优先 JLC 基础件 → 优选件 → 扩展件。这是「优先用户库存」这一路的排序；另外两路（优先 JLC 基础件、优先最小面积）换排序，细则 M4 做选型时定（ADR-0016）。三路共同的底线：额定值（含降额）和封装可装配（spec.constraints）都必须满足，策略只决定底线之内怎么排序。
- 采购申请：缺件列表、数量（含损耗）、单价、总价、替代料说明；导出 LCSC/JLC 可上传的 BOM CSV。**审批通过只生成文件和记录，不下单。**

---

## 9. 数据手册检索

- 来源：目录 CSV 里的 datasheet URL；下载到 `data/datasheets/<mpn>.pdf`。
- 解析：PyMuPDF 抽文本和表格 → 按页/段切块；保留页码。
- 向量：embedding 模型部署在自有 GPU 服务器上，`eda_agent/retrieval/` 只通过一个 API 端口调用，地址和模型名写在 `config.yaml`（ADR-0010）。具体模型 M4 用真实数据手册对比后再定（候选如 bge-m3、Qwen3-Embedding 系列，多语种）。本系统用到 GPU 的只有这一件事；开发机没有可用的 GPU。
- 索引：`data/index/<mpn>.npy` + `<mpn>.jsonl`（块文本、页码），检索用 numpy 余弦相似度；全库规模不大，不需要向量数据库。
- 关键参数抽取：选型时由模型从检索到的段落里抽 `ratings`，**必须附页码**；抽不到的参数标为 unknown，G1 对 unknown 的额定值检查报 warning。
- 缺 SPICE 模型时，智能体用同一套检索从数据手册抽参数、生成行为模型，参数同样**必须附页码**（§7 G2）。
- 同一机制索引历史设计（`memory/designs/*/README.md`），用于"类似需求以前怎么做的"。
- 评委网页的部署环境访问不到 GPU 服务器的 API 端口时 → 退化为：预先建好索引随镜像发布，查询向量改走另一个 embedding API（配置开关；查询用的模型必须和建索引用的是同一个），保证评委环境能跑。部署环境能不能访问这个端口，M6 前确认（§16）。

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
- 运行结束，编排器追加 history；用户在对话里说"以后都用 xx"时写入 profile（需用户确认）。
- 这对应赛题第 3 点"长记忆与个性化演进"：演示时展示第二次许愿时系统主动用上库存与焊接偏好。

---

## 11. 交互入口

只做网页对话（用户 2026-11-01 决定，ADR-0015）：不接飞书、Discord 等聊天软件。

### 11.1 网页对话（唯一入口，对应赛题第 4 点「Agent 将软件功能主动推送到对话框」）
- 用户在对话里许愿；需要人的时候，Agent 把对应的功能以卡片推到对话里：spec 确认卡（可编辑字段）、方案对比卡、
  进度卡（状态 + 当前回退次数 + 费用，分路显示）、采购审批卡、交付卡。
  S9 的审批卡片展示胜出方案和它的采购申请，附其余各路的一行摘要和淘汰理由（方案对比卡的内容）；采购只针对胜出方案（ADR-0016）。
  卡片上可以改选另一条全部通过的路；改选后系统为它生成采购申请，再推一张审批卡片（ADR-0016）。
- 需要用户决定的关键选择（例如方案取舍、缺货替代料）以选项的形式出现在这些卡片里，用户点选确认；不另做一套交互。必经的人工节点仍然只有 S1、S9。
- 卡片里的原理图、3D、仿真波形、Gate 报告由自写的渲染引擎展示（用户：纯网页「更好自己写渲染引擎来展示模型成果」）。
- 事件：对话消息 → 新建/继续运行；卡片按钮和选项 → 确认 spec、审批采购、改选方案（只能选全部通过的路）、点选替代料。
  收到的事件先写进运行目录、马上回应，实际工作在后台执行，完成后推送新卡片或更新卡片；对话不被长任务卡住，重启也不丢。
- 技术：FastAPI + SSE 推送事件；前端用原生 JS 或轻量框架，3D 用 three.js 加载 GLB（若 KiCad 无法导出 GLB，退回 STEP→GLB 转换或只展示渲染图，见 M0）。

### 11.2 评委体验（同一个网站）
- 页面：对话 / 运行列表 / 运行详情（时间线：每个智能体发言、每个 Gate 报告、回退箭头；**能分路显示**：按 `trace.jsonl` 里的 branch id 把记录归到各路，共享阶段和汇合步骤单独一段，被终止的路标出终止原因，路与路之间的交流也显示出来，汇合处显示名次和淘汰理由）/ 产物（原理图 SVG：`kicad-cli sch export svg`；3D：渲染图 + 可旋转模型）/ 采购审批。
- **回放模式**：读取已完成运行的 `trace.jsonl` 按时间重放（各路按 branch id 分开重放），无需 API 费用；评委可直接看完整过程。
- **实时模式**：限流（每 IP 每日次数、每次费用上限，上限按全部路合计），防止被刷费用。默认跑几路待定（§16）。

---

## 12. 里程碑（共约 18 周）

每个里程碑结束都要有可运行的演示与测试，且更新本文件的"已验证"标记。

| # | 周 | 内容 | 完成标准 |
|---|---|---|---|
| M0 | 11/02–11/08 | **工具链冒烟（本机 `.tools/` + GitHub Actions，ADR-0001）**：KiCad 10.0.8（自带 ngspice）、fastroute / KiCadRoutingTools、SKiDL、lark-oapi 跑通（lark-oapi 2026-11-01 随飞书一起去掉，ADR-0015）；手写一个 LED+电阻电路走完：IR→原理图→ERC→PCB→布线（DSN→SES 回导，或直接读写 .kicad_pcb）→DRC→Gerber/STEP/GLB/render | `python tasks.py smoke` 全绿。**必须验证并记录**：① 无界面下 DSN 导出与 SES 导入的方法（pcbnew Python API 的 Specctra 导出/导入函数在所选 KiCad 版本是否可用）；② `kicad-cli` 是否支持导出 GLB；③ SKiDL `generate_schematic` 对 30+ 器件电路的可读性；④ 选定 embedding 模型（已推迟到 M4，ADR-0010） |
| M1 | 已完成（2026-11-01；原计划 11/09–11/22） | IR（pydantic + JSON Schema）、JSON Patch 应用器、IR→自写原理图引擎→KiCad 发射器、运行目录与 trace | 10 个手写 IR 样例全部生成可打开的工程 —— **已达到（2026-11-01）**：`examples/` 10 个样例，KiCad 的 ERC、网表一致、原理图与 PCB 一致全过（ADR-0013）；同日又加了分页用的大样例 uno_like_board（ADR-0014） |
| M2 | 11/09–11/29（3 周：M1 提前完成后提前开工，并且比原来多了自动摆放） | G1/G2/G3 全部 Gate；测试台生成器；收敛重试；G2 报告对每条验收标准记实测值、阈值和余量（通过的也记，ADR-0016）；自动摆放（`placement/`，确定性，不调用模型，ADR-0018） | 每条规则有正反单测；5 个手写电路的仿真判定与手算一致；自动摆放在 11 个样例上元件不重叠、不出板、连接器贴边，拿到 li4s_protect 的 design.json 后和手工摆放对比 |
| M3 | 11/30–12/27（4 周；结束日期不变，多出来的一周来自 M1 提前完成） | LLM 抽象、各智能体、编排状态机、回退与预算、断点续跑；CLI：`eda-agent run "需求"`；编排层设计内容的第一版（§3.1）。运行目录和 trace 从一开始就按 `branches/<k>/` 和 branch id 写（单路只有 `branches/0/`）。布线器按配置顺序选，`ci.yml` 的冒烟在 Linux 上加 TraceMaker（ADR-0017）。**单路（1 × 1）跑通后**再做多方案并行：分叉、指纹去重、路间交流、汇合选优（`orchestrator/swarm.py`，ADR-0016） | ① 单路：3 个演示需求（含 ZVS 过流保护器和 li4s_protect）在 CLI 端到端跑出交付物（允许人工节点用命令行确认）。② 并行（3 × 3）：同一个需求分叉、汇合，写出 `ranking.json`；①验收后才开工，12/27 前做不完就挪到 M4 之后、M5 之前补完 |
| M4 | 12/28–01/10 | 目录/库存/采购；数据手册下载+解析+向量检索（向量服务是自有 GPU 服务器上的 API，ADR-0010）；三种选型策略换成真实目录和库存数据；缺 SPICE 模型时由智能体拟合行为模型并过核对检查（§7 G2） | 选型结果都带出处页码；缺件生成采购申请；拟合模型的核对检查有正反单测 |
| M5 | 01/11–01/24 | 网页对话（许愿、spec 确认卡、方案对比卡、采购审批卡、交付卡，关键选择以选项出现）与自写渲染引擎、分路时间线、记忆读写（ADR-0015） | 在网页对话里从许愿到收到交付卡；一次并行运行的各路能在时间线上分开看 |
| M6 | 01/25–02/07 | 网页回放模式、实时模式限流、部署到云服务器；部署镜像里装 TraceMaker 作主用布线器（ADR-0017） | 评委可在网页回放 3 个案例、可实时跑 1 个小案例 |
| M7 | 02/08–02/21 | 评测：自建对照集 + 基线对比 + 报告（含单路串行 vs 九路并行、等预算串行两组对比，§13）；bring-up 流程（S11） | `python tasks.py eval` 一键重跑出表格与图 |
| M8 | 02/22–03/04 | 提交三样东西：产品宣传海报、产品交互演示视频、协创者号平台上的可体验网址（M6 部署，§1.4）；提交材料、修 bug；03/05 前一天提交 | 提交完成 |

进度落后时的处理顺序（实现先后，不是砍产品范围）：先保证 S0–S10 单路主链路在 CLI 与网页可跑；多方案并行（ADR-0016）是我们和别的工具的主要差异（§1.5），单路跑通后优先做，不能挡住单路；对话卡片的编辑能力、bring-up、实物测量仪器接入依次后移。

---

## 13. 评测（证明"质量更高"）

- **对照集**：从 GitHub / OSHWLab 选 20–30 个公开的开源板级项目，覆盖电源、保护、模拟、MCU 外设、电机驱动等；由开发者把每个项目的功能写成需求原话 + 可测验收标准（`eval/tasks/<id>/spec.json`），参考设计只用于写验收标准，不给系统看。
- **对比组**：
  1. 单次生成基线：同一强模型，一次提示直接输出 design.json，不迭代；
  2. 本系统完整流程；
  3. 消融：去掉仿真回退 / 去掉数据手册检索 / 去掉规则检查，各一组；
  4. 多方案并行（ADR-0016）：单路串行（1 × 1）vs 九路并行（3 × 3），比较全部 Gate 通过率、最终指标、费用、耗时；再加一组等预算对比：串行流程用同样的总预算，多次重试。
- **指标**：G1/G2/G3 各自通过率、全部通过率、`sim_error` 比例、平均迭代次数、平均费用与耗时、**每次全部通过所需的 token 数和费用**、额定值违规数。平均迭代次数和每次全部通过的 token 数、费用用来衡量编排质量（§3.1）。
- **与已有评测的关系**：LG 的 PCBWorld（§1.5）已有布线评测，我们的对照集面向「需求到可打板」全流程，两者不重叠。
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
├── config.example.yaml       模型档位映射、费用上限、回退上限、路数与选型策略、排名权重、布线器顺序、路径
├── schemas/                  spec / design / gate_report / trace 的 JSON Schema
├── prompts/                  每个角色一个 .md
├── eda_agent/
│   ├── orchestrator/         state machine, run manager, budget, resume；swarm.py（规划中：分叉、指纹去重、路间交流、汇合选优，ADR-0016）
│   ├── agents/               各角色
│   ├── llm/                  provider 抽象、重试、结构化输出、计费
│   ├── ir/                   pydantic 模型、patch 应用、校验
│   ├── emit/                 IR→自写原理图引擎→kicad_sch / netlist / kicad_pcb；IR→SPICE
│   ├── kicad_side/           在 KiCad 自带 Python 里运行、调用 pcbnew 的脚本（建板、Specctra 导出导回）
│   ├── placement/            规划中：自动摆放（按功能块分区、按连接关系靠拢、HPWL / 重叠 / 边距打分、迭代优化，不调用模型，ADR-0018）
│   ├── gates/                static(rules/), sim, drc；model_check.py（规划中：拟合 SPICE 模型的核对，§7 G2）
│   ├── tools/                kicad_cli, 布线器, ngspice, sandbox（外部工具环境）, runner（统一超时与 trace）
│   ├── parts/                catalog, inventory, procurement
│   ├── retrieval/            datasheet 下载/解析、embed client、numpy 检索
│   ├── memory/               profile / history / design memory 读写
│   ├── gateway/              网页对话的消息与审批事件 → RunManager
│   └── web/                  FastAPI + static 前端（对话、自写渲染引擎）
├── embed_server/             自有 GPU 服务器上的 embedding 服务（本系统只通过 API 端口调用，ADR-0010；要不要放进本仓库，M4 再定）
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
| SKiDL 自动生成的原理图可读性差 | 已换成自写版面引擎（ADR-0011、ADR-0013），按用户看图后指出的毛病改过一轮（ADR-0014）；好不好看仍要人看，机器只查重叠、压字、穿线、线穿过文字、出图纸和连接 |
| 自动布线质量（含功率/大电流板）。用户 2026-11-08 的判断：这不是项目的难点，布线器之外有智能体兜底 | 布线前：自动摆放让连得多的元件靠拢、信号按流向排（ADR-0018）；版图智能体按电流算线宽（IPC-2221，代码计算）、设间距、禁布区、电源网络铺铜规则，并给摆放提示。布线后：版图智能体读 DRC 报告和未布通的网络，调整规则、摆放提示或板尺寸后重新布线，次数受 G3 回退上限约束。仍不承诺高速/射频/高密度板（§1.3）；文档如实写"建议人工复核的网络" |
| 缺厂商 SPICE 模型，或模型在 ngspice 里跑不通。用户 2026-11-08 的判断：这不是难点，模型可以由智能体做 | 智能体按数据手册参数生成行为模型，参数附页码；程序把模型仿真出的典型曲线或关键点与数据手册数值对比，误差在阈值内才能用；报告里标"近似"；实物回来后用 BringupAgent 的测量值修正（§7 G2）。能跑的厂商模型仍用兼容模式 `set ngbehavior=ltpsa`；收集常用器件模型到 `models/` |
| 仿真不收敛（尤其自激振荡电路） | 自动重试策略；`sim_error` 单独统计，不当作通过 |
| 模型自我评价造成"假通过" | 架构上禁止：只有 Gate 能写 passed；多方案的名次也只由程序算，总审智能体不能改（ADR-0016） |
| API 费用失控；多路并行使成本放大 | 档位分级、每次运行上限（多路按全部合计）、网页实时模式限流、回放模式；共享阶段（S0–S2）只跑一次。并行专门的费用控制（分支阶段过 G1 前用便宜模型、超预算按排名终止路）先不做，用户 2026-11-08：费用先不管（§4、ADR-0016） |
| 评委不是电子专家 | 网页时间线把"失败→诊断→修改→通过"可视化；海报只放一张前后对比图与一个数字 |
| 赛题第 4 点举例的是飞书、Discord，只做网页对话可能被看作没接现成聊天软件（ADR-0015） | 演示和海报里突出「Agent 把软件功能主动推送到对话框」：spec 确认、方案对比、审批、交付都以卡片出现在网页对话里 |
| 实物验证周期：每轮打板、实测要数天，还有打板费用；仿真通过不等于实物通过 | 仿真给出每一步的期望读数，用 bring-up 流程（S11、`bringup.md`）比对；多个设计合并到同一批打板 |
| 编排效率：同一个模型能发挥多少，取决于智能体循环本身的质量 | 把上下文取舍、工具调用时机、失败回退、重复调用的缓存当作设计内容（§3.1）；用「每次全部通过所需的 token 数和费用、平均迭代次数」衡量（§13） |
| 主用布线器 TraceMaker 只有 Linux 版，本机 Windows 跑不了（ADR-0017） | 本机开发和冒烟用 fastroute（备用 KiCadRoutingTools）；TraceMaker 在 Linux 的 GitHub Actions 和部署镜像里跑；评测和演示案例的运行放在 Linux 上做，本机结果只用于开发。布线器按配置顺序选第一个可用的，trace 里记下实际用了哪个 |
| 一人开发进度 | 里程碑每两周可演示；所有外部工具在 M0 一次性打通 |

---

## 16. 待验证清单（实现前/中逐项勾掉）

- [x] 所选 KiCad 版本下，无界面 DSN 导出 / SES 导入的可用方法 —— KiCad 10.0.8 用 `pcbnew.ExportSpecctraDSN` / `ImportSpecctraSES`，LED 板实测导回后 DRC 0 错误（ADR-0004）
- [x] `kicad-cli` 是否可导出 GLB（否则 STEP→GLB 转换方案）—— `pcb export glb` 可用，冒烟已生成 board.glb（ADR-0002）
- [x] SKiDL `generate_schematic` 在 KiCad 9/10 下对中等规模电路的效果 —— 替代做法见 ADR-0011（自写版面引擎样张：机器检查全过，插座页导线总长约为原图的 1/8；用户看过：「还不错，先这样」，完整版未批准）。SKiDL 本身实测不能直接用：默认模式只适合很小的电路；`auto_stub` 下 50 个元件的单页会把大部分符号甩出图纸；只有 labels 模式能放下 63 个元件；不连线的元件会让它崩溃（ADR-0009）。替代做法待定
- [x] kinet2pcb 与所选 KiCad 版本的 `pcbnew` 模块兼容性 —— Windows 上 import 即报错，改用自写建板脚本（ADR-0007）
- [x] 布线器在 30 个以上元件的板子上的对比 —— 5 块 KiCad 示例板 × 4 个布线器（ADR-0004）：规则一致的前三块上 fastroute 与 Freerouting 错误数相当、快 1.2–7.6 倍；kct 的 C++ 版 4 块板超时；两块大板的错误主要是规则没传进布线器，不能排名。用户当时决定：fastroute 主用 + KiCadRoutingTools 备用；是否继续找更强的仍开放。（后续：2026-11-01 云端测了 TraceMaker；2026-11-08 用户决定 TraceMaker 主用，fastroute、KiCadRoutingTools 备用，已被 ADR-0017 取代）
- [x] Linux 上的 Actions 验证（ADR-0001、ADR-0012；M0 的最后一项，也是测 TraceMaker 的前提）—— github.com/EriDeLee/HuacanCUP 上 lint、单元测试、工具测试、冒烟全过；提速后缓存命中 1 分 45 秒、没命中 2 分 17 秒（原来 4 分 43 秒）
- [ ] embedding 模型与接口格式（自有 GPU 服务器只暴露一个 API 端口）—— 推迟到 M4，用真实数据手册对比后再定（ADR-0010）
- [ ] 评委网页的部署环境能不能访问向量服务的 API 端口（不能时用 §9 的退路）
- [ ] 协创者号平台的提交要求：海报、演示视频、可体验网址的格式；项目展示网站的提交格式（仓库 + Demo URL 的具体要求）
- [ ] HWE-Bench 作者回复
- [ ] TraceMaker 在我们自己生成的板子上的布线结果（`examples/` 的 11 个样例，含 uno_like_board）；Linux CI 的冒烟里接入它（ADR-0017）
- [ ] 换布线器、版图智能体调整后重布，这两层回退怎么嵌套（M3，ADR-0017、ADR-0018）
- [ ] 多方案并行（ADR-0016）：余量怎么归一、四项指标怎么合成一个分数；板面积怎么算（现在 `board.size_mm` 是输入）；指纹算法；`prefer_inventory`、`budget_cny` 和选型策略、排名怎么衔接；并行专门的费用控制（先不管，以后再定）；实时模式默认跑几路、费用上限多少
- [ ] 路与路之间的交流（ADR-0016，轻量版）：消息的字段、哪些事实该写进记事板、各路读多少（§3.1 上下文取舍）、和「防止各路趋同」怎么共存；评测要不要加「有交流 / 无交流」一组；以后要不要加评审、协调员、借用子电路、拼混合路
- [ ] 自动摆放（ADR-0018）：~~迭代算法和分数权重；种子的用法~~ 已在 M2 定下（模拟退火加三档合法化、分数五项、一路默认一个种子，review-queue D32–D36）；11 个样例上元件不重叠、不出板、连接器贴边、auto 不比 rows 差，已验证（`eval/m2_placement.py`）。还没定：li4s_protect 的 design.json（等用户提供）；和手工摆放比哪些指标、比到什么程度算过；IR 的 `board` 里摆放提示的字段；auto 多出来的丝印警告怎么处理（review-queue P2）
- [ ] 拟合 SPICE 模型核对用的误差阈值（§7 G2）；等做出第一批拟合模型后定
- ~~飞书自建应用创建与卡片回调权限~~ —— 不做飞书（ADR-0015）
