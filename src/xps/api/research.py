"""Buying-research profiles, run linkage, and dynamic model candidates."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from xps.errors import INVALID_QUERY, RESEARCH_BUDGET_EXCEEDED, ServiceError
from xps.storage.research_repository import ResearchRepository

router = APIRouter(prefix="/v1", tags=["research"])


class SourcedValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = Field(min_length=1, max_length=300)
    source: Literal["user_explicit", "user_inferred_needs_confirmation", "agent_proposed"]


class SearchProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str | None = Field(default=None, max_length=100)
    goal: str = Field(min_length=1, max_length=1000)
    currency: Literal["CNY"] = "CNY"
    target_budget_fen: int | None = Field(default=None, ge=0)
    hard_budget_fen: int | None = Field(default=None, ge=0)
    required: list[SourcedValue] = Field(default_factory=list)
    preferred: list[SourcedValue] = Field(default_factory=list)
    excluded_listing_types: list[str] = Field(default_factory=list)
    allow_alternative_models: bool = True
    allow_extra: bool = True
    prefer_new_if_used_saving_at_most: Decimal | None = Field(
        default=None,
        ge=Decimal(0),
        le=Decimal(1),
        description="二手相对新品节省比例不高于此值时提示倾向新品；不删除候选",
    )

    @model_validator(mode="after")
    def budget_order(self):
        if (self.target_budget_fen is not None and self.hard_budget_fen is not None
                and self.target_budget_fen > self.hard_budget_fen):
            raise ValueError("target_budget_fen 不能高于 hard_budget_fen")
        return self


class ResearchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["model_search", "category_research", "listing_check"]
    profile: SearchProfileInput
    max_xianyu_requests: int = Field(default=8, ge=0, le=100)
    pace: Literal["economy", "balanced", "fast"] = "balanced"


class RunLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    purpose: Literal["discovery", "focused", "listing_check"] = "focused"


class CandidateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    canonical_model: str = Field(min_length=1, max_length=200)
    variant: str = Field(default="", max_length=200)
    bucket: Literal["primary", "extra", "review", "excluded"]
    source_kind: Literal["user_explicit", "agent_proposed", "market_discovered"]
    source_ref: str | None = Field(default=None, max_length=500)
    reason: str = Field(min_length=1, max_length=1000)
    deviation: str | None = Field(default=None, max_length=1000)


class CompleteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stopping_reason: str = Field(min_length=1, max_length=1000)


class ModelEvaluationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    canonical_model: str = Field(min_length=1, max_length=200)
    verdict: Literal["primary", "extra", "review", "excluded"]
    fit_score: int | None = Field(default=None, ge=0, le=100)
    evidence: list[str] = Field(default_factory=list)
    source_summary: str = Field(min_length=1, max_length=2000)


def _repo(request: Request) -> ResearchRepository:
    return ResearchRepository(request.app.state.conn)


@router.post("/researches", status_code=201, summary="创建购物研究与版本化 SearchProfile")
async def create_research(payload: ResearchCreate, request: Request):
    return _repo(request).create(payload.model_dump(mode="json"))


@router.get("/researches/{research_id}", summary="读取研究、预算、runs 与动态候选池")
async def get_research(research_id: str, request: Request):
    result = _repo(request).get(research_id)
    if result is None:
        raise ServiceError(INVALID_QUERY, "research_id 不存在", status_code=404)
    return result


@router.post("/researches/{research_id}/runs", summary="关联已有搜索 run 并消耗研究预算")
async def link_run(research_id: str, payload: RunLink, request: Request):
    try:
        return _repo(request).link_run(research_id, payload.run_id, payload.purpose)
    except KeyError as exc:
        raise ServiceError(INVALID_QUERY, f"{exc.args[0]} 不存在", status_code=404) from exc
    except OverflowError as exc:
        raise ServiceError(RESEARCH_BUDGET_EXCEEDED, "研究请求预算已耗尽") from exc


@router.post("/researches/{research_id}/candidates", summary="新增或更新可审计型号候选")
async def upsert_candidate(research_id: str, payload: CandidateInput, request: Request):
    try:
        return _repo(request).upsert_candidate(research_id, payload.model_dump())
    except KeyError as exc:
        raise ServiceError(INVALID_QUERY, "research_id 不存在", status_code=404) from exc


@router.post("/researches/{research_id}/complete", summary="记录研究停止原因并完成")
async def complete_research(research_id: str, payload: CompleteInput, request: Request):
    try:
        return _repo(request).complete(research_id, payload.stopping_reason)
    except KeyError as exc:
        raise ServiceError(INVALID_QUERY, "research_id 不存在", status_code=404) from exc


@router.post("/researches/{research_id}/model-evaluations", status_code=201,
             summary="记录与挂牌评估分离的型号层判断")
async def add_model_evaluation(research_id: str, payload: ModelEvaluationInput, request: Request):
    try:
        return _repo(request).add_model_evaluation(research_id, payload.model_dump())
    except KeyError as exc:
        raise ServiceError(INVALID_QUERY, "research_id 不存在", status_code=404) from exc
