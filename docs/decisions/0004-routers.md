# ADR-0004 自动布线：Freerouting 与 KiCadRoutingTools 实测后二选一

- 状态：进行中（2026-10-31）
- 影响：PLAN §2「布线」、§12 M0、§16 第 1 项

## 决定（用户）

M0 里两个都装，用同样的板子比「没连上的线」和「DRC 报错」数量，留下更好的那个。

| | Freerouting | KiCadRoutingTools |
|---|---|---|
| 版本 | 2.5.0（2026-10-05），GPL-3.0 | v0.23.7（2026-10-23），MIT |
| 输入输出 | Specctra .dsn → .ses | 直接读写 .kicad_pcb |
| 运行环境 | Java 版：freerouting-2.5.0.jar + Temurin JRE 25（待装，见下） | `.tools/venv-krt`（Python 3.14 + numpy、scipy、shapely、Pillow）+ 预编译模块 `grid_router.pyd`（SHA-256 `68bf6b96…ed3d`，与 GitHub 公布一致） |
| 必须的参数 | 关闭匿名统计（`.tools/home` 里 `freerouting.json` 的 `disable_analytics: true`） | `--no-fix-drc-settings`：否则它会把工程里的 DRC 下限放宽到布出来的尺寸，等于自己降低 DRC 关的门槛 |

## Freerouting 2.5.0 免 Java 版不能用

- 现象：只要 DSN 里有元件外框（KiCad 导出的都有），布线线程就抛出
  `UnsupportedFeatureError: SerializationConstructorAccessor class not found ... ComponentOutline`
  （GraalVM 打包时漏了序列化配置），之后主进程一直挂着不退出。
  LED 板上挂了 15 分钟，到超时后先发 CTRL_BREAK 无响应，再强制结束。
- 对照：删掉 DSN 里 30 处元件外框后，同一块板 0.05 秒布完，0 条没连上、0 处违规；
  导回 KiCad 后 DRC 0 错误。
- 用户决定：改用 Java 版（需要 Temurin JRE 25）。

## M0 待验证项①：无界面 DSN 导出 / SES 导回（已验证）

KiCad 10.0.8 的 kicad-cli 没有这两个命令。用 KiCad 自带 Python 调
`pcbnew.ExportSpecctraDSN(board, path)` 和 `pcbnew.ImportSpecctraSES(board, path)`
（`eda_agent/kicad_side/specctra.py`）：LED 板导出 DSN、导回 Freerouting 的 SES 后 DRC 0 错误。

## 实测记录

| 板子 | Freerouting | KiCadRoutingTools |
|---|---|---|
| LED（3 个元件、3 个网络） | 免 Java 版崩溃；删外框后布通，DRC 0 错误；Java 版待测 | 布通，DRC 0 违规、0 未连接、0 一致性错误 |
| 30 个以上元件的板 | 待测 | 待测 |
