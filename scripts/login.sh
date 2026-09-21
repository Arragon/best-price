#!/usr/bin/env bash
# 人工登录：**必须由你本人在本机终端完成扫码/扫脸**。
#
# 安全约束（指南 §0.5）：
#   - 不要把 Cookie、密码、短信验证码发给任何人或粘贴进聊天
#   - 本脚本不读取、不打印、不上传任何凭据
#   - 登录态落在 upstream/xianyu_spider/data/session.json，脚本会把它收紧到 600
#
# 备选方式：
#   scripts/login.sh --cookie     粘贴浏览器 Cookie（在本机终端里输入）
#   scripts/login.sh --browser    直接打开官方登录页扫码
#
# 注意：实测未登录(guest)也能搜索，登录不是必需项；登录后能否拿到不同结果集
# 尚未验证（见 IMPLEMENTATION_REPORT.md 的 NOT_VERIFIED_LIVE 项）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPSTREAM_DIR="$ROOT/upstream/xianyu_spider"

[ -d "$UPSTREAM_DIR" ] || { echo "✗ 缺少上游 checkout，请先运行 scripts/setup.sh" >&2; exit 1; }
[ -x "$ROOT/.venv/bin/python" ] || { echo "✗ 缺少 .venv，请先运行 scripts/setup.sh" >&2; exit 1; }

cd "$UPSTREAM_DIR"
set +e
"$ROOT/.venv/bin/python" spider.py login "$@"
STATUS=$?
set -e

SESSION_FILE="data/session.json"
if [ -f "$SESSION_FILE" ]; then
  chmod 600 "$SESSION_FILE"
  echo "→ 已将 $SESSION_FILE 权限收紧为 600（仅当前用户可读写）"
  echo "  该文件已被 .gitignore 排除，不会入库。"
fi

exit $STATUS
