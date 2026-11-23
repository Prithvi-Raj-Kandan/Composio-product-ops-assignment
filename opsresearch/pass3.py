"""Pass 3: cut 'unknown' without guessing.

1. Policy fix: a pass-1 claim whose verbatim quote is on the cached official page, and that no
   verifier contradicted, is kept as quote-verified even if the second model said insufficient.
   Only contradicted demotes a claim.
2. Composio metadata: composio_has_toolkit and, for covered apps, auth_methods from the toolkit API.
3. MCP registry for existing_mcp still unknown.
4. Re-research without search: probe common doc paths on official hosts, extract only the
   still-unknown fields with strict quotes, then a second model checks the new claims.
5. buildability / main_blocker re-derived by the fixed rule from evidenced fields.
"""

from __future__ import annotations

import copy
import json
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from opsresearch.extract import derive_verdict
from opsresearch.fetch import CachedDoc, app_slug, cache_path, read_cache
from opsresearch.schema import AppRecord
from opsresearch.textmatch import quote_in_text
from opsresearch.verify import AUTH_MODE_MAP, slug_candidates

logger = logging.getLogger("opsresearch")

FIELDS = [
    "one_liner",
    "auth_methods",
    "self_serve",
    "gate_type",
    "gate_detail",
    "api_surface.rest",
    "api_surface.graphql",
    "api_surface.breadth",
    "api_surface.styles",
    "existing_mcp",
    "buildability",
    "main_blocker",
    "blocker_detail",
]
COUNTED = [
    "self_serve",
    "gate_type",
    "buildability",
    "main_blocker",
    "auth_methods",
    "existing_mcp",
    "composio_has_toolkit",
    "one_liner",
    "api_surface.rest",
]
RESEARCH_FIELDS = [
    "auth_methods",
    "self_serve",
    "gate_type",
    "api_surface.rest",
    "api_surface.graphql",
    "one_liner",
]
PROBE_PATHS = [
    "docs",
    "api",
    "developers",
    "authentication",
    "auth",
    "oauth",
    "pricing",
    "getting-started",
    "api-reference",
    "reference",
]
DEMOTING = {"cross_model", "quote"}
COMPOSIO_API = "https://backend.composio.dev/api/v3.1/toolkits"


def get_field(record: dict[str, Any], field: str) -> Any:
    if field.startswith("api_surface."):
        return record["api_surface"][field.split(".", 1)[1]]
    return record[field]


def set_field(record: dict[str, Any], field: str, value: Any) -> None:
    if field.startswith("api_surface."):
        record["api_surface"][field.split(".", 1)[1]] = value
    else:
        record[field] = value


def is_unknown(value: Any) -> bool:
    if isinstance(value, list):
        return [v for v in value if v != "unknown"] == []
    return value in (None, "unknown", "")


def _unknown_value(field: str) -> Any:
    return ["unknown"] if field in ("auth_methods", "api_surface.styles") else "unknown"


def _evidence_for(record: dict[str, Any], field: str) -> list[dict[str, Any]]:
    names = {field}
    if field.startswith("api_surface."):
        names.add("api_surface")
    return [e for e in record.get("evidence") or [] if e["field"] in names]


def count_unknowns(records: list[dict[str, Any]]) -> dict[str, int]:
    return {f: sum(1 for r in records if is_unknown(get_field(r, f))) for f in COUNTED}


def _verdict_map(findings: list[dict[str, Any]]) -> dict[str, str]:
    """Per field: contradicted (by cross_model or quote check) > supported > insufficient."""
    out: dict[str, str] = {}
    rank = {"insufficient": 0, "flag": 0, "supported": 1, "contradicted": 2}
    for item in findings:
        verifier, field, status = item.get("verifier"), item.get("field"), item.get("status")
        if field == "record":
            continue
        if status == "contradicted" and verifier not in DEMOTING:
            continue
        if verifier not in DEMOTING and verifier != "cross_model":
            continue
        if verifier == "quote" and status != "contradicted":
            continue
        if rank.get(status, 0) >= rank.get(out.get(field, "insufficient"), 0) or field not in out:
            out[field] = status
    return out


def _verify_nodes(log: dict[str, Any]) -> list[dict[str, Any]]:
    return [n for n in (log or {}).get("nodes") or [] if n.get("node") == "verify"]


