"""Derived listing evaluations and comparable-price reads."""

from __future__ import annotations

import json
import sqlite3
import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from xps.storage.repository import utcnow_iso


class EvaluationRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def latest_observation(self, research_id: str, product_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            """SELECT o.*, p.canonical_url, p.title_latest
                 FROM observations o JOIN products p ON p.id=o.product_id
                 JOIN research_runs rr ON rr.run_id=o.run_id
                WHERE rr.research_id=? AND o.product_id=?
                ORDER BY o.observed_at DESC, o.id DESC LIMIT 1""", (research_id, product_id)
        ).fetchone()

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        observation = self.latest_observation(payload["research_id"], payload["product_id"])
        if observation is None:
            raise KeyError("product")
        profile = self.conn.execute(
            "SELECT research_id FROM search_profiles WHERE id=?", (payload["profile_id"],)
        ).fetchone()
        if profile is None or profile["research_id"] != payload["research_id"]:
            raise KeyError("profile")
        for evidence in payload.get("evidence", []):
            source = str(observation[evidence["source_field"]] or "")
            if evidence["evidence_text"] not in source:
                raise ValueError("evidence_not_found")
        evaluation_id = str(uuid.uuid4())
        with self.conn:
            self.conn.execute(
                """INSERT INTO evaluations
                   (id,research_id,profile_id,product_id,sku_key,eligibility,price_comparable,
                    score,score_status,subscores_json,evidence_coverage,rule_version,
                    text_analysis_id,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (evaluation_id, payload["research_id"], payload["profile_id"], payload["product_id"],
                 payload.get("sku_key"), payload["eligibility"], int(payload["price_comparable"]),
                 payload.get("score"), payload["score_status"],
                 json.dumps(payload.get("subscores", {}), ensure_ascii=False),
                 payload["evidence_coverage"], payload.get("rule_version", "score-v1"),
                 payload.get("text_analysis_id"), utcnow_iso()),
            )
            for seq, evidence in enumerate(payload.get("evidence", []), start=1):
                source = observation[evidence["source_field"]]
                text = evidence["evidence_text"]
                start = str(source or "").find(text)
                verified = True
                self.conn.execute(
                    """INSERT INTO evaluation_evidence
                       (evaluation_id,evidence_seq,code,source_field,evidence_text,start_offset,end_offset,verified)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (evaluation_id, seq, evidence["code"], evidence["source_field"], text,
                     start, start + len(text), int(verified)),
                )
            for risk in payload.get("risk_flags", []):
                self.conn.execute(
                    """INSERT INTO risk_flags
                       (evaluation_id,code,severity,effect,requires_review,evidence_seq)
                       VALUES (?,?,?,?,?,?)""",
                    (evaluation_id, risk["code"], risk["severity"], risk["effect"],
                     int(risk.get("requires_review", True)), risk.get("evidence_seq")),
                )
        return self.get(evaluation_id)  # type: ignore[return-value]

    def get(self, evaluation_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM evaluations WHERE id=?", (evaluation_id,)).fetchone()
        if row is None:
            return None
        evidence = self.conn.execute(
            "SELECT * FROM evaluation_evidence WHERE evaluation_id=? ORDER BY evidence_seq",
            (evaluation_id,),
        ).fetchall()
        risks = self.conn.execute(
            "SELECT code,severity,effect,requires_review,evidence_seq FROM risk_flags WHERE evaluation_id=?",
            (evaluation_id,),
        ).fetchall()
        result = dict(row)
        result["price_comparable"] = bool(row["price_comparable"])
        result["subscores"] = json.loads(row["subscores_json"])
        result["evidence"] = [{**dict(x), "verified": bool(x["verified"])} for x in evidence]
        result["risk_flags"] = [{**dict(x), "requires_review": bool(x["requires_review"])} for x in risks]
        result.pop("subscores_json", None)
        return result

    def comparable_prices(self, research_id: str, sku_key: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT e.product_id, o.price_fen, o.observed_at, o.run_id, p.canonical_url, o.title_raw
                 FROM evaluations e JOIN products p ON p.id=e.product_id
                 JOIN observations o ON o.product_id=e.product_id
                 JOIN research_runs rr ON rr.run_id=o.run_id AND rr.research_id=e.research_id
                WHERE e.research_id=? AND e.sku_key=? AND e.price_comparable=1
                  AND e.eligibility IN ('eligible','extra') AND o.price_parse_status='valid'
                ORDER BY o.observed_at DESC, o.id DESC""", (research_id, sku_key)
        ).fetchall()
        latest: dict[int, dict[str, Any]] = {}
        for row in rows:
            latest.setdefault(int(row["product_id"]), dict(row))
        return list(latest.values())

    def list_for_research(self, research_id: str) -> list[dict[str, Any]]:
        ids = self.conn.execute(
            """SELECT id FROM evaluations WHERE research_id=?
               ORDER BY CASE eligibility WHEN 'eligible' THEN 1 WHEN 'extra' THEN 2
                        WHEN 'review' THEN 3 ELSE 4 END, score DESC, created_at""",
            (research_id,),
        ).fetchall()
        results = []
        for row in ids:
            result = self.get(row["id"])
            assert result is not None
            observation = self.latest_observation(research_id, result["product_id"])
            result["listing"] = {
                "title": observation["title_raw"] if observation else None,
                "description": observation["description"] if observation else None,
                "price_fen": observation["price_fen"] if observation else None,
                "observed_at": observation["observed_at"] if observation else None,
                "canonical_url": observation["canonical_url"] if observation else None,
            }
            matches = self.conn.execute(
                """SELECT qm.*, q.platform, q.canonical_product_url, q.observed_at,
                          q.verification_status, q.price_conditions_json
                     FROM quote_matches qm JOIN new_price_quotes q ON q.id=qm.quote_id
                    WHERE qm.evaluation_id=?""", (row["id"],)
            ).fetchall()
            result["new_vs_used"] = [
                {**dict(match), "price_conditions": json.loads(match["price_conditions_json"])}
                for match in matches
            ]
            for match in result["new_vs_used"]:
                match.pop("price_conditions_json", None)
            results.append(result)
        return results

    def add_feedback(self, evaluation_id: str, verdict: str, note: str) -> dict[str, Any]:
        if self.get(evaluation_id) is None:
            raise KeyError("evaluation")
        feedback_id = str(uuid.uuid4())
        with self.conn:
            self.conn.execute(
                "INSERT INTO evaluation_feedback(id,evaluation_id,verdict,note,created_at) VALUES (?,?,?,?,?)",
                (feedback_id, evaluation_id, verdict, note, utcnow_iso()),
            )
        return {"feedback_id": feedback_id, "evaluation_id": evaluation_id,
                "verdict": verdict, "note": note}

    def match_quote(
        self,
        evaluation_id: str,
        quote_id: str,
        match_status: str,
        used_shipping_price_fen: int | None,
        mandatory_used_fees_fen: int | None,
    ) -> dict[str, Any]:
        evaluation = self.get(evaluation_id)
        quote = self.conn.execute("SELECT * FROM new_price_quotes WHERE id=?", (quote_id,)).fetchone()
        if evaluation is None or quote is None:
            raise KeyError("evaluation_or_quote")
        if match_status == "exact" and evaluation["sku_key"] != quote["sku_key"]:
            raise ValueError("sku_mismatch")
        observation = self.latest_observation(evaluation["research_id"], evaluation["product_id"])
        used_total = new_total = saving = None
        ratio = None
        quote_unconditional = (
            quote["verification_status"] == "verified"
            and json.loads(quote["price_conditions_json"]) == []
            and json.loads(quote["eligibility_json"]) == []
        )
        if match_status in {"exact", "equivalent_adjusted"} and quote_unconditional:
            used_price = observation["price_fen"] if observation else None
            new_price = quote["payable_price_fen"] or quote["listed_price_fen"]
            if (used_price is not None and used_shipping_price_fen is not None
                    and mandatory_used_fees_fen is not None and new_price is not None
                    and quote["shipping_price_fen"] is not None):
                used_total = used_price + used_shipping_price_fen + mandatory_used_fees_fen
                new_total = new_price + quote["shipping_price_fen"]
                saving = new_total - used_total
                if new_total:
                    ratio = str((Decimal(saving) / Decimal(new_total)).quantize(
                        Decimal("0.0001"), rounding=ROUND_HALF_UP
                    ))
        with self.conn:
            self.conn.execute(
                """INSERT INTO quote_matches
                   (evaluation_id,quote_id,match_status,used_total_fen,new_total_fen,saving_fen,saving_ratio)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(evaluation_id,quote_id) DO UPDATE SET
                     match_status=excluded.match_status,used_total_fen=excluded.used_total_fen,
                     new_total_fen=excluded.new_total_fen,saving_fen=excluded.saving_fen,
                     saving_ratio=excluded.saving_ratio""",
                (evaluation_id, quote_id, match_status, used_total, new_total, saving, ratio),
            )
        if not quote_unconditional:
            calculation_status = "quote_not_verified_or_conditional"
        elif ratio is None:
            calculation_status = "incomplete_costs"
        else:
            calculation_status = "available"
        return {"evaluation_id": evaluation_id, "quote_id": quote_id,
                "match_status": match_status, "used_total_fen": used_total,
                "new_total_fen": new_total, "saving_fen": saving, "saving_ratio": ratio,
                "calculation_status": calculation_status}
