"""Resource -> upsert-body mapping and the on-the-wire request shape (spec §4.1, §8).

A discovered resource is a raw ``dict`` of attributes already in the §5.3 camelCase key
space (the per-kind crawler is responsible for that projection). This module turns it
into a validated :class:`~tenant_inventory_discovery.models.InventoryItem` and then into
the JSON body ``POST /inventory`` expects -- crucially, ``attributes`` travels **as a
JSON-object string** (§8).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from .models import InventoryItem, Kind
from .schemas import AttributeCaps, validate_attributes


def map_resource(
    kind: Kind,
    attributes: Mapping[str, object],
    *,
    caps: AttributeCaps | None = None,
    connector_id: str | None = None,
) -> InventoryItem:
    """Map one discovered resource to a validated :class:`InventoryItem` (spec §4.1).

    - Composes ``naturalKey`` from the kind's identity fields (env-scoped kinds compose
      ``environmentId`` so cross-environment names never collide).
    - Sets ``environmentId`` (containment edge, §5.5) for env-scoped kinds.
    - Sets ``connectorId`` (reference edge, §5.5) when provided -- typically on
      ``Connection``.
    - Pre-validates attributes against §5.3 + caps to fail fast.

    Raises on invalid attributes; the caller records the item as ``skipped_invalid`` and
    the scope becomes incomplete if a required key is missing (§6, §7).
    """
    attrs = dict(attributes)
    validate_attributes(kind, attrs, caps)

    natural_key = kind.compose_natural_key(attrs)

    environment_id = str(attrs["environmentId"]) if kind.is_env_scoped else None

    resolved_connector_id = connector_id
    if resolved_connector_id is None and "connectorId" in attrs:
        resolved_connector_id = str(attrs["connectorId"])

    return InventoryItem(
        kind=kind,
        natural_key=natural_key,
        attributes=attrs,
        environment_id=environment_id,
        connector_id=resolved_connector_id,
    )


def idempotency_key(item: InventoryItem) -> str:
    """Stable per-item idempotency key (spec §5.2), watermark-free.

    Derived from ``kind + naturalKey + attributes`` so a network **retry of the same
    upsert** replays rather than duplicating, while a genuinely *changed* resource gets a
    fresh key (and is applied). There is no per-run watermark in the key.
    """
    canonical_attrs = json.dumps(item.attributes, separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(
        f"{item.kind.discriminator}\x1f{item.natural_key}\x1f{canonical_attrs}".encode()
    )
    return digest.hexdigest()


def to_request_body(item: InventoryItem) -> dict[str, Any]:
    """Build the ``POST /inventory`` JSON body (spec §4.1, §8).

    ``attributes`` is serialized to a **JSON-object string** per §8. There is no ``runId``
    on the wire -- the design carries no per-run watermark; drift is reconciled from the
    observed-key set reported at crawl completion (see ``inventory_client.reconcile``).
    Provenance/audit/concurrency fields are intentionally omitted -- the server stamps
    them.
    """
    body: dict[str, Any] = {
        "kind": item.kind.discriminator,
        "naturalKey": item.natural_key,
        "attributes": json.dumps(item.attributes, separators=(",", ":"), sort_keys=True),
    }
    if item.environment_id is not None:
        body["environmentId"] = item.environment_id
    if item.connector_id is not None:
        body["connectorId"] = item.connector_id
    return body
