# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
ESS Maker Kit - Tenant Inventory Discovery (crawl) wrapper

Thin bridge that runs the admin-run tenant-inventory crawler implemented in
``tools/tenant-inventory-discovery`` and writes a results JSON the /discover
skill renders. This is the script the ``src/skills/discover/SKILL.md`` invokes.

The crawler enumerates a tenant's shared agent resources across eight kinds
(Environment, EntraApp, Connector, Connection, SharePointSite, KnowledgeSource,
ExtensionPack, ScenarioTemplate) and upserts each to the WeveNova Inventory API,
then signals a scoped server-side reconcile. The crawl is always scoped to the
**single environment configured during /setup** -- there is no full-tenant crawl.

.. note::
   The **write path** (WeveNova Inventory API) is still stubbed (Dep-1 / Dep-3): both
   ``--demo`` and the live crawl upsert into the module's in-memory ``FakeInventoryClient``
   sink and dump the discovered inventory to the results JSON. What differs is the
   **crawl source**:

   - ``--demo``  -> in-memory ``FakePlatform`` (a small representative environment).
   - default     -> live enumeration of all eight kinds for the configured environment:
     Dataverse (``Connection``, ``ExtensionPack``, ``ScenarioTemplate``) via
     ``scripts/auth.py``; Microsoft Graph (``EntraApp``, ``SharePointSite``) via the
     kit's ``GraphClient``; the BAP admin API (``Connector``) via ``PPAdminClient``; and
     Copilot Studio (``KnowledgeSource``, and the sites behind ``SharePointSite``) via
     ``PVAClient``. Any kind whose platform call fails is reported as an incomplete
     scope (and excluded from reconcile) rather than aborting the run.

Usage:
    # Live Dataverse crawl of the configured environment (prompts for admin sign-in;
    # requires /setup completed):
    python scripts/discover_inventory.py --tenant-id contoso

    # Offline demo against in-memory fakes:
    python scripts/discover_inventory.py --tenant-id contoso --demo \
        --json-out workspace/discover/results.json
