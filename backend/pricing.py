"""Single source of truth for per-model token pricing (USD per 1M tokens).

Imported by backend/llm_provider.py (usage logging on every live call) and
backend/scripts/test_anthropic.py (standalone smoke test) so the two never
drift apart. Add a new model's pricing here, once, when it's confirmed.
"""
from __future__ import annotations

# model id -> (input price per 1M tokens, output price per 1M tokens), USD.
# Only include prices that are actually confirmed — see DEFAULT_PRICING below
# for what happens to a model that isn't listed.
PRICING_PER_MTOK: dict[str, tuple[float, float]] = {
    # Claude Haiku 4.5 — https://platform.claude.com/docs/en/pricing
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

# Fallback for any model not in PRICING_PER_MTOK (e.g. Groq's
# llama-3.1-8b-instant — no confirmed pricing wired in here). Reports $0.00
# rather than a guessed number, so estimated_cost_usd is never wrong, only
# incomplete for that model.
DEFAULT_PRICING: tuple[float, float] = (0.0, 0.0)


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = PRICING_PER_MTOK.get(model, DEFAULT_PRICING)
    return (input_tokens * price_in / 1_000_000) + (output_tokens * price_out / 1_000_000)
