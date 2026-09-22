"""Machine-readable feature and degradation discovery."""

from __future__ import annotations

from fastapi import APIRouter, Request

from xps import __version__

router = APIRouter(prefix="/v1", tags=["capabilities"])


@router.get("/capabilities", summary="当前实现、配置与外部验证状态")
async def capabilities(request: Request):
    settings = request.app.state.settings
    return {
        "version": __version__,
        "research_modes": ["model_search", "category_research", "listing_check"],
        "listing_buckets": ["primary", "extra", "review", "excluded"],
        "search": {
            "platforms": ["xianyu"],
            "pace": ["economy", "balanced", "fast"],
            "cache": True,
            "bounded_queue": True,
        },
        "text_analysis": {
            "rules": "enabled",
            "cloud_model": "configured" if settings.ai_enabled else "disabled",
            "model_id": settings.ai_model if settings.ai_enabled else None,
            "evidence_validation": True,
        },
        "evaluation": {
            "evidence_backed": True,
            "comparable_stats": True,
            "categories": ["generic"],
            "rule_version": "score-v1",
        },
        "retail": {
            "quote_import": "enabled",
            "automatic_adapters": [],
            "automatic_status": "not_configured",
            "platforms": ["jd", "taobao", "tmall", "pdd"],
            "mock_rejected": True,
        },
        "live_verification": {
            # The last observed AUTH_EXPIRED result is recorded in the dated
            # optimization baseline.  Capabilities must not present that
            # volatile observation as the current account state.
            "xianyu_current": "not_verified_current",
            "cloud_ai": "not_verified",
            "automatic_retail": "not_configured",
        },
    }
