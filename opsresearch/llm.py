"""Gemini extraction client, with Groq or a second Gemini model as fallback."""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Mapping
from typing import Any, Protocol

from opsresearch.ratelimit import RateLimiter, with_retries

logger = logging.getLogger("opsresearch")

# gemini-2.5-* were withdrawn for new keys (404 on 2026-11-23); the 3.x flash models are current.
# On 2026-11-23 gemini-3.8/3.7 returned 429 (quota) and 3.5/3.6 returned 503/504 for this key, so the
# working model leads. Override with OPSRESEARCH_EXTRACT_MODELS="a,b".
GEMINI_EXTRACT_MODEL = "gemini-3.5-flash-lite"
GEMINI_EXTRACT_FALLBACK = "gemini-3.1-flash-lite"
GEMINI_VERIFIER_MODEL = "gemini-3.5-flash"
# A different model family from the extractor, so the cross-check is not the model grading itself.
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"

# USD per 1M tokens (input, output), list prices read on 2026-11-23 from
# aistudio.google.com/docs/pricing and console.groq.com/docs/models. Free-tier calls may bill $0.
PRICES_PER_M: dict[str, tuple[float, float]] = {
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "openai/gpt-oss-120b": (0.15, 0.60),
}


class UsageLedger:
    """Thread-safe token counter. Cost is an estimate at list price; unpriced models stay None."""

    def __init__(self, prices: Mapping[str, tuple[float, float]] | None = None) -> None:
        import threading

        self.prices = dict(PRICES_PER_M if prices is None else prices)
        self._lock = threading.Lock()
        self._rows: dict[str, dict[str, int]] = {}

    def add(self, model: str, input_tokens: int | None, output_tokens: int | None) -> None:
        with self._lock:
            row = self._rows.setdefault(model, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
            row["calls"] += 1
            row["input_tokens"] += int(input_tokens or 0)
            row["output_tokens"] += int(output_tokens or 0)

    def reset(self) -> None:
        with self._lock:
            self._rows.clear()

    def summary(self) -> dict[str, Any]:
        with self._lock:
            rows = {name: dict(row) for name, row in self._rows.items()}
        total = 0.0
        unpriced = []
        for name, row in rows.items():
            price = self.prices.get(name)
            if price is None:
                row["cost_usd"] = None
                unpriced.append(name)
                continue
            cost = row["input_tokens"] / 1e6 * price[0] + row["output_tokens"] / 1e6 * price[1]
            row["cost_usd"] = round(cost, 6)
            total += cost
        return {
            "models": rows,
            "cost_usd_known": round(total, 6),
            "unpriced_models": sorted(unpriced),
            "price_note": "estimate at published list price per 1M tokens; free-tier calls may bill $0",
        }


LEDGER = UsageLedger()

GEMINI_VERIFIER_FALLBACK = "gemini-3.1-flash-lite"
_MODEL_LIMITERS: dict[str, RateLimiter] = {}
_LIMITER_LOCK = threading.Lock()


def model_limiter(model: str, env: Mapping[str, str] | None = None) -> RateLimiter:
    """Free-tier Gemini quotas are per model per minute (15 RPM for flash-lite), so pace each model."""
    with _LIMITER_LOCK:
        if model not in _MODEL_LIMITERS:
            values = os.environ if env is None else env
            rpm = float(values.get("OPSRESEARCH_GEMINI_RPM", "14"))
            _MODEL_LIMITERS[model] = RateLimiter(min_interval_s=60.0 / rpm)
        return _MODEL_LIMITERS[model]


class FallbackClient:
    """Try each client in order; model_name reports the one that answered last."""

    def __init__(self, clients: list[LlmClient]) -> None:
        self.clients = list(clients)
        self.model_name = self.clients[0].model_name if self.clients else "none"

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        errors = []
        for client in self.clients:
            try:
                out = client.complete_json(prompt, schema)
            except Exception as exc:
                errors.append(f"{client.model_name}: {type(exc).__name__}")
                continue
            self.model_name = client.model_name
            return out
        raise RuntimeError("all verifier models failed: " + "; ".join(errors))


def verifier_clients(env: Mapping[str, str] | None = None) -> list[LlmClient]:
    values = os.environ if env is None else env
    chain: list[LlmClient] = []
    if values.get("GROQ_API_KEY"):
        chain.append(GroqJsonClient(values.get("GROQ_MODEL") or DEFAULT_GROQ_MODEL, api_key=values.get("GROQ_API_KEY")))
    chain.append(GeminiJsonClient(GEMINI_VERIFIER_FALLBACK, api_key=values.get("GEMINI_API_KEY")))
    return chain


def extraction_model_names(env: Mapping[str, str] | None = None) -> list[str]:
    values = os.environ if env is None else env
    raw = values.get("OPSRESEARCH_EXTRACT_MODELS", "")
    names = [part.strip() for part in raw.split(",") if part.strip()]
    return names or [GEMINI_EXTRACT_MODEL, GEMINI_EXTRACT_FALLBACK]


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
        model_limiter(self.model_name).wait()
        client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=120_000))
        response = client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0,
                response_mime_type="application/json",
                response_json_schema=schema,
            ),
        )
        usage = getattr(response, "usage_metadata", None)
        if usage is not None:
            out = (usage.candidates_token_count or 0) + (getattr(usage, "thoughts_token_count", 0) or 0)
            LEDGER.add(self.model_name, usage.prompt_token_count, out)
        text = getattr(response, "text", None) or ""
        return parse_json_object(text)


class GroqJsonClient:
    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        self.model_name = model or os.environ.get("GROQ_MODEL") or DEFAULT_GROQ_MODEL
        self._api_key = api_key

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        from groq import Groq

        key = self._api_key or os.environ["GROQ_API_KEY"]
        client = Groq(api_key=key, timeout=120, max_retries=0)
        # The schema is described in the prompt. Groq json_object mode does not take a JSON Schema.
        del schema
        response = client.chat.completions.create(
            model=self.model_name,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
        )
        usage = getattr(response, "usage", None)
        if usage is not None:
            LEDGER.add(self.model_name, usage.prompt_tokens, usage.completion_tokens)
        content = response.choices[0].message.content or ""
        return parse_json_object(content)


def extraction_clients(env: Mapping[str, str] | None = None) -> list[LlmClient]:
    values = os.environ if env is None else env
    clients: list[LlmClient] = [
        GeminiJsonClient(name, api_key=values.get("GEMINI_API_KEY")) for name in extraction_model_names(values)
    ]
    if values.get("GROQ_API_KEY"):
        clients.append(
            GroqJsonClient(values.get("GROQ_MODEL") or DEFAULT_GROQ_MODEL, api_key=values.get("GROQ_API_KEY"))
        )
    return clients


def verifier_fallback_message(env: Mapping[str, str] | None = None) -> str | None:
    values = os.environ if env is None else env
    if values.get("GROQ_API_KEY"):
        return None
    return f"GROQ_API_KEY missing; cross-model verifier using Gemini fallback model {GEMINI_VERIFIER_MODEL}"


def choose_verifier_backend(env: Mapping[str, str] | None = None) -> tuple[str, str]:
    values = os.environ if env is None else env
    if values.get("GROQ_API_KEY"):
        return "groq", values.get("GROQ_MODEL") or DEFAULT_GROQ_MODEL
    return "gemini", GEMINI_VERIFIER_MODEL


def build_verifier_client(env: Mapping[str, str] | None = None) -> LlmClient:
    message = verifier_fallback_message(env)
    if message:
        logger.warning("%s", message)
    return FallbackClient(verifier_clients(env))


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
