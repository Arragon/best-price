"""Auditable imported new-price quotes; automatic adapters remain independent."""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from xps.storage.repository import utcnow_iso


class RetailRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def import_quote(self, payload: dict[str, Any]) -> dict[str, Any]:
        run_id, quote_id, now = str(uuid.uuid4()), str(uuid.uuid4()), utcnow_iso()
        with self.conn:
            self.conn.execute(
                "INSERT INTO retail_price_runs(id,source_kind,status,created_at) VALUES (?,?,?,?)",
                (run_id, payload["source_kind"], "succeeded", now),
            )
            self.conn.execute(
                """INSERT INTO new_price_quotes
                   (id,retail_run_id,platform,source_kind,external_listing_id,canonical_product_url,
                    sku_key,brand,model,variant,bundle_json,listed_price_fen,payable_price_fen,
                    shipping_price_fen,price_conditions_json,eligibility_json,stock_status,
                    verification_status,observed_at,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (quote_id, run_id, payload["platform"], payload["source_kind"],
                 payload.get("external_listing_id"), payload["canonical_product_url"], payload["sku_key"],
                 payload.get("brand"), payload["model"], payload.get("variant"),
                 json.dumps(payload.get("bundle", []), ensure_ascii=False), payload.get("listed_price_fen"),
                 payload.get("payable_price_fen"), payload.get("shipping_price_fen"),
                 json.dumps(payload.get("price_conditions", []), ensure_ascii=False),
                 json.dumps(payload.get("eligibility_context", []), ensure_ascii=False),
                 payload.get("stock_status", "unknown"), payload["verification_status"],
                 payload["observed_at"], now),
            )
        return self.get(quote_id)  # type: ignore[return-value]

    def get(self, quote_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM new_price_quotes WHERE id=?", (quote_id,)).fetchone()
        return self._record(row) if row else None

    def list_for_sku(self, sku_key: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM new_price_quotes WHERE sku_key=? ORDER BY observed_at DESC", (sku_key,)
        ).fetchall()
        return [self._record(row) for row in rows]

    @staticmethod
    def _record(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["bundle"] = json.loads(row["bundle_json"])
        result["price_conditions"] = json.loads(row["price_conditions_json"])
        result["eligibility_context"] = json.loads(row["eligibility_json"])
        for key in ("bundle_json", "price_conditions_json", "eligibility_json"):
            result.pop(key, None)
        return result
