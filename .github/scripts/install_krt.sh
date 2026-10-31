#!/usr/bin/env bash
# CI 用：装 KiCadRoutingTools（钉提交）和它的 Python 依赖（ADR-0012）。
# 布线器缓存没命中时，ci.yml 把它放到后台（bg.sh start krt），输出在 ci-logs/bg/krt.log。
# KRT_COMMIT、KRT_SO_SHA256 来自 ci.yml 的 env；python 是 setup-python 装的 3.14。
set -euo pipefail

mark() { echo "== $(date -u +%H:%M:%S.%3N) $*"; }

mark "克隆 KiCadRoutingTools，检出 $KRT_COMMIT"
mkdir -p .tools
git clone --no-checkout https://github.com/drandyhaas/KiCadRoutingTools .tools/KiCadRoutingTools
git -C .tools/KiCadRoutingTools checkout --detach "$KRT_COMMIT"
mark "下载预编译模块并校验哈希"
curl -fsSL -o .tools/KiCadRoutingTools/rust_router/grid_router.so \
  https://github.com/drandyhaas/KiCadRoutingTools/releases/download/v0.23.7/grid_router-linux-x86_64.so
echo "$KRT_SO_SHA256  .tools/KiCadRoutingTools/rust_router/grid_router.so" | sha256sum -c -
mark "建 venv-krt，装依赖"
python -m venv .tools/venv-krt
.tools/venv-krt/bin/python -m pip install -r .github/krt-requirements.txt
mark "完成"
