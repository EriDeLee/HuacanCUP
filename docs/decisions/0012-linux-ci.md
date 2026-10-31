# ADR-0012 用 GitHub Actions 在 Linux 上做完整验证

- 状态：已推送到 github.com/EriDeLee/HuacanCUP（2026-10-31）；第一次运行发现 1 个真问题，已修，见「运行记录」。
  之后按用户要求提速（缓存 + 同时安装），见「提速」
- 影响：ADR-0001 第 1 条（Linux 上的验证交给 GitHub Actions）；PLAN §16；AGENTS.md「测试」

## 做什么

`.github/workflows/ci.yml` 在 `ubuntu-24.04` 上做：检出后马上在后台装 KiCad 10；取出 Python 依赖和布线器的缓存
（布线器没命中就也在后台装）；装 fastroute；Python 依赖没命中才装；`lint` → `test`；等后台装完 →
`test-tools` → `smoke` → 上传运行产物。
触发条件：推送到 main、拉取请求、手动运行。工具测试和冒烟在 `xvfb-run` 下跑，因为 KiCad 的 3D 渲染需要一个显示。
Linux 用的路径在 `config.linux.yaml`，工作流通过环境变量 `EDA_AGENT_CONFIG` 指向它。

## 钉住了什么

| 东西 | 钉法 |
|---|---|
| actions/checkout、setup-python、cache、upload-artifact | 40 位提交哈希（对应 v7.0.1、v7.1.0、v6.1.0、v7.0.2）；测试 `tests/test_workflow.py` 强制 |
| fastroute 0.1.14 Linux 包 | SHA-256 `079a109f…49c6`，与本机下载的和官方 SHA256SUMS 一致 |
| KiCadRoutingTools v0.23.7 | 提交 `83646c8b…`（与本机安装的同一个），预编译模块 `grid_router-linux-x86_64.so` 的 SHA-256 `02e54aa4…d186` |
| KRT 的 Python 依赖 | `.github/krt-requirements.txt`：numpy 2.5.4、scipy 1.18.1、shapely 2.2.0、pillow 13.0.0（与本机 venv-krt 一致） |
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
（唯一命中的是测试里用来验证「密钥不会泄漏」的假字符串）。文档里原来有一些本机路径会暴露 Windows 用户名；推送前已全部换成 `<用户目录>`、`<用户名>`
（旧提交里仍有原文，用户知情并接受，没有改写历史）。

## 运行记录

| 运行 | 提交 | 结果 |
|---|---|---|
| 41207656553 | a14e953 | 失败，共 4 分 35 秒。释放磁盘 1 分 53 秒，装 KiCad 1 分 9 秒，fastroute 1 秒，KRT 21 秒，Python 依赖 59 秒，lint 通过；单元测试 45 过、1 失败 |

第一次运行已经证实：装 KiCad 不会超出磁盘；`/usr/bin/python3` 能 `import pcbnew`；`libngspice.so.0` 和
`/usr/share/kicad` 都在（这些命令写在安装步骤里，步骤通过就说明它们都成功了）。第 2–5 项要等工具测试和冒烟跑到才知道。

失败原因（真问题，本机 Windows 上测不出来）：`config.py` 把相对路径变成绝对路径时用了 `Path.resolve()`，
它会跟着符号链接走。Linux 上 venv 里的 `bin/python` 是指向系统 Python 的符号链接，于是 `krt_python`
变成了 `/opt/hostedtoolcache/Python/3.14.9/x64/bin/python3.14`——一个没有装 numpy、scipy 的解释器。
新加的测试 `test_linux_config_loads_and_points_at_the_ci_install_locations` 抓到了它。
改法：用 `os.path.abspath`（只规范化路径，不跟符号链接），并加测试「相对路径不跟符号链接」
（在 Linux 上真正执行；Windows 没开开发者模式建不了符号链接，会跳过）。
Windows 不受影响：那里 venv 的 `python.exe` 不是符号链接。

运行页面用 Chrome DevTools 协议（CDP）在用户的浏览器里读：公开仓库的 Actions 日志要登录才能看，用户的浏览器已登录。

