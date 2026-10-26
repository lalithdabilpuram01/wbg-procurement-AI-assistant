"""Token counting and the context budget.

The counter is a deterministic approximation (no tokenizer download, no API call): about 3.5 characters per token for
English and SQL, plus a small per-item overhead, rounded up. It deliberately over-counts a little; a real tokenizer
(e.g. the provider's count-tokens endpoint) can replace it behind the same `TokenCounter` protocol.
"""
from __future__ import annotations

import math
from typing import Protocol

from pydantic import BaseModel, Field, model_validator

CHARS_PER_TOKEN = 3.5
ITEM_OVERHEAD_TOKENS = 4


class TokenCounter(Protocol):
    def count(self, text: str) -> int: ...


class ApproxTokenCounter:
    def count(self, text: str) -> int:
        return math.ceil(len(text) / CHARS_PER_TOKEN) + ITEM_OVERHEAD_TOKENS if text else 0


class ContextBudget(BaseModel):
    """Token budget of one model request. Only `available_for_context` is spent on conversation memory."""

    total_tokens: int = Field(default=12_000, gt=0)
    reserved_instructions: int = Field(default=1_500, ge=0)     # system prompt and fixed rules
    reserved_tool_results: int = Field(default=6_000, ge=0)     # registry metadata and other tool output
    reserved_output: int = Field(default=1_200, ge=0)           # the model's reply
    safety_margin: int = Field(default=300, ge=0)               # absorbs counting error

    @model_validator(mode="after")
    def _leaves_room(self) -> "ContextBudget":
        if self.available_for_context <= 0:
            raise ValueError("the reserved amounts leave no tokens for conversation context")
        return self

    @property
    def available_for_context(self) -> int:
        return (self.total_tokens - self.reserved_instructions - self.reserved_tool_results - self.reserved_output
                - self.safety_margin)
