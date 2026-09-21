#!/usr/bin/env bash
# 在线备份 SQLite 并立即校验。
#
#   scripts/backup-sqlite.sh                     # 自动时间戳文件名
#   scripts/backup-sqlite.sh /tmp/before-upgrade.sqlite3
#
# 用 VACUUM INTO（SQLite ≥ 3.27）而不是裸拷主库文件：WAL 模式下裸拷会得到
# 不完整快照（指南 §9）。可在服务运行时执行，取的是读快照。
# 已存在的目标文件不会被覆盖，避免悄悄毁掉上一份可用备份。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

[ -x .venv/bin/python ] || { echo "✗ 缺少 .venv，请先运行 scripts/setup.sh" >&2; exit 1; }

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
DEST="${1:-data/backups/price-$(date -u +%Y%m%dT%H%M%SZ).sqlite3}"

.venv/bin/python - "$DEST" <<'PY'
import sys
from pathlib import Path

from xps.settings import Settings
from xps.storage.db import backup, connect, integrity_check

dest = Path(sys.argv[1])
settings = Settings()

source = connect(settings.database_path)
try:
    backup(source, dest)
finally:
    source.close()

restored = connect(dest)
try:
    status = integrity_check(restored)
    counts = {
        table: restored.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("search_runs", "products", "observations")
    }
finally:
    restored.close()

print(f"✓ 备份 → {dest}")
print(f"  integrity_check = {status}")
print(f"  行数 = {counts}")
if status != "ok":
    raise SystemExit(f"✗ 备份校验未通过：{status}")
PY
