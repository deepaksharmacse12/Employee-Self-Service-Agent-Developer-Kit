"""In-memory Inventory API client (spec §5) -- for tests, dry-runs, and local demos.

Implements the :class:`~tenant_inventory_discovery.inventory_client.InventoryClient`
Protocol with idempotent ``(kind, naturalKey)`` storage and a server-like scoped
reconcile so the full write+retire contract can be exercised without the live WeveNova
API (Dep-1/Dep-3 not yet available).

This mirrors the *expected* server behavior (spec §6.3): reconcile retires
``Source = Discovered`` rows within each reported ``(EnvironmentId, Kind)`` scope whose
``naturalKey`` was **not** observed by the crawl (set-based, watermark-free). Manually-authored
rows (``Source != Discovered``) are never retired.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .mapping import idempotency_key
from .models import InventoryItem, Kind, ScopeSnapshot, UpsertResult


@dataclass
class StoredItem:
    kind: Kind
    natural_key: str
    attributes: dict[str, object]
    environment_id: str
    connector_id: str | None
    source: str = "Discovered"
    state: str = "Active"
    version: int = 1


@dataclass
class FakeInventoryClient:
    """Server-like in-memory store honoring the idempotency + reconcile contract."""

    items: dict[tuple[str, str], StoredItem] = field(default_factory=dict)
    seen_idempotency_keys: dict[str, int] = field(default_factory=dict)
    upsert_calls: int = 0
    reconcile_calls: int = 0

    def _key(self, item: InventoryItem) -> tuple[str, str]:
        return (item.kind.discriminator, item.natural_key)

    def upsert(
        self, item: InventoryItem, *, if_match: str | None = None
    ) -> UpsertResult:
        self.upsert_calls += 1
        idem = idempotency_key(item)
        key = self._key(item)

        if idem in self.seen_idempotency_keys:
            # Replay of the same upsert -> no duplicate, no version bump (spec §5.2).
            stored = self.items[key]
            return UpsertResult(item.natural_key, item.kind, etag=str(stored.version))

        created = key not in self.items
        env_id = item.environment_id or ""
        if created:
            stored = StoredItem(
                kind=item.kind,
                natural_key=item.natural_key,
                attributes=dict(item.attributes),
                environment_id=env_id,
                connector_id=item.connector_id,
            )
        else:
            stored = self.items[key]
            stored.attributes = dict(item.attributes)  # overwrite in place (idempotent)
            stored.environment_id = env_id
            stored.connector_id = item.connector_id
            stored.version += 1
            if stored.source == "Discovered":
                stored.state = "Active"  # re-asserting revives a previously-retired row

        self.items[key] = stored
        self.seen_idempotency_keys[idem] = stored.version
        return UpsertResult(item.natural_key, item.kind, etag=str(stored.version), created=created)

    def reconcile(self, snapshots: list[ScopeSnapshot]) -> dict[str, int]:
        """Retire drift within each reported scope (server-like, set-based, spec §6.3)."""
        self.reconcile_calls += 1
        retired: dict[str, int] = {}
        present_by_scope = {
            (s.scope.environment_id, s.scope.kind): s.present_keys for s in snapshots
        }
        for stored in self.items.values():
            if stored.source != "Discovered" or stored.state != "Active":
                continue
            scope = (stored.environment_id, stored.kind)
            present = present_by_scope.get(scope)
            if present is None:  # scope not reported this run -> never swept
                continue
            if stored.natural_key not in present:
                stored.state = "Retired"
                label = f"{stored.environment_id}|{stored.kind.discriminator}"
                retired[label] = retired.get(label, 0) + 1
        return retired

    # -- test helpers ---------------------------------------------------------------

    def active_items(self) -> list[StoredItem]:
        return [s for s in self.items.values() if s.state == "Active"]

    def get(self, kind: Kind, natural_key: str) -> StoredItem | None:
        return self.items.get((kind.discriminator, natural_key))
