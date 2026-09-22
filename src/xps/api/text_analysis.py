from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from xps.ai.text_analyzer import TextAnalyzer
from xps.errors import INVALID_QUERY, ServiceError
from xps.storage.analysis_repository import AnalysisRepository

router = APIRouter(prefix="/v1", tags=["text-analysis"])


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    product_id: int


@router.post("/text-analyses", status_code=201, summary="对已有 observation 做证据约束文本分析")
async def analyze(payload: AnalysisRequest, request: Request):
    analyzer = TextAnalyzer(AnalysisRepository(request.app.state.conn), request.app.state.settings)
    try:
        return await analyzer.analyze(payload.run_id, payload.product_id)
    except KeyError as exc:
        raise ServiceError(INVALID_QUERY, "run_id/product_id observation 不存在", status_code=404) from exc
