"""Token and cost tracking helpers."""

from __future__ import annotations

from dataclasses import dataclass


_MODEL_PRICING_USD_PER_1M: list[tuple[str, tuple[float, float]]] = [
    ("claude-opus", (15.0, 75.0)),
    ("claude-sonnet", (3.0, 15.0)),
    ("gpt-4o-mini", (0.15, 0.60)),
    ("gpt-4o", (2.5, 10.0)),
    ("deepseek", (0.27, 1.10)),
    ("qwen", (0.30, 1.20)),
]


@dataclass
class TokenTracker:
    """Tracks cumulative token usage and estimated cost."""

    model: str
    token_budget: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def record(self, usage: dict[str, int] | None) -> dict[str, int]:
        usage = usage or {}
        input_tokens = int(usage.get("input_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or 0)
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def estimated_cost_usd(self) -> float:
        input_rate, output_rate = self._lookup_rates()
        return (self.input_tokens / 1_000_000) * input_rate + (
            self.output_tokens / 1_000_000
        ) * output_rate

    @property
    def over_budget(self) -> bool:
        return self.token_budget > 0 and self.total_tokens >= self.token_budget

    def remaining_budget(self) -> int:
        if self.token_budget <= 0:
            return 0
        return max(self.token_budget - self.total_tokens, 0)

    def snapshot(self) -> dict[str, int | float]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "token_budget": self.token_budget,
            "estimated_cost_usd": round(self.estimated_cost_usd, 6),
        }

    def _lookup_rates(self) -> tuple[float, float]:
        model_name = (self.model or "").lower()
        for prefix, rates in _MODEL_PRICING_USD_PER_1M:
            if prefix in model_name:
                return rates
        return 1.0, 3.0
