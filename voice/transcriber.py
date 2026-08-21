"""Voice transcription — OpenAI Whisper or Google Gemini, provider-switched."""

from __future__ import annotations

import logging
from io import BytesIO
from typing import Optional

logger = logging.getLogger(__name__)

# Telegram voice notes are OGG/Opus; the rest cover photos-as-audio edge cases.
_MIME_BY_EXT = {
    "ogg": "audio/ogg",
    "oga": "audio/ogg",
    "opus": "audio/ogg",
    "mp3": "audio/mp3",
    "m4a": "audio/mp4",
    "mp4": "audio/mp4",
    "wav": "audio/wav",
    "webm": "audio/webm",
    "flac": "audio/flac",
    "aac": "audio/aac",
}


def _mime_for(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return _MIME_BY_EXT.get(ext, "audio/ogg")


class VoiceTranscriber:
    """Transcribes short voice notes, backend chosen by `provider`.

    - ``provider="openai"``      → ``AsyncOpenAI.audio.transcriptions.create``
      (Whisper / ``gpt-4o-transcribe``).
    - ``provider="google_genai"`` → Gemini ``generate_content`` over inline audio.

    A per-tenant ``prompt`` (domain vocabulary, language hints) drastically
    improves accuracy on accented Russian/Uzbek code-switched audio. For OpenAI
    it's the API ``prompt`` field; for Gemini it's folded into the transcription
    instruction. SDK imports are lazy so importing this module stays cheap and a
    tenant only pays for the backend it actually uses.
    """

    def __init__(
        self,
        *,
        model: str = "gpt-4o-transcribe",
        prompt: Optional[str] = None,
        provider: str = "openai",
        api_key: Optional[str] = None,
    ) -> None:
        self._model = model
        self._prompt = prompt
        self._provider = provider
        self._api_key = api_key
        self._client = None  # lazy — built on first transcribe

    async def transcribe(
        self,
        audio: BytesIO,
        *,
        filename: str = "voice.ogg",
        prompt: Optional[str] = None,
        mime_type: Optional[str] = None,
    ) -> str:
        """Transcribe a clip. Returns "" on any failure — callers must treat an
        empty string as "not understood" and say so, never as "nothing to do".

        `mime_type` overrides the guess from `filename`: a Telegram video note
        is an `.mp4` whose extension says audio, but the payload is video and
        the provider has to be told so.
        """
        try:
            if self._provider == "google_genai":
                return await self._transcribe_google(
                    audio, filename, prompt or self._prompt, mime_type
                )
            return await self._transcribe_openai(audio, filename, prompt or self._prompt)
        except Exception:
            logger.exception("Voice transcription failed (provider=%s)", self._provider)
            return ""

    async def _transcribe_openai(
        self, audio: BytesIO, filename: str, prompt: Optional[str]
    ) -> str:
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(api_key=self._api_key) if self._api_key else AsyncOpenAI()
        audio.name = filename  # OpenAI client sniffs MIME from the filename
        response = await self._client.audio.transcriptions.create(
            model=self._model,
            file=audio,
            prompt=prompt,
        )
        return (getattr(response, "text", "") or "").strip()

    async def _transcribe_google(
        self, audio: BytesIO, filename: str, prompt: Optional[str],
        mime_type: Optional[str] = None,
    ) -> str:
        from google import genai
        from google.genai import types

        if self._client is None:
            self._client = genai.Client(api_key=self._api_key) if self._api_key else genai.Client()
        instruction = (
            "Transcribe this audio verbatim. Output only the transcript text — no "
            "commentary, labels, quotes, or translation. Preserve the original "
            "language(s) exactly as spoken."
        )
        if prompt:
            instruction += f"\n\nDomain / language hints: {prompt}"
        audio.seek(0)
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=[
                instruction,
                types.Part.from_bytes(
                    data=audio.read(), mime_type=mime_type or _mime_for(filename)
                ),
            ],
        )
        return (getattr(response, "text", "") or "").strip()
