"""Gemini extraction client, with Groq or a second Gemini model as fallback."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from typing import Any, Protocol

from opsresearch.ratelimit import RateLimiter, with_retries

logger = logging.getLogger("opsresearch")

GEMINI_EXTRACT_MODEL = "gemini-2.5-flash"
GEMINI_EXTRACT_FALLBACK = "gemini-2.5-flash-lite"
GEMINI_VERIFIER_MODEL = "gemini-2.5-pro"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"


class LlmClient(Protocol):
    model_name: str

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]: ...


def parse_json_object(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        raw = raw.rsplit("```", 1)[0]
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("model returned JSON that is not an object")
    return data


class GeminiJsonClient:
    def __init__(self, model: str, api_key: str | None = None) -> None:
        self.model_name = model
        self._api_key = api_key

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        from google import genai
        from google.genai import types

        key = self._api_key or os.environ["GEMINI_API_KEY"]
        client = genai.Client(api_key=key)
        response = client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0,
                response_mime_type="application/json",
                response_json_schema=schema,
            ),
        )
        text = getattr(response, "text", None) or ""
        return parse_json_object(text)


class GroqJsonClient:
    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        self.model_name = model or os.environ.get("GROQ_MODEL") or DEFAULT_GROQ_MODEL
        self._api_key = api_key

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        from groq import Groq

        key = self._api_key or os.environ["GROQ_API_KEY"]
        client = Groq(api_key=key)
        # The schema is described in the prompt. Groq json_object mode does not take a JSON Schema.
        del schema
        response = client.chat.completions.create(
            model=self.model_name,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content or ""
        return parse_json_object(content)


def extraction_clients(env: Mapping[str, str] | None = None) -> list[LlmClient]:
    values = os.environ if env is None else env
    clients: list[LlmClient] = [
        GeminiJsonClient(GEMINI_EXTRACT_MODEL, api_key=values.get("GEMINI_API_KEY"))
    ]
    if values.get("GROQ_API_KEY"):
        clients.append(
            GroqJsonClient(values.get("GROQ_MODEL") or DEFAULT_GROQ_MODEL, api_key=values.get("GROQ_API_KEY"))
        )
    else:
        clients.append(GeminiJsonClient(GEMINI_EXTRACT_FALLBACK, api_key=values.get("GEMINI_API_KEY")))
    return clients


def verifier_fallback_message(env: Mapping[str, str] | None = None) -> str | None:
    values = os.environ if env is None else env
    if values.get("GROQ_API_KEY"):
        return None
    return (
        "GROQ_API_KEY missing; cross-model verifier using Gemini fallback model "
        f"{GEMINI_VERIFIER_MODEL}"
    )


def choose_verifier_backend(env: Mapping[str, str] | None = None) -> tuple[str, str]:
    values = os.environ if env is None else env
    if values.get("GROQ_API_KEY"):
        return "groq", values.get("GROQ_MODEL") or DEFAULT_GROQ_MODEL
    return "gemini", GEMINI_VERIFIER_MODEL


def build_verifier_client(env: Mapping[str, str] | None = None) -> LlmClient:
    message = verifier_fallback_message(env)
    if message:
        logger.warning("%s", message)
        values = os.environ if env is None else env
        return GeminiJsonClient(GEMINI_VERIFIER_MODEL, api_key=values.get("GEMINI_API_KEY"))
    values = os.environ if env is None else env
    return GroqJsonClient(values.get("GROQ_MODEL") or DEFAULT_GROQ_MODEL, api_key=values.get("GROQ_API_KEY"))


def complete_with_fallback(
    prompt: str,
    schema: dict[str, Any],
    clients: list[LlmClient],
    limiter: RateLimiter,
) -> tuple[dict[str, Any], str]:
    errors: list[str] = []
    for client in clients:
        try:
            data = with_retries(lambda client=client: client.complete_json(prompt, schema), limiter)
            return data, client.model_name
        except Exception as exc:
            errors.append(f"{client.model_name}: {type(exc).__name__}")
            logger.warning("llm %s failed: %s", client.model_name, type(exc).__name__)
    raise RuntimeError("extraction failed: " + "; ".join(errors))
