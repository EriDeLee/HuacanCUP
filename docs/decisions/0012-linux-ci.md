# ADR-0012 用 GitHub Actions 在 Linux 上做完整验证

- 状态：配置写好，**尚未推送、尚未运行**（2026-10-31）
- 影响：ADR-0001 第 1 条（Linux 上的验证交给 GitHub Actions）；PLAN §16；AGENTS.md「测试」

## 做什么

`.github/workflows/ci.yml` 在 `ubuntu-24.04` 上依次做：释放磁盘 → 装 KiCad 10 → 装 fastroute 和 KiCadRoutingTools →
装 Python 依赖 → `lint` → `test` → `test-tools` → `smoke` → 上传运行产物。
触发条件：推送到 main、拉取请求、手动运行。工具测试和冒烟在 `xvfb-run` 下跑，因为 KiCad 的 3D 渲染需要一个显示。
Linux 用的路径在 `config.linux.yaml`，工作流通过环境变量 `EDA_AGENT_CONFIG` 指向它。

## 钉住了什么

| 东西 | 钉法 |
|---|---|
| actions/checkout、setup-python、upload-artifact | 40 位提交哈希（对应 v7.0.1、v7.1.0、v7.0.2）；测试 `tests/test_workflow.py` 强制 |
| fastroute 0.1.14 Linux 包 | SHA-256 `079a109f…49c6`，与本机下载的和官方 SHA256SUMS 一致 |
| KiCadRoutingTools v0.23.7 | 提交 `83646c8b…`（与本机安装的同一个），预编译模块 `grid_router-linux-x86_64.so` 的 SHA-256 `02e54aa4…d186` |
| KRT 的 Python 依赖 | numpy 2.5.4、scipy 1.18.1、shapely 2.2.0、pillow 13.0.0（与本机 venv-krt 一致） |
| 主程序依赖 | `requirements-dev.txt`（直接依赖写死版本） |
| Python | 3.14（setup-python 取最新的 3.14.x） |

安全底线（`tests/test_workflow.py`）：令牌只读（`contents: read`）、不用任何 secrets、不用 `pull_request_target`、
每次 `curl` 下载后都要校验哈希。仓库是公开的，工作流里没有也不需要任何密钥。

## 没钉住的

- **KiCad 来自官方 PPA `ppa:kicad/kicad-10.0-releases` 的最新 10.0.x。** 升到 10.0.9 时，
  工具测试里「版本以 10.0.8 开头」会报错，提醒我们有意识地升级，而不是悄悄变。
- Ubuntu 的 apt 依赖（libngspice0 等）和 runner 镜像随时间变。

## 第一次运行前没法验证的地方（本机是 Windows，只能到这里）

1. 装 `kicad` 带推荐包会不会超出磁盘（工作流里先删了 dotnet、android 等约 20 GB，并在前后打印 `df -h`）。
2. Ubuntu 的 `libngspice0` 版本不是 KiCad 10 Windows 版自带的 ngspice-46；LED 工作点电流的测试对此应该不敏感，但没跑过。
3. `pcbnew.Version()` 和 `kicad-cli version` 在 Linux 包里的写法（现在只比前缀 `10.0.8`）。
4. `xvfb` 下 `kicad-cli pcb render` 能不能出图。
5. fastroute 的 Linux 可执行文件和 KRT 的 `.so` 在这台 runner 上能不能跑，`.so` 能不能被 Python 3.14 加载。
6. `/usr/bin/python3`（Ubuntu 24.04 自带的 3.12）能 `import pcbnew`。
7. `config.linux.yaml` 里的几个路径（`/usr/share/kicad`、`libngspice.so.0`）是否和 PPA 的包一致。

以上任何一项出错，工作流会在对应步骤失败并上传日志；这是预期的第一次调试，不代表方案不行。
本机已验证：工作流 YAML 能解析；Windows 上 lint、38 个单元测试、7 个工具测试、冒烟全过；
Linux 分支的沙箱（透传 `DISPLAY`、仍然丢掉令牌和密钥）有单元测试。

## 推送前检查过的

公开之前查了会被推送的内容：64 个文件，只有项目文件（没有 `.tools`、`runs`、`venv`、`config.yaml`、`compare`）；
全部提交的作者邮箱都是 GitHub 的 no-reply 地址；仓库内容和历史里没有令牌、密钥、私钥的特征串
（唯一命中的是测试里用来验证「密钥不会泄漏」的假字符串）。文档里有几处本机路径 `C:\Users\Lee\...`，会暴露 Windows 用户名。

## 后续

- 公开仓库目前没有 LICENSE 文件，默认别人无权复用；要不要加、加哪种由用户定。
  依赖里 fastroute、KiCad 是 GPL-3.0，KiCadRoutingTools 是 MIT；我们只调用它们的可执行文件，不合并代码。
- 第一次运行通过后，在 PLAN §16 勾掉「Linux 上的 Actions 验证」，并用这个仓库去云端测 TraceMaker（ADR-0004）。