def loop0_verdicts(log: dict[str, Any]) -> dict[str, str]:
    """Verdicts on the pass-1 record, which the first pass-2 verify loop judged."""
    nodes = _verify_nodes(log)
    return _verdict_map(nodes[0].get("findings") or []) if nodes else {}


def final_verdicts(log: dict[str, Any]) -> dict[str, str]:
    nodes = _verify_nodes(log)
    return _verdict_map(nodes[-1].get("findings") or []) if nodes else {}


def _verdict(verdicts: dict[str, str], field: str) -> str:
    if field in verdicts:
        return verdicts[field]
    if field.startswith("api_surface.") and "api_surface" in verdicts:
        return verdicts["api_surface"]
    return "not_run"


def page_text(cache_dir: Path, app: str, url: str) -> str | None:
    path = cache_path(cache_dir, app, url)
    if not path.exists():
        return None
    return read_cache(path)[1]


def quote_checks(cache_dir: Path, app: str, evidence: list[dict[str, Any]]) -> bool:
    docs = [e for e in evidence if e.get("source") == "doc"]
    if not docs:
        return False
    for item in docs:
        text = page_text(cache_dir, app, item["url"])
        if text is None or not quote_in_text(item["quote"], text):
            return False
    return True


def _tag(record: dict[str, Any], field: str, confidence: str, verdict: str | None = None) -> None:
    record.setdefault("field_confidence", {})[field] = confidence
    if verdict is not None:
        record.setdefault("verifier_verdicts", {})[field] = verdict


def tag_existing(record: dict[str, Any], verdicts: dict[str, str], cache_dir: Path) -> None:
    for field in FIELDS + ["composio_has_toolkit"]:
        if is_unknown(get_field(record, field)) or field in record.get("field_confidence", {}):
            continue
        ev = _evidence_for(record, field)
        sources = {e["source"] for e in ev}
        verdict = _verdict(verdicts, field)
        if "doc" in sources:
            ok = quote_checks(cache_dir, record["app"], ev)
            if ok and verdict == "supported":
                conf = "verifier-supported"
            elif ok:
                conf = "quote-verified"
            else:
                conf = "quote-not-rechecked"
        elif "derived_rule" in sources:
            conf = "derived_rule"
        elif sources & {"composio_oracle", "composio_metadata"}:
            conf = "composio_metadata"
        elif "mcp_registry" in sources:
            conf = "mcp_registry"
        else:
            conf = "quote-not-rechecked"
        _tag(record, field, conf, verdict)


def demote_contradicted(record: dict[str, Any], verdicts: dict[str, str]) -> list[str]:
    demoted = []
    for field in FIELDS:
        if field in ("buildability", "main_blocker"):
            continue
        if _verdict(verdicts, field) != "contradicted" or is_unknown(get_field(record, field)):
            continue
        set_field(record, field, _unknown_value(field))
        record["evidence"] = [e for e in record["evidence"] if e not in _evidence_for(record, field)]
        record["unknown_reasons"][field] = "contradicted by the second-model verifier in pass 2"
        record.setdefault("verifier_verdicts", {})[field] = "contradicted"
        record.get("field_confidence", {}).pop(field, None)
        demoted.append(field)
    return demoted


def restore_from_prior(
    record: dict[str, Any],
    prior: dict[str, Any] | None,
    verdicts: dict[str, str],
    cache_dir: Path,
) -> tuple[dict[str, Any], list[str]]:
    out = copy.deepcopy(record)
    filled: list[str] = []
    if not prior:
        return out, filled
    for field in FIELDS:
        if field in ("buildability", "main_blocker", "blocker_detail"):
            continue  # rule-derived below
        if not is_unknown(get_field(out, field)) or is_unknown(get_field(prior, field)):
            continue
        verdict = _verdict(verdicts, field)
        if verdict == "contradicted":
            continue
        ev = [e for e in _evidence_for(prior, field) if e["source"] == "doc"]
        if not ev or not quote_checks(cache_dir, prior["app"], ev):
            continue
        set_field(out, field, copy.deepcopy(get_field(prior, field)))
        if field == "existing_mcp" and prior.get("mcp_url") and not out.get("mcp_url"):
            out["mcp_url"] = prior["mcp_url"]
        known = {(e["field"], e["url"], e["quote"]) for e in out["evidence"]}
        out["evidence"] += [e for e in ev if (e["field"], e["url"], e["quote"]) not in known]
        out["unknown_reasons"].pop(field, None)
        _tag(out, field, "verifier-supported" if verdict == "supported" else "quote-verified", verdict)
        filled.append(field)
    for key in ("official_domain", "extraction_model"):
        if not out.get(key) and prior.get(key):
            out[key] = prior[key]
    return out, filled


