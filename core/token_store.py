"""Per-tenant token-usage accounting types + the store interface (Protocol).

For every chat we keep two `RunTokens` records:

  - `current` — the breakdown for the most recent `Agent.invoke()` turn.
  - `spent`   — the cumulative sum across every turn this chat has ever done.

`RunTokens` splits a single turn into:

  - `input_tokens`         — prompt tokens billed to the model (the total
    includes the cached portion below; this matches LangChain's standardised
    `usage_metadata.input_tokens`).
  - `cached_input_tokens`  — subset of `input_tokens` that hit the provider's
    prompt cache and is billed at the lower cached rate. Comes from
    `usage_metadata.input_token_details.cache_read`.
  - `output_tokens`        — model output tokens, including any reasoning
    tokens billed by thinking models.
  - `total_tokens`         — input + output. Convenience field for callers
    that just want a single number.

To get the *fresh* (non-cached) input that's billed at full price:
`input_tokens - cached_input_tokens`.

`db.PostgresTokenStore` is the implementation; tenant wiring constructs it in
`services.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable


@dataclass
class RunTokens:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def is_empty(self) -> bool:
        return (
            self.input_tokens == 0
            and self.cached_input_tokens == 0
            and self.output_tokens == 0
            and self.total_tokens == 0
        )

    def __add__(self, other: "RunTokens") -> "RunTokens":
        return RunTokens(
            input_tokens=self.input_tokens + other.input_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


@dataclass
class ChatTokens:
    current: RunTokens = field(default_factory=RunTokens)
    spent: RunTokens = field(default_factory=RunTokens)
    updated_at: Optional[str] = None


@runtime_checkable
class TokenStore(Protocol):
    """The token-accounting interface the framework depends on;
    `db.PostgresTokenStore` satisfies it."""

    async def record_run(self, chat_id: int, run: "RunTokens") -> "ChatTokens": ...
    async def get(self, chat_id: int) -> "ChatTokens": ...
    async def snapshot(self) -> dict[int, "ChatTokens"]: ...
    async def reset(self, chat_id: int) -> None: ...
