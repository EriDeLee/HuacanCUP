# ADR-0004 自动布线：候选工具实测

- 状态：已采纳（2026-10-31，用户决定）：**fastroute 主用，KiCadRoutingTools 备用**；去掉 Freerouting 和 kicad-tools。
  2026-11-01 用户看过 TraceMaker 云端结果后补充决定：**部署（Linux）时 TraceMaker 主用，本机 Windows 照旧 fastroute + KRT**。
  部署在 M6 才做，到时把 TraceMaker 装进部署镜像、接进布线流程；现在只记下决定，代码没改
- 影响：PLAN §2「布线」、§12 M0、§16；`eda_agent/tools/routers.py`、`config.py`、`smoke.py`

## 最终决定

1. 先用 fastroute（Specctra DSN → SES，单个 2 MB 的 exe，不需要 Java，五块板上比 Freerouting 快 1.2–7.6 倍）。
2. 它的 DRC 不通过或出错时，换 KiCadRoutingTools（直接读写 .kicad_pcb），原因见下面「第三方评测」：
   换一个布线器比重跑同一个更有效。这个回退逻辑到 M2 的编排器里实现；冒烟现在是两个都跑，
   制造文件用 fastroute 的结果，fastroute 的 DRC 不过冒烟就失败。
3. Freerouting（需要 Java 25）和 kicad-tools（C++ 版 4/5 块板超时）从代码和配置里删除，
   适配代码留在提交 aeee649，实测成绩留在本文。`.tools/` 里的 Freerouting（62 MB）、JRE 25（179 MB）、
   venv-kct（277 MB）本地文件，用户决定留着（2026-10-31）；它们已不在配置里，不会被代码调用。
4. 仍然开放（用户说「再找找更顶级的」）：TraceMaker 已在云端测完（2026-11-01，见「TraceMaker 云端测试」），
   两块大板上明显更好，但只有 Linux 版；用户 2026-11-01 决定部署（Linux）时它主用、本机照旧（见本文开头）。其余开放项：
   托管服务（Zap!、Koppar）要上传设计；两块大板要把板子的最小线宽、过孔传给每个布线器后重测。
   我们自己生成的板子规则是一致的，不会有大板上那种「规则没传进布线器」的问题，但 M1 加网络类以后要验证 fastroute 收得到。
   （2026-11-01 更正：板边间距就没传进 fastroute，见文末「板边间距没传给 fastroute」。）

## 决定（用户）

M0 里把候选布线器装好，用同样的板子比「没连上的线」和「DRC 报错」，再选。候选在实测中从 2 个扩到 4 个：
Freerouting、KiCadRoutingTools（KRT）、fastroute、kicad-tools（kct）。

| | Freerouting | KRT | fastroute | kct |
|---|---|---|---|---|
| 版本 | 2.5.0（2026-10-05），GPL-3.0 | v0.23.7（2026-10-23），MIT | 0.1.14（2026-10-13，仓库 2026-09-29 创建），GPL-3.0 | 0.26.0（2026-10-30，PyPI） |
| 输入输出 | Specctra .dsn → .ses | 直接读写 .kicad_pcb | Specctra .dsn → .ses，命令行与 Freerouting 相同 | 直接读写 .kicad_pcb |
| 运行环境 | Java 版：`freerouting-2.5.0.jar`（SHA-256 `f6f51bb0…e3c7`）+ Temurin JRE 25.0.5（`da78d54a…beae`），均与官方公布值一致 | `.tools/venv-krt`（Python 3.14 + numpy、scipy、shapely、Pillow）+ 预编译模块 `grid_router.pyd`（`68bf6b96…ed3d`） | 单个 `fastroute.exe`（2 MB，`a017ebc3…9913`，与官方 SHA256SUMS 一致），不需要 Java | `.tools/venv-kct`；C++ 后端要自己编译（见下） |
| 必须的参数 | `--gui.enabled=false`、`-da`（关匿名统计）、`--user_data_path` 指向沙箱；JVM 加 `-Duser.home`、`-Dstdout.encoding=UTF-8` | `--no-fix-drc-settings`：否则它会把工程里的 DRC 下限放宽到布出来的尺寸，等于自己降低 DRC 关的门槛 | `--max-time` 到点自己写出当前最好结果 | 关掉自动铺铜、自动加层、挪元件（`--no-auto-pour --no-auto-layers --no-placement-feedback --no-placement-delta-feedback`） |

