"""Search for official docs and drop third-party pages."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any, Protocol

from pydantic import BaseModel

from opsresearch.seed import AppSeed, ResolvedDomain, hint_url, url_is_official

logger = logging.getLogger("opsresearch")


class SearchHit(BaseModel):
    url: str
    title: str = ""
    snippet: str = ""


class SearchClient(Protocol):
    name: str

    def search(self, query: str, *, max_results: int = 5) -> list[SearchHit]: ...


class HintOnlySearch:
    name = "hint-only"

    def search(self, query: str, *, max_results: int = 5) -> list[SearchHit]:
        return []


def discovery_queries(app: str, fields: list[str] | None = None) -> list[str]:
    if not fields:
        return [
            f"{app} official API authentication documentation",
            f"{app} developer API key self serve OR partner OR pricing",
            f"{app} official MCP server",
            f"{app} REST OR GraphQL API reference",
        ]
    targeted = {
        "auth_methods": f"{app} API authentication OAuth API key documentation",
        "self_serve": f"{app} API pricing free trial partner program signup",
        "gate_type": f"{app} API access requirements sales partner",
        "gate_detail": f"{app} API access requirements sales partner",
        "api_surface": f"{app} API reference REST GraphQL endpoints",
        "api_surface.rest": f"{app} REST API reference",
        "api_surface.graphql": f"{app} GraphQL API reference",
        "api_surface.breadth": f"{app} API endpoint list reference",
        "existing_mcp": f"{app} official MCP server model context protocol",
        "buildability": f"{app} public API documentation getting started",
        "main_blocker": f"{app} API limitations partner approval",
        "blocker_detail": f"{app} API limitations partner approval",
        "one_liner": f"{app} official product description",
        "composio_has_toolkit": f"{app} official API documentation",
    }
    queries = []
    for field in fields:
        queries.append(targeted.get(field, f"{app} {field} official documentation"))
    return queries


def filter_official(hits: list[SearchHit], resolved: ResolvedDomain) -> list[SearchHit]:
    kept: list[SearchHit] = []
    seen: set[str] = set()
    for hit in hits:
        if hit.url in seen:
            continue
        if not url_is_official(hit.url, resolved):
            continue
        seen.add(hit.url)
        kept.append(hit)
    return kept


def discover(
    app: AppSeed,
    resolved: ResolvedDomain,
    search: SearchClient,
    *,
    fields: list[str] | None = None,
    max_pages: int = 6,
) -> list[SearchHit]:
    hits: list[SearchHit] = [
        SearchHit(url=url, title=app.app, snippet="human override") for url in resolved.seed_urls
    ]
    for query in discovery_queries(app.app, fields):
        try:
            hits.extend(search.search(query, max_results=5))
        except Exception as exc:
            logger.warning("search failed for %s: %s", app.app, type(exc).__name__)
    official = filter_official(hits, resolved)
    if not official and resolved.source != "human_override":
        seed = hint_url(app, resolved)
        if seed:
            official = [SearchHit(url=seed, title=app.app, snippet="hint url")]
    return official[:max_pages]


def parse_search_payload(payload: Any) -> list[SearchHit]:
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump()
    if isinstance(payload, dict) and "data" in payload and "results" not in payload:
        payload = payload["data"]
    rows: list[Any]
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("results") or payload.get("items") or []
    else:
        rows = []
    hits: list[SearchHit] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = row.get("url") or row.get("link") or row.get("href")
        if not url:
            continue
        hits.append(
            SearchHit(
                url=str(url),
                title=str(row.get("title") or ""),
                snippet=str(row.get("content") or row.get("snippet") or row.get("text") or ""),
            )
        )
    return hits


class DirectHttpSearch:
    def __init__(self, name: str, endpoint: str, key: str, key_location: str) -> None:
        self.name = name
        self.endpoint = endpoint
        self._key = key
        self._key_location = key_location

    def search(self, query: str, *, max_results: int = 5) -> list[SearchHit]:
        import httpx

        if self._key_location == "body":
            response = httpx.post(
                self.endpoint,
                json={"api_key": self._key, "query": query, "max_results": max_results},
                timeout=30,
            )
        else:
            response = httpx.post(
                self.endpoint,
                headers={"x-api-key": self._key},
                json={"query": query, "numResults": max_results},
                timeout=30,
            )
        response.raise_for_status()
        return parse_search_payload(response.json())


class ComposioSessionSearch:
    """Run Tavily or Exa through a Composio session. The session is injected so tests stay offline."""

    name = "composio"

    def __init__(self, session: Any, slugs: tuple[str, ...] = ("TAVILY_SEARCH", "EXA_SEARCH")) -> None:
        self._session = session
        self._slugs = slugs

    def search(self, query: str, *, max_results: int = 5) -> list[SearchHit]:
        errors: list[str] = []
        for slug in self._slugs:
            arguments = {"query": query, "max_results": max_results}
            if slug.startswith("EXA"):
                arguments = {"query": query, "num_results": max_results}
            try:
                raw = self._session.execute(slug, arguments=arguments)
                hits = parse_search_payload(raw)
                if hits:
                    return hits[:max_results]
            except Exception as exc:
                errors.append(f"{slug}: {type(exc).__name__}")
        raise RuntimeError("Composio search returned no hits (" + "; ".join(errors) + ")")


def open_composio_session(toolkits: list[str] | None = None) -> Any:
    from composio import Composio
    from composio_langchain import LangchainProvider

    composio = Composio(provider=LangchainProvider())
    return composio.create(user_id="opsresearch-product-ops", toolkits=toolkits or ["tavily", "exa"])


def select_search_backend(
    env: Mapping[str, str] | None = None,
    *,
    factories: Mapping[str, Any] | None = None,
) -> SearchClient:
    values = dict(os.environ if env is None else env)
    makers = dict(factories or {})
    if values.get("COMPOSIO_API_KEY"):
        factory = makers.get("composio")
        if factory is None:

            def factory():
                return ComposioSessionSearch(open_composio_session())

        try:
            return factory()
        except Exception as exc:
            logger.warning("Composio search unavailable (%s); trying direct keys", type(exc).__name__)
    if values.get("TAVILY_API_KEY"):
        factory = makers.get("tavily")
        if factory is None:

            def factory():
                return DirectHttpSearch(
                    "tavily",
                    "https://api.tavily.com/search",
                    values["TAVILY_API_KEY"],
                    "body",
                )

        return factory()
    if values.get("EXA_API_KEY"):
        factory = makers.get("exa")
        if factory is None:

            def factory():
                return DirectHttpSearch(
                    "exa",
                    "https://api.exa.ai/search",
                    values["EXA_API_KEY"],
                    "header",
                )

        return factory()
    logger.warning("no search key set; discovery will use the hint URL only")
    return HintOnlySearch()
