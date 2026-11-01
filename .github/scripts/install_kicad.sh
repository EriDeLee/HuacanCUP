#!/usr/bin/env bash
# CI 用：装 KiCad 10（官方 PPA）和无界面显示（ADR-0012）。
# ci.yml 一开始就把它放到后台（bg.sh start kicad），输出在 ci-logs/bg/kicad.log。
# 每个阶段前打一行时间，用来量各阶段耗时。
set -euo pipefail

mark() { echo "== $(date -u +%H:%M:%S.%3N) $*"; }

mark "添加 KiCad 10.0 的官方 PPA"
sudo add-apt-repository --yes ppa:kicad/kicad-10.0-releases
mark "更新软件包索引"
sudo apt-get update
# 推荐包照装：符号库、封装库、3D 模型都在里面（3D 模型渲染要用，用户 2026-10-31 决定保留）。
# 名字后面带减号 = 不装：英文手册用不到；示例工程 ci.yml 用不到（下载共 128 MB），
# 布线器对比 bakeoff.yml 要用里面的板子，它设 KICAD_WITH_DEMOS=1。
# 在后台运行时没有终端，DEBIAN_FRONTEND 让安装过程不去等人回答问题。
skip="kicad-doc-en-"
if [ "${KICAD_WITH_DEMOS:-0}" != 1 ]; then
  skip="kicad-demos- $skip"
fi
# 先只下载、再安装。2026-11-01 有两次运行下载卡住 15 分钟（同时别的机器正常），
# 连接一直开着、只是极慢，所以每个连接的超时管不住。下载单独做，每次最多 5 分钟、最多 3 次，
# 没下完的文件下次接着下；只下载时打断是安全的，安装到一半被打断才会弄坏软件包数据库。
# shellcheck disable=SC2086 # $skip 故意按空格拆成多个包名
for attempt in 1 2 3; do
  mark "下载第 $attempt 次（不装：$skip）"
  if sudo timeout 300 apt-get install --download-only -y --install-recommends \
    -o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20 -o Acquire::Retries=5 \
    kicad xvfb libngspice0 $skip; then
    break
  fi
  if [ "$attempt" = 3 ]; then
    mark "下载 3 次都没完成"
    exit 1
  fi
done
mark "安装（文件都已下载，不再联网）"
# shellcheck disable=SC2086
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --install-recommends --no-download \
  kicad xvfb libngspice0 $skip
mark "检查"
kicad-cli version
/usr/bin/python3 -c "import pcbnew; print('pcbnew', pcbnew.Version())"
ls /usr/lib/x86_64-linux-gnu/libngspice.so.0
ls /usr/share/kicad
df -h /
mark "完成"
