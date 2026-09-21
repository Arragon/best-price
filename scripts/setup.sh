#!/usr/bin/env bash
# 一次性初始化：建 venv、拉上游 checkout、装依赖。
#
# 许可提示：上游 superboyyy/xianyu_spider **没有 LICENSE 文件**（README 声称 MIT
# 但引用的文件不存在）。因此本项目不复制、不 vendor 其任何源码，只保留独立
# checkout 并进程内调用其公开函数，且仅限本机个人使用。详见
# docs/superpowers/specs/2026-09-22-xianyu-price-service-design.md §1.3。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

UPSTREAM_REPO="https://github.com/superboyyy/xianyu_spider.git"
UPSTREAM_DIR="upstream/xianyu_spider"
# 本项目 Gate A 验证时所用的上游 commit
TESTED_COMMIT="eb52bd4d1901eee9ba8035e860583cddf50ead4c"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"

command -v uv >/dev/null 2>&1 || { echo "✗ 需要 uv：https://docs.astral.sh/uv/" >&2; exit 1; }
command -v git >/dev/null 2>&1 || { echo "✗ 需要 git" >&2; exit 1; }

if [ ! -d .venv ]; then
  echo "→ 创建虚拟环境（Python $PYTHON_VERSION）"
  uv venv --python "$PYTHON_VERSION" .venv
fi

if [ ! -d "$UPSTREAM_DIR/.git" ]; then
  echo "→ clone 上游采集器（gitignored，不 vendor）"
  mkdir -p upstream
  git clone --quiet "$UPSTREAM_REPO" "$UPSTREAM_DIR"
fi

ACTUAL_COMMIT="$(git -C "$UPSTREAM_DIR" rev-parse HEAD)"
mkdir -p data data/backups
printf '%s\n' "$ACTUAL_COMMIT" > data/upstream-commit.txt

if [ "$ACTUAL_COMMIT" != "$TESTED_COMMIT" ]; then
  echo "⚠ 上游 HEAD=$ACTUAL_COMMIT"
  echo "  与本项目已验证的 $TESTED_COMMIT 不一致，接口可能已漂移。"
  echo "  请先重跑 .venv/bin/python scripts/verify_upstream.py 再信任采集结果。"
else
  echo "✓ 上游 commit=$ACTUAL_COMMIT（与 Gate A 验证时一致）"
fi

echo "→ 安装上游依赖"
uv pip install --python .venv -r "$UPSTREAM_DIR/requirements.txt"
echo "→ 安装本项目依赖（含 dev）"
uv pip install --python .venv -r pyproject.toml --group dev

cat <<EOF

✓ 初始化完成。下一步：
    scripts/start-local.sh              启动服务（默认 127.0.0.1:8765）
    scripts/login.sh                    可选：需要登录态数据时由你本人扫码
    scripts/smoke-local.sh "富士 X-T4"   真实低频烟测（POST→poll→products→stats）

  离线测试：  .venv/bin/python -m pytest -q
  Playwright Chromium 只在扫脸核身时才需要：
      .venv/bin/python -m playwright install chromium
EOF
