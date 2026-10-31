#!/usr/bin/env bash
# CI 用：把慢的安装放到后台，后面的步骤照常往下走（ADR-0012「提速」）。
#
#   bash .github/scripts/bg.sh start <名字> <命令...>     后台运行，输出写进 ci-logs/bg/<名字>.log
#   bash .github/scripts/bg.sh wait <名字> [最多等几秒]    等它结束，原样打印它的输出，再用它的退出码退出
#
# 后台日志单独放在 ci-logs/bg/：失败注释脚本 annotate_failure.py 只看 ci-logs/*.log，
# 以免把出错之后还在写的后台日志当成出错的那一步。等待的那一步会把后台输出打印进它自己的日志。
set -euo pipefail

dir=ci-logs/bg

start() {
  local name=$1
  shift
  mkdir -p "$dir"
  # 后台进程的输入输出都重定向走，否则这一步要等它关掉输出管道才算结束。
  # 退出码先写临时文件再改名，等待的一方不会读到写了一半的文件。
  (
    if "$@" >"$dir/$name.log" 2>&1; then code=0; else code=$?; fi
    echo "$code" >"$dir/$name.exit.tmp"
    mv "$dir/$name.exit.tmp" "$dir/$name.exit"
  ) </dev/null >/dev/null 2>&1 &
  echo "已在后台开始 $name：$*"
}

wait_for() {
  local name=$1 limit=${2:-900} begin=$SECONDS code
  if [ ! -f "$dir/$name.log" ] && [ ! -f "$dir/$name.exit" ]; then
    echo "$name 没有在后台启动过（工作流里 start 和 wait 的条件不一致？）"
    return 2
  fi
  until [ -f "$dir/$name.exit" ]; do
    if [ $((SECONDS - begin)) -ge "$limit" ]; then
      echo "等了 $limit 秒，$name 还没结束。到目前为止的输出："
      cat "$dir/$name.log" || true
      return 124
    fi
    sleep 0.25
  done
  echo "== $name 后台的完整输出（这一步等了 $((SECONDS - begin)) 秒）"
  cat "$dir/$name.log"
  code=$(cat "$dir/$name.exit")
  echo "== $name 退出码 $code"
  return "$code"
}

case "${1:-}" in
  start)
    shift
    start "$@"
    ;;
  wait)
    shift
    wait_for "$@"
    ;;
  *)
    echo "用法：bg.sh start <名字> <命令...> | bg.sh wait <名字> [最多等几秒]" >&2
    exit 2
    ;;
esac
