#!/usr/bin/env bash
# 真实低频烟测：走完 §8.7 的完整 Agent 调用链路。
#
#   scripts/smoke-local.sh "富士 X-T4" 1
#   scripts/smoke-local.sh "RTX 4090" 1        # 任意品类，同一套用法
#
# 会对闲鱼发**真实**请求（一次，逐页串行）。请保持低频，不要循环调用。
# 出现 CHALLENGE_REQUIRED / RATE_LIMITED 时脚本会如实报出，不会重试硬撞。
#
# 本服务不做相关性筛选：下面打印的是平台原样字段，判断可比性由调用方做。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

KEYWORD="${1:-富士 X-T4}"
MAX_PAGES="${2:-1}"
BASE="${BASE_URL:-http://127.0.0.1:8765}"
TIMEOUT="${POLL_TIMEOUT:-180}"
PY=".venv/bin/python"

[ -x "$PY" ] || { echo "✗ 缺少 .venv，请先运行 scripts/setup.sh" >&2; exit 1; }

get() {
  # 必须用 -c：`python - <<HEREDOC` 会把 heredoc 当程序读掉 stdin，
  # 那样 json.load(sys.stdin) 就拿不到管道里的 JSON 了。
  "$PY" -c '
import json, sys
node = json.load(sys.stdin)
for key in sys.argv[1].split("."):
    if not key:
        continue
    node = node[int(key)] if isinstance(node, list) else node[key]
print("" if node is None else node)
' "$1"
}

echo "== 服务地址 $BASE =="

HEALTH="$(curl -fsS "$BASE/health")"
echo "health      : $HEALTH"

AUTH="$(curl -fsS "$BASE/v1/auth/status")"
echo "auth/status : $AUTH"

PAYLOAD="$(
  "$PY" - "$KEYWORD" "$MAX_PAGES" <<'PY'
import json, sys
print(json.dumps(
    {"keyword": sys.argv[1], "max_pages": int(sys.argv[2]), "sort": "newest"},
    ensure_ascii=False,
))
PY
)"

echo
if [ -n "${RUN_ID:-}" ]; then
  # 复用已完成的 run，避免为了验证脚本本身而重复对平台发真实请求
  echo "== 复用已有 RUN_ID=$RUN_ID（跳过 POST）=="
else
  echo "== POST /v1/search  $PAYLOAD =="
  ACCEPTED="$(curl -fsS -X POST "$BASE/v1/search" -H 'Content-Type: application/json' -d "$PAYLOAD")"
  echo "$ACCEPTED"
  RUN_ID="$(printf '%s' "$ACCEPTED" | get run_id)"
fi

echo
echo "== 轮询 /v1/search-runs/$RUN_ID =="
DEADLINE=$(( $(date +%s) + TIMEOUT ))
while :; do
  RUN="$(curl -fsS "$BASE/v1/search-runs/$RUN_ID")"
  STATUS="$(printf '%s' "$RUN" | get status)"
  case "$STATUS" in
    succeeded | partial | failed | blocked_login) break ;;
  esac
  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    echo "✗ ${TIMEOUT}s 内未结束，最后状态：$STATUS" >&2
    echo "  $RUN" >&2
    exit 2
  fi
  printf '.'
  sleep 2
done
echo
echo "$RUN" | "$PY" -m json.tool

if [ "$STATUS" = "failed" ] || [ "$STATUS" = "blocked_login" ]; then
  echo
  echo "✗ 本轮采集未成功（status=$STATUS）。这不是「闲鱼没有商品」。" >&2
  echo "  请按 error.code 处理；requires_human_action=true 时需你本人到合法客户端操作。" >&2
  exit 3
fi

echo
echo "== GET /v1/products?run_id=$RUN_ID （按价格升序，前 8 条；平台字段原样透出）=="
curl -fsS "$BASE/v1/products?run_id=$RUN_ID&limit=8" | "$PY" -c "
import json, sys
body = json.load(sys.stdin)
print('total=%s  run_status=%s  partial=%s' % (body['total'], body['run_status'], body['partial']))
for item in body['items']:
    price = item['price']
    shown = ('¥' + price['yuan']) if price['yuan'] else '(无有效价格: %s)' % price['parse_status']
    title = (item['title'] or '(无标题)').replace(chr(10), ' ')[:46]
    seller = item['seller']
    who = ' · '.join(x for x in (
        seller.get('display_name'), item.get('area'), seller.get('credit'),
        ('好评率%s(%s评价)' % (seller['positive_rate'], seller['review_count']))
        if seller.get('positive_rate') is not None and seller.get('review_count') is not None else None,
    ) if x)
    marks = []
    sig = item['signals']
    if sig.get('is_auction'): marks.append('拍卖')
    if sig.get('is_ad'): marks.append('广告位')
    if price.get('coupon_text'): marks.append(price['coupon_text'])
    if sig.get('want_count') is not None: marks.append('%s人想要' % sig['want_count'])
    if sig.get('free_shipping'): marks.append('包邮')
    print('  %-14s %s' % (shown, title))
    print('                 %s' % (who or '(卖家信息缺失)'))
    if marks: print('                 标记: %s' % ' · '.join(marks))
    desc = (item.get('description') or '').replace(chr(10), ' ')[:60]
    if desc: print('                 描述: %s' % desc)
    print('                 %s' % item['canonical_url'])
"

echo
echo "== GET /v1/stats?run_id=$RUN_ID （未筛选的算术分布）=="
curl -fsS "$BASE/v1/stats?run_id=$RUN_ID" | "$PY" -m json.tool

echo
echo "✓ 烟测结束。以上为**采集时刻的公开在售报价**，不是成交价。"
echo "  样本**未筛选**：租赁盘、拍卖起拍价、配件、故障机、广告位都在分布里，"
echo "  请读上面的描述与卖家信息自行判断可比性。"
