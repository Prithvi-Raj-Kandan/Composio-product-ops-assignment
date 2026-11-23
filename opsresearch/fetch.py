"""Fetch a doc into cache/<app>/<hash>.md and reuse it on the next run."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol


class FetchClient(Protocol):
    def get_text(self, url: str) -> str: ...


@dataclass(frozen=True)
class CachedDoc:
    url: str
    text: str
    path: str
    cache_hit: bool


def app_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "app"


def cache_path(cache_dir: Path, app: str, url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return cache_dir / app_slug(app) / f"{digest}.md"


def _front_matter(url: str, fetched_at: str) -> str:
    return f"---\nurl: {url}\nfetched_at: {fetched_at}\n---\n\n"


def read_cache(path: Path) -> tuple[str, str]:
    raw = path.read_text(encoding="utf-8")
    if raw.startswith("---\n"):
        end = raw.find("\n---\n", 4)
        if end != -1:
            header = raw[4:end]
            body = raw[end + 5 :]
            url = ""
            for line in header.splitlines():
                if line.startswith("url:"):
                    url = line.split(":", 1)[1].strip()
            return url, body.lstrip("\n")
    return "", raw


def html_to_markdown(raw: str) -> str:
    lowered = raw.lower()
    looks_html = "<html" in lowered or "<body" in lowered or "<p" in lowered or "<div" in lowered
    if not looks_html:
        return raw.strip()
    try:
        import trafilatura

        extracted = trafilatura.extract(raw, output_format="markdown", include_comments=False)
        if extracted and extracted.strip():
            return extracted.strip()
    except Exception:
        pass
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", raw)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def fetch_url(
    url: str,
    app: str,
    cache_dir: Path,
    client: FetchClient,
    *,
    now: datetime | None = None,
) -> CachedDoc:
    path = cache_path(cache_dir, app, url)
    if path.exists():
        cached_url, body = read_cache(path)
        return CachedDoc(url=cached_url or url, text=body, path=str(path), cache_hit=True)
    raw = client.get_text(url)
    body = html_to_markdown(raw)
    stamp = (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_front_matter(url, stamp) + body, encoding="utf-8")
    return CachedDoc(url=url, text=body, path=str(path), cache_hit=False)


class HttpxFetchClient:
    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    def get_text(self, url: str) -> str:
        if self._client is not None:
            response = self._client.get(url)
        else:
            import httpx

            response = httpx.get(
                url,
                follow_redirects=True,
                timeout=30,
                headers={"User-Agent": "opsresearch/0.1"},
            )
        response.raise_for_status()
        return response.text
