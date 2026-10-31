# ADR-0004 自动布线：Freerouting 与 KiCadRoutingTools 实测后二选一

- 状态：实测完成，等用户选（2026-10-31）
- 影响：PLAN §2「布线」、§12 M0、§16

## 决定（用户）

M0 里两个都装，用同样的板子比「没连上的线」和「DRC 报错」数量，留下更好的那个。

| | Freerouting | KiCadRoutingTools（KRT） |
|---|---|---|
| 版本 | 2.5.0（2026-10-05），GPL-3.0 | v0.23.7（2026-10-23），MIT |
| 输入输出 | Specctra .dsn → .ses | 直接读写 .kicad_pcb |
| 运行环境 | Java 版：`freerouting-2.5.0.jar`（SHA-256 `f6f51bb0…e3c7`）+ Temurin JRE 25.0.5（`da78d54a…beae`），均与官方公布值一致 | `.tools/venv-krt`（Python 3.14 + numpy、scipy、shapely、Pillow）+ 预编译模块 `grid_router.pyd`（`68bf6b96…ed3d`） |
| 必须的参数 | `--gui.enabled=false`、`-da`（关匿名统计）、`--user_data_path` 指向沙箱；JVM 加 `-Duser.home`、`-Dstdout.encoding=UTF-8` | `--no-fix-drc-settings`：否则它会把工程里的 DRC 下限放宽到布出来的尺寸，等于自己降低 DRC 关的门槛 |

## Freerouting 2.5.0 免 Java 版不能用（已删除）

- 现象：只要 DSN 里有元件外框（KiCad 导出的都有），布线线程就抛
  `UnsupportedFeatureError: SerializationConstructorAccessor class not found ... ComponentOutline`
  （GraalVM 打包漏了序列化配置），之后主进程一直挂着。LED 板上挂了 15 分钟，超时后先发 CTRL_BREAK 无响应，再强制结束。
- 对照：删掉 DSN 里 30 处元件外框后，0.05 秒布完，导回 KiCad 后 DRC 0 错误。
- 用户决定改用 Java 版；免 Java 版的压缩包和 exe 经用户同意已删除。

## M0 待验证项①：无界面 DSN 导出 / SES 导回（已验证）

KiCad 10.0.8 的 kicad-cli 没有这两个命令。用 KiCad 自带 Python 调
`pcbnew.ExportSpecctraDSN(board, path)`、`pcbnew.ImportSpecctraSES(board, path)`（`eda_agent/kicad_side/specctra.py`），
LED 板和 pic_programmer 都能导出、导回。

## 实测记录

| 板子 | Freerouting 2.5.0（Java 版） | KRT v0.23.7 |
|---|---|---|
| LED（3 个元件、3 个网络，`smoke-led-20261031-163913`） | 全部连通，DRC 0 错误 | 全部连通，DRC 0 错误 |
| pic_programmer（KiCad 示例，63 个元件、两层；删掉原有 376 段走线/过孔和 1 块铺铜，元件不动，`m0-pic-20261031-161138`） | 14.6 秒；全部连通；0 个过孔、412 段走线；DRC 2 个错误：一根 `/DATA-RB7` 走线压在铜层文字「1=>>」上（短路 + 间距各 1） | 50.4 秒；全部连通；33 个过孔、472 段走线；DRC 6 个错误，都是丝印重叠：它把板上 4 段铜层文字挪到了丝印层 |
| 同一块板的原始人工布线（参照） | DRC 0 错误；370 段走线、6 个过孔 | |

两边的问题都出在「铜层文字」上：

- KiCad 导出 DSN 时不把铜层文字当障碍交给 Freerouting，于是它直接压了过去。
- KRT 的输出代码无条件执行 `move_copper_text_to_silkscreen` / `move_copper_graphics_to_silkscreen`
  （`py_router/kicad_writer.py`），把铜层文字和图形挪到丝印层来腾地方——这改动了布线以外的设计内容。

我们自己从 IR 生成的板子目前不放铜层文字，所以这两个问题在 LED 冒烟里都没出现。证据只有 2 块板。
