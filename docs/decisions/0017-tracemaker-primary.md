# ADR-0017 布线器：流水线主用 TraceMaker，fastroute 和 KiCadRoutingTools 备用

- 状态：已采纳（2026-11-08，用户决定）。取代 ADR-0004 里「本机 fastroute 主用、部署时 TraceMaker 主用」的结论；
  ADR-0004 的实测数据和其余内容不变。平台问题的处理和「接入时间」是我按事实推出来的，
  用户 2026-11-08 选了「Linux 上用它，M3 就接入」，等于确认
- 影响：PLAN §1.2、§2（布线、开发环境）、§4（S7）、§12（M3、M6）、§15、§16；
  以后要改的代码和配置（这次没改）：`eda_agent/tools/routers.py`、`eda_agent/smoke.py`、`eda_agent/config.py`、
  `config.example.yaml`、`config.linux.yaml`、`.github/workflows/ci.yml`

## 决定（用户）

流水线主用 **TraceMaker 0.12.1**；**fastroute 0.1.14** 和 **KiCadRoutingTools 0.23.7** 作为备用后端。
备用的先后沿用 ADR-0004：fastroute 在前，KiCadRoutingTools 在后。一个布线器 DRC 不过或出错，换下一个
（ADR-0004 的回退逻辑，顺序变成 TraceMaker → fastroute → KiCadRoutingTools）。

## 平台问题：TraceMaker 没有 Windows 版

开发机是 Windows，TraceMaker 在上面跑不了。已有的事实：

- ADR-0004：发布包只有 Linux x86_64；它在 GitHub Actions 上测过（`bakeoff.yml`）。
  `config.linux.yaml` 里已有它的路径，`routers.py` 里已有 `tracemaker_route`，目前只有布线器对比脚本在用。
- 2026-11-08 查 GitHub（DingoOz/TraceMaker）：最新发布仍是 v0.12.1（2026-10-29），文件只有
  `tracemaker-0.12.1-linux-x86_64.tar.gz` 和 KiCad 插件包 `tracemaker-0.12.1-pcm-linux-x86_64.zip`（插件包也只有 Linux）。
  README（main 分支，2026-11-08 读取）写明：Windows 没有原生版本；Linux 版可以在 WSL2（Ubuntu 24.04）里跑，
  但它标的是 **untested**；macOS 要从源码编译，由贡献者在 Apple 芯片上测过，不在它的 CI 里。
- ADR-0001（2026-10-31 的记录，这次没有重新核实）：本机没有 WSL 发行版，也没有 Docker，BIOS 里虚拟化没打开；两台 VPS 暂停中。

按这些事实的处理：

1. **Linux 上主用 TraceMaker**：GitHub Actions（CI）和 M6 的部署镜像。
2. **本机 Windows 不跑 TraceMaker，布线退回 fastroute（备用 KiCadRoutingTools）**，也就是 ADR-0004 里本机原来的做法。
   布线器按配置里的顺序，选第一个可用的；trace 里记下实际用了哪个、为什么换。
3. **不选 WSL2**：本机现在没有 WSL、虚拟化关着，要先改 BIOS、再装 WSL；装好以后跑的也是 TraceMaker 自己标注「untested」的路线。
   用户 2026-11-08 的选项里有这一条，没有选它。
4. **后果**：本机 Windows 上跑出的布线结果，和 Linux 上主用的结果可能不同（ADR-0004 的实测里，两块大板上差别很大）。
   所以评测（M7）和演示案例的运行放在 Linux 上做（CI 或部署镜像），本机的运行只用来开发。这一条是我从事实推出来的，用户 2026-11-08 确认。

## 依据和保留意见

依据是 ADR-0004「TraceMaker 云端测试」：5 块 KiCad 示例板，每个布线器跑两次。
大板上 TraceMaker 的错误最少：StickHub 1 个（fastroute 105、KRT 17），multichannel 0 个（fastroute 488、KRT 281）。

保留意见，都已写在 ADR-0004，这里再列一遍：

- 那次对比没有把板子规则（最小线宽、过孔尺寸）传给 fastroute 和 KRT，TraceMaker 自己读了，
  所以大板上的差距有一部分是传参的差别。之后 fastroute 已经改成传线宽和板边间距（ADR-0013、ADR-0004 文末）。
- pic_programmer 上 TraceMaker 有 4 条没连上；interf_u 和 pic_programmer 用满了默认的 120 秒。
- StickHub 第二次运行是 4 个阻焊桥错误、用满 120 秒（第一次 1 个、39 秒）：有时间限制时结果取决于机器当时的速度，同一块板两次可以不同。
- 只测了 5 块 KiCad 示例板。我们自己生成的板（`examples/` 的 11 个样例，含 34 个元件的 uno_like_board）和 li4s_protect
  都还没有用 TraceMaker 布过。
- 第三方评测（fabPlane 2026-10-16，评测方有自己的产品，单一来源）里，TraceMaker 通过 18/26 个用例，
  低于 KiCadRoutingTools 的 20/26，高于 fastroute 的 16/26。

## 接入时间（相对 ADR-0004 的变化，用户 2026-11-08 确认）

ADR-0004 写「部署在 M6 才做」。主用以后，建议提前：

- 编排器的 S7（PLAN M3）按配置里的顺序选布线器，本机没有 TraceMaker 就用下一个；
- `ci.yml` 的冒烟在 Linux 上加 TraceMaker 一步（`bakeoff.yml` 里已有安装和校验哈希的步骤可以照搬）；
- M6 把它装进部署镜像。

这样 M3–M5 做的版图和 G3 回退，是在主用布线器上调的，不会到 M6 才第一次接触它。

ADR-0004 还写着回退逻辑「到 M2 的编排器里实现」，PLAN 里编排器在 M3，这里按 M3。

## 没定的（PLAN §16）

- 换布线器、版图智能体调整规则后重布，这两层回退怎么嵌套（M3 做编排器时定，见 ADR-0018）。
- TraceMaker 的时间限制：现在用它的默认值（每个变体 120 秒，review-queue D1）。主用以后要不要给固定的 `--time` 或 `--work`，
  等在我们自己的板子上测过再定。
- TraceMaker 在我们自己生成的板子上的结果（见上）。
