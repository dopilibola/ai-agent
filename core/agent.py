"""Agent — thin wrapper over LangChain's `create_agent` + LangGraph checkpointer.

One Agent per role (e.g. customer, merchant, booking). Multiple channels can
share the same Agent — conversations are partitioned by `thread_id`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable, Optional, Union

from langchain.agents import create_agent
from langchain.agents.middleware import SummarizationMiddleware
from langchain.chat_models import init_chat_model
from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from core.context import current_channel, current_chat_id, current_tenant_id
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



def _final_text(messages: list[Any]) -> str:
    """The last assistant message's text — what the customer actually gets."""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            return _text_of(msg.content) or (
                msg.content if isinstance(msg.content, str) else ""
            )
    return ""


def _turn_tail(messages: list[Any]) -> list[Any]:
    """The messages produced *this* turn: everything after the user message we
    just appended (the last HumanMessage in the returned state)."""
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            return messages[i + 1:]
    return list(messages)


def _tool_events(tail: list[Any]) -> list[dict]:
    """Pair each tool call in this turn with its result.

    Returns [{name, args, result, ok}]. `ok` is False when the tool errored —
    a LangChain ToolMessage marks that with status="error"; tenants also return
    a plain "error: …" string, which counts too. That flag is the cheapest
    signal of where the agent is failing in production.
    """
    calls: dict[str, dict] = {}
    ordered: list[dict] = []
    for msg in tail:
        if isinstance(msg, AIMessage):
            for call in getattr(msg, "tool_calls", None) or []:
                entry = {
                    "name": call.get("name"),
                    "args": call.get("args"),
                    "result": None,
                    "ok": True,
                }
                ordered.append(entry)
                call_id = call.get("id")
                if call_id:
                    calls[call_id] = entry
        elif isinstance(msg, ToolMessage):
            entry = calls.get(getattr(msg, "tool_call_id", "") or "")
            if entry is None:
                entry = {"name": msg.name, "args": None, "result": None, "ok": True}
                ordered.append(entry)
            result = _text_of(msg.content) or (
                msg.content if isinstance(msg.content, str) else str(msg.content)
            )
            entry["result"] = result
            entry["ok"] = _result_ok(msg, result)
    return ordered


def _result_ok(msg: Any, result: Any) -> bool:
    """Did this tool call actually succeed?

    Three shapes count as a failure: LangChain's own `status="error"`, a bare
    "error: …" string, and the tenants' JSON convention
    `{"success": false, "error": …}` (see `apps/*/tools.py::_fail`). Getting this
    right is what makes "which tool is failing in production" answerable from
    the corpus.
    """
    if getattr(msg, "status", "success") == "error":
        return False
    text = result.strip() if isinstance(result, str) else ""
    if text.lower().startswith("error"):
        return False
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except Exception:
            return True
        if isinstance(data, dict) and (data.get("success") is False or data.get("error")):
            return False
    return True