## 方法

`eval/router_bakeoff.py`：取 KiCad 自带的 5 块示例板，删掉全部走线、过孔、铺铜，元件位置不动，
每个布线器各布一次，再用 KiCad DRC（含原理图一致性）打分。「错误数」= DRC 错误 + 没连上的线。
原板的 DRC 结果作为参照。外层超时 900 秒。运行目录：`runs/bakeoff-20261031-165306`（Freerouting、KRT）、
`-172224`、`-172959`（fastroute 与 kct 纯 Python 版）、`-184717`（kct C++ 版）。

## 结果（错误数 / 用时）

| 板子（元件数） | 原板 | Freerouting | KRT | fastroute | kct（C++ 后端） |
|---|---|---|---|---|---|
| ecc83（15） | 0 | 0 / 7.0 s | 0 / 4.6 s | 0 / 1.2 s | 0 / 56.8 s |
| interf_u（25） | 3 | 0 / 97.9 s | 超时（900 s） | 0 / 81.4 s | 超时（879 s） |
| pic_programmer（63） | 0 | 2 / 15.1 s | 6 / 52.7 s | 3 / 2.2 s | 超时（878 s） |
| StickHub（94） | 0 | 134 / 227 s | 16 / 99.8 s | 105 / 162 s | 超时（878 s） |
| multichannel（114） | 12 | 495 / 84.1 s | 271 / 46.9 s | 454 / 11.0 s | 超时（878 s） |

kct 纯 Python 后端在 ecc83 上 359 s，1 条没连上。kct 的 C++ 后端超时的 4 块板，日志里反复出现
`C++ pathfinder gave up … falling back to the pure-Python A*`；超时后它写出的是未验证的部分结果（退出码 124），不算布通。

### 错误类型（`.tools/audit/drc_breakdown.py` 读已保存的 DRC 报告）

- pic_programmer：Freerouting 2 个、fastroute 3 个，都是 `clearance`（走线压在铜层文字「1=>>」上，KiCad 导出 DSN 时不把铜层文字当障碍）。
  KRT 6 个 `silk_overlap`：它的输出代码无条件执行 `move_copper_text_to_silkscreen`（`py_router/kicad_writer.py`），
  把铜层文字挪到丝印层，改动了布线以外的设计内容。
- StickHub：Freerouting 126 个 `track_width`（实际 0.075 mm，板规定最小 0.15 mm）+ 5 条没连上 + 3 个 `solder_mask_bridge`；
  fastroute 101 个 `track_width`（实际 0.1124 mm）+ 4 条没连上；KRT 9 个 `solder_mask_bridge` + 7 条没连上，没有线宽错误。
- multichannel：三个布线器都有「线宽 0.2 mm < 板规定最小 0.3 mm」「过孔直径 0.6 mm < 最小 1.5 mm」「孔环 0.15 mm < 0.3 mm」和间距错误；
  原板自己就有 12 个间距错误（0.2 mm < 0.3 mm）。
- 所以 StickHub 与 multichannel 的错误数主要反映布线器没拿到板子的最小线宽和过孔尺寸，**不能用来给布线器排名**。
  这是推测，没有逐条核实原因。公平比较要把最小值传给每个布线器后重测（未做）。
- 前三块板的原板基本没错误，可以比较：fastroute 与 Freerouting 错误数相当（0、0、3 对 0、0、2），
  fastroute 在五块板上比 Freerouting 快 1.2–7.6 倍。

