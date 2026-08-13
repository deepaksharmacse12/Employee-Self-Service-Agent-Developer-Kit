"""§10: retire-on-drift, manual-item exemption, tenant-root exemption."""

from __future__ import annotations

from conftest import ENV_A, build_platform
from tenant_inventory_discovery.discovery_skill import DiscoverySkill
from tenant_inventory_discovery.fake_inventory import StoredItem
from tenant_inventory_discovery.models import Kind


def test_removed_resource_is_retired_after_next_run(platform, inventory):
    skill = DiscoverySkill(platform, inventory)
    skill.discover("t1")
    assert inventory.get(Kind.CONNECTOR, "conn-catalog-1").state == "Active"

    # Second run over a tenant where the connector disappeared.
    platform2 = build_platform()
    platform2.connectors = []
    skill2 = DiscoverySkill(platform2, inventory)
    skill2.discover("t1")

    stored = inventory.get(Kind.CONNECTOR, "conn-catalog-1")
    assert stored.state == "Retired"  # key not observed this run -> swept (spec §6.3)


def test_manual_item_never_retired(platform, inventory):
    # A manually-authored row (Source != Discovered) in a swept scope.
    inventory.items[(Kind.CONNECTOR.discriminator, "manual-1")] = StoredItem(
        kind=Kind.CONNECTOR,
        natural_key="manual-1",
        attributes={"connectorId": "manual-1", "displayName": "Hand-made"},
        environment_id="",
        connector_id=None,
        source="Manual",
    )
    skill = DiscoverySkill(platform, inventory)
    skill.discover("t1")
    assert inventory.get(Kind.CONNECTOR, "manual-1").state == "Active"  # exempt (§6.3)


def test_partial_env_run_does_not_retire_tenant_root(inventory):
    # First: full crawl records everything.
    skill = DiscoverySkill(build_platform(), inventory)
    skill.discover("t1")

    # Then: a subset run touching only ENV_A. Tenant-root kinds must NOT be swept even
    # though they were enumerated (tenant-root exemption, spec §6.3).
    skill2 = DiscoverySkill(build_platform(), inventory)
    summary = skill2.discover("t1", environment_ids=[ENV_A])

    completed_kinds = {s.kind for s in summary.completed_scopes}
    assert Kind.CONNECTOR not in completed_kinds
    assert Kind.ENVIRONMENT not in completed_kinds
    # The connector row stays Active (never swept by the subset run).
    assert inventory.get(Kind.CONNECTOR, "conn-catalog-1").state == "Active"


def test_idempotent_rerun_produces_identical_state(inventory):
    skill = DiscoverySkill(build_platform(), inventory)
    skill.discover("t1")
    snapshot = {k: (v.natural_key, v.state) for k, v in inventory.items.items()}

    skill2 = DiscoverySkill(build_platform(), inventory)
    skill2.discover("t1")
    after = {k: (v.natural_key, v.state) for k, v in inventory.items.items()}
    assert after == snapshot  # unchanged tenant -> identical stored state, all Active
    assert all(v.state == "Active" for v in inventory.items.values())