"""

import argparse
import json
import os
import sys

# Make the standalone crawler package importable without installing it. The module
# lives at repo-root: tools/tenant-inventory-discovery/src (three levels up from here:
# scripts/ -> ess-maker-skills/ -> solutions/ -> repo root).
_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
)
_CRAWLER_SRC = os.path.join(
    _REPO_ROOT, "tools", "tenant-inventory-discovery", "src"
)
if _CRAWLER_SRC not in sys.path:
    sys.path.insert(0, _CRAWLER_SRC)

from tenant_inventory_discovery.config import DiscoveryConfig  # noqa: E402
from tenant_inventory_discovery.discovery_skill import DiscoverySkill  # noqa: E402
from tenant_inventory_discovery.local_store import build_document  # noqa: E402
from tenant_inventory_discovery.models import RunSummary  # noqa: E402

DEFAULT_JSON_OUT = "workspace/discover/results.json"

# Durable local mirror of the (server-authoritative) tenant inventory. Unlike the
# transient results JSON, this file is merged across runs with the same per-scope
# reconcile semantics the server uses, so it stays a faithful offline picture.
DEFAULT_INVENTORY_OUT = os.path.join(".local", "inventory.json")

# Demo mode has no config, so it crawls this one representative environment (the tool
# only ever crawls a single environment -- there is no full-tenant crawl).
_DEMO_ENV_ID = "env-prod"


def _demo_platform_and_inventory():
    """Build the in-memory fakes with a small representative tenant (demo mode)."""
    from tenant_inventory_discovery.fake_inventory import FakeInventoryClient
    from tenant_inventory_discovery.platform_clients import FakePlatform

    platform = FakePlatform(
        environments=[
            {"environmentId": "env-prod", "displayName": "Contoso Prod"},
            {"environmentId": "env-test", "displayName": "Contoso Test"},
        ],
        entra_apps=[{"appId": "app-ess", "displayName": "ESS Agent App"}],
        connectors=[
            {"connectorId": "shared_service-now", "displayName": "ServiceNow"},
            {"connectorId": "shared_workdaysoap", "displayName": "Workday"},
        ],
        sharepoint_sites=[
            {"siteUrl": "https://contoso.sharepoint.com/hr", "siteId": "site-hr"}
        ],
        connections={
            "env-prod": [
                {
                    "environmentId": "env-prod",
                    "connectionId": "sn-1",
                    "connectorId": "shared_service-now",
                    "status": "Connected",
                }
            ],
        },
        knowledge_sources={
            "env-prod": [
                {
                    "environmentId": "env-prod",
                    "botId": "bot-ess",
                    "sourceId": "ks-hr",
                    "sourceType": "SharePoint",
                }
            ],
        },
        extension_packs={
            "env-prod": [
                {"environmentId": "env-prod", "packName": "ESS.HRSD", "version": "1.2.3"}
            ],
        },
        scenario_templates={
            "env-prod": [
                {
                    "environmentId": "env-prod",
                    "uniqueName": "GetPayslip",
                    "scenarioName": "Get Payslip",
                }
            ],
        },
    )
    return platform, FakeInventoryClient()


def _live_platform_and_inventory():
    """Build the live Dataverse-backed platform + in-memory sink (default mode).

    Reuses the kit's ``scripts/auth.py`` (delegated admin sign-in + paged Web API
    queries) via :class:`DataverseBackedPlatform`. The setup config binds a **single**
    environment (``dataverseEndpoint``); its identity is derived from that config URL
    (no environment discovery). The crawl is **always** scoped to that one environment
    -- there is no full-tenant crawl -- so the tenant-root exemption keeps the
    not-yet-wired tenant-root kinds out of reconcile (spec §6.3).
    """
    import auth
    from discover_dataverse_platform import DataverseBackedPlatform

    from tenant_inventory_discovery.fake_inventory import FakeInventoryClient

    cfg = auth.load_config()
    env_url = cfg.get("dataverseEndpoint")
    if not env_url:
        raise SystemExit(
            "No 'dataverseEndpoint' in .local/config.json -- run /setup first."
        )

    platform = DataverseBackedPlatform(
        env_url,
        entra_app_id=cfg.get("entraAppId"),
        bot_id=(cfg.get("agent") or {}).get("botId"),
    )
    # Always scope to the single configured environment (identity from config).
    return platform, FakeInventoryClient(), [platform.environment_id]


def _summary_to_dict(summary: RunSummary) -> dict:
    """Serialize a RunSummary into a stable, render-friendly JSON shape."""
    return {
        "correlationId": summary.correlation_id,
        "aborted": summary.aborted,
        "retiredCounts": summary.retired_counts,
        "completedScopes": [
            {"environmentId": s.environment_id, "kind": s.kind.discriminator}
            for s in summary.completed_scopes
        ],
        "scopes": [
            {
                "environmentId": r.scope.environment_id,
                "kind": r.scope.kind.discriminator,
                "enumerated": r.enumerated,
                "upserted": r.upserted,
                "skippedInvalid": r.skipped_invalid,
                "complete": r.complete,
                "error": r.error,
            }
            for r in summary.scopes
        ],
        "totals": {
            "kindsCrawled": len({r.scope.kind for r in summary.scopes}),
            "enumerated": sum(r.enumerated for r in summary.scopes),
            "upserted": sum(r.upserted for r in summary.scopes),
            "skippedInvalid": sum(r.skipped_invalid for r in summary.scopes),
            "incompleteScopes": sum(1 for r in summary.scopes if not r.complete),
        },
    }


def _discovered_from_inventory(inventory) -> dict:
    """Group the in-memory sink's stored items by kind for the results JSON.

    Surfaces what the crawl actually discovered (natural key + §5.3 attributes + state)
    so a live run shows real Dataverse data even though the write path is still the
    in-memory ``FakeInventoryClient`` sink (Dep-1 not live).
    """
    out: dict[str, list[dict]] = {}
    for stored in inventory.items.values():
        out.setdefault(stored.kind.discriminator, []).append(
            {
                "naturalKey": stored.natural_key,
                "attributes": stored.attributes,
                "state": stored.state,
            }
        )
    for rows in out.values():
        rows.sort(key=lambda r: r["naturalKey"])
    return out


def _load_prior_inventory(path: str) -> dict | None:
    """Read the existing local mirror if present; treat any problem as 'no prior'."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        return doc if isinstance(doc, dict) else None
    except (OSError, ValueError):
        return None


