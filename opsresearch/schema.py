"""Stable record for one researched app.

`unknown` is a real value. A concrete value with no usable evidence is
rewritten to `unknown` so a missing quote cannot survive as a finding.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "1.0.0"
MIN_QUOTE_CHARS = 8

SCORED_FIELDS = (
    "auth_methods",
    "self_serve",
    "api_rest",
    "api_graphql",
    "api_breadth",
    "existing_mcp",
    "buildability",
    "main_blocker",
)


class AuthMethod(StrEnum):
    oauth2 = "oauth2"
    oauth1 = "oauth1"
    api_key = "api_key"
    basic = "basic"
    bearer_token = "bearer_token"
    jwt = "jwt"
    session = "session"
    none = "none"
    unknown = "unknown"


class SelfServe(StrEnum):
    self_serve_free = "self_serve_free"
    self_serve_trial = "self_serve_trial"
    paid_plan_required = "paid_plan_required"
    admin_approval = "admin_approval"
    partner_gated = "partner_gated"
    contact_sales = "contact_sales"
    unknown = "unknown"


class GateType(StrEnum):
    none = "none"
    paid_plan = "paid_plan"
    admin_approval = "admin_approval"
    partner = "partner"
    contact_sales = "contact_sales"
    platform_review = "platform_review"
    login_wall = "login_wall"
    unknown = "unknown"


class YesNoUnknown(StrEnum):
    yes = "yes"
    no = "no"
    unknown = "unknown"


class ApiStyle(StrEnum):
    rest = "rest"
    graphql = "graphql"
    soap = "soap"
    sdk_only = "sdk_only"
    cli_only = "cli_only"
    none = "none"
    unknown = "unknown"


class ApiBreadth(StrEnum):
    narrow = "narrow"
    medium = "medium"
    broad = "broad"
    unknown = "unknown"


class ExistingMcp(StrEnum):
    official = "official"
    community = "community"
    none = "none"
    unknown = "unknown"


class Buildability(StrEnum):
    yes = "yes"
    yes_with_caveats = "yes_with_caveats"
    no = "no"
    unknown = "unknown"


class Blocker(StrEnum):
    none = "none"
    partner_program = "partner_program"
    contact_sales = "contact_sales"
    no_public_api = "no_public_api"
    login_walled_docs = "login_walled_docs"
    unclear_entity = "unclear_entity"
    platform_review = "platform_review"
    enterprise_only = "enterprise_only"
    unknown = "unknown"


class EvidenceSource(StrEnum):
    doc = "doc"
    composio_oracle = "composio_oracle"
    mcp_registry = "mcp_registry"
    derived_rule = "derived_rule"
    composio_metadata = "composio_metadata"


class Provenance(BaseModel):
    """Where the official domain came from. human_override means a person set the row."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(
        default="hint",
        description="human_override when data/domain_overrides.csv supplied the domain, otherwise hint.",
    )


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(description="Record field this quote supports.")
    url: str = Field(description="http(s) URL of the page or API response.")
    quote: str = Field(description="Verbatim span from that URL.")
    source: EvidenceSource = Field(
        default=EvidenceSource.doc,
        description="doc quotes are checked against the page. Oracle and registry quotes are not.",
    )


