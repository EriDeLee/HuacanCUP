# AGENTS.md — 给编码智能体的工作规则

本仓库实现「基于AI智能体的电子电路全流程自动化设计系统」。**完整设计在 `docs/PLAN.md`，动手前先读完。** 本文件只写实现时必须遵守的规则。

## 不可违反的原则
1. **判定只来自确定性代码。** 任何 `passed=True` 只能由 `eda_agent/gates/` 中的代码写出。智能体（LLM）的输出永远不能直接或间接把 Gate 置为通过。代码评审时发现违反即视为 bug。
2. **spec 冻结后不可被智能体修改。** 修改必须回到人工确认节点。
3. **只用文件存储。** 不引入 Postgres/MySQL/Redis/向量数据库等服务。允许以只读方式打开下载来的 SQLite 数据文件（元器件目录）。
4. **模型全部走 API，本地 GPU 只跑 embedding。** 不在本地部署对话/推理大模型。
5. **不自动付款下单。** 采购流程止于生成文件和审批记录。
6. **不针对某一类电路写特化逻辑**（例如只为 ZVS 写的规则）。规则必须是通用电气规则。
7. 不使用 OpenClaw；不引入 LangGraph/CrewAI 等编排框架。编排是 `eda_agent/orchestrator/` 中的自写状态机。

## 工程约定
- Python 3.11+，依赖用 `uv` 管理；类型注解 + pydantic v2；格式化 `ruff format`，检查 `ruff check`，类型 `mypy`（核心包严格模式）。
- 外部工具（kicad-cli、ngspice、java/Freerouting）一律经 `eda_agent/tools/` 的封装调用：统一超时、捕获 stdout/stderr、把完整命令行写入 trace。不要在别处直接 `subprocess`。
- 所有 LLM 调用经 `eda_agent/llm/`：结构化输出（JSON Schema 校验失败自动重试 ≤3 次）、记录 token 与费用、可按配置切换供应商。模型名只出现在 `config.yaml`。
- 智能体只返回结构化结果（多为 JSON Patch），不直接写文件。
- 每个运行的所有事件追加写入 `runs/<id>/trace.jsonl`，格式见 `schemas/trace.schema.json`。网页回放完全依赖它，字段变更要同步更新回放代码。
- 密钥只从环境变量读取（`.env` 不入库），提供 `.env.example`。

## 测试
- `make test`：单元测试不调用真实 LLM（用录制的响应 fixture）；需要 KiCad/ngspice 的测试打 `@pytest.mark.tools` 标记，在 Docker 内运行。
- 每条电气规则必须有至少一个"应报错"和一个"不应报错"的用例。
- 新增 Gate 或改动判定逻辑，必须附带 `examples/` 中手写电路的回归结果。
- `make smoke`：手写 LED 电路走完整链路（IR→原理图→ERC→PCB→布线→DRC→Gerber/STEP/渲染）。任何改动后 smoke 必须保持通过。

## 工作方式
- 按 `docs/PLAN.md` §12 的里程碑顺序推进，先做 M0；M0 的验证结论写进 `docs/decisions/` 并更新 PLAN.md §16 勾选状态。
- 发现 PLAN.md 中的接口/命令与实际工具行为不符时：先写 ADR 说明现象与改法，再改 PLAN.md，再改代码。不要静默绕过。
- 小步提交，提交信息说明"做了什么 + 如何验证"。
- 不确定某个外部工具的参数时，运行其 `--help` 或查官方文档确认，不要凭记忆写。
