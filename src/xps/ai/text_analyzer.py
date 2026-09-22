from __future__ import annotations

import hashlib

import httpx

from xps.ai.client import PROMPT_VERSION, OpenAICompatibleTextClient
from xps.ai.evidence_validator import evidence_is_valid
from xps.ai.fallback import analyze_rules
from xps.settings import Settings
from xps.storage.analysis_repository import AnalysisRepository


class TextAnalyzer:
    def __init__(self, repo: AnalysisRepository, settings: Settings) -> None:
        self.repo, self.settings = repo, settings

    async def analyze(self, run_id: str, product_id: int) -> dict:
        row = self.repo.observation(run_id, product_id)
        if row is None:
            raise KeyError("observation")
        title, description = row["title_raw"] or row["title_latest"] or "", row["description"] or ""
        combined = (title + "\n" + description)[: self.settings.ai_max_input_chars]
        text_hash = hashlib.sha256(combined.encode()).hexdigest()
        model_id = self.settings.ai_model or "rules-v1"
        cached = self.repo.cached(row["id"], text_hash, model_id, PROMPT_VERSION, "1")
        if cached:
            cached["cache_hit"] = True
            return cached
        fallback = analyze_rules(title, description, is_auction=bool(row["is_auction"]))
        if not self.settings.ai_enabled:
            result = self.repo.save(row["id"], text_hash, model_id, PROMPT_VERSION,
                                    "rules_only", fallback.model_dump(mode="json"))
            result["cache_hit"] = False
            return result
        try:
            output = await OpenAICompatibleTextClient(self.settings).analyze(title, description)
            if not evidence_is_valid(output, title=title, description=description):
                raise ValueError("evidence_not_found")
        except httpx.HTTPError as exc:
            result = self.repo.save(row["id"], text_hash, model_id, PROMPT_VERSION,
                                    "unavailable", fallback.model_dump(mode="json"),
                                    type(exc).__name__)
        except (ValueError, KeyError, IndexError) as exc:
            result = self.repo.save(row["id"], text_hash, model_id, PROMPT_VERSION,
                                    "invalid_output", fallback.model_dump(mode="json"),
                                    type(exc).__name__)
        else:
            result = self.repo.save(row["id"], text_hash, model_id, PROMPT_VERSION,
                                    "succeeded", output.model_dump(mode="json"))
        result["cache_hit"] = False
        return result
