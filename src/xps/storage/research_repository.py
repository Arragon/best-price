"""Persistence for buying researches and their dynamic candidate pool."""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from xps.storage.repository import utcnow_iso


def _json(text: str) -> Any:
    return json.loads(text)


class ResearchRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        research_id = str(uuid.uuid4())
        profile_id = str(uuid.uuid4())
        now = utcnow_iso()
        profile = payload["profile"]
        with self.conn:
            self.conn.execute(
                """INSERT INTO researches
                   (id, mode, category, goal, currency, target_budget_fen, hard_budget_fen,
                    allow_alternatives, allow_extra, max_xianyu_requests, pace, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    research_id, payload["mode"], profile.get("category"), profile["goal"],
                    profile.get("currency", "CNY"), profile.get("target_budget_fen"),
                    profile.get("hard_budget_fen"), int(profile.get("allow_alternative_models", True)),
                    int(profile.get("allow_extra", True)), payload["max_xianyu_requests"],
                    payload.get("pace", "balanced"), now, now,
                ),
            )
            self.conn.execute(
                """INSERT INTO search_profiles(id, research_id, profile_version, profile_json, created_at)
                   VALUES (?, ?, 1, ?, ?)""",
                (profile_id, research_id, json.dumps(profile, ensure_ascii=False), now),
            )
        return self.get(research_id)  # type: ignore[return-value]

    def get(self, research_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM researches WHERE id=?", (research_id,)).fetchone()
        if row is None:
            return None
        profile = self.conn.execute(
            """SELECT * FROM search_profiles WHERE research_id=?
               ORDER BY profile_version DESC LIMIT 1""", (research_id,)
        ).fetchone()
        runs = self.conn.execute(
            """SELECT rr.run_id, rr.purpose, rr.linked_at, sr.status, sr.keyword, sr.started_at,
                      sr.ended_at, sr.exhausted
                 FROM research_runs rr JOIN search_runs sr ON sr.id=rr.run_id
                WHERE rr.research_id=? ORDER BY rr.linked_at""", (research_id,)
        ).fetchall()
        candidates = self.conn.execute(
            """SELECT canonical_model, variant, bucket, source_kind, source_ref, reason,
                      deviation, created_at, updated_at
                 FROM model_candidates WHERE research_id=?
                ORDER BY CASE bucket WHEN 'primary' THEN 1 WHEN 'extra' THEN 2
                         WHEN 'review' THEN 3 ELSE 4 END, canonical_model""", (research_id,)
        ).fetchall()
        model_evaluations = self.conn.execute(
            """SELECT id,canonical_model,verdict,fit_score,evidence_json,source_summary,created_at
                 FROM model_evaluations WHERE research_id=? ORDER BY created_at""",
            (research_id,),
        ).fetchall()
        return {
            **dict(row),
            "allow_alternatives": bool(row["allow_alternatives"]),
            "allow_extra": bool(row["allow_extra"]),
            "profile_id": profile["id"],
            "profile_version": profile["profile_version"],
            "profile": _json(profile["profile_json"]),
            "runs": [{**dict(item), "exhausted": bool(item["exhausted"])} for item in runs],
            "candidates": [dict(item) for item in candidates],
            "model_evaluations": [self._model_evaluation(item) for item in model_evaluations],
        }

    @staticmethod
    def _model_evaluation(item: sqlite3.Row) -> dict[str, Any]:
        result = dict(item)
        result["evidence"] = _json(result.pop("evidence_json"))
        return result

    def link_run(self, research_id: str, run_id: str, purpose: str) -> dict[str, Any]:
        research = self.conn.execute(
            "SELECT max_xianyu_requests, used_xianyu_requests FROM researches WHERE id=?",
            (research_id,),
        ).fetchone()
        if research is None:
            raise KeyError("research")
        run = self.conn.execute("SELECT id FROM search_runs WHERE id=?", (run_id,)).fetchone()
        if run is None:
            raise KeyError("run")
        existing = self.conn.execute(
            "SELECT 1 FROM research_runs WHERE research_id=? AND run_id=?", (research_id, run_id)
        ).fetchone()
        if existing:
            return self.get(research_id)  # type: ignore[return-value]
        if research["used_xianyu_requests"] >= research["max_xianyu_requests"]:
            raise OverflowError("budget")
        with self.conn:
            self.conn.execute(
                "INSERT INTO research_runs(research_id, run_id, purpose, linked_at) VALUES (?,?,?,?)",
                (research_id, run_id, purpose, utcnow_iso()),
            )
            self.conn.execute(
                """UPDATE researches SET used_xianyu_requests=used_xianyu_requests+1,
                   updated_at=? WHERE id=?""", (utcnow_iso(), research_id)
            )
        return self.get(research_id)  # type: ignore[return-value]

    def upsert_candidate(self, research_id: str, item: dict[str, Any]) -> dict[str, Any]:
        if self.conn.execute("SELECT 1 FROM researches WHERE id=?", (research_id,)).fetchone() is None:
            raise KeyError("research")
        now = utcnow_iso()
        with self.conn:
            self.conn.execute(
                """INSERT INTO model_candidates
                   (research_id, canonical_model, variant, bucket, source_kind, source_ref,
                    reason, deviation, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(research_id, canonical_model, variant) DO UPDATE SET
                     bucket=excluded.bucket, source_kind=excluded.source_kind,
                     source_ref=excluded.source_ref, reason=excluded.reason,
                     deviation=excluded.deviation, updated_at=excluded.updated_at""",
                (research_id, item["canonical_model"], item.get("variant", ""), item["bucket"],
                 item["source_kind"], item.get("source_ref"), item["reason"],
                 item.get("deviation"), now, now),
            )
        return self.get(research_id)  # type: ignore[return-value]

    def complete(self, research_id: str, reason: str) -> dict[str, Any]:
        with self.conn:
            cursor = self.conn.execute(
                "UPDATE researches SET status='complete', stopping_reason=?, updated_at=? WHERE id=?",
                (reason, utcnow_iso(), research_id),
            )
        if not cursor.rowcount:
            raise KeyError("research")
        return self.get(research_id)  # type: ignore[return-value]

    def add_model_evaluation(self, research_id: str, item: dict[str, Any]) -> dict[str, Any]:
        if self.get(research_id) is None:
            raise KeyError("research")
        evaluation_id = str(uuid.uuid4())
        with self.conn:
            self.conn.execute(
                """INSERT INTO model_evaluations
                   (id,research_id,canonical_model,verdict,fit_score,evidence_json,source_summary,created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (evaluation_id, research_id, item["canonical_model"], item["verdict"],
                 item.get("fit_score"), json.dumps(item.get("evidence", []), ensure_ascii=False),
                 item["source_summary"], utcnow_iso()),
            )
        return self.get(research_id)  # type: ignore[return-value]
