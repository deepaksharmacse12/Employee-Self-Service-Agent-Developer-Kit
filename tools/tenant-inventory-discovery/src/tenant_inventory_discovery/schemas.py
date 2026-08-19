"""Per-kind attribute schemas and caps (spec §5.3).

.. warning::
   The exact attribute key sets live in ``Tenant-Inventory-DesignSpec.md`` §5.3, which
   is **not vendored in this repo**. The schemas below are derived from the identity
   fields and edge attributes named in the ADK implementation spec and are marked
   ``[verify]``. Confirm the required/allowed keys against §5.3 before production use --
   the server schema-validates and is the authoritative gate; this module pre-validates
   only to *fail fast* (spec §4.1, §6).
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Kind


@dataclass(frozen=True)
class AttributeCaps:
    """Server-enforced caps the skill pre-checks to avoid whole-item rejection (§5.3, §6).

    Values are conservative placeholders -- ``[verify]`` against §5.3.
    """

    max_attribute_count: int = 50
    max_key_length: int = 128
    max_value_length: int = 4096


@dataclass(frozen=True)
class KindSchema:
    """Allowed/required camelCase attribute keys for a kind (spec §5.3)."""

    kind: Kind
    required: frozenset[str]
    optional: frozenset[str]

    @property
    def allowed(self) -> frozenset[str]:
        return self.required | self.optional


# [verify] Every key set below must be reconciled with Tenant-Inventory-DesignSpec.md
# §5.3. Required keys always include the kind's natural-key fields (models.Kind.key_fields).
_SCHEMAS: dict[Kind, KindSchema] = {
    Kind.ENVIRONMENT: KindSchema(
        Kind.ENVIRONMENT,
        required=frozenset({"environmentId", "displayName"}),
        optional=frozenset({"environmentUrl", "region", "environmentType", "state"}),
    ),
    Kind.ENTRA_APP: KindSchema(
        Kind.ENTRA_APP,
        required=frozenset({"appId", "displayName"}),
        optional=frozenset({"publisherDomain", "signInAudience"}),
    ),
    Kind.CONNECTOR: KindSchema(
        Kind.CONNECTOR,
        required=frozenset({"connectorId", "displayName"}),
        optional=frozenset({"tier", "publisher"}),
    ),
    Kind.CONNECTION: KindSchema(
        Kind.CONNECTION,
        required=frozenset({"environmentId", "connectionId", "connectorId"}),
        optional=frozenset({"displayName", "status"}),
    ),
    Kind.SHAREPOINT_SITE: KindSchema(
        Kind.SHAREPOINT_SITE,
        required=frozenset({"siteUrl", "siteId"}),
        optional=frozenset({"displayName"}),
    ),
    Kind.KNOWLEDGE_SOURCE: KindSchema(
        Kind.KNOWLEDGE_SOURCE,
        required=frozenset({"environmentId", "botId", "sourceId"}),
        optional=frozenset({"sourceType", "displayName"}),
    ),
    Kind.EXTENSION_PACK: KindSchema(
        Kind.EXTENSION_PACK,
        required=frozenset({"environmentId", "packName"}),
        optional=frozenset({"version", "publisher"}),
    ),
    Kind.SCENARIO_TEMPLATE: KindSchema(
        Kind.SCENARIO_TEMPLATE,
        required=frozenset({"environmentId", "uniqueName"}),
        optional=frozenset({"displayName", "scenarioName"}),
    ),
}


def schema_for(kind: Kind) -> KindSchema:
    return _SCHEMAS[kind]


class AttributeValidationError(ValueError):
    """Raised when an item's attributes violate the §5.3 schema or caps."""


def validate_attributes(
    kind: Kind,
    attributes: dict[str, object],
    caps: AttributeCaps | None = None,
) -> None:
    """Pre-validate attributes against the §5.3 schema and caps (spec §4.1, §6).

    Fails fast on: missing required keys, unlisted keys, non-camelCase-object shape, and
    cap violations. Per §6, a **required** key is never silently dropped -- the item is
    failed and the caller logs + skips it (contributing to scope incompleteness).
    """
    caps = caps or AttributeCaps()
    schema = schema_for(kind)

    missing = schema.required - attributes.keys()
    if missing:
        raise AttributeValidationError(
            f"{kind.discriminator}: missing required attribute(s): {sorted(missing)}"
        )

    unlisted = attributes.keys() - schema.allowed
    if unlisted:
        raise AttributeValidationError(
            f"{kind.discriminator}: unlisted attribute(s) not in §5.3 schema: "
            f"{sorted(unlisted)}"
        )

    if len(attributes) > caps.max_attribute_count:
        raise AttributeValidationError(
            f"{kind.discriminator}: attribute count {len(attributes)} exceeds cap "
            f"{caps.max_attribute_count}"
        )

    for key, value in attributes.items():
        if len(key) > caps.max_key_length:
            raise AttributeValidationError(
                f"{kind.discriminator}: attribute key {key!r} exceeds cap "
                f"{caps.max_key_length}"
            )
        if isinstance(value, str) and len(value) > caps.max_value_length:
            raise AttributeValidationError(
                f"{kind.discriminator}: value for {key!r} exceeds cap "
                f"{caps.max_value_length}"
            )