## 提速（2026-10-31，用户要求「最顶级的缓存、冷启动压到最短」）

### 时间花在哪（运行 41208611729，提交 b8a5e2e，共 4 分 43 秒）

环境准备占 225 秒，测试本身 52 秒。下面的拆分来自原始日志每行的时间戳：

| 步骤 | 秒 | 拆开 |
|---|---|---|
| 释放磁盘 | 92.5 | 全花在 `sudo rm -rf`。删之前磁盘已空 86 GB，KiCad 装完只占 4776 MB |
| 装 KiCad | 51.1 | add-apt-repository 10.3（其中联网查 PPA 信息和公钥 5.6）；重复的 `apt-get update` 1.9；下载 570 MB 9（65 MB/s）；解包 23（3D 模型一个包 9.7，装完 3216 MB）；配置和触发器 3.2（man-db 触发器几乎不花时间）；needrestart 0.8；版本检查 2.8 |
| Python 依赖 | 59.4 | 建 venv 3.5；解析、下载、从源码包构建 skidl/kinet2pcb/hierplace 约 12；写入并预编译约 1.46 万个 .py 共 44（本机 venv 里数的，其中 lark-oapi 占 1.07 万个） |
| KiCadRoutingTools | 21.7 | clone 5.7；建 venv 5；装 numpy 等 4 个包 8.5；`--help` 1.4 |
| fastroute | 0.3 | |

KiCad PPA 的包关系（`Packages.gz`）：`kicad` 推荐 kicad-libraries、kicad-doc-en、kicad-demos、unixodbc；
kicad-libraries 推荐符号库、封装库、模板和 3D 模型。示例工程 72.9 MB、英文手册 55.2 MB（装完 339 MB、59 MB），CI 用不到。

### 用户的选择

- 做：去掉释放磁盘；三样同时装；缓存 Python 依赖；缓存布线器；不装示例工程和英文手册。
- 保留 3D 模型（用户：「3d模型对渲染挺重要的呀」）。
- 不做：失败的运行也保存缓存（保持 actions/cache 的默认：整次运行通过才在最后保存）；
  KiCad 安装的其余精简（公钥存进仓库、去掉重复的索引更新和 needrestart、dpkg 不逐个文件等写盘）；pip 不预编译；
  另外 3 个只在没缓存时省几秒的小项。
- 更重的做法都不做：自建 Docker 镜像、官方 `kicad/kicad:10.0.8-full` 镜像（压缩后 1448 MB）、
  缓存装好的 KiCad 文件（或第三方 cache-apt-pkgs-action）、缓存 .deb、CI 用 uv、VPS 自托管 runner、付费大机器。

### 怎么做的

- `.github/scripts/bg.sh`：`start` 把命令放到后台，输出写进 `ci-logs/bg/<名字>.log`，结束时把退出码写进文件；
  `wait` 等它结束、原样打印输出、用它的退出码退出（超时 124，没启动过 2）。有 4 个测试（`tests/test_bg_script.py`，只在 Linux 上跑）。
  后台日志放在 `ci-logs/bg/`：失败注释脚本只看 `ci-logs/` 第一层，不会把出错之后还在写的后台日志当成出错的那一步。
- KiCad 检出后立刻在后台装（`install_kicad.sh`，命令和以前一样）。`apt-get install` 末尾加 `kicad-demos- kicad-doc-en-`
  （名字后加减号表示不装），其余推荐包照装：3D 模型、符号库、封装库都是作为推荐包装上的，照装其余推荐包最不容易漏东西。
- 布线器缓存没命中时也在后台装（`install_krt.sh`）。Python 依赖、`lint`、单元测试在前台同时进行，工具测试之前才等两个后台安装。
- 缓存钥匙：Python 依赖 = 镜像名 + Python 完整版本号（setup-python 的输出）+ `requirements.txt` 和 `requirements-dev.txt` 的哈希；
  布线器 = 镜像名 + Python 版本 + KRT 提交 + 预编译模块的 SHA-256 + `.github/krt-requirements.txt` 的哈希。
  任何一项变了就重装一次。从缓存取出的布线器仍核对提交号和预编译模块的哈希。
