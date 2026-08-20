"""Agent — thin wrapper over LangChain's `create_agent` + LangGraph checkpointer.

One Agent per role (e.g. customer, merchant, booking). Multiple channels can
share the same Agent — conversations are partitioned by `thread_id`.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional, Union

from langchain.agents import create_agent
from langchain.agents.middleware import SummarizationMiddleware
from langchain.chat_models import init_chat_model
from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from core.context import current_chat_id
from core.token_store import RunTokens, TokenStore

logger = logging.getLogger(__name__)

PromptLike = Union[str, Callable[[], str]]

# System prompt for `Agent.compose` — the agent writes a single proactive
# outbound message in the customer's language, mirroring the live conversation's
# tone. Kept generic (no tenant specifics — those ride in the per-call directive
# and the conversation history that's prepended).
_COMPOSER_SYSTEM = (
    "You are the same assistant continuing the conversation above. Your task: "
    "write the NEXT message to send to the customer, following the instruction. "
    "Write in the SAME language the customer uses above — if they wrote in Uzbek, "
    "write in Uzbek; in Russian, in Russian; in English, in English. Keep the "
    "warm, human, concise tone of the conversation. No markdown. Output ONLY the "
    "text of the message to send — no quotes, no preamble, no explanation. Do not "
    "invent facts beyond the instruction; include any links/addresses it gives "
    "exactly as written."
)


def _text_of(content: Any) -> str:
    """Flatten LangChain message content (str or list of blocks) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def _aggregate_usage(usage_cb: UsageMetadataCallbackHandler) -> RunTokens:
    """Fold every model entry in the callback into a single RunTokens."""
    input_t = cached_t = output_t = 0
    for u in usage_cb.usage_metadata.values():
        input_t += int(u.get("input_tokens", 0))
        output_t += int(u.get("output_tokens", 0))
        details = u.get("input_token_details") or {}
        cached_t += int(details.get("cache_read", 0))
    return RunTokens(
        input_tokens=input_t,
        cached_input_tokens=cached_t,
        output_tokens=output_t,
        total_tokens=input_t + output_t,
    )


