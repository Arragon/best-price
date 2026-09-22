"""End-to-end offline tests for research, analysis, evaluation, and retail quote import."""

from __future__ import annotations

from datetime import UTC, datetime

from tests.fake_adapter import FakeAdapter, synthetic_listing
from tests.helpers import build_client, search_and_wait


def _create_research(
    client,
    *,
    budget: int = 2,
    prefer_new_threshold: str | None = None,
) -> dict:
    profile = {
        "category": "camera",
        "goal": "旅行拍摄，接受二手",
        "target_budget_fen": 500000,
        "hard_budget_fen": 550000,
        "required": [{"value": "功能正常", "source": "user_explicit"}],
        "preferred": [{"value": "便携", "source": "user_explicit"}],
        "allow_alternative_models": True,
        "allow_extra": True,
    }
    if prefer_new_threshold is not None:
        profile["prefer_new_if_used_saving_at_most"] = prefer_new_threshold
    response = client.post(
        "/v1/researches",
        json={
            "mode": "category_research",
            "max_xianyu_requests": budget,
            "pace": "balanced",
            "profile": profile,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _link(client, research_id: str, run_id: str, purpose: str = "focused") -> dict:
    response = client.post(
        f"/v1/researches/{research_id}/runs",
        json={"run_id": run_id, "purpose": purpose},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_dynamic_candidate_and_budget_are_auditable(tmp_path) -> None:
    adapter = FakeAdapter(pages=[[synthetic_listing("7001")]])
    with build_client(tmp_path, adapter, search_cache_ttl_seconds=0) as client:
        research = _create_research(client, budget=1)
        first = search_and_wait(client, {"keyword": "微单", "cache_policy": "force_refresh"})
        linked = _link(client, research["id"], first["run_id"], "discovery")
        duplicate = _link(client, research["id"], first["run_id"], "discovery")
        second = search_and_wait(client, {"keyword": "X-T4", "cache_policy": "force_refresh"})
        over = client.post(
            f"/v1/researches/{research['id']}/runs",
            json={"run_id": second["run_id"], "purpose": "focused"},
        )
        candidate = client.post(
            f"/v1/researches/{research['id']}/candidates",
            json={
                "canonical_model": "Fujifilm X-T4",
                "bucket": "extra",
                "source_kind": "market_discovered",
                "source_ref": first["run_id"],
                "reason": "宽泛搜索发现的老旗舰",
                "deviation": "预算外 500 元",
            },
        )

    assert linked["used_xianyu_requests"] == 1
    assert duplicate["used_xianyu_requests"] == 1
    assert over.status_code == 429
    assert over.json()["code"] == "RESEARCH_BUDGET_EXCEEDED"
    assert candidate.status_code == 200
    assert candidate.json()["candidates"][0]["source_kind"] == "market_discovered"


def test_rules_analysis_is_evidence_backed_and_cached(tmp_path) -> None:
    listing = synthetic_listing(
        "7001", description="X-T4 仅收定金，标价非售价，偶尔黑屏，请勿平台外交易"
    )
    adapter = FakeAdapter(pages=[[listing]])
    with build_client(tmp_path, adapter) as client:
        run = search_and_wait(client)
        product = client.get("/v1/products", params={"run_id": run["run_id"]}).json()["items"][0]
        payload = {"run_id": run["run_id"], "product_id": product["product_id"]}
        first = client.post("/v1/text-analyses", json=payload)
        second = client.post("/v1/text-analyses", json=payload)

    assert first.status_code == 201
    assert first.json()["analysis_status"] == "rules_only"
    assert first.json()["result"]["listing_type"] == "deposit"
    assert first.json()["result"]["price_flags"][0]["evidence"] in listing.description
    assert second.json()["cache_hit"] is True


def test_evaluation_and_comparable_stats_keep_raw_stats_separate(tmp_path) -> None:
    adapter = FakeAdapter(pages=[[
        synthetic_listing("7001", price_text="4800", description="X-T4 功能正常"),
        synthetic_listing("7002", price_text="5000", description="X-T4 功能正常"),
    ]])
    with build_client(tmp_path, adapter) as client:
        run = search_and_wait(client)
        research = _create_research(client)
        linked = _link(client, research["id"], run["run_id"])
        products = client.get("/v1/products", params={"run_id": run["run_id"]}).json()["items"]
        evaluation_ids = []
        for product in products:
            response = client.post(
                "/v1/evaluations",
                json={
                    "research_id": research["id"],
                    "profile_id": linked["profile_id"],
                    "product_id": product["product_id"],
                    "sku_key": "fujifilm:x-t4:body",
                    "eligibility": "eligible",
                    "price_comparable": True,
                    "score": 80,
                    "score_status": "provisional",
                    "subscores": {"match": 90, "price": 80, "condition": 70,
                                  "integrity": 80, "seller": 80},
                    "evidence_coverage": 70,
                    "evidence": [{"code": "FUNCTION_OK", "source_field": "description",
                                  "evidence_text": "功能正常"}],
                    "risk_flags": [],
                },
            )
            assert response.status_code == 201, response.text
            evaluation_ids.append(response.json()["id"])
        comparable = client.get(
            f"/v1/researches/{research['id']}/comparable-stats",
            params={"sku_key": "fujifilm:x-t4:body"},
        )
        raw = client.get("/v1/stats", params={"run_id": run["run_id"]})
        invalid = client.post(
            "/v1/evaluations",
            json={
                "research_id": research["id"], "profile_id": linked["profile_id"],
                "product_id": products[0]["product_id"], "eligibility": "review",
                "price_comparable": False, "score": None, "score_status": "insufficient_data",
                "subscores": {}, "evidence_coverage": 10, "rule_version": "score-v2",
                "evidence": [{"code": "MADE_UP", "source_field": "description",
                              "evidence_text": "不存在的证据"}], "risk_flags": [],
            },
        )

    assert comparable.status_code == 200
    assert comparable.json()["sample_size"] == 2
    assert comparable.json()["median_fen"] == 490000
    assert raw.json()["sample_quality"][0] == "unfiltered"
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "INSUFFICIENT_EVIDENCE"


def test_quote_import_rejects_mock_and_calculates_only_complete_costs(tmp_path) -> None:
    adapter = FakeAdapter(pages=[[synthetic_listing("7001", price_text="4800", description="功能正常")]])
    with build_client(tmp_path, adapter) as client:
        run = search_and_wait(client)
        research = _create_research(client, prefer_new_threshold="0.10")
        linked = _link(client, research["id"], run["run_id"])
        product = client.get("/v1/products", params={"run_id": run["run_id"]}).json()["items"][0]
        evaluation = client.post(
            "/v1/evaluations",
            json={
                "research_id": research["id"], "profile_id": linked["profile_id"],
                "product_id": product["product_id"], "sku_key": "fujifilm:x-t4:body",
                "eligibility": "eligible", "price_comparable": True, "score": 80,
                "score_status": "provisional",
                "subscores": {"match": 80, "price": 80, "condition": 80,
                              "integrity": 80, "seller": 80}, "evidence_coverage": 60,
                "evidence": [{"code": "OK", "source_field": "description",
                              "evidence_text": "功能正常"}], "risk_flags": [],
            },
        ).json()
        base_quote = {
            "platform": "jd", "canonical_product_url": "https://item.jd.com/123.html",
            "sku_key": "fujifilm:x-t4:body", "model": "X-T4", "variant": "body",
            "bundle": ["机身"], "listed_price_fen": 520000, "payable_price_fen": 500000,
            "shipping_price_fen": 0, "verification_status": "verified",
            "observed_at": datetime.now(UTC).isoformat(),
        }
        mocked = client.post("/v1/new-prices/quotes", json={**base_quote, "source_kind": "mock_test"})
        imported = client.post(
            "/v1/new-prices/quotes", json={**base_quote, "source_kind": "manual_user"}
        )
        incomplete = client.post(
            f"/v1/evaluations/{evaluation['id']}/quote-match",
            json={"quote_id": imported.json()["id"], "match_status": "exact"},
        )
        complete = client.post(
            f"/v1/evaluations/{evaluation['id']}/quote-match",
            json={"quote_id": imported.json()["id"], "match_status": "exact",
                  "used_shipping_price_fen": 0, "mandatory_used_fees_fen": 0},
        )
        ranked = client.get(f"/v1/researches/{research['id']}/ranked")

    assert mocked.status_code == 422 and mocked.json()["code"] == "RETAIL_MOCK_REJECTED"
    assert imported.status_code == 201
    assert incomplete.json()["saving_ratio"] is None
    assert complete.json()["saving_fen"] == 20000
    assert complete.json()["saving_ratio"] == "0.0400"
    ranked_body = ranked.json()
    assert ranked_body["new_vs_used_preference"]["used_saving_at_most"] == "0.10"
    assert (
        ranked_body["listing_buckets"]["primary"][0]["new_vs_used"][0]["preference_signal"]
        == "prefer_new"
    )


def test_capabilities_do_not_claim_unconfigured_external_services(tmp_path) -> None:
    with build_client(tmp_path, FakeAdapter()) as client:
        body = client.get("/v1/capabilities").json()
    assert body["text_analysis"]["cloud_model"] == "disabled"
    assert body["retail"]["automatic_adapters"] == []
    assert body["retail"]["quote_import"] == "enabled"


def test_model_and_listing_layers_are_separate_in_ranked_report(tmp_path) -> None:
    adapter = FakeAdapter(pages=[[synthetic_listing("7001", description="功能正常")]])
    with build_client(tmp_path, adapter) as client:
        run = search_and_wait(client)
        research = _create_research(client)
        linked = _link(client, research["id"], run["run_id"])
        model = client.post(
            f"/v1/researches/{research['id']}/model-evaluations",
            json={"canonical_model": "Fujifilm X-T4", "verdict": "primary", "fit_score": 85,
                  "evidence": ["用户需要防抖"], "source_summary": "外部 Agent 已核对规格来源"},
        )
        product = client.get("/v1/products", params={"run_id": run["run_id"]}).json()["items"][0]
        listing = client.post(
            "/v1/evaluations",
            json={
                "research_id": research["id"], "profile_id": linked["profile_id"],
                "product_id": product["product_id"], "eligibility": "review",
                "price_comparable": False, "score": None, "score_status": "insufficient_data",
                "subscores": {}, "evidence_coverage": 30, "evidence": [], "risk_flags": [],
            },
        )
        complete = client.post(
            f"/v1/researches/{research['id']}/complete",
            json={"stopping_reason": "达到请求预算且候选已覆盖"},
        )
        ranked = client.get(f"/v1/researches/{research['id']}/ranked")

    assert model.status_code == 201 and listing.status_code == 201 and complete.status_code == 200
    assert ranked.json()["model_evaluations"][0]["fit_score"] == 85
    assert ranked.json()["listing_buckets"]["review"][0]["score"] is None
    assert ranked.json()["stopping_reason"] == "达到请求预算且候选已覆盖"


def test_score_is_computed_and_suspending_risk_forces_null(tmp_path) -> None:
    adapter = FakeAdapter(pages=[[synthetic_listing("7001", description="功能正常")]])
    with build_client(tmp_path, adapter) as client:
        run = search_and_wait(client)
        research = _create_research(client)
        linked = _link(client, research["id"], run["run_id"])
        product = client.get("/v1/products", params={"run_id": run["run_id"]}).json()["items"][0]
        computed = client.post(
            "/v1/evaluations",
            json={
                "research_id": research["id"], "profile_id": linked["profile_id"],
                "product_id": product["product_id"], "eligibility": "eligible",
                "price_comparable": False, "score": None, "score_status": "final",
                "subscores": {"match": 100, "price": 80, "condition": 70,
                              "integrity": 90, "seller": 60},
                "evidence_coverage": 80, "rule_version": "score-computed",
                "evidence": [], "risk_flags": [],
            },
        )
        suspended = client.post(
            "/v1/evaluations",
            json={
                "research_id": research["id"], "profile_id": linked["profile_id"],
                "product_id": product["product_id"], "eligibility": "review",
                "price_comparable": False, "score": None, "score_status": "insufficient_data",
                "subscores": {"match": 100, "price": 100, "condition": 100,
                              "integrity": 100, "seller": 100},
                "evidence_coverage": 50, "rule_version": "score-suspended",
                "evidence": [],
                "risk_flags": [{"code": "MODEL_CONTRADICTION", "severity": "critical",
                                "effect": "suspend_score", "requires_review": True}],
            },
        )

    assert computed.status_code == 201, computed.text
    assert computed.json()["score"] == 81
    assert suspended.status_code == 201
    assert suspended.json()["score"] is None


def test_exact_quote_match_rejects_different_sku(tmp_path) -> None:
    adapter = FakeAdapter(pages=[[synthetic_listing("7001", description="功能正常")]])
    with build_client(tmp_path, adapter) as client:
        run = search_and_wait(client)
        research = _create_research(client)
        linked = _link(client, research["id"], run["run_id"])
        product = client.get("/v1/products", params={"run_id": run["run_id"]}).json()["items"][0]
        evaluation = client.post(
            "/v1/evaluations",
            json={"research_id": research["id"], "profile_id": linked["profile_id"],
                  "product_id": product["product_id"], "sku_key": "camera:body",
                  "eligibility": "eligible", "price_comparable": True, "score": None,
                  "score_status": "insufficient_data", "subscores": {},
                  "evidence_coverage": 20, "evidence": [], "risk_flags": []},
        ).json()
        quote = client.post(
            "/v1/new-prices/quotes",
            json={"platform": "jd", "source_kind": "manual_user",
                  "canonical_product_url": "https://item.jd.com/999.html?utm_source=x",
                  "sku_key": "camera:kit", "model": "X-T4 套机", "listed_price_fen": 600000,
                  "shipping_price_fen": 0, "verification_status": "verified",
                  "observed_at": datetime.now(UTC).isoformat()},
        ).json()
        mismatch = client.post(
            f"/v1/evaluations/{evaluation['id']}/quote-match",
            json={"quote_id": quote["id"], "match_status": "exact",
                  "used_shipping_price_fen": 0, "mandatory_used_fees_fen": 0},
        )

    assert quote["canonical_product_url"] == "https://item.jd.com/999.html"
    assert mismatch.status_code == 422
    assert mismatch.json()["code"] == "SKU_MISMATCH"