## kct 的 C++ 后端怎么编译出来的

PyPI 上的 kct 只有纯 Python 包；C++ 后端要在本机用 cmake + 编译器编译。本机没有 MSVC，用用户已装的 WinLibs MinGW-w64 g++ 16.2.0。
`kct build-native` 原样跑会失败，下面三处各自有证据，脚本是 `.tools/build-kct-native.cmd`：

1. **头文件路径 260 字符。** g++ 的默认头文件搜索路径带 `..`，在这个很长的安装路径下，
   `…/bin/../lib/gcc/x86_64-w64-mingw32/16.2.0/../../../../include/c++/16.2.0/x86_64-w64-mingw32/bits/messages_members.h`
   恰好 260 个字符，超过 Windows 的 MAX_PATH（可用 259），报 `No such file or directory`，而文件本身存在且可读。
   规范化后的路径只有 207 个字符。用 `CPLUS_INCLUDE_PATH` 把不带 `..` 的同样目录排到前面，编译就通过。
   （证据：`#include <locale>` 的最小文件，改前报错、改后通过；换成 8.3 短路径无效，因为 g++ 会自己展开成长路径。）
2. **nanobind 的 `-Os` 与 GCC 16.2。** nanobind 给 Release 构建加 `-Os`；GCC 16.2 在 `-Os` 下生成对 `std::string` 统一构造函数 `C4` 的引用，
   `libstdc++.a` 里没有 `C4`，链接报 `undefined reference to basic_string(basic_string&&)`。
   用 10 行测试文件逐个旗标验证，只有 `-Os` 能复现；加 `-fno-declone-ctor-dtor` 无效。
   改用 `CMAKE_BUILD_TYPE=None`（kct 的 CMakeLists 自带 `-O2`，另加 `-DNDEBUG`），不再有 `-Os`。
3. **运行库。** Python 3.8 以后加载扩展模块不查 PATH，所以用 `LDFLAGS=-static` 把 MinGW 运行库链进 `.pyd`。

编译产物 `router_cpp.cp314-win_amd64.pyd`（4.2 MB）放在 `.tools/venv-kct`，`kct build-native --check` 显示 C++ 后端可用（build version 65）。

## 其余候选

- **TraceMaker** v0.12.1（GPL-3.0-or-later，C++）：只发布 Linux x86_64 包，本机跑不了，在 GitHub Actions 上测（用户 2026-10-31 选定），见下节。
- **fab_router**：fabPlane 自己的路由器，只在它的桌面程序里，不能批量测，已排除。
- **托管服务 Zap!、Koppar、DeepPCB、Quilter**：要注册账号并上传设计，未测，需要用户批准。

## TraceMaker 云端测试（2026-11-01）

- 工作流 `.github/workflows/bakeoff.yml`：三台 `ubuntu-24.04` 机器同时跑，各测一个布线器（fastroute、KRT、TraceMaker），
  板子、删线方法、打分方法都和上面本机测的一样（`eval/router_bakeoff.py`）。fastroute 和 KRT 也在云端重跑，
  是为了三者的用时在同一种机器上可比；它们本机的成绩照旧留在上面的表里。
- 只在改了这个工作流或对比脚本时自动运行，也能在 Actions 页面手动运行；外人的拉取请求不能触发它。
- 安装：发布包 `tracemaker-0.12.1-linux-x86_64.tar.gz`，SHA-256 `d46e399f…8749`，与 GitHub 发布页记录的一致
  （本机下载后用 certutil 核对过，并看过包里的文件清单：3 个程序、查看器网页、一块示例板、说明和许可证）。
  它说明里写的要求是 glibc 2.38 以上、不需要 CUDA，Ubuntu 24.04 满足。
