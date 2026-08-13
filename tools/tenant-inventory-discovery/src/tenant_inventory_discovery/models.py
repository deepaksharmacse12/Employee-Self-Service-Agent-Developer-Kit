"""Core domain models for the Tenant Inventory discovery skill.

Grounding: ``Tenant-Inventory-DesignSpec.md`` (§5 model, §5.5 graph edges) and the
companion ADK implementation spec. Server-side storage/projection/redaction and the
``ensure-parent`` materialization are out of scope here -- these types model only what
the *skill* produces and sends on the wire.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass, field

# Delimiter used to compose multi-part natural keys for env-scoped kinds so identical
# names across environments never collide (spec §4 / §4.1).
NATURAL_KEY_DELIMITER = "|"

# Tenant-root kinds carry an empty EnvironmentId; reconcile treats them specially
# (spec §6.3 tenant-root exemption). We model "no environment" as the empty string to
# match the server's ``(EnvironmentId, Kind)`` scoping where EnvironmentId is empty.
TENANT_ROOT_ENVIRONMENT_ID = ""


class Scope(enum.Enum):
    """Whether a kind is enumerated once per tenant or once inside each environment."""

    TENANT_ROOT = "tenant-root"
    ENVIRONMENT = "env-scoped"


class Kind(enum.Enum):
    """The eight inventory kinds (spec §4).

    Each member records its crawl scope and the ordered set of attribute keys that
    compose its ``naturalKey``. Env-scoped kinds always lead with ``environmentId`` so
    the composed key is unique per environment.
    """

    ENVIRONMENT = ("Environment", Scope.TENANT_ROOT, ("environmentId",))
    ENTRA_APP = ("EntraApp", Scope.TENANT_ROOT, ("appId",))
    CONNECTOR = ("Connector", Scope.TENANT_ROOT, ("connectorId",))
    CONNECTION = ("Connection", Scope.ENVIRONMENT, ("environmentId", "connectionId"))
    SHAREPOINT_SITE = ("SharePointSite", Scope.TENANT_ROOT, ("siteUrl",))
    KNOWLEDGE_SOURCE = (
        "KnowledgeSource",
        Scope.ENVIRONMENT,
        ("environmentId", "botId", "sourceId"),
    )
    EXTENSION_PACK = ("ExtensionPack", Scope.ENVIRONMENT, ("environmentId", "packName"))
    SCENARIO_TEMPLATE = (
        "ScenarioTemplate",
        Scope.ENVIRONMENT,
        ("environmentId", "uniqueName"),
    )

    def __init__(self, discriminator: str, scope: Scope, key_fields: tuple[str, ...]):
        self.discriminator = discriminator
        self.scope = scope
        self.key_fields = key_fields

    @property
    def is_env_scoped(self) -> bool:
        return self.scope is Scope.ENVIRONMENT

    @property
    def is_tenant_root(self) -> bool:
        return self.scope is Scope.TENANT_ROOT

    @classmethod
    def from_discriminator(cls, discriminator: str) -> Kind:
        for member in cls:
            if member.discriminator == discriminator:
                return member
        raise ValueError(f"Unknown kind discriminator: {discriminator!r}")

    def compose_natural_key(self, attributes: Mapping[str, object]) -> str:
        """Compose the natural key from the kind's identity fields (spec §4/§4.1).

        Env-scoped kinds join their parts with :data:`NATURAL_KEY_DELIMITER` so, e.g.,
        two ``Connection`` rows with the same ``connectionId`` in different
        environments produce distinct keys.
        """
        parts: list[str] = []
        for field_name in self.key_fields:
            value = attributes.get(field_name)
            if value is None or value == "":
                raise ValueError(
                    f"{self.discriminator}: missing natural-key field {field_name!r}"
                )
            parts.append(str(value))
        return NATURAL_KEY_DELIMITER.join(parts)


@dataclass(frozen=True)
class ScopeKey:
    """A reconcile scope: ``(EnvironmentId, Kind)`` (spec §6.3).

    Tenant-root kinds use :data:`TENANT_ROOT_ENVIRONMENT_ID` (empty string) for the
    environment component.
    """

    environment_id: str
    kind: Kind

    @classmethod
    def for_kind(cls, kind: Kind, environment_id: str | None = None) -> ScopeKey:
        if kind.is_tenant_root:
            return cls(TENANT_ROOT_ENVIRONMENT_ID, kind)
        if not environment_id:
            raise ValueError(f"{kind.discriminator} is env-scoped and needs environment_id")
        return cls(environment_id, kind)


@dataclass
class InventoryItem:
    """One resource mapped to a single inventory row (spec §4.1).

    The skill sets only the fields below. It deliberately does **not** set
    ``source``/``submittedById``/``state``/``createdAt``/``updatedAt``/``version`` --
    the server stamps provenance (``Source = Discovered``), audit, and concurrency.
    """

    kind: Kind
    natural_key: str
    attributes: dict[str, object]
    environment_id: str | None = None  # containment edge (§5.5); set for env-scoped kinds
    connector_id: str | None = None  # reference edge Connection -> Connector (§5.5)

    @property
    def scope_key(self) -> ScopeKey:
        return ScopeKey.for_kind(self.kind, self.environment_id)


@dataclass
class UpsertResult:
    """Outcome of a single ``POST /inventory`` upsert."""

    natural_key: str
    kind: Kind
    etag: str | None = None
    created: bool = False


@dataclass(frozen=True)
class ScopeSnapshot:
    """The observed natural keys for one fully-crawled scope (set-based reconcile).

    Replaces the RunId watermark: the server retires ``Source = Discovered``,
    ``State = Active`` rows in this ``(EnvironmentId, Kind)`` scope whose ``naturalKey`` is
    **not** in :attr:`present_keys` (spec §6.3, re-expressed without a watermark).
    """

    scope: ScopeKey
    present_keys: frozenset[str]


@dataclass
class ScopeReport:
    """Per-scope crawl bookkeeping -- the reconcile gate (spec §6.3, §7)."""

    scope: ScopeKey
    enumerated: int = 0
    upserted: int = 0
    skipped_invalid: int = 0
    fully_enumerated: bool = False
    error: str | None = None
    # Natural keys observed (successfully upserted) in this scope. On a complete scope
    # this is the snapshot the server diffs against to retire drift (set-based reconcile,
    # replacing the old RunId watermark).
    observed_keys: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """A scope is reconcile-eligible only if it enumerated fully with no fatal error."""
        return self.fully_enumerated and self.error is None


@dataclass
class RunSummary:
    """Structured per-run telemetry (spec §8).

    ``correlation_id`` is a **local-only** log/trace id -- it is never sent to the
    Inventory API and never stamped onto rows (the design carries no per-run watermark).
    """

    correlation_id: str = ""
    scopes: list[ScopeReport] = field(default_factory=list)
    completed_scopes: list[ScopeKey] = field(default_factory=list)
    retired_counts: dict[str, int] = field(default_factory=dict)
    aborted: bool = False