def _count_images(content: Any) -> int:
    if not isinstance(content, list):
        return 0
    return sum(
        1
        for part in content
        if isinstance(part, dict) and part.get("type") in ("image_url", "image")
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
        examples_tenant: Optional[str] = None,
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
        # Approved operator answers retrieved per message and appended to the
        # prompt. Off unless a tenant id is given.
        self._examples_tenant = examples_tenant
        self._extra_prompt = ""
        self._prompt_lock = asyncio.Lock()
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

    def _base_prompt(self) -> str:
        return (
            self._system_prompt()
            if callable(self._system_prompt)
            else self._system_prompt
        )

    def _render_prompt(self) -> str:
        return self._base_prompt() + self._extra_prompt

    @property
    def prompt_version(self) -> str:
        """Short fingerprint of the prompt this turn actually ran under.

        Prompts are edited live (admin panel, file edit) with no restart, so a
        hand-maintained version number would drift the moment someone fixes a
        typo. Hashing the rendered text is automatic and exact: every corpus row
        carries the prompt that produced it, which is what makes "did that edit
        help?" answerable, and a rollback comparable.

        Volatile substitutions (the clock some tenants inject) would otherwise
        change the hash every minute, so digits are stripped before hashing.
        """
        import hashlib
        import re as _re

        text = self._base_prompt()
        stable = _re.sub(r"\d", "", text)
        return hashlib.sha1(stable.encode("utf-8")).hexdigest()[:10]

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
        extra = await self._examples_for(content)
        config = self.make_config(thread_id)
        usage_cb = UsageMetadataCallbackHandler()
        invoke_config = {**config, "callbacks": [usage_cb]}
        started = time.monotonic()
        # One Agent serves every chat, so the retrieved block is per-call state
        # on a shared object. Build under the lock and keep a local reference to
        # the graph: a concurrent turn may rebuild `self._graph` immediately
        # after, but this call keeps the graph carrying *its* examples. The lock
        # covers a graph construction, never the model call.
        async with self._prompt_lock:
            self._extra_prompt = extra
            self._rebuild_if_needed()
            graph = self._graph
        result = await graph.ainvoke(  # type: ignore[union-attr]
            {"messages": [HumanMessage(content=content)]},
            config=invoke_config,
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        await self._record_token_usage(usage_cb)
        messages = result.get("messages", [])
        reply = _final_text(messages)
        # Append the turn to the conversation corpus (db/training.py). Last,
        # and self-swallowing, so it can never cost the customer a reply.
        await self._log_turn(
            thread_id=thread_id,
            content=content,
            messages=messages,
            usage_cb=usage_cb,
            latency_ms=latency_ms,
            reply=reply,
        )
        return reply

    async def _examples_for(self, content: Union[str, list[Any]]) -> str:
        """Approved operator answers similar to what the customer just asked.

        Always returns a string ("" when disabled, empty or failing) — a
        retrieval problem must cost the reply nothing.
        """
        if not self._examples_tenant:
            return ""
        try:
            from core.learning import example_store

            query = _text_of(content) or (content if isinstance(content, str) else "")
            if not query:
                return ""
            found = await example_store.search(self._examples_tenant, query)
            return example_store.render_block(found)
        except Exception:
            logger.debug("example retrieval failed", exc_info=True)
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
        text = _text_of(ai.content).strip()
        await self._log_outbound(thread_id=thread_id, directive=directive, text=text)
        return text

    async def record_user_message(
        self,
        content: Union[str, list[Any]],
        *,
        thread_id: str,
        log_role: Optional[str] = None,
        log_text: Optional[str] = None,
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

        `log_role` also appends the message to the durable corpus
        (`conversation_events`). Worth doing for an operator reply above all:
        what the human wrote after a handoff is the answer the agent *should*
        have given, and the checkpoint it otherwise lives in gets summarised and
        overwritten. `log_text` carries the raw text when `content` is a wrapped
        note ("[A human operator replied: …]").
        """
        self._rebuild_if_needed()
        config = self.make_config(thread_id)
        await self._graph.aupdate_state(  # type: ignore[union-attr]
            config,
            {"messages": [HumanMessage(content=content)]},
            as_node="model",
        )
        if log_role:
            await self._log_recorded(
                thread_id=thread_id,
                role=log_role,
                text=log_text if log_text is not None else content,
            )

    async def _log_recorded(
        self, *, thread_id: str, role: str, text: Any
    ) -> None:
        """Corpus row for a message that bypassed the model. Self-swallowing:
        the same rule as `_log_turn` — logging never costs a conversation."""
        try:
            from db import training

            if not training.enabled():
                return
            tenant_id, _, rest = thread_id.partition(":")
            channel, _, chat = rest.partition(":")
            await training.log_event(
                tenant_id=tenant_id or None,
                chat_id=int(chat) if chat.lstrip("-").isdigit() else 0,
                thread_id=thread_id,
                channel=channel or None,
                agent=self.name,
                role=role,
                text=text if isinstance(text, str) else str(text),
            )
        except Exception:
            logger.debug("training log_recorded failed for %s", thread_id, exc_info=True)

    async def _log_turn(
        self,
        *,
        thread_id: str,
        content: Any,
        messages: list[Any],
        usage_cb: UsageMetadataCallbackHandler,
        latency_ms: int,
        reply: str,
    ) -> None:
        """Append this turn (customer message → tool calls → reply) to the
        conversation corpus. Best-effort — see db/training.py."""
        try:
            from db import training

            if not training.enabled():
                return
            run = _aggregate_usage(usage_cb)
            channel = current_channel.get()
            await training.log_turn(
                tenant_id=current_tenant_id.get(),
                chat_id=current_chat_id.get(),
                thread_id=thread_id,
                channel=getattr(channel, "name", None),
                agent=self.name,
                user_text=_text_of(content) or (content if isinstance(content, str) else ""),
                reply_text=reply,
                tool_events=_tool_events(_turn_tail(messages)),
                tokens={
                    "input": run.input_tokens,
                    "cached": run.cached_input_tokens,
                    "output": run.output_tokens,
                    "total": run.total_tokens,
                },
                latency_ms=latency_ms,
                images=_count_images(content),
                model=self.model_name,
                prompt_version=self.prompt_version,
            )
        except Exception:  # pragma: no cover
            logger.debug("training log_turn failed for %s", thread_id, exc_info=True)

    async def _log_outbound(
        self, *, thread_id: str, directive: str, text: str
    ) -> None:
        """Record a composed proactive message (a scheduled funnel touch): what
        the funnel asked for, and what the agent actually wrote."""
        try:
            from db import training

            if not training.enabled() or not text:
                return
            channel = current_channel.get()
            await training.log_event(
                tenant_id=current_tenant_id.get(),
                chat_id=current_chat_id.get(),
                thread_id=thread_id,
                channel=getattr(channel, "name", None),
                agent=self.name,
                role="outbound",
                text=text,
                meta={"directive": directive[:1000], "model": self.model_name},
            )
        except Exception:  # pragma: no cover
            logger.debug("training log_outbound failed for %s", thread_id, exc_info=True)

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
