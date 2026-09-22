"""Evidence-backed listing evaluations and comparable asking-price statistics."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from xps.errors import INSUFFICIENT_EVIDENCE, INVALID_QUERY, ServiceError
from xps.evaluation.scoring import RULE_VERSION, weighted_score
from xps.services.comparable_statistics import comparable_stats
from xps.storage.evaluation_repository import EvaluationRepository
from xps.storage.research_repository import ResearchRepository

router = APIRouter(prefix="/v1", tags=["evaluations"])


class EvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=100)
    source_field: Literal["title_raw", "description", "price_raw", "seller_credit"]
    evidence_text: str = Field(min_length=1, max_length=1000)


class RiskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=100)
    severity: Literal["info", "warning", "critical"]
    effect: Literal["exclude_from_price", "suspend_score", "limit_recommendation", "inform_only"]
    requires_review: bool = True
    evidence_seq: int | None = Field(default=None, ge=1)


class EvaluationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    research_id: str
    profile_id: str
    product_id: int
    sku_key: str | None = Field(default=None, max_length=300)
    eligibility: Literal["eligible", "extra", "review", "excluded"]
    price_comparable: bool = False
    score: int | None = Field(default=None, ge=0, le=100)
    score_status: Literal["final", "provisional", "insufficient_data", "not_applicable"]
    subscores: dict[Literal["match", "price", "condition", "integrity", "seller"], int] = Field(default_factory=dict)
    evidence_coverage: int = Field(ge=0, le=100)
    rule_version: str = RULE_VERSION
    text_analysis_id: str | None = None
    evidence: list[EvidenceInput] = Field(default_factory=list)
    risk_flags: list[RiskInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def coherent_score(self):
        if any(not 0 <= value <= 100 for value in self.subscores.values()):
            raise ValueError("subscores 必须在 0..100")
        computed = weighted_score(self.subscores)
        suspended = any(flag.effect == "suspend_score" for flag in self.risk_flags)
        if computed is None or suspended:
            if self.score is not None:
                raise ValueError("核心分项不完整或风险暂停评分时 score 必须为 null")
            if self.score_status not in {"insufficient_data", "not_applicable"}:
                raise ValueError("无总分时 score_status 必须说明证据不足或不适用")
        else:
            if self.score is not None and self.score != computed:
                raise ValueError(f"score 必须由 score-v1 计算为 {computed}")
            self.score = computed
            if self.score_status not in {"final", "provisional"}:
                raise ValueError("完整分项应使用 final 或 provisional")
        if self.price_comparable and not self.sku_key:
            raise ValueError("price_comparable=true 时必须提供 sku_key")
        if self.price_comparable and self.eligibility not in {"eligible", "extra"}:
            raise ValueError("只有 eligible/extra 可进入可比价格集合")
        return self


class FeedbackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: str = Field(min_length=1, max_length=100)
    note: str = Field(min_length=1, max_length=1000)


@router.post("/evaluations", status_code=201, summary="提交证据化挂牌评估")
async def create_evaluation(payload: EvaluationCreate, request: Request):
    repo = EvaluationRepository(request.app.state.conn)
    dumped = payload.model_dump()
    try:
        result = repo.create(dumped)
    except KeyError as exc:
        raise ServiceError(INVALID_QUERY, f"{exc.args[0]} 不属于该研究", status_code=404) from exc
    except ValueError as exc:
        raise ServiceError(
            INSUFFICIENT_EVIDENCE,
            "evidence_text 不存在于声明的 source_field；评估未写入",
        ) from exc
    return result


@router.get("/evaluations/{evaluation_id}", summary="读取分项评分、风险与逐条证据")
async def get_evaluation(evaluation_id: str, request: Request):
    result = EvaluationRepository(request.app.state.conn).get(evaluation_id)
    if result is None:
        raise ServiceError(INVALID_QUERY, "evaluation_id 不存在", status_code=404)
    return result


@router.post("/evaluations/{evaluation_id}/feedback", status_code=201, summary="记录人工纠正")
async def add_feedback(evaluation_id: str, payload: FeedbackInput, request: Request):
    try:
        return EvaluationRepository(request.app.state.conn).add_feedback(
            evaluation_id, payload.verdict, payload.note
        )
    except KeyError as exc:
        raise ServiceError(INVALID_QUERY, "evaluation_id 不存在", status_code=404) from exc


@router.get("/researches/{research_id}/comparable-stats", summary="独立的同 SKU 可比挂牌统计")
async def get_comparable_stats(
    research_id: str,
    request: Request,
    sku_key: str = Query(min_length=1, max_length=300),
):
    if ResearchRepository(request.app.state.conn).get(research_id) is None:
        raise ServiceError(INVALID_QUERY, "research_id 不存在", status_code=404)
    items = EvaluationRepository(request.app.state.conn).comparable_prices(research_id, sku_key)
    return {"research_id": research_id, "sku_key": sku_key, **comparable_stats(items)}


@router.get("/researches/{research_id}/ranked", summary="按 Primary/Extra/Review 返回研究结果")
async def ranked(research_id: str, request: Request):
    research = ResearchRepository(request.app.state.conn).get(research_id)
    if research is None:
        raise ServiceError(INVALID_QUERY, "research_id 不存在", status_code=404)
    buckets = {name: [] for name in ("primary", "extra", "review", "excluded")}
    for candidate in research["candidates"]:
        buckets[candidate["bucket"]].append(candidate)
    listing_buckets = {name: [] for name in ("primary", "extra", "review", "excluded")}
    mapping = {"eligible": "primary", "extra": "extra", "review": "review", "excluded": "excluded"}
    threshold_raw = research["profile"].get("prefer_new_if_used_saving_at_most")
    threshold = Decimal(str(threshold_raw)) if threshold_raw is not None else None
    for evaluation in EvaluationRepository(request.app.state.conn).list_for_research(research_id):
        for comparison in evaluation["new_vs_used"]:
            ratio_raw = comparison.get("saving_ratio")
            comparison["preference_signal"] = "not_configured"
            if threshold is None:
                continue
            if ratio_raw is None:
                comparison["preference_signal"] = "unavailable"
                continue
            try:
                ratio = Decimal(str(ratio_raw))
            except InvalidOperation:
                comparison["preference_signal"] = "unavailable"
                continue
            comparison["preference_signal"] = (
                "prefer_new" if ratio <= threshold else "used_saves_more_than_threshold"
            )
        listing_buckets[mapping[evaluation["eligibility"]]].append(evaluation)
    return {
        "research_id": research_id,
        "status": research["status"],
        "profile": research["profile"],
        "buckets": buckets,
        "model_evaluations": research["model_evaluations"],
        "listing_buckets": listing_buckets,
        "new_vs_used_preference": {
            "used_saving_at_most": str(threshold) if threshold is not None else None,
            "effect": "advisory_only",
        },
        "stopping_reason": research["stopping_reason"],
        "note": "model candidates are not transaction guarantees; inspect listing evaluations separately",
    }
