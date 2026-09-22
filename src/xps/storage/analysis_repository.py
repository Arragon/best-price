from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from xps.storage.repository import utcnow_iso


class AnalysisRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def observation(self, run_id: str, product_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            """SELECT o.*, p.title_latest FROM observations o JOIN products p ON p.id=o.product_id
                WHERE o.run_id=? AND o.product_id=?""", (run_id, product_id)
        ).fetchone()

    def cached(self, observation_id: int, text_hash: str, model_id: str,
               prompt_version: str, schema_version: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """SELECT * FROM text_analyses WHERE observation_id=? AND text_hash=? AND model_id=?
               AND prompt_version=? AND schema_version=?""",
            (observation_id, text_hash, model_id, prompt_version, schema_version),
        ).fetchone()
        return self._record(row) if row else None

    def save(self, observation_id: int, text_hash: str, model_id: str, prompt_version: str,
             status: str, result: dict[str, Any], error_code: str | None = None) -> dict[str, Any]:
        analysis_id = str(uuid.uuid4())
        with self.conn:
            self.conn.execute(
                """INSERT INTO text_analyses
                   (id,observation_id,text_hash,model_id,prompt_version,schema_version,
                    analysis_status,result_json,error_code,created_at)
                   VALUES (?,?,?,?,?,'1',?,?,?,?)""",
                (analysis_id, observation_id, text_hash, model_id, prompt_version, status,
                 json.dumps(result, ensure_ascii=False), error_code, utcnow_iso()),
            )
        row = self.conn.execute("SELECT * FROM text_analyses WHERE id=?", (analysis_id,)).fetchone()
        return self._record(row)

    @staticmethod
    def _record(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["result"] = json.loads(row["result_json"])
        result.pop("result_json", None)
        return result