- 命令：`tracemaker route 板.kicad_pcb -o 输出.kicad_pcb --json 摘要.json --no-gpu --no-kb`
  （`eda_agent/tools/routers.py` 的 `tracemaker_route`）。参数按官方 `docs/reference/cli.md`（v0.12.1）核对：
  - `--no-gpu`：云端机器没有显卡；文档写明结果与用显卡相同。
  - `--no-kb`：不读写它跨运行的「失败记忆」，每块板都从零开始，结果不受前一块板影响。
  - 时间：用它的默认值，不加 `--time`、`--work`。文档写的默认做法是每个变体 120 秒，没布完再用 `--relax` 各重布最多 120 秒。
    fastroute 我们给了 870 秒、KRT 不限（外层都是 900 秒），所以这是「各用各的出厂默认」的比较，不是同样时间预算的比较。
  - 退出码 0 = 全部布通，3 = 有没布通的但照样写出结果；两种都交给 KiCad DRC 打分，其他退出码算出错。
- 结果从运行摘要和注释读（不用登录），写进下表。

### 结果（运行 41252764835，提交 8568986，三台 ubuntu-24.04 同时跑）

错误数 / 用时；错误数 = DRC 错误 + 没连上的线 + 与原理图不一致的项（三者都没有不一致项）。

| 板子（元件数） | 原板 | fastroute | KRT | TraceMaker |
|---|---|---|---|---|
| ecc83（15） | 0 | 0 / 0.3 s | 0 / 6.8 s | 0 / 0.3 s |
| interf_u（25） | 3 | 0 / 9.1 s | 9 / 609 s（8 条没连上，1 个丝印重叠） | 0 / 120.5 s |
| pic_programmer（63） | 0 | 2 / 0.9 s（间距） | 6 / 21.4 s（丝印重叠） | 4 / 120.2 s（4 条没连上，125 条布通 121 条） |
| StickHub（94） | 0 | 105 / 76.6 s（101 线宽 + 4 没连上） | 17 / 41.0 s（10 阻焊桥 + 7 没连上） | 1 / 39.0 s（1 阻焊桥） |
| multichannel（114） | 12 | 488 / 2.7 s（线宽 199、间距 167、过孔 61、孔环 61） | 281 / 20.2 s（间距 177、过孔 52、孔环 52） | 0 / 50.5 s |

- **两块大板上 TraceMaker 远好于另外两个**：StickHub 1 个错误、multichannel 0 个（原板自己还有 12 个）。
  它在这两块板上没有一个线宽、过孔尺寸错误，说明它读到了板子规定的最小值；fastroute 和 KRT 的错误主要就是这一类。
  这推翻了上面「两块大板的错误数不能用来排名」的说法：规则是能带进布线器的，只是 fastroute 和 KRT 没带进去。
- **小板上三者接近**；TraceMaker 在 pic_programmer 上留了 4 条没连上（它自己报 121/125），比 fastroute 的 2 个间距错误更糟；
  interf_u 和 pic_programmer 它都用满了默认的 120 秒。
- fastroute 和 KRT 的错误数和本机基本一致（fastroute 本机 454 / 105 / 3 / 0 / 0，云端 488 / 105 / 2 / 0 / 0），
  用时云端快 4–9 倍（这台机器比本机快，所以用时只能在同一张表里比）。KRT 在 interf_u 上云端 609 秒布完，本机 900 秒超时。
- 第二次运行（41262079839，提交 4303a50，同样三台机器）：fastroute、KRT 的错误数和第一次几乎一样
  （multichannel 486、275，其余四块相同）；TraceMaker 在 StickHub 上是 4 个阻焊桥错误、用满了 120 秒
  （第一次 1 个、39 秒），其余四块和第一次一样。在时间限制下，跑到哪一步取决于机器当时有多快，
  所以同一块板两次结果可以不同（这是推测，没有逐项核实）。
- 结论基于这 5 块板、每个布线器跑两次。TraceMaker 只有 Linux 版，本机跑不了；
  用户 2026-11-01 决定部署（Linux）时它主用，本机照旧 fastroute + KRT。