# ---------- Composio metadata ----------


class ComposioHttp:
    def __init__(self, api_key: str, client: Any | None = None) -> None:
        import httpx

        self._client = client or httpx.Client(timeout=30, headers={"x-api-key": api_key})

    def get(self, slug: str) -> dict[str, Any] | None:
        response = self._client.get(f"{COMPOSIO_API}/{slug}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) and data.get("slug") else None

    def search(self, name: str) -> list[dict[str, Any]]:
        response = self._client.get(COMPOSIO_API, params={"search": name, "limit": 10})
        response.raise_for_status()
        return list(response.json().get("items") or [])


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def find_toolkit(app: str, client: Any) -> tuple[dict[str, Any] | None, list[str]]:
    tried: list[str] = []
    for slug in slug_candidates(app):
        tried.append(slug)
        meta = client.get(slug)
        if meta:
            return meta, tried
    target = _norm(app)
    for item in client.search(app):
        if _norm(item.get("name", "")) == target or _norm(item.get("slug", "")) == target:
            slug = item.get("slug")
            tried.append(f"search:{app}")
            full = client.get(slug) if slug and slug not in tried else None
            return (full or item), tried
    tried.append(f"search:{app}")
    return None, tried


def toolkit_auth_modes(meta: dict[str, Any]) -> list[str]:
    raw: list[str] = []
    for detail in meta.get("auth_config_details") or []:
        if isinstance(detail, dict) and detail.get("mode"):
            raw.append(str(detail["mode"]))
    raw += [str(x) for x in meta.get("auth_schemes") or []]
    raw += [str(x) for x in meta.get("composio_managed_auth_schemes") or []]
    modes: list[str] = []
    for item in raw:
        mapped = AUTH_MODE_MAP.get(item.strip().upper().replace("-", "_").replace(" ", "_"))
        if mapped and mapped not in modes:
            modes.append(mapped)
    return modes


def apply_composio_metadata(
    record: dict[str, Any], meta: dict[str, Any] | None, *, tried: list[str]
) -> tuple[dict[str, Any], list[str]]:
    out = copy.deepcopy(record)
    filled: list[str] = []
    was_unknown = is_unknown(out["composio_has_toolkit"])
    out["evidence"] = [e for e in out["evidence"] if e["field"] != "composio_has_toolkit"]
    if meta:
        slug = meta.get("slug")
        modes = toolkit_auth_modes(meta)
        tools = (meta.get("meta") or {}).get("tools_count")
        url = f"{COMPOSIO_API}/{slug}"
        out["composio_has_toolkit"] = "yes"
        out["composio_slug"] = slug
        out["composio_toolkit_type"] = meta.get("toolkit_type")
        out["composio_tools_count"] = tools if isinstance(tools, int) else None
        out["evidence"].append(
            {
                "field": "composio_has_toolkit",
                "url": url,
                "quote": f"slug={slug} toolkit_type={meta.get('toolkit_type')} tools_count={tools} "
                f"auth_schemes={','.join(m.upper() for m in modes) or 'none listed'}",
                "source": "composio_metadata",
            }
        )
        if is_unknown(out["auth_methods"]) and modes:
            out["auth_methods"] = modes
            out["evidence"].append(
                {
                    "field": "auth_methods",
                    "url": url,
                    "quote": "auth_config_details / composio_managed_auth_schemes: "
                    + ", ".join(m.upper() for m in modes),
                    "source": "composio_metadata",
                }
            )
            out["unknown_reasons"].pop("auth_methods", None)
            _tag(out, "auth_methods", "composio_metadata", "not_run")
            filled.append("auth_methods")
    else:
        out["composio_has_toolkit"] = "no"
        out["composio_slug"] = None
        out["composio_toolkit_type"] = None
        out["composio_tools_count"] = None
        out["evidence"].append(
            {
                "field": "composio_has_toolkit",
                "url": f"{COMPOSIO_API}/{tried[0] if tried else app_slug(out['app'])}",
                "quote": f"no toolkit for slugs {', '.join(t for t in tried if not t.startswith('search:'))} "
                f"and no exact name match in toolkit search",
                "source": "composio_metadata",
            }
        )
    out["unknown_reasons"].pop("composio_has_toolkit", None)
    _tag(out, "composio_has_toolkit", "composio_metadata", "not_run")
    if was_unknown:
        filled.insert(0, "composio_has_toolkit")
    return out, filled


# ---------- re-research ----------


def _host(value: str) -> str:
    value = value.strip()
    if "://" not in value:
        value = "https://" + value
    return (urlparse(value).hostname or "").lower()


def probe_targets(sources: list[str]) -> list[str]:
    urls: list[str] = []
    for src in sources:
        if not src:
            continue
        if "://" in src and urlparse(src).path.strip("/"):
            if src not in urls:
                urls.append(src)
        host = _host(src)
        if not host:
            continue
        for path in PROBE_PATHS:
            url = f"https://{host}/{path}"
            if url not in urls:
                urls.append(url)
    return urls


KEYWORDS = {
    "auth_methods": ["oauth", "api key", "bearer", "token", "authenticat"],
    "self_serve": ["free", "trial", "pricing", "sign up", "contact sales", "partner"],
    "gate_type": ["approval", "partner", "contact sales", "plan", "enterprise"],
    "api_surface.rest": ["rest", "endpoint", "http", "json"],
    "api_surface.graphql": ["graphql"],
    "one_liner": [],
}


def rank_docs(docs: list[CachedDoc], fields: list[str]) -> list[CachedDoc]:
    words = [w for f in fields for w in KEYWORDS.get(f, [])]

    def score(doc: CachedDoc) -> int:
        low = doc.text[:20000].lower()
        return sum(low.count(w) for w in words) + (5 if len(doc.text) > 500 else -50)

    return sorted(docs, key=score, reverse=True)


def merge_new_fields(
    base: dict[str, Any], update: dict[str, Any], fields: list[str]
) -> tuple[dict[str, Any], list[str]]:
    out = copy.deepcopy(base)
    filled: list[str] = []
    for field in fields:
        if not is_unknown(get_field(out, field)) or is_unknown(get_field(update, field)):
            continue
        ev = [e for e in _evidence_for(update, field) if e["source"] == "doc"]
        if not ev:
            continue
        set_field(out, field, copy.deepcopy(get_field(update, field)))
        for e in ev:
            out["evidence"].append(dict(e, field=field))
        out["unknown_reasons"].pop(field, None)
        _tag(out, field, "quote-verified", "not_run")
        filled.append(field)
    if "self_serve" in filled and is_unknown(out["gate_detail"]) and not is_unknown(update.get("gate_detail")):
        out["gate_detail"] = update["gate_detail"]
    return out, filled


def rederive(record: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    data = copy.deepcopy(record)
    before = {f: data[f] for f in ("buildability", "main_blocker")}
    for field in ("buildability", "main_blocker"):
        if any(e["field"] == field and e["source"] == "derived_rule" for e in data["evidence"]):
            data[field] = "unknown"
    data["evidence"] = [e for e in data["evidence"] if e["source"] != "derived_rule"]
    derived = derive_verdict(AppRecord.model_validate(data)).model_dump(mode="json")
    derived["field_confidence"] = data.get("field_confidence", {})
    derived["verifier_verdicts"] = data.get("verifier_verdicts", {})
    filled = []
    for field in ("buildability", "main_blocker"):
        if any(e["field"] == field and e["source"] == "derived_rule" for e in derived["evidence"]):
            _tag(derived, field, "derived_rule")
            if is_unknown(before[field]):
                filled.append(field)
        elif is_unknown(derived[field]):
            derived["field_confidence"].pop(field, None)
    return derived, filled


def pass3_record(
    p2: dict[str, Any],
    p1: dict[str, Any] | None,
    log2: dict[str, Any] | None,
    cache_dir: Path,
    *,
    toolkit: tuple[dict[str, Any] | None, list[str]] | None = None,
    registry: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    researcher: Callable[[dict[str, Any], list[str]], tuple[dict[str, Any], list[str]]] | None = None,
) -> tuple[dict[str, Any], dict[str, int]]:
    steps = {
        "demoted_contradicted": 0,
        "policy_restore": 0,
        "composio_metadata": 0,
        "mcp_registry": 0,
        "reresearch": 0,
        "derived_rule": 0,
    }
    record = copy.deepcopy(p2)
    record.setdefault("field_confidence", {})
    record.setdefault("verifier_verdicts", {})
    crashed = any("pipeline error" in r for r in record.get("needs_human_reasons") or [])
    final = final_verdicts(log2 or {})
    first = loop0_verdicts(log2 or {})
    steps["demoted_contradicted"] = len(demote_contradicted(record, final))
    tag_existing(record, final, cache_dir)
    record, filled = restore_from_prior(record, p1, first, cache_dir)
    steps["policy_restore"] = len(filled)
    if toolkit is not None:
        record, filled = apply_composio_metadata(record, toolkit[0], tried=toolkit[1])
        steps["composio_metadata"] = len(filled)
    if registry is not None and is_unknown(record["existing_mcp"]):
        before = record["existing_mcp"]
        record = registry(record)
        if is_unknown(before) and not is_unknown(record["existing_mcp"]):
            _tag(record, "existing_mcp", "mcp_registry", "not_run")
            steps["mcp_registry"] = 1
    if researcher is not None:
        todo = [f for f in RESEARCH_FIELDS if f != "one_liner" and is_unknown(get_field(record, f))]
        if todo and is_unknown(record["one_liner"]):
            todo.append("one_liner")
        if todo:
            record, filled = researcher(record, todo)
            steps["reresearch"] = len(filled)
    record, filled = rederive(record)
    steps["derived_rule"] = len(filled)
    reasons = [r for r in record.get("needs_human_reasons") or [] if "pipeline error" not in r]
    if crashed:
        reasons.append(
            "pass 2 extraction crashed (model quota); pass 3 kept only pass-1 claims whose quotes "
            "are on the cached page, with no second-model check"
        )
    record["needs_human_reasons"] = reasons
    record["needs_human"] = bool(reasons) or bool(record.get("needs_human") and not crashed)
    record["pass_id"] = 3
    return AppRecord.model_validate(record).model_dump(mode="json"), steps


# ---------- live research helper ----------


def load_docs(cache_dir: Path, app: str) -> list[CachedDoc]:
    folder = Path(cache_dir) / app_slug(app)
    out = []
    for path in sorted(folder.glob("*.md")) if folder.exists() else []:
        url, body = read_cache(path)
        if url and body.strip():
            out.append(CachedDoc(url=url, text=body, path=str(path), cache_hit=True))
    return out


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def now() -> float:
    return time.monotonic()


class _ShortClient:
    """httpx GET with a short timeout, so dead probe paths do not stall the run."""

    def get(self, url: str) -> Any:
        import httpx

        return httpx.get(url, follow_redirects=True, timeout=10, headers={"User-Agent": "opsresearch/0.1"})


class LiveResearcher:
    """Probe official doc paths, extract only still-unknown fields, then a second model checks them."""

    def __init__(self, cache_dir: Path, seed: Any, resolved: Any, extra_sources: list[str], log: list) -> None:
        self.cache_dir = cache_dir
        self.seed = seed
        self.resolved = resolved
        self.extra_sources = extra_sources
        self.log = log

    def _probe(self, urls: list[str]) -> int:
        from concurrent.futures import ThreadPoolExecutor

        from opsresearch.fetch import HttpxFetchClient, fetch_url

        fails_path = self.cache_dir / "probe_failures" / f"{app_slug(self.seed.app)}.json"
        failed = set(json.loads(fails_path.read_text())) if fails_path.exists() else set()
        client = HttpxFetchClient(_ShortClient())
        todo = [u for u in urls if u not in failed and not cache_path(self.cache_dir, self.seed.app, u).exists()]

        def get(url: str) -> bool:
            try:
                doc = fetch_url(url, self.seed.app, self.cache_dir, client)
            except Exception:
                failed.add(url)
                return False
            if len(doc.text) < 200:
                Path(doc.path).unlink(missing_ok=True)
                failed.add(url)
                return False
            return True

        with ThreadPoolExecutor(max_workers=6) as pool:
            fetched = sum(pool.map(get, todo))
        write_json(fails_path, sorted(failed))
        return fetched

    def __call__(self, record: dict[str, Any], todo: list[str]) -> tuple[dict[str, Any], list[str]]:
        from opsresearch.extract import extract_record
        from opsresearch.llm import GeminiJsonClient, GroqJsonClient
        from opsresearch.ratelimit import get_rate_limiter
        from opsresearch.seed import url_is_official

        sources = [record.get("official_domain") or "", self.resolved.display_domain, *self.extra_sources]
        urls = [u for u in probe_targets(sources) if url_is_official(u, self.resolved)][:14]
        fetched = self._probe(urls)
        docs = [d for d in load_docs(self.cache_dir, self.seed.app) if url_is_official(d.url, self.resolved)]
        docs = [d for d in docs if len(d.text) >= 200]
        entry: dict[str, Any] = {
            "node": "reresearch3",
            "fields": todo,
            "probed": len(urls),
            "fetched": fetched,
            "docs": len(docs),
        }
        self.log.append(entry)
        if not docs:
            return record, []
        ranked = rank_docs(docs, todo)
        limiter = get_rate_limiter()
        update = None
        attempts = [
            (GeminiJsonClient("gemini-3.5-flash"), ranked[:5], 8000),
            (GroqJsonClient("openai/gpt-oss-20b"), ranked[:2], 3500),
        ]
        for client, chosen, clip in attempts:
            clipped = [CachedDoc(url=d.url, text=d.text[:clip], path=d.path, cache_hit=True) for d in chosen]
            try:
                update = extract_record(
                    self.seed.app, record["category"], clipped, [client], limiter, only_fields=todo
                ).model_dump(mode="json")
                entry["model"] = client.model_name
                break
            except Exception as exc:
                entry.setdefault("errors", []).append(f"{client.model_name}: {type(exc).__name__}")
        if update is None:
            return record, []
        merged, filled = merge_new_fields(record, update, todo)
        if not filled:
            return merged, []
        return self._verify(merged, filled, ranked, entry)

    def _verify(self, record: dict[str, Any], filled: list[str], docs: list[CachedDoc], entry: dict) -> tuple:
        from opsresearch.llm import GeminiJsonClient, GroqJsonClient
        from opsresearch.verify import CROSS_SCHEMA, cross_model_prompt, parse_cross_model

        temp = copy.deepcopy(record)
        temp["evidence"] = [e for e in record["evidence"] if e["field"] in filled and e["source"] == "doc"]
        pages = {d.url: d.text for d in docs}
        prompt = cross_model_prompt(AppRecord.model_validate(temp), pages)
        extractor = entry.get("model")
        chain = [GroqJsonClient("openai/gpt-oss-20b"), GeminiJsonClient("gemini-3.5-flash")]
        chain = [c for c in chain if c.model_name != extractor]
        verdicts: dict[str, str] = {}
        for client in chain:
            try:
                findings = parse_cross_model(client.complete_json(prompt, CROSS_SCHEMA))
                verdicts = {f.field: f.status for f in findings if f.field != "record"}
                entry["verifier"] = client.model_name
                break
            except Exception as exc:
                entry.setdefault("errors", []).append(f"verify {client.model_name}: {type(exc).__name__}")
        kept = []
        for field in filled:
            verdict = _verdict(verdicts, field)
            if verdict == "contradicted":
                set_field(record, field, _unknown_value(field))
                record["evidence"] = [e for e in record["evidence"] if e not in _evidence_for(record, field)]
                record["unknown_reasons"][field] = "pass 3 claim contradicted by the second-model verifier"
                record["field_confidence"].pop(field, None)
                record["verifier_verdicts"][field] = "contradicted"
                continue
            _tag(record, field, "verifier-supported" if verdict == "supported" else "quote-verified", verdict)
            kept.append(field)
        entry["kept"] = kept
        return record, kept


def research_priority(record: dict[str, Any]) -> int:
    """Apps missing the fields the page leads with are researched first."""
    weights = {"self_serve": 4, "auth_methods": 3, "api_surface.rest": 2, "gate_type": 1}
    return -sum(w for f, w in weights.items() if is_unknown(get_field(record, f)))


def run_pass3(
    repo: Path,
    *,
    resume: bool = True,
    concurrency: int = 4,
    only: str = "",
    research: bool = True,
    deadline: float | None = None,
) -> dict:
    import os
    from concurrent.futures import ThreadPoolExecutor
    from urllib.parse import quote as urlquote

    from opsresearch.llm import LEDGER
    from opsresearch.seed import load_apps, load_overrides, resolve_domain, url_is_official
    from opsresearch.verify import MCP_REGISTRY_URL, HttpRegistryClient, apply_registry

    data = repo / "data"
    cache = repo / "cache"
    ckpt = cache / "checkpoints"
    p1 = {r["app"].lower(): r for r in json.loads((data / "results_pass1.json").read_text())["records"]}
    p2 = json.loads((data / "results_pass2.json").read_text())["records"]
    seeds = {s.app.lower(): s for s in load_apps(data / "apps.csv")}
    overrides = load_overrides(data / "domain_overrides.csv")
    composio = ComposioHttp(os.environ["COMPOSIO_API_KEY"])
    registry_client = HttpRegistryClient()
    LEDGER.reset()
    started = time.time()
    if only:
        p2 = [r for r in p2 if r["app"].lower() == only.lower()]

    def one(rec2: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        slug = app_slug(rec2["app"])
        path = ckpt / f"{slug}_pass3.json"
        if resume and path.exists():
            saved = json.loads(path.read_text())
            return saved["record"], saved["log"]
        seed = seeds.get(rec2["app"].lower())
        log_path = data / "runs" / f"{slug}_pass2.json"
        log2 = json.loads(log_path.read_text()) if log_path.exists() else {}
        resolved = resolve_domain(seed, overrides)
        nodes: list[dict[str, Any]] = []
        try:
            meta, tried = find_toolkit(rec2["app"], composio)
        except Exception as exc:
            meta, tried = None, None
            nodes.append({"node": "composio", "error": type(exc).__name__})
        extra = []
        override = overrides.get(rec2["app"].lower()) or overrides.get(rec2["app"])
        if override:
            extra += override.urls()
        if meta:
            for key in ("docs_url",):
                if meta.get(key):
                    extra.append(meta[key])
            app_url = (meta.get("meta") or {}).get("app_url")
            if app_url:
                extra.append(app_url)
        extra = [u for u in extra if url_is_official(u if "://" in u else "https://" + u, resolved)]

        def registry(record: dict[str, Any]) -> dict[str, Any]:
            try:
                payload = registry_client.search(record["app"])
            except Exception as exc:
                nodes.append({"node": "registry", "error": type(exc).__name__})
                return record
            obj = apply_registry(
                AppRecord.model_validate(record),
                payload,
                set(resolved.allowed_hosts),
                search_url=f"{MCP_REGISTRY_URL}?search={urlquote(record['app'])}",
            )
            out = obj.model_dump(mode="json")
            return out

        late = deadline is not None and time.time() > deadline
        if late:
            nodes.append({"node": "reresearch3", "skipped": "time budget for this run was spent"})
        researcher = LiveResearcher(cache, seed, resolved, extra, nodes) if research and not late else None
        record, steps = pass3_record(
            rec2,
            p1.get(rec2["app"].lower()),
            log2,
            cache,
            toolkit=(meta, tried) if tried is not None else None,
            registry=registry,
            researcher=researcher,
        )
        log = {"app": rec2["app"], "pass": 3, "steps": steps, "nodes": nodes}
        write_json(path, {"record": record, "log": log})
        write_json(data / "runs" / f"{slug}_pass3.json", log)
        return record, log

    order = sorted(p2, key=lambda r: research_priority(p1.get(r["app"].lower()) or r))
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        done = dict(zip([r["app"] for r in order], pool.map(one, order), strict=True))
    results = [done[r["app"]] for r in p2]
    records = [r for r, _ in results]
    totals: dict[str, int] = {}
    for _, log in results:
        for key, value in log["steps"].items():
            totals[key] = totals.get(key, 0) + value
    stats = {
        "pass": 3,
        "apps": len(records),
        "wall_clock_s": round(time.time() - started, 1),
        "concurrency": concurrency,
        "needs_human": sum(1 for r in records if r.get("needs_human")),
        "fields_filled_by_step": totals,
        "unknown_pass1": count_unknowns(list(p1.values())),
        "unknown_pass2": count_unknowns(json.loads((data / "results_pass2.json").read_text())["records"]),
        "unknown_pass3": count_unknowns(records),
        "confidence_tags": _tag_counts(records),
        "usage": LEDGER.summary(),
    }
    return {"records": records, "stats": stats}


def _tag_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in records:
        for tag in (r.get("field_confidence") or {}).values():
            out[tag] = out.get(tag, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))
