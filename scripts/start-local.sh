#!/usr/bin/env bash
# 启动本地服务。默认只监听 127.0.0.1；要跨设备访问请显式设 ALLOW_REMOTE_ACCESS=true
# 并优先用 Tailscale / SSH 隧道（指南 §9）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

[ -x .venv/bin/python ] || { echo "✗ 缺少 .venv，请先运行 scripts/setup.sh" >&2; exit 1; }

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

if [ ! -f data/upstream-commit.txt ]; then
  echo "⚠ data/upstream-commit.txt 不存在，本轮 source_commit 将记为 null。" >&2
  echo "  运行 scripts/setup.sh 可写入。" >&2
fi

exec .venv/bin/python -m xps.main