class ApiSurface(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rest: YesNoUnknown = Field(description="yes, no, or unknown. no is a claim and needs evidence.")
    graphql: YesNoUnknown = Field(description="yes, no, or unknown.")
    breadth: ApiBreadth = Field(description="narrow is under 20 endpoints, medium is 20 to 99, broad is 100 or more.")
    styles: list[ApiStyle] = Field(
        default_factory=lambda: [ApiStyle.unknown],
        description="API styles actually offered. unknown if the docs do not say.",
    )


def _usable(item: Evidence) -> bool:
    quote = item.quote.strip()
    return len(quote) >= MIN_QUOTE_CHARS and item.url.startswith(("http://", "https://"))


def _covered(evidence: list[Evidence]) -> set[str]:
    return {item.field for item in evidence if _usable(item)}


def _has(covered: set[str], *names: str) -> bool:
    return any(name in covered for name in names)


class AppRecord(BaseModel):
    """One app after extraction. Concrete fields without evidence become unknown."""

    model_config = ConfigDict(extra="forbid")

    app: str
    category: str
    one_liner: str = "unknown"
    auth_methods: list[AuthMethod] = Field(default_factory=lambda: [AuthMethod.unknown])
    self_serve: SelfServe = SelfServe.unknown
    gate_type: GateType = GateType.unknown
    gate_detail: str = "unknown"
    api_surface: ApiSurface = Field(
        default_factory=lambda: ApiSurface(
            rest=YesNoUnknown.unknown,
            graphql=YesNoUnknown.unknown,
            breadth=ApiBreadth.unknown,
            styles=[ApiStyle.unknown],
        )
    )
    existing_mcp: ExistingMcp = ExistingMcp.unknown
    mcp_url: str | None = None
    composio_has_toolkit: YesNoUnknown = YesNoUnknown.unknown
    composio_slug: str | None = None
    composio_toolkit_type: str | None = None
    composio_tools_count: int | None = None
    buildability: Buildability = Buildability.unknown
    main_blocker: Blocker = Blocker.unknown
    blocker_detail: str = "unknown"
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    unknown_reasons: dict[str, str] = Field(default_factory=dict)
    needs_human: bool = False
    needs_human_reasons: list[str] = Field(default_factory=list)
    hint_url: str | None = None
    official_domain: str | None = None
    provenance: Provenance = Field(default_factory=Provenance)
    source_id: int | None = None
    extraction_model: str | None = None
    verifier_model: str | None = None
    pass_id: int | None = None
    field_confidence: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Per-field confidence tag: verifier-supported, quote-verified, composio_metadata, "
            "mcp_registry, derived_rule, or quote-not-rechecked."
        ),
    )
    verifier_verdicts: dict[str, str] = Field(
        default_factory=dict,
        description="Per-field second-model verdict: supported, insufficient, contradicted, or not_run.",
    )

    @model_validator(mode="after")
    def _force_unknown_without_evidence(self) -> AppRecord:
        evidence = [item for item in self.evidence if _usable(item)]
        dropped = len(evidence) != len(self.evidence)
        covered = _covered(evidence)
        reasons = dict(self.unknown_reasons)
        if dropped:
            reasons["evidence"] = "dropped evidence with a short quote or a non-http url"

        if self.one_liner.strip().lower() in {"", "unknown"}:
            self.one_liner = "unknown"
        elif not _has(covered, "one_liner"):
            self.one_liner = "unknown"
            reasons["one_liner"] = "no evidence quote and url"

        methods = [m for m in self.auth_methods if m != AuthMethod.unknown]
        if not methods:
            self.auth_methods = [AuthMethod.unknown]
        elif not _has(covered, "auth_methods"):
            self.auth_methods = [AuthMethod.unknown]
            reasons["auth_methods"] = "no evidence quote and url"
        else:
            self.auth_methods = methods

        self.self_serve = _enum_or_unknown(
            self.self_serve, SelfServe.unknown, covered, ("self_serve",), reasons, "self_serve"
        )
        self.gate_type = _enum_or_unknown(
            self.gate_type,
            GateType.unknown,
            covered,
            ("gate_type", "self_serve"),
            reasons,
            "gate_type",
        )
        self.gate_detail = _text_or_unknown(
            self.gate_detail, covered, ("gate_detail", "self_serve"), reasons, "gate_detail"
        )

        surface = self.api_surface
        rest = surface.rest
        graphql = surface.graphql
        breadth = surface.breadth
        styles = list(surface.styles)
        if rest != YesNoUnknown.unknown and not _has(covered, "api_surface", "api_surface.rest"):
            rest = YesNoUnknown.unknown
            reasons["api_surface.rest"] = "no evidence quote and url"
        if graphql != YesNoUnknown.unknown and not _has(covered, "api_surface", "api_surface.graphql"):
            graphql = YesNoUnknown.unknown
            reasons["api_surface.graphql"] = "no evidence quote and url"
        if breadth != ApiBreadth.unknown and not _has(covered, "api_surface", "api_surface.breadth"):
            breadth = ApiBreadth.unknown
            reasons["api_surface.breadth"] = "no evidence quote and url"
        concrete_styles = [style for style in styles if style != ApiStyle.unknown]
        if concrete_styles and not _has(covered, "api_surface", "api_surface.styles"):
            styles = [ApiStyle.unknown]
            reasons["api_surface.styles"] = "no evidence quote and url"
        elif not concrete_styles:
            styles = [ApiStyle.unknown]
        else:
            styles = concrete_styles
        self.api_surface = ApiSurface(rest=rest, graphql=graphql, breadth=breadth, styles=styles)

        self.existing_mcp = _enum_or_unknown(
            self.existing_mcp,
            ExistingMcp.unknown,
            covered,
            ("existing_mcp",),
            reasons,
            "existing_mcp",
        )
        self.composio_has_toolkit = _enum_or_unknown(
            self.composio_has_toolkit,
            YesNoUnknown.unknown,
            covered,
            ("composio_has_toolkit",),
            reasons,
            "composio_has_toolkit",
        )
        if self.composio_toolkit_type and not _has(covered, "composio_has_toolkit", "composio_toolkit_type"):
            self.composio_toolkit_type = None
            reasons["composio_toolkit_type"] = "no evidence quote and url"
        if self.composio_tools_count is not None and not _has(covered, "composio_has_toolkit", "composio_tools_count"):
            self.composio_tools_count = None
            reasons["composio_tools_count"] = "no evidence quote and url"

        self.buildability = _enum_or_unknown(
            self.buildability,
            Buildability.unknown,
            covered,
            ("buildability",),
            reasons,
            "buildability",
        )
        self.main_blocker = _enum_or_unknown(
            self.main_blocker,
            Blocker.unknown,
            covered,
            ("main_blocker",),
            reasons,
            "main_blocker",
        )
        self.blocker_detail = _text_or_unknown(
            self.blocker_detail,
            covered,
            ("blocker_detail", "main_blocker"),
            reasons,
            "blocker_detail",
        )
        self.evidence = evidence
        self.unknown_reasons = reasons
        return self


def _enum_or_unknown(value, unknown, covered, names, reasons, key):
    if value == unknown:
        return unknown
    if _has(covered, *names):
        return value
    reasons[key] = "no evidence quote and url"
    return unknown


def _text_or_unknown(value: str, covered: set[str], names: tuple[str, ...], reasons: dict, key: str) -> str:
    text = value.strip()
    if text.lower() in {"", "unknown"}:
        return "unknown"
    if _has(covered, *names):
        return text
    reasons[key] = "no evidence quote and url"
    return "unknown"


def app_record_json_schema() -> dict[str, Any]:
    schema = AppRecord.model_json_schema()
    schema["$id"] = (
        "https://github.com/Prithvi-Raj-Kandan/Composio-product-ops-assignment/blob/main/schema/app_record.schema.json"
    )
    schema["$comment"] = (
        f"schema_version {SCHEMA_VERSION}. unknown is a value. "
        "A concrete value with no evidence is not valid agent output."
    )
    return schema


def unknown_record(app: str, category: str = "unknown") -> AppRecord:
    return AppRecord(app=app, category=category)
