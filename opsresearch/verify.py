"""Independent checks: quote in page, Composio toolkit oracle, MCP registry, second model."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from opsresearch.schema import (
    AppRecord,
    AuthMethod,
    Evidence,
    EvidenceSource,
    ExistingMcp,
    YesNoUnknown,
)
from opsresearch.seed import host_allowed
from opsresearch.textmatch import quote_in_text

logger = logging.getLogger("opsresearch")

AUTH_MODE_MAP = {
    "OAUTH2": "oauth2",
    "OAUTH1": "oauth1",
    "API_KEY": "api_key",
    "BASIC": "basic",
    "BEARER_TOKEN": "bearer_token",
    "JWT": "jwt",
    "SERVICE_ACCOUNT": "jwt",
    "NO_AUTH": "none",
    "SESSION": "session",
    "BASIC_AUTH": "basic",
}

MCP_REGISTRY_URL = "https://registry.modelcontextprotocol.io/v0/servers"


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verifier: str
    field: str
    status: str = Field(description="supported, contradicted, insufficient, or flag")
    detail: str


@dataclass(frozen=True)
class ToolkitSnapshot:
    found: bool
    slug: str | None
    auth_modes: tuple[str, ...]
    managed_modes: tuple[str, ...]
    toolkit_type: str | None
    tools_count: int | None
    docs_url: str | None


class ToolkitLookup(Protocol):
    def lookup(self, app: str) -> ToolkitSnapshot | None: ...


class RegistryLookup(Protocol):
    def search(self, app: str) -> dict[str, Any]: ...


def _map_mode(raw: str) -> str | None:
    key = raw.strip().upper().replace("-", "_").replace(" ", "_")
    return AUTH_MODE_MAP.get(key)


def normalize_toolkit(payload: dict[str, Any], slug: str) -> ToolkitSnapshot:
    modes: list[str] = []
    for detail in payload.get("auth_config_details") or []:
        if not isinstance(detail, dict):
            continue
        mapped = _map_mode(str(detail.get("mode") or ""))
        if mapped and mapped not in modes:
            modes.append(mapped)
    managed: list[str] = []
    for raw in payload.get("composio_managed_auth_schemes") or []:
        mapped = _map_mode(str(raw)) or str(raw).lower()
        if mapped not in managed:
            managed.append(mapped)
    meta = payload.get("meta") or {}
    tools = meta.get("tools_count")
    return ToolkitSnapshot(
        found=True,
        slug=str(payload.get("slug") or slug),
        auth_modes=tuple(modes),
        managed_modes=tuple(managed),
        toolkit_type=payload.get("toolkit_type"),
        tools_count=int(tools) if isinstance(tools, int) else None,
        docs_url=payload.get("docs_url"),
    )


def slug_candidates(app: str) -> list[str]:
    import re

    lowered = app.lower().strip()
    hyphen = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    compact = re.sub(r"[^a-z0-9]+", "", lowered)
    first = hyphen.split("-")[0] if hyphen else compact
    ordered = []
    for item in (hyphen, compact, first):
        if item and item not in ordered:
            ordered.append(item)
    return ordered


def check_quotes(record: AppRecord, pages: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    by_field: dict[str, list[Evidence]] = {}
    for item in record.evidence:
        if item.source != EvidenceSource.doc:
            continue
        by_field.setdefault(item.field, []).append(item)
    for field, items in by_field.items():
        bad = []
        for item in items:
            page = pages.get(item.url)
            if page is None:
                bad.append(f"{item.url} is not in the fetched pages")
                continue
            if not quote_in_text(item.quote, page):
                bad.append("quote not found in page")
        if bad:
            findings.append(
                Finding(
                    verifier="quote",
                    field=field,
                    status="insufficient",
                    detail="; ".join(bad),
                )
            )
        else:
            findings.append(
                Finding(
                    verifier="quote",
                    field=field,
                    status="supported",
                    detail="quote appears in the fetched page",
                )
            )
    return findings


def oracle_findings(record: AppRecord, snapshot: ToolkitSnapshot | None) -> list[Finding]:
    findings: list[Finding] = []
    if snapshot is None or not snapshot.found:
        if record.composio_has_toolkit == YesNoUnknown.yes:
            status = "contradicted"
            detail = "Composio toolkit lookup did not find this app"
        elif record.composio_has_toolkit == YesNoUnknown.unknown:
            status = "supported"
            detail = "filled composio_has_toolkit=no from a not-found lookup"
        else:
            status = "supported"
            detail = "agent no matches a not-found lookup"
        findings.append(Finding(verifier="oracle", field="composio_has_toolkit", status=status, detail=detail))
        return findings

    if record.composio_has_toolkit == YesNoUnknown.no:
        findings.append(
            Finding(
                verifier="oracle",
                field="composio_has_toolkit",
                status="contradicted",
                detail=f"Composio has toolkit {snapshot.slug}",
            )
        )
    elif record.composio_has_toolkit == YesNoUnknown.unknown:
        findings.append(
            Finding(
                verifier="oracle",
                field="composio_has_toolkit",
                status="supported",
                detail=f"filled composio_has_toolkit=yes from {snapshot.slug}",
            )
        )
    else:
        findings.append(
            Finding(
                verifier="oracle",
                field="composio_has_toolkit",
                status="supported",
                detail=f"agent yes matches {snapshot.slug}",
            )
        )

    agent_modes = {method.value for method in record.auth_methods if method != AuthMethod.unknown}
    oracle_modes = set(snapshot.auth_modes)
    if agent_modes and agent_modes - oracle_modes:
        extra = ", ".join(sorted(agent_modes - oracle_modes))
        findings.append(
            Finding(
                verifier="oracle",
                field="auth_methods",
                status="flag",
                detail=(
                    f"agent auth {extra} is not in the Composio toolkit. "
                    "Composio lists what it supports, not every vendor mode, so this is a flag."
                ),
            )
        )
    if oracle_modes and agent_modes and oracle_modes - agent_modes:
        missing = ", ".join(sorted(oracle_modes - agent_modes))
        findings.append(
            Finding(
                verifier="oracle",
                field="auth_methods",
                status="flag",
                detail=f"Composio also lists {missing}",
            )
        )
    if snapshot.toolkit_type == "mcp" and record.existing_mcp == ExistingMcp.none:
        findings.append(
            Finding(
                verifier="oracle",
                field="existing_mcp",
                status="insufficient",
                detail="Composio toolkit_type is mcp but existing_mcp is none",
            )
        )
    return findings


def apply_oracle(record: AppRecord, snapshot: ToolkitSnapshot | None, attempted_slug: str) -> AppRecord:
    data = record.model_dump(mode="json")
    if snapshot is not None and snapshot.found:
        if data["composio_has_toolkit"] == "unknown":
            data["composio_has_toolkit"] = "yes"
            data["composio_slug"] = snapshot.slug
            data["composio_toolkit_type"] = snapshot.toolkit_type
            data["composio_tools_count"] = snapshot.tools_count
            data["evidence"].append(
                {
                    "field": "composio_has_toolkit",
                    "url": f"https://backend.composio.dev/api/v3.1/toolkits/{snapshot.slug}",
                    "quote": (
                        f"slug={snapshot.slug} toolkit_type={snapshot.toolkit_type} "
                        f"tools_count={snapshot.tools_count} auth={','.join(snapshot.auth_modes)}"
                    ),
                    "source": "composio_oracle",
                }
            )
            data["unknown_reasons"].pop("composio_has_toolkit", None)
    elif data["composio_has_toolkit"] == "unknown":
        data["composio_has_toolkit"] = "no"
        data["evidence"].append(
            {
                "field": "composio_has_toolkit",
                "url": f"https://backend.composio.dev/api/v3.1/toolkits/{attempted_slug}",
                "quote": f"toolkit {attempted_slug} was not found",
                "source": "composio_oracle",
            }
        )
        data["unknown_reasons"].pop("composio_has_toolkit", None)
    return AppRecord.model_validate(data)


def _server_hosts(server: dict[str, Any]) -> list[str]:
    hosts: list[str] = []
    for key in ("websiteUrl",):
        url = server.get(key)
        if isinstance(url, str) and "://" in url:
            host = urlparse(url).hostname
            if host:
                hosts.append(host)
    repository = server.get("repository") or {}
    if isinstance(repository, dict):
        url = repository.get("url")
        if isinstance(url, str) and "://" in url:
            host = urlparse(url).hostname
            if host:
                hosts.append(host)
    for remote in server.get("remotes") or []:
        if isinstance(remote, dict) and isinstance(remote.get("url"), str):
            host = urlparse(remote["url"]).hostname
            if host:
                hosts.append(host)
    return hosts


def classify_registry(payload: dict[str, Any], vendor_hosts: set[str]) -> tuple[str, str | None, str]:
    official = None
    community = None
    for item in payload.get("servers") or []:
        server = item.get("server") or {}
        name = str(server.get("name") or "")
        website = server.get("websiteUrl") if isinstance(server.get("websiteUrl"), str) else None
        hosts = _server_hosts(server)
        if any(host_allowed(host, vendor_hosts) for host in hosts):
            official = (website, name)
            break
        if community is None:
            community = (website, name)
    if official:
        return "official", official[0], f"registry server {official[1]}"
    if community:
        return "community", community[0], f"registry server {community[1]}"
    return "none", None, "servers: []"


def registry_findings(record: AppRecord, classification: str) -> list[Finding]:
    agent = record.existing_mcp.value
    if agent == "unknown":
        return [
            Finding(
                verifier="registry",
                field="existing_mcp",
                status="supported",
                detail=f"filled existing_mcp={classification} from the MCP registry",
            )
        ]
    if agent == classification:
        return [
            Finding(
                verifier="registry",
                field="existing_mcp",
                status="supported",
                detail=f"agent {agent} matches the registry",
            )
        ]
    if agent == "official" and classification != "official":
        return [
            Finding(
                verifier="registry",
                field="existing_mcp",
                status="contradicted",
                detail=f"registry classification is {classification}, not official",
            )
        ]
    if agent == "none" and classification in {"official", "community"}:
        return [
            Finding(
                verifier="registry",
                field="existing_mcp",
                status="contradicted",
                detail=f"registry has a {classification} server",
            )
        ]
    return [
        Finding(
            verifier="registry",
            field="existing_mcp",
            status="insufficient",
            detail=f"agent {agent} does not match registry {classification}",
        )
    ]


def apply_registry(
    record: AppRecord,
    payload: dict[str, Any],
    vendor_hosts: set[str],
    *,
    search_url: str,
) -> AppRecord:
    kind, mcp_url, quote = classify_registry(payload, vendor_hosts)
    if record.existing_mcp != ExistingMcp.unknown:
        return record
    data = record.model_dump(mode="json")
    data["existing_mcp"] = kind
    data["mcp_url"] = mcp_url
    data["evidence"].append(
        {
            "field": "existing_mcp",
            "url": search_url,
            "quote": quote,
            "source": "mcp_registry",
        }
    )
    data["unknown_reasons"].pop("existing_mcp", None)
    return AppRecord.model_validate(data)


CROSS_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field": {"type": "string"},
                    "status": {"type": "string", "enum": ["supported", "contradicted", "insufficient"]},
                    "reason": {"type": "string"},
                },
                "required": ["field", "status", "reason"],
            },
        }
    },
    "required": ["verdicts"],
}


def cross_model_prompt(record: AppRecord, pages: dict[str, str]) -> str:
    claims = []
    for item in record.evidence:
        if item.source != EvidenceSource.doc:
            continue
        page = pages.get(item.url, "")
        claims.append(
            {
                "field": item.field,
                "url": item.url,
                "quote": item.quote,
                "page_excerpt": page[:1500],
            }
        )
    values = {
        "auth_methods": [method.value for method in record.auth_methods],
        "self_serve": record.self_serve.value,
        "gate_type": record.gate_type.value,
        "api_surface": record.api_surface.model_dump(mode="json"),
        "existing_mcp": record.existing_mcp.value,
        "buildability": record.buildability.value,
        "main_blocker": record.main_blocker.value,
        "one_liner": record.one_liner,
    }
    return (
        "You are an independent verifier. You did not extract these claims. "
        "Judge each claim only from its quote and page excerpt. "
        "Reply with JSON {\"verdicts\": [{\"field\", \"status\", \"reason\"}]}. "
        "status is supported, contradicted, or insufficient. "
        "insufficient means the excerpt does not settle it. Do not invent a better value.\n"
        f"Claims and values:\n{json.dumps({'values': values, 'evidence': claims})}"
    )


def parse_cross_model(payload: dict[str, Any]) -> list[Finding]:
    findings = []
    verdicts = payload.get("verdicts") if isinstance(payload, dict) else None
    if not isinstance(verdicts, list):
        return [
            Finding(
                verifier="cross_model",
                field="record",
                status="insufficient",
                detail="verifier returned no verdicts array",
            )
        ]
    allowed = {"supported", "contradicted", "insufficient"}
    for item in verdicts:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "")
        if status not in allowed:
            status = "insufficient"
        findings.append(
            Finding(
                verifier="cross_model",
                field=str(item.get("field") or "record"),
                status=status,
                detail=str(item.get("reason") or ""),
            )
        )
    return findings


def failing_fields(findings: list[Finding]) -> list[str]:
    found = []
    for item in findings:
        if item.status in {"contradicted", "insufficient"} and item.field not in found:
            found.append(item.field)
    return found


class ComposioToolkitClient:
    """Live oracle. Tests pass a fake with .lookup instead of constructing this."""

    def __init__(self, sdk: Any | None = None) -> None:
        self._sdk = sdk

    def _sdk_client(self) -> Any:
        if self._sdk is not None:
            return self._sdk
        from composio import Composio

        self._sdk = Composio()
        return self._sdk

    def lookup(self, app: str) -> ToolkitSnapshot | None:
        sdk = self._sdk_client()
        last_slug = slug_candidates(app)[:1] or [app]
        for slug in slug_candidates(app):
            last_slug = [slug]
            try:
                raw = sdk.toolkits.get(slug)
            except Exception as exc:
                name = type(exc).__name__
                if "NotFound" in name or getattr(exc, "status_code", None) == 404:
                    continue
                message = str(exc).lower()
                if "404" in message or "not found" in message:
                    continue
                raise
            if hasattr(raw, "model_dump"):
                raw = raw.model_dump()
            if isinstance(raw, dict):
                return normalize_toolkit(raw, slug)
        logger.info("no Composio toolkit for %s (last slug %s)", app, last_slug[0])
        return None


class HttpRegistryClient:
    def __init__(self, base_url: str = MCP_REGISTRY_URL, client: Any | None = None) -> None:
        self.base_url = base_url
        self._client = client

    def search(self, app: str) -> dict[str, Any]:
        import httpx

        params = {"search": app, "limit": 20, "version": "latest"}
        if self._client is not None:
            response = self._client.get(self.base_url, params=params)
        else:
            response = httpx.get(self.base_url, params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("MCP registry returned a non-object")
        return payload
