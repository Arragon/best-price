from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tag: str = Field(min_length=1, max_length=100)
    evidence: str = Field(min_length=1, max_length=500)
    source_field: Literal["title", "description"]


class TextAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1"] = "1"
    listing_type: Literal[
        "physical_product", "accessory", "rental", "wanted", "deposit",
        "auction", "bundle", "unknown"
    ] = "unknown"
    claimed_models: list[str] = Field(default_factory=list)
    state_claims: list[Claim] = Field(default_factory=list)
    price_flags: list[Claim] = Field(default_factory=list)
    marketing_flags: list[Claim] = Field(default_factory=list)
    transaction_flags: list[Claim] = Field(default_factory=list)
    contradictions: list[Claim] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    model_uncertain: bool = True