def _atomic_write_json(path: str, doc: dict) -> None:
    """Write ``doc`` to ``path`` atomically (temp file in the same dir + os.replace)."""
    out_dir = os.path.dirname(path) or "."
    os.makedirs(out_dir, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
    os.replace(tmp, path)


def _persist_local_inventory(inventory, summary, *, tenant_id, mode, write_path, path):
    """Merge this run into the durable local mirror and update the config pointer."""
    prior = _load_prior_inventory(path)
    doc = build_document(
        prior,
        list(inventory.items.values()),
        summary,
        tenant_id=tenant_id,
        mode=mode,
        write_path=write_path,
    )
    _atomic_write_json(path, doc)

    # Best-effort config pointer -- never fatal.
    config_path = os.path.join(".local", "config.json")
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        if isinstance(cfg, dict):
            cfg["inventoryPath"] = path
            cfg["inventoryUpdatedAt"] = doc["updatedAt"]
            _atomic_write_json(config_path, cfg)
    except (OSError, ValueError):
        pass
    return doc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="discover_inventory.py",
        description="Run the tenant inventory discovery crawl and write results JSON.",
    )
    parser.add_argument("--tenant-id", required=True, help="Tenant identifier to crawl.")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run against in-memory fakes (until live API/clients are wired).",
    )
    parser.add_argument(
        "--json-out",
        default=DEFAULT_JSON_OUT,
        help=f"Path to write the results JSON (default: {DEFAULT_JSON_OUT}).",
    )
    parser.add_argument(
        "--inventory-out",
        default=DEFAULT_INVENTORY_OUT,
        help=(
            "Path to the durable local inventory mirror, merged across runs "
            f"(default: {DEFAULT_INVENTORY_OUT})."
        ),
    )
    args = parser.parse_args(argv)

    if args.demo:
        platform, inventory = _demo_platform_and_inventory()
        environment_ids = [_DEMO_ENV_ID]
        mode = "demo"
    else:
        platform, inventory, environment_ids = _live_platform_and_inventory()
        mode = "live-crawl"

    skill = DiscoverySkill(platform, inventory, config=DiscoveryConfig())

    aborted = False
    try:
        summary = skill.discover(args.tenant_id, environment_ids=environment_ids)
    except Exception as exc:  # crash path: nothing reconciled (crawler guarantees this)
        aborted = True
        summary = RunSummary(correlation_id="unknown")
        summary.aborted = True
        result = _summary_to_dict(summary)
        result["fatalError"] = str(exc)
    else:
        result = _summary_to_dict(summary)

    result["mode"] = mode
    result["tenantId"] = args.tenant_id
    result["discovered"] = _discovered_from_inventory(inventory)
    if mode == "live-crawl":
        # The crawl source is live Dataverse; the write path is still the in-memory sink.
        result["writePath"] = "in-memory-sink"
        result["writePathNote"] = (
            "Live Dataverse crawl. The WeveNova Inventory API write path is not yet "
            "available (Dep-1/Dep-3), so results were upserted to an in-memory sink and "
            "reported here rather than persisted server-side."
        )

    out_path = os.path.abspath(args.json_out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)

    # Merge into the durable local mirror. Skip on abort so a partial/crashed run
    # never overwrites the last good picture (matching the "no reconcile on abort" rule).
    inventory_written = None
    if not (aborted or result["aborted"]):
        inventory_written = args.inventory_out
        _persist_local_inventory(
            inventory,
            summary,
            tenant_id=args.tenant_id,
            mode=mode,
            write_path=result.get("writePath", "in-memory-sink"),
            path=args.inventory_out,
        )

    print(
        json.dumps(
            {
                "status": "aborted" if (aborted or result["aborted"]) else "ok",
                "correlationId": result["correlationId"],
                "resultsPath": args.json_out,
                "inventoryPath": inventory_written,
                "upserted": result["totals"]["upserted"],
                "incompleteScopes": result["totals"]["incompleteScopes"],
                "retired": result["retiredCounts"],
            }
        )
    )
    return 1 if (aborted or result["aborted"]) else 0


if __name__ == "__main__":
    sys.exit(main())
