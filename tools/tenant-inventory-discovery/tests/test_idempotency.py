"""§10: idempotency-key replay + idempotent re-run."""

from __future__ import annotations

from tenant_inventory_discovery.mapping import idempotency_key, map_resource
from tenant_inventory_discovery.models import Kind


def test_idempotency_key_stable_for_same_item():
    item = map_resource(Kind.ENVIRONMENT, {"environmentId": "e1", "displayName": "P"})
    assert idempotency_key(item) == idempotency_key(item)


def test_idempotency_key_differs_across_items_and_attrs():
    a = map_resource(Kind.ENVIRONMENT, {"environmentId": "e1", "displayName": "P"})
    b = map_resource(Kind.ENVIRONMENT, {"environmentId": "e2", "displayName": "P"})
    # Different natural key -> different key.
    assert idempotency_key(a) != idempotency_key(b)
    # Same natural key, changed attributes -> different key (so a changed resource is
    # re-applied rather than deduped as a replay).
    a2 = map_resource(Kind.ENVIRONMENT, {"environmentId": "e1", "displayName": "Q"})
    assert idempotency_key(a) != idempotency_key(a2)


def test_replay_does_not_duplicate(inventory):
    item = map_resource(Kind.CONNECTOR, {"connectorId": "c1", "displayName": "SN"})
    inventory.upsert(item)
    inventory.upsert(item)  # retried upsert, same idempotency key
    assert inventory.upsert_calls == 2
    stored = inventory.get(Kind.CONNECTOR, "c1")
    assert stored is not None
    assert stored.version == 1  # no version bump on replay -> no duplicate write