## 第三方评测（单一来源，且是评测方自己做的）

fabPlane 2026-10-16：5 个开源布线器在 11 块公开 2 层 KiCad 板上的 26 个用例，每例跑 3 次。
通过用例数：KRT 20/26，Zap!（托管，临时数据）19/26，TraceMaker 18/26，fastroute 16/26，fab_router 13/26，Freerouting 12/26。
整板布线 fastroute 6/6，每块 0.7–1.6 s；改动后局部重布 KRT 7/9、fastroute 3/9。
文章建议「先 fastroute，失败再 KRT」，能覆盖 26 个用例中的 21 个。

## 「更顶级」的线索（2026-10-31 查证）

评测方都有自己的产品，各家排序不一致，只当线索，没有一项经我们复现：

- **Koppar 2026-10-17**：Koppar、Freerouting 2.5.0、tscircuit、KiCad Routing Tools 0.1.9、TraceMaker 0.9.0、托管的 Zap!，
  在 8 块开源硬件板（125–997 个连接，2–4 层）上，每块 1200 秒，用 KiCad 打分。
  Koppar 自评 8 块全布通、新增 DRC 0 个；TraceMaker 每块返回的板新增 DRC 都是 0，但只布通 4 块；
  Zap! 新增 DRC 0 个，三块小板 100%、五块载板 88–97%；tscircuit 5 块 100% 但每块新增 115–1052 个 DRC 错误；
  Freerouting 在 CM5 IO 板上超时。文中「KiCad Routing Tools 0.1.9」与我们装的 v0.23.7 版本号差很远，是否同一个项目我没核实。
  文中还说 2 层板差别不明显，4 层载板才拉开差距。
- **PUNKFAB 2026-10-05**：在他们的 119 块板上，TraceMaker 小/中/大板的完成率为 99%/100%/90%，他们自己的路由器为 78%/70%/20%。
- **Zap! 的规则对照表**：Freerouting 有 9 类规则带不进去（自定义规则 .kicad_dru、铺铜、差分对不能给；间距、禁布区只能近似）；
  Zap! 与 TraceMaker 基本能完整接收。这支持上面「大板的错误主要是规则没传进布线器」的推测。
- **Zap!**：托管、早期访问，有 HTTP 接口；免费试用不用注册，限小板（≤300 焊盘，2 或 4 层）；
  隐私条款写明上传文件在任务结束时删除，布好的板 7 天后删除。
- **Koppar**：托管，tools.koppar.ai，≤300 个连接不用账号；评测用的板、规则、脚本公开在 autorouter-bench。
- **DeepPCB**：收费；30 分钟免费试用（1 块板，≤4 层，≤150 条飞线，≤100 个元件）；有 KiCad 插件与 API。
- **Quilter**：按项目收费（按待布引脚数），有免费版；没查到公开的批量调用接口（未确认）。
- **TraceMaker**：fabPlane 排第 2、Koppar 评测里质量最接近、PUNKFAB 评测里领先；只发布 Linux x86_64 包，本机测不了。
  最终部署是 Linux（PLAN M6），所以它有可能成为生产环境的布线器，本机仍用别的。

小板（60 个元件以内）上现有三个工具已经只剩 0–3 个错误，其中 pic_programmer 的 2–3 个是铜层文字造成的，
我们生成的板子不放铜层文字；「更顶级」主要在 90 个元件以上的 4 层板上才有差别。

## Freerouting 2.5.0 免 Java 版不能用（已删除）

- 现象：只要 DSN 里有元件外框（KiCad 导出的都有），布线线程就抛
  `UnsupportedFeatureError: SerializationConstructorAccessor class not found … ComponentOutline`
  （GraalVM 打包漏了序列化配置），之后主进程一直挂着。LED 板上挂了 15 分钟，超时后先发 CTRL_BREAK 无响应，再强制结束。