- GitHub 的缓存规则（官方文档 Dependency caching reference）：每个仓库默认共 10 GB；7 天没被用过的缓存自动删除；
  PR 里建的缓存只给这个 PR 自己用，写不进 main 的缓存。
- `tests/test_workflow.py` 新增：每次下载后紧跟哈希校验（`ci.yml` 和 `.github/scripts/*.sh` 都查）；工作流引用的脚本都存在；
  缓存钥匙含 Python 版本和依赖清单的哈希，且 `hashFiles` 指向的文件都存在（文件名写错时 `hashFiles` 返回空串，钥匙就不再随依赖变化）；
  每个后台安装都在工具测试之前恰好等一次，启动和等待的条件一致。

预计（估计，未验证）：缓存命中约 1.6 分钟，KiCad 安装是最慢的一路；没命中约 2.1 分钟，
Python 依赖的 59 秒加 lint、单元测试会比 KiCad 安装更慢。实测见下面的「提速后的运行」。

### 提速后的运行

| 运行 | 提交 | 缓存 | 总时长 | 结果 |
|---|---|---|---|---|
| 41216281037 | 89e6a71 | 都没命中（第一次） | 2 分 17 秒 | 通过 |
| 41216653405 | ab48bf5 | 都命中 | 1 分 45 秒 | 通过 |

命中的这次：取出 Python 依赖（117 MB）和布线器（192 MB）的缓存各约 3 秒；lint 3 秒、单元测试 5 秒后，
等 KiCad 33 秒。后台的 KiCad 用了 46.3 秒 = 添加 PPA 8.4 + 更新索引 1.9 + 安装 34.7（下载 441 MB 用了 6 秒，79 MB/s）+ 检查 1.3。
之后工具测试 12 秒、冒烟 35 秒、上传 6 秒；缓存已命中，不再保存。

结论：缓存命中时，测试开始前真正花在环境上的时间从 225 秒降到约 40 秒（取缓存约 6 秒 + 等 KiCad 33 秒）。
剩下最慢的一路是 KiCad 安装本身，其中 3D 模型约 14 秒（用户决定保留）。
没命中那次 KiCad 用了 80 秒、这次 46 秒，差别看起来来自和 pip 同时抢网络和磁盘，但只有两次运行，不能确定。

没命中的这次（原始日志时间戳）：

- 前台：Python 依赖 64 秒 → lint 3 秒 → 单元测试 5 秒（54 个，含只在 Linux 上跑的 4 个后台脚本测试）。
- 后台：布线器 22.9 秒；KiCad 80.4 秒 = 添加 PPA 19.5 + 更新索引 2.4 + 安装 57.8（下载 441 MB 用了 15 秒，30 MB/s）+ 检查 0.6。
- 之后：等 KiCad 6 秒，工具测试 9 秒，冒烟 31 秒，上传 2 秒，保存两个缓存各 4 秒。
- 不装示例和手册生效：新装 115 个包（原来 117），下载 441 MB（原来 570），装完占 4359 MB（原来 4776）；
  apt 对两个排除的包只提示「not installed, so not removed」。
- KiCad 比单独装时慢：添加 PPA 19.5 秒（原来 10.3），下载 30 MB/s（原来 65）。可能是和 pip 同时抢网络和磁盘，
  也可能是 Launchpad 和镜像本身时快时慢；只有一次运行，分不清。

## 后续

- 公开仓库目前没有 LICENSE 文件，默认别人无权复用；要不要加、加哪种由用户定。
  依赖里 fastroute、KiCad 是 GPL-3.0，KiCadRoutingTools 是 MIT；我们只调用它们的可执行文件，不合并代码。
- PLAN §16「Linux 上的 Actions 验证」已勾掉（b8a5e2e、89e6a71、ab48bf5 三次运行都通过）。
  这个仓库可以用来在云端测 TraceMaker（ADR-0004），要不要测由用户定。
