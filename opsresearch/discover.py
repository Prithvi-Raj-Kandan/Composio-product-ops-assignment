"""Search for official docs and drop third-party pages."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path
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
    hits: list[SearchHit] = [SearchHit(url=url, title=app.app, snippet="human override") for url in resolved.seed_urls]
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


COMPOSIO_SEARCH_USER = "opsresearch-product-ops"


class CachedSearch:
    """Store each query's hits under cache/_search/ so a rerun spends no search credits."""

    def __init__(self, inner: Any, cache_dir: Path) -> None:
        self.inner = inner
        self.name = f"{inner.name}+cache"
        self.dir = Path(cache_dir) / "_search"

    def search(self, query: str, *, max_results: int = 5) -> list[SearchHit]:
        import hashlib
        import json

        key = hashlib.sha256(f"{query}|{max_results}".encode()).hexdigest()[:16]
        path = self.dir / f"{key}.json"
        if path.exists():
            rows = json.loads(path.read_text(encoding="utf-8"))["hits"]
            return [SearchHit(**row) for row in rows]
        hits = self.inner.search(query, max_results=max_results)
        self.dir.mkdir(parents=True, exist_ok=True)
        payload = {"query": query, "hits": [h.model_dump() for h in hits]}
        path.write_text(json.dumps(payload), encoding="utf-8")
        return hits


PROBE_PATHS = {
    "self_serve": ["pricing", "developers", "docs/getting-started"],
    "gate_type": ["pricing", "partners", "developers"],
    "auth_methods": ["docs/authentication", "docs/api/authentication", "developers"],
    "api_surface": ["docs/api", "api", "developers/docs"],
    "existing_mcp": ["mcp", "docs/mcp"],
}


def probe_urls(host: str, fields: list[str]) -> list[str]:
    """Common doc paths on the official host, used only when search is unavailable."""
    host = host.replace("https://", "").replace("http://", "").strip("/").split("/")[0]
    if not host:
        return []
    out: list[str] = []
    for field in fields:
        for path in PROBE_PATHS.get(field.split(".")[0], []):
            url = f"https://{host}/{path}"
            if url not in out:
                out.append(url)
    return out[:6]


def cached_page_urls(cache_dir: Path, app: str) -> list[str]:
    """URLs already fetched for this app in earlier runs, oldest first."""
    from opsresearch.fetch import app_slug, read_cache

    folder = Path(cache_dir) / app_slug(app)
    if not folder.exists():
        return []
    files = sorted(folder.glob("*.md"), key=lambda p: (p.stat().st_mtime, p.name))
    urls = []
    for path in files:
        url, body = read_cache(path)
        if url and body.strip() and url not in urls:
            urls.append(url)
    return urls


class ComposioToolsSearch:
    """Run TAVILY_SEARCH through composio.tools.execute for a user with an active Tavily connection.

    The connection is a one-time human setup step (`python -m opsresearch connect-tavily`).
    """

    name = "composio"

    def __init__(self, composio: Any | None = None, user_id: str = COMPOSIO_SEARCH_USER) -> None:
        if composio is None:
            from composio import Composio

            composio = Composio()
        self._composio = composio
        self._user_id = user_id

    def search(self, query: str, *, max_results: int = 5) -> list[SearchHit]:
        raw = self._composio.tools.execute(
            "TAVILY_SEARCH",
            user_id=self._user_id,
            arguments={"query": query, "max_results": max_results},
            dangerously_skip_version_check=True,
        )
        if isinstance(raw, dict) and raw.get("successful") is False:
            raise RuntimeError("Composio TAVILY_SEARCH unsuccessful")
        hits = parse_search_payload(raw)
        if not hits:
            raise RuntimeError("Composio TAVILY_SEARCH returned no hits")
        return hits[:max_results]


class FallbackSearch:
    """Try each backend in order on every call, so a Composio hiccup falls through to direct Tavily."""

    def __init__(self, backends: list[Any]) -> None:
        self.backends = list(backends)
        self.name = "+".join(backend.name for backend in self.backends)
        self.used: dict[str, int] = {}
        import threading

        self._lock = threading.Lock()

    def search(self, query: str, *, max_results: int = 5) -> list[SearchHit]:
        errors: list[str] = []
        for backend in self.backends:
            try:
                hits = backend.search(query, max_results=max_results)
            except Exception as exc:
                errors.append(f"{backend.name}: {type(exc).__name__}")
                continue
            with self._lock:
                self.used[backend.name] = self.used.get(backend.name, 0) + 1
            return hits
        raise RuntimeError("all search backends failed (" + "; ".join(errors) + ")")


def connect_composio_tavily(api_key: str, user_id: str = COMPOSIO_SEARCH_USER) -> str:
    """One-time setup: custom API-key auth config plus a connected account. Returns the account id."""
    from composio import Composio

    composio = Composio()
    existing = composio.connected_accounts.list(toolkit_slugs=["tavily"], user_ids=[user_id])
    for item in getattr(existing, "items", []) or []:
        if str(getattr(item, "status", "")).upper() == "ACTIVE":
            return str(item.id)
    config = composio.auth_configs.create(
        "tavily", {"type": "use_custom_auth", "auth_scheme": "API_KEY", "credentials": {}}
    )
    request = composio.connected_accounts.initiate(
        user_id=user_id,
        auth_config_id=config.id,
        config={"auth_scheme": "API_KEY", "val": {"status": "ACTIVE", "generic_api_key": api_key}},
    )
    return str(request.id)


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
    chain: list[Any] = []
    if values.get("COMPOSIO_API_KEY"):
        factory = makers.get("composio")
        if factory is None:

            def factory():
                return ComposioToolsSearch()

        try:
            chain.append(factory())
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

        chain.append(factory())
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

        chain.append(factory())
    if len(chain) == 1:
        return chain[0]
    if chain:
        return FallbackSearch(chain)
    logger.warning("no search key set; discovery will use the hint URL only")
    return HintOnlySearch()
