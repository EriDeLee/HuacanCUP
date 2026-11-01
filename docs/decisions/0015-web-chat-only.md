# ADR-0015 交互入口：只做网页对话，不接飞书和 Discord

- 状态：已采纳（2026-11-01，用户决定）
- 影响：PLAN §1.2、§1.3、§2、§3、§4、§8、§10、§11、§12（M0 备注、M5、M6、进度落后时的顺序）、§14、§15、§16；
  `requirements.txt`（删掉 lark-oapi）

## 决定

用户原话：「不过我不打算用飞书啊，我打算纯网页对话，这样更好自己写渲染引擎来展示模型成果。」
随后在选项「纯网页，再拿掉飞书包」上确认。

- 唯一的交互入口是我们自己的网页对话：用户在对话里许愿、确认 spec、看方案对比、审批采购、收交付。
  需要人的时候，Agent 把对应的功能以卡片推到对话里；原理图、3D、仿真波形、Gate 报告由自写的渲染引擎在网页里展示。
- 飞书（原来的主入口）和 Discord（原来的可选第二入口）都不做。「纯网页」理解为两个都不做；要恢复 Discord 再改本文。
- `lark-oapi` 从 `requirements.txt` 删掉，本机 venv 里一并卸掉它和只为它装的包（见「依赖」）。

## 赛题第 4 点

PLAN 原来写飞书「对应赛题第 4 点」。第 4 点原文（设计竞赛网转载的赛题页，单一来源，官网原文没找到：
https://www.shejijingsai.com/2026/09/1642560.html ）：

> 4. 人机交互界面重构：过去是"人去找软件界面"，现在趋势是Agent将软件功能主动推送到对话框（如飞书、Discord）。

- 它是「鼓励参赛者探索和创造……包括：」下面列的四个方向之一，不是硬性要求。
- 网页对话框也是对话框：Agent 把原理图、3D、审批卡片这些功能主动推到对话里，就是这一条说的意思。
- 风险：举例写的是飞书和 Discord，评委会不会更看重接入现成的聊天软件，查证不了。
  对策写进 PLAN §15：演示和海报里突出「Agent 把软件功能主动推送到对话框」。
- 同一页「03 创作总体要求」本来就要求交一个「供评审交互体验产品网址链接」，网页对话就是它。

## 去掉的

- 飞书长连接、企业自建应用、卡片回调「3 秒内必须返回」的限制，以及 M5 里飞书卡片和 Discord 的工作量。
- 对应的风险（「飞书长连接仅限企业自建应用」）和待验证项（「飞书自建应用创建与卡片回调权限」）。

## 没变的

- 两个必经的人工节点（S1 确认 spec、S9 批准采购）照旧，只是从飞书卡片换成网页对话里的卡片。
- 收到的事件先放进队列、马上回应、实际工作在后台做，这条照旧（不再是为了 3 秒限制，而是为了对话不卡、重启不丢）。
- 网页的回放模式、实时模式限流、FastAPI + SSE 推送照旧。

## 依赖

- `requirements.txt` 删掉 `lark-oapi==1.7.5`。
- 本机 venv 卸掉 lark-oapi 1.7.5，以及只有它用到的 12 个包：anyio 4.15.1、certifi 2026.10.23、charset-normalizer 3.5.2、
  h11 0.16.0、httpcore 1.0.9、httpx 0.28.1、idna 3.20、pycryptodome 4.2.0、requests 2.34.2、requests-toolbelt 1.0.0、
  urllib3 2.8.0、websockets 15.0.1。依赖关系用 `importlib.metadata` 查过：venv 里别的包都不依赖它们，我们的代码也不 import 它们。
  CI 每次按 `requirements*.txt` 新建 venv，本来就只装直接依赖和它们需要的包，卸掉这些让本机和 CI 一致。
- 卸完后 `site-packages\idna`、`site-packages\anyio` 两个文件夹里只剩 `.pyc` 缓存（pip 没删）。留着的话 Python 会把空的
  `idna` 文件夹当成一个包导入，jsonschema 以为装了 idna，导入时报 `module 'idna' has no attribute 'IDNAError'`，单元测试起不来。
  这两个文件夹已删（查过里面没有 `.pyc` 以外的文件）。`pip check` 无问题。
- 将来网页后端（FastAPI）会用到其中几个（如 anyio、h11），到时按 ADR-0005 把 FastAPI 写进 requirements，由 pip 一起装上。
- ADR-0012 里「lark-oapi 占 1.07 万个文件」是那次测时的历史记录，不改。

## 撤回

把 `lark-oapi==1.7.5` 加回 `requirements.txt`，在 venv 里 `pip install -r requirements-dev.txt`；
PLAN 里飞书相关段落按本文之前的版本（提交 6525c6d）恢复。
