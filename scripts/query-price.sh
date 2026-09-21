#!/usr/bin/env bash
# 一键查询：POST /v1/search → 轮询 → /v1/stats → /v1/products，
# 输出可直接转述给用户的结果（口径、样本量、未筛选声明、质量限制、可追溯链接）。
#
#   scripts/query-price.sh "富士 X-T4"                       # 默认 1 页
#   scripts/query-price.sh "RTX 4090" --pages 2              # 任意品类，同一套用法
#   scripts/query-price.sh "9950X3D" --min-price 2000
#
# 本服务不做相关性筛选：租赁盘、拍卖、配件、广告位都会照常返回，
# 由调用方读 /v1/products 的原始字段（完整描述、卖家信用、平台标记）自行判断。
#
# 退出码：0 成功 / 2 服务或参数问题 / 3 采集失败 / 4 轮询超时
# 会对闲鱼发**真实**请求，请保持低频。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

[ -x .venv/bin/python ] || { echo "✗ 缺少 .venv，请先运行 scripts/setup.sh" >&2; exit 1; }

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec .venv/bin/python -m xps.cli "$@"
