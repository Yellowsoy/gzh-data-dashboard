#!/usr/bin/env bash
# 微信公众号数据看板 - 一键启动（统信 UOS / Linux）
# 用法：
#   ./run.sh            启动看板服务（默认 http://127.0.0.1:8000），并尝试打开浏览器
#   ./run.sh --port 8080  指定端口
#   ./run.sh --no-browser 不自动打开浏览器
#   ./run.sh --sync-only  只跑一次采集（不启动服务），等价于旧版行为
#
# 页面上的「更新数据」按钮会在服务端子进程运行 sync.py，无需再手动执行本脚本。

set -u
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "未检测到 python3。统信 UOS 一般自带；若没有，请在「应用商店」安装或执行：sudo apt install python3"
  exit 1
fi

PYVER=$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')
echo "使用 Python 版本：$PYVER"

if [ "${1:-}" = "--sync-only" ]; then
  shift
  python3 sync.py "$@"
  exit $?
fi

python3 serve.py "$@"
