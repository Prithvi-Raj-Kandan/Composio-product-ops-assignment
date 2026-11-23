"""Check each configured service on one app. Key values are never printed."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass

SECRET_ENV = (
    "COMPOSIO_API_KEY",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "TAVILY_API_KEY",
    "EXA_API_KEY",
)


@dataclass(frozen=True)
class SmokeRow:
    service: str
    status: str
    detail: str


def redact(text: str, env: Mapping[str, str]) -> str:
    redacted = text
    for name in SECRET_ENV:
        secret = env.get(name) or ""
        if len(secret) >= 4:
            redacted = redacted.replace(secret, "[redacted]")
    return redacted


def format_table(rows: list[SmokeRow]) -> str:
    headers = ("service", "status", "detail")
    body = [(row.service, row.status, row.detail) for row in rows]
    widths = [len(headers[index]) for index in range(3)]
    for row in body:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    lines = [
        "  ".join(headers[index].ljust(widths[index]) for index in range(3)),
        "  ".join("-" * widths[index] for index in range(3)),
    ]
    for row in body:
        lines.append("  ".join(row[index].ljust(widths[index]) for index in range(3)))
    return "\n".join(lines)


def _row_for(
    service: str,
    env_name: str,
    env: Mapping[str, str],
    probe: Callable[[], str] | None,
    *,
    optional: bool,
    missing_detail: str,
) -> SmokeRow:
    if not env.get(env_name):
        status = "skipped" if optional else "missing"
        return SmokeRow(service, status, missing_detail)
    if probe is None:
        return SmokeRow(service, "error", "no probe configured")
    try:
        detail = probe()
    except Exception as exc:
        return SmokeRow(service, "error", type(exc).__name__)
    return SmokeRow(service, "ok", detail)


def run_smoke(
    app: str,
    *,
    env: Mapping[str, str] | None = None,
    probes: Mapping[str, Callable[[], str]] | None = None,
) -> tuple[list[SmokeRow], int, str]:
    values = dict(os.environ if env is None else env)
    calls = dict(probes or {})
    rows = [
        _row_for(
            "composio",
            "COMPOSIO_API_KEY",
            values,
            calls.get("composio"),
            optional=False,
            missing_detail="COMPOSIO_API_KEY is not set",
        ),
        _row_for(
            "gemini",
            "GEMINI_API_KEY",
            values,
            calls.get("gemini"),
            optional=False,
            missing_detail="GEMINI_API_KEY is not set",
        ),
        _row_for(
            "groq",
            "GROQ_API_KEY",
            values,
            calls.get("groq"),
            optional=True,
            missing_detail="GROQ_API_KEY not set; cross-model verifier will use a second Gemini call",
        ),
        _row_for(
            "tavily",
            "TAVILY_API_KEY",
            values,
            calls.get("tavily"),
            optional=True,
            missing_detail="optional; direct Tavily search is not configured",
        ),
        _row_for(
            "exa",
            "EXA_API_KEY",
            values,
            calls.get("exa"),
            optional=True,
            missing_detail="optional; direct Exa search is not configured",
        ),
    ]
    exit_code = 0 if all(row.status in {"ok", "skipped"} for row in rows) else 1
    table = redact(format_table(rows), values)
    table = f"smoke app: {app}\n{table}"
    return rows, exit_code, table


def live_probes(app: str) -> dict[str, Callable[[], str]]:
    """Real one-shot probes. Used by the CLI, not by unit tests."""

    def composio() -> str:
        from opsresearch.discover import ComposioToolsSearch
        from opsresearch.verify import ComposioToolkitClient

        snapshot = ComposioToolkitClient().lookup(app)
        found = f"lookup returned slug {snapshot.slug}" if snapshot else f"lookup for {app} returned no toolkit"
        try:
            ComposioToolsSearch().search(f"{app} API documentation", max_results=1)
            return found + "; TAVILY_SEARCH ok"
        except Exception as exc:
            return found + f"; TAVILY_SEARCH {type(exc).__name__} (run connect-tavily)"

    def gemini() -> str:
        from opsresearch.llm import GeminiJsonClient, extraction_model_names

        errors = []
        for name in extraction_model_names():
            try:
                GeminiJsonClient(name).complete_json(
                    'Reply with JSON {"ok": true} and nothing else.', {"type": "object"}
                )
                return f"{name} responded" + (f" (after {'; '.join(errors)})" if errors else "")
            except Exception as exc:
                errors.append(f"{name} {type(exc).__name__}")
        raise RuntimeError("; ".join(errors))

    def groq() -> str:
        from opsresearch.llm import GroqJsonClient

        client = GroqJsonClient()
        client.complete_json('Reply with JSON {"ok": true}.', {"type": "object"})
        return f"{client.model_name} responded"

    def tavily() -> str:
        from opsresearch.discover import DirectHttpSearch

        DirectHttpSearch("tavily", "https://api.tavily.com/search", os.environ["TAVILY_API_KEY"], "body").search(
            f"{app} API documentation",
            max_results=1,
        )
        return "Tavily search returned"

    def exa() -> str:
        from opsresearch.discover import DirectHttpSearch

        DirectHttpSearch("exa", "https://api.exa.ai/search", os.environ["EXA_API_KEY"], "header").search(
            f"{app} API documentation",
            max_results=1,
        )
        return "Exa search returned"

    return {
        "composio": composio,
        "gemini": gemini,
        "groq": groq,
        "tavily": tavily,
        "exa": exa,
    }
