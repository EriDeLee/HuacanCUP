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
# 名字后面带减号 = 不装：示例工程和英文手册 CI 用不到，下载共 128 MB。
# 在后台运行时没有终端，DEBIAN_FRONTEND 让安装过程不去等人回答问题。
mark "安装"
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --install-recommends \
  kicad xvfb libngspice0 kicad-demos- kicad-doc-en-
mark "检查"
kicad-cli version
/usr/bin/python3 -c "import pcbnew; print('pcbnew', pcbnew.Version())"
ls /usr/lib/x86_64-linux-gnu/libngspice.so.0
ls /usr/share/kicad
df -h /
mark "完成"
