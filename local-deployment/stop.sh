#!/usr/bin/env bash
# 微信公众号数据看板 - 停止服务（统信 UOS / Linux）
# 优先级：1) serve.pid 精确停止  2) 兜底按 /proc 精确匹配 argv（python + serve.py + cwd 在本目录）
# 用法：./stop.sh
set -u
cd "$(dirname "$0")"

stopped=""

# 方式一：PID 文件（serve.py 启动时写入，退出时自动清理）
if [ -f serve.pid ]; then
  PID=$(cat serve.pid 2>/dev/null)
  if [ -n "${PID:-}" ] && kill -0 "$PID" 2>/dev/null; then
    kill "$PID" 2>/dev/null
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "$PID" 2>/dev/null || break
      sleep 0.3
    done
    if kill -0 "$PID" 2>/dev/null; then
      kill -9 "$PID" 2>/dev/null
    fi
    stopped="$PID"
  fi
  rm -f serve.pid
fi

# 方式二：兜底扫描（PID 文件丢失时）
# 精确匹配 argv：解释器为 python 且某一参数恰为 serve.py（或以其结尾的绝对路径），
# 且进程 cwd 就是本目录。避免误伤命令行里只提到 serve.py 的进程
# （例如 bash -c "... serve.py ..."、vim serve.py、grep serve.py 等）。
if [ -z "$stopped" ]; then
  HERE=$(pwd)
  for p in /proc/[0-9]*; do
    pid=${p#/proc/}
    [ "$pid" = "$$" ] && continue
    [ "$pid" = "$PPID" ] && continue
    [ -r "$p/cmdline" ] || continue
    # 以 NUL 分隔逐项读取 argv
    argv0=""; hit=0
    while IFS= read -r -d '' item; do
      if [ -z "$argv0" ]; then argv0=$item; fi
      case "$item" in
        serve.py|*/serve.py) hit=1 ;;
      esac
    done < "$p/cmdline" 2>/dev/null
    [ "$hit" = "1" ] || continue
    # argv[0] 必须是 python 解释器
    case "$argv0" in
      *python*) ;;
      *) continue ;;
    esac
    # cwd 必须在本目录
    cwd=$(readlink -f "$p/cwd" 2>/dev/null)
    [ "$cwd" = "$HERE" ] || continue
    kill "$pid" 2>/dev/null && stopped="$stopped $pid"
  done
  # 等 3 秒让优雅退出（atexit 清理 PID 文件）完成
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    alive=0
    for pid in $stopped; do
      kill -0 "$pid" 2>/dev/null && alive=1
    done
    [ "$alive" = "0" ] && break
    sleep 0.3
  done
fi

if [ -n "$stopped" ]; then
  echo "看板服务已停止（PID:$stopped）"
else
  echo "未发现运行中的看板服务"
fi