class Agent:
    """A LangChain agent bound to a model, prompt, and tool set.

    The prompt may be a string OR a zero-arg callable returning a string. If
    callable, it is re-evaluated on every `invoke()` — useful for prompts that
    reference the current date/time. The underlying agent graph is rebuilt
    only when the rendered prompt changes.
    """

    def __init__(
        self,
        *,
        name: str,
        model: str,
        system_prompt: PromptLike,
        tools: list[Any],
        checkpointer: Optional[BaseCheckpointSaver] = None,
        summarize_at_tokens: int = 10_000,
        summarize_keep: int = 20,
        model_kwargs: Optional[dict] = None,
        model_provider: Optional[str] = None,
        token_store: Optional[TokenStore] = None,
    ) -> None:
        self.name = name
        self.model_name = model
        self.model_provider = model_provider
        self._tools = tools
        self._system_prompt = system_prompt
        self._summarize_at = summarize_at_tokens
        self._summarize_keep = summarize_keep
        self._checkpointer = checkpointer or InMemorySaver()
        self._model_kwargs = dict(model_kwargs or {})
        self._llm = self._build_llm()
        self._token_store = token_store
        self._cached_prompt: Optional[str] = None
        self._graph = None
        self._rebuild_if_needed()

    def _build_llm(self) -> BaseChatModel:
        """Construct a chat model via `init_chat_model` so the provider is
        config-driven (openai, groq, anthropic, …). `temperature=0` for
        deterministic tool-calling; per-call overrides go through model_kwargs."""
        kwargs = {"temperature": 0, **self._model_kwargs}
        if self.model_provider:
            kwargs["model_provider"] = self.model_provider
        return init_chat_model(self.model_name, **kwargs)

    def _render_prompt(self) -> str:
        return (
            self._system_prompt()
            if callable(self._system_prompt)
            else self._system_prompt
        )

    def _rebuild_if_needed(self) -> None:
        prompt = self._render_prompt()
        if prompt == self._cached_prompt and self._graph is not None:
            return
        self._cached_prompt = prompt
        # Summariser uses a fresh chat model of the same provider/model —
        # required because Middleware re-uses messages_to_keep with its own
        # scratch context and shouldn't share the main agent's LLM client state.
        summariser_llm = self._build_llm()
        self._graph = create_agent(
            self._llm,
            self._tools,
            system_prompt=prompt,
            middleware=[
                SummarizationMiddleware(
                    model=summariser_llm,
                    trigger=("tokens", self._summarize_at),
                    keep=("messages", self._summarize_keep),
                ),
            ],
            checkpointer=self._checkpointer,
        )

    @staticmethod
    def make_config(thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id}}

    async def invoke(
        self,
        content: Union[str, list[Any]],
        *,
        thread_id: str,
    ) -> str:
        """Send a single user message and return the final assistant text.

        `content` may be a plain string or a multimodal list of content blocks
        (e.g. `[{"type": "text", ...}, {"type": "image_url", ...}]`).
        """
        self._rebuild_if_needed()
        config = self.make_config(thread_id)
        usage_cb = UsageMetadataCallbackHandler()
        invoke_config = {**config, "callbacks": [usage_cb]}
        result = await self._graph.ainvoke(  # type: ignore[union-attr]
            {"messages": [HumanMessage(content=content)]},
            config=invoke_config,
        )
        await self._record_token_usage(usage_cb)
        for msg in reversed(result.get("messages", [])):
            if isinstance(msg, AIMessage):
                text = msg.content
                if isinstance(text, list):
                    text = "".join(
                        part.get("text", "")
                        for part in text
                        if isinstance(part, dict) and part.get("type") == "text"
                    )
                return text or ""
        return ""

    async def compose(self, directive: str, *, thread_id: str) -> str:
        """Write a single proactive outbound message to the customer, in the
        customer's language, using this thread's conversation for language + tone.

        Tool-free and read-only: it runs a one-off model completion (no tools, no
        state mutation) so it can't misfire a tool or pollute history — the caller
        sends + records the returned text. Used for scripted funnel touches
        (reminders, drip, post-sale) that must speak the customer's language
        instead of going out as a fixed-language template. Returns '' on failure
        so the caller can fall back to a verbatim template.

        `directive` describes WHAT to say (internal, any language); the customer
        never sees it. The returned message is in the customer's language.
        """
        self._rebuild_if_needed()
        config = self.make_config(thread_id)
        history: list[Any] = []
        try:
            state = await self._graph.aget_state(config)  # type: ignore[union-attr]
            history = list((getattr(state, "values", None) or {}).get("messages", []))
        except Exception:
            logger.debug("compose: no history for %s", thread_id, exc_info=True)
        # Keep only conversational turns with text (drop tool calls/results so the
        # raw completion has no tool-pairing constraints) — most recent window.
        convo: list[Any] = []
        for m in history:
            if not isinstance(m, (HumanMessage, AIMessage)):
                continue
            txt = _text_of(m.content)
            if txt and txt.strip():
                convo.append(m.__class__(content=txt))
        convo = convo[-self._summarize_keep:]
        messages = (
            [SystemMessage(content=_COMPOSER_SYSTEM)]
            + convo
            + [HumanMessage(content="Инструкция (что сообщить клиенту): " + directive)]
        )
        usage_cb = UsageMetadataCallbackHandler()
        try:
            ai = await self._llm.ainvoke(messages, config={"callbacks": [usage_cb]})
        except Exception:
            logger.exception("compose model call failed for %s", thread_id)
            return ""
        await self._record_token_usage(usage_cb)
        return _text_of(ai.content).strip()

    async def record_user_message(
        self,
        content: Union[str, list[Any]],
        *,
        thread_id: str,
    ) -> None:
        """Append a message to a thread's history WITHOUT invoking the model.

        Used while the agent is muted (operator handoff): both the customer's
        messages and a labeled note for each operator reply must still land in
        the conversation so the agent has the full context when control is handed
        back. No model call, no reply, no token usage — just a state write. The
        message is stored with the user role; callers pre-label operator notes in
        the content so they read as a human intervention, not the agent's words.

        `as_node="model"` attributes the write to the agent's single LLM node
        (the node name `create_agent` uses), which keeps the update unambiguous
        even on a thread that has no prior checkpoint.
        """
        self._rebuild_if_needed()
        config = self.make_config(thread_id)
        await self._graph.aupdate_state(  # type: ignore[union-attr]
            config,
            {"messages": [HumanMessage(content=content)]},
            as_node="model",
        )

    async def _record_token_usage(
        self, usage_cb: UsageMetadataCallbackHandler
    ) -> None:
        """Persist this turn's token usage to the configured TokenStore.

        Sums per-model entries from the callback (one turn can drive the main
        LLM and the summariser; the handler keys by model name so we always
        aggregate). Pulls cached input tokens out of
        `input_token_details.cache_read` — providers without prompt caching
        (Groq today) just leave it at 0. Silently no-ops when no store is
        configured or no chat_id is in context (e.g. tests).
        """
        if self._token_store is None:
            return
        chat_id = current_chat_id.get()
        if chat_id is None:
            return
        run = _aggregate_usage(usage_cb)
        if run.is_empty():
            return
        try:
            await self._token_store.record_run(int(chat_id), run)
        except Exception:
            logger.exception(
                "Failed to record token usage for chat %s on agent %s",
                chat_id,
                self.name,
            )

    async def clear_thread(self, thread_id: str) -> None:
        """Best-effort wipe of a thread's conversation state.

        Tries async `adelete_thread` first (LangGraph >= 0.2 InMemorySaver),
        falls back to sync `delete_thread`. Silently no-ops if the checkpointer
        exposes neither.
        """
        for attr in ("adelete_thread", "delete_thread"):
            deleter = getattr(self._checkpointer, attr, None)
            if deleter is None:
                continue
            try:
                res = deleter(thread_id)
                if hasattr(res, "__await__"):
                    await res
                return
            except Exception:
                logger.exception("Failed to clear thread %s via %s", thread_id, attr)
                return
        logger.warning(
            "Checkpointer %s has no (a)delete_thread; cannot clear %s",
            type(self._checkpointer).__name__,
            thread_id,
        )

    async def clear_tokens(self, chat_id: int) -> None:
        """Reset a chat's token accounting to zero.

        Paired with `clear_thread` on a /clear: forgetting the conversation
        should also reset its token meter. Best-effort and a no-op when no
        TokenStore is configured.
        """
        if self._token_store is None:
            return
        try:
            await self._token_store.reset(int(chat_id))
        except Exception:
            logger.exception(
                "Failed to clear token usage for chat %s on agent %s",
                chat_id,
                self.name,
            )