- 对照：删掉 DSN 里 30 处元件外框后，0.05 秒布完，导回 KiCad 后 DRC 0 错误。
- 用户决定改用 Java 版；免 Java 版的压缩包和 exe 经用户同意已删除。

## 无界面 DSN 导出 / SES 导回（已验证）

KiCad 10.0.8 的 kicad-cli 没有这两个命令。用 KiCad 自带 Python 调
`pcbnew.ExportSpecctraDSN(board, path)`、`pcbnew.ImportSpecctraSES(board, path)`（`eda_agent/kicad_side/specctra.py`），
LED 板和 5 块示例板都能导出、导回。

## LED 冒烟（`smoke-led-20261031-163913`）

3 个元件、3 个网络：Freerouting 与 KRT 都全部连通，DRC 0 错误。我们自己生成的板子目前不放铜层文字，所以铜层文字的两个问题没有出现。

## 板边间距没传给 fastroute（2026-11-01，用户选「用 fastroute 自带的设置」）

**现象**：大样例 uno_like_board（34 个元件，ADR-0014）的冒烟，fastroute 跑 10 次有 2 次 DRC 不过：
1 次一根线离板边 0.30 mm（板子规则 `min_copper_edge_clearance` 0.5 mm），1 次 2 根线没布通。KRT 8 次全过。

**测了什么**（`.tools/audit/route_repeat.py`，运行目录 `runs/route-repeat/`）：

- fastroute 本身不随机：同一份 .dsn 跑 5 次（两份文件各 3 次、2 次），.ses 逐字节相同。
- 变的是输入：每次建板元件位置一样，但 KiCad 存盘时按元件的内部编号（UUID）排序，编号是随机的，
  导出的 .dsn 里元件和引脚的顺序每次不同（10 次建板 10 种顺序）。试过：`FOOTPRINT.SetUuid` 能改编号，
  存盘就按编号排；按位号算出编号后，两次不同的建板导出的 .dsn 除了第一行的文件路径完全相同。
- 离板边太近的原因：KiCad 导出的 .dsn 只有一般间距（`(clearance 200)`，0.2 mm），没有板边间距，
  fastroute 不知道 0.5 mm 这条，它自己的报告写着 0 违规。fastroute 有现成的参数
  `--router.copper_to_edge_clearance_um`（`fastroute --help` 里写着）。
- 两种改法在同样 24 块板上重布（另外 10 个样例的 11 块板 + uno 13 种顺序，其中含原来失败的两块）：
  把 .dsn 的板框往里收 0.3 mm，或者加上面那个参数（500 µm）。两种都是 **0 条离板边太近**。
  uno 上其余失败两种改法都还在：收板框 13 块里 1 块没布通；加参数 13 块里 1 块没布通、1 块两条线间距
  0.1986 mm（规则 0.2 mm，差 0.0014 mm）。另外那 11 块两种改法都全过。

**决定（用户）**：只修板边间距，用 fastroute 自带的参数，数值从工程文件的板子规则里读
（和最小线宽 `--router.min_trace_width_um` 的传法一样）；「没布通」这类失败这次不管，
建板编号也先不固定。对比脚本 `eval/router_bakeoff.py` 照旧不传板子规则，和前两次对比可比。

**改法**：`eda_agent/tools/routers.py` 加 `board_copper_edge_um()`，`fastroute_route(..., edge_clearance_um=)`；
冒烟（`eda_agent/smoke.py`）把工程里的板边间距传进去。

**结果**：全链路检查（`.tools/audit/smoke_all.py`，11 个样例、两个布线器，`runs/m1-route-all` 15:44–15:54）全部通过；
uno 那次的 trace 里 fastroute 的命令行带着 `--router.min_trace_width_um=200 --router.copper_to_edge_clearance_um=500`。
「没布通」那类失败没修，uno 上大约十几次里 1–2 次，还会碰到。
