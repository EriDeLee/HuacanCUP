# ADR-0001 工具链在本机 Windows 原生运行，工具放在项目内 `.tools/`

- 状态：已采纳（2026-10-31，用户决定）
- 影响：PLAN §2「部署」、§12 M0、§14；AGENTS.md「测试」

## 背景

PLAN 的 M0 要求把 KiCad、ngspice、Freerouting 等装进同一个 Docker 镜像跑通。开发机实际情况：

- CPU 为兆芯 KX-7000，硬件支持 VT-x，但 BIOS 里虚拟化没有打开（`VirtualizationFirmwareEnabled=False`）；
- 没有 WSL 发行版，也没有 Docker；
- 两台 VPS 分别从 2026-07-14、2026-08-01 起暂停。

## 决定

1. M0 到 M5 在本机 Windows 上直接运行工具；Linux 上的验证交给 GitHub Actions；
   装箱（Docker）推迟到 M6 部署评委网页时再做。
2. 所有工具只解压、不安装，放在 `.tools/`（不进 git）：不运行安装程序，不写 Program Files、开始菜单、文件关联，
   也不需要管理员权限。
3. 外部工具运行时，设置、缓存、临时文件全部指到 `.tools/home`：
   代码里由 `eda_agent/tools/sandbox.py` 设置，手工调用用 `.tools/toolenv.cmd`。
   覆盖的变量包括 `APPDATA`、`LOCALAPPDATA`、`USERPROFILE`、`HOME`、`TEMP`、`TMP`、`TMPDIR`、`TEMPDIR`、
   `KICAD_CONFIG_HOME`、`KICAD_DOCUMENTS_HOME`、`KICAD_CACHE_HOME`、`PYTHONPYCACHEPREFIX`。
4. 只把运行必需的系统变量传给外部工具；API 密钥等其他环境变量一律不传。
5. pytest 的临时目录放在项目里的 `.pytest_tmp/`。

## 污染检查结果（2026-10-31，用 Everything 按修改时间查项目外的文件，加上注册表对比）

| 写到项目外的东西 | 原因 | 处理 |
|---|---|---|
| 注册表 `HKCU\Software\kicad-cli`（空键，无任何值） | kicad-cli 所用的 wxWidgets 在 Windows 上默认用注册表存设置，环境变量改不了 | 用户决定保留，以后每次运行也不处理 |
| `C:\Users\Lee\opencode\tmp\org.kicad.kicad\instances\`（空文件夹） | opencode 设了 `TMPDIR`，wxWidgets 优先读它，当时沙箱漏设 | 已在沙箱里补上 `TMPDIR`/`TEMPDIR` 并复查；旧文件夹用户决定保留 |
| `C:\Users\Lee\opencode\tmp\pytest-of-Lee\` | 第一次跑测试时 pytest 用了系统临时目录 | 已改为 `.pytest_tmp/`；旧目录经用户同意已删除 |

加装 Freerouting Java 版和 JRE 25 后复查（2026-10-31 16:00 起）：JVM 的 `hsperfdata_Lee` 写在 `.tools/home/Temp/`；
注册表无新增。同一时段 `C:\Users\Lee\opencode\tmp\hsperfdata_Lee\` 也出现了，但重跑我们的 Freerouting 后它的时间戳没变，
说明不是我们的 Java 进程写的；同时段另一个项目（HiSH）正在构建签名，推测是它，未证实。

系统自身留下、无法避免的记录：`C:\Windows\Prefetch` 预读文件、`Amcache.hve` 程序清单、Windows Defender 扫描记录、
校验数字签名时的 `CryptnetUrlCache` 证书缓存、PowerShell 的启动缓存。

未发现写入：Program Files、开始菜单、文件关联、卸载列表、`C:\Users\Lee\AppData\Roaming`、用户真实的「文档」文件夹。

## 后果

- 只在 Linux 上出现的问题，要到 GitHub Actions 或 M6 装箱时才会暴露。
- `.tools/` 约 6 GB（KiCad 解压后 4.8 GB）。删掉项目文件夹即全部清除。
