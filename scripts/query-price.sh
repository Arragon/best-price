#!/usr/bin/env bash
# 一键查询：POST /v1/search → 轮询 → /v1/stats → /v1/products，
# 输出可直接转述给用户的结果（口径、样本量、排除原因、质量限制、可追溯链接）。
#
#   scripts/query-price.sh "富士 X-T4"                        # 默认 1 页、不限配置
#   scripts/query-price.sh "富士 X-T4" --kind body --pages 2   # 单机身口径，抓 2 页
#   scripts/query-price.sh "9950X3D" --kind any --min-price 2000
#
# 退出码：0 成功 / 2 服务或参数问题 / 3 采集失败 / 4 轮询超时
# 会对闲鱼发**真实**请求，请保持低频。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

[ -x .venv/bin/python ] || { echo "✗ 缺少 .venv，请先运行 scripts/setup.sh" >&2; exit 1; }

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec .venv/bin/python -m xps.cli "$@"
