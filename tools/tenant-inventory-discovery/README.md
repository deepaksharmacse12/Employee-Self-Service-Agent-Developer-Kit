# Tenant Inventory — Discovery Skill (ADK)

Admin-run crawler that enumerates a tenant's shared agent resources across **eight
kinds** and writes each as one idempotent `InventoryItem` to the **WeveNova Inventory
API**, then triggers a **scoped server-side reconcile** so the tenant picture stays
current on every re-run.

This is the ADK-side implementation of the *Tenant Inventory — Discovery Skill
Implementation Spec*. It is a **self-contained standalone module** — the target WeveNova
API and the design spec it grounds against are not vendored in this repo, so the platform
and Inventory clients ship as Protocols with in-memory fakes, and everything that must be
confirmed against an external source is marked **`[verify]`**.

## Layout

```
src/tenant_inventory_discovery/
  models.py            # Kind enum, ScopeKey, InventoryItem, natural-key composition (§4, §5)
  schemas.py           # §5.3 per-kind attribute schemas + caps            [verify §5.3]
  mapping.py           # resource -> InventoryItem -> wire body (§4.1, §8)
  config.py            # tunable page size, concurrency, retry, endpoints, lock TTL (§8)
  inventory_client.py  # WeveNova client Protocol + HttpInventoryClient    [verify Dep-1/Dep-3]
  fake_inventory.py    # server-like in-memory client (idempotency + reconcile) for tests
  local_store.py       # durable local inventory mirror: cross-run merge (server-faithful reconcile)
  platform_clients.py  # BAP/Dataverse/Graph/Copilot Studio surfaces + FakePlatform [verify Q-A]
  crawlers/            # the eight per-kind crawlers, declarative (§4)
  runner.py            # enumerate -> map -> upsert -> completeness gate (§5-§7)
  discovery_skill.py   # lifecycle facade: correlation id, lock, run, reconcile, telemetry (§5.1)
  lock.py              # per-tenant single-flight run lock (interim D6 mitigation, §7)
  telemetry.py         # structured run-summary event (§8)
  __main__.py          # CLI (uses fakes by default)
tests/                 # the §10 test matrix
```

## Run the demo / tests

```bash
cd tools/tenant-inventory-discovery
python -m pip install -e ".[dev]"      # or: pip install pytest ruff
python -m pytest                        # runs the §10 matrix against the in-memory fakes
python -m tenant_inventory_discovery --tenant-id contoso --verbose
```

## What is real vs. stubbed (the `[verify]` / TODO list)

These map directly to the spec's **§9 Dependencies & Open Questions**. Resolve them
before production use:

| Spec ref | Item | Where | Status |
|---|---|---|---|
| **Dep-1** | `POST /inventory` write path live | `HttpInventoryClient.upsert` | Route/headers encode the *expected* contract — `[verify]` |
| **Dep-3** | Reconcile **trigger** route + payload | `HttpInventoryClient.reconcile`, `config.reconcile_route` | Not enumerated in DesignSpec §8 — **must be pinned with the server team** |
| **Dep-2** | Server `ensure-parent` auto-materialization | `mapping.map_resource` / `runner` | Skill "sends just the leaf" **and** still upserts enumerated `Environment` nodes |
| **Q-A** | Platform surfaces for `EntraApp` / `SharePointSite` / `KnowledgeSource` | `crawlers/registry.py`, `platform_clients.py` | Graph / Copilot Studio are *expected* — `[verify]` against live APIs |
| **Q-B** | Overlapping-run rule (**D6**) | `lock.py` (`FileRunLock`) | Interim mitigation: **serialize runs per tenant** |
| **Q-C** | Reconcile server-side vs client | `runner`/`discovery_skill` | Assumes **server-side**; skill only signals complete |
| **§5.3** | Exact per-kind attribute key sets | `schemas.py` | Derived from identity/edge fields — `[verify]` against the DesignSpec |

## Wiring for production

1. Replace `FakePlatform` with bindings to the ADK's existing tenant-platform client
   layer (BAP, Dataverse, Microsoft Graph, Copilot Studio). **Do not add new SDKs if a
   client already exists** (spec §2). Each enumerator must **page to completion**.
2. Replace `FakeInventoryClient` with `HttpInventoryClient`, passing an
   `auth_token_provider` that yields the **admin's delegated** bearer token (spec §8) —
   the skill runs as admin end-to-end and never writes with a lower-privilege identity.
3. Confirm the `schemas.py` key sets against DesignSpec §5.3.
4. Pin the `reconcile_route` + run-complete payload with the server team (Dep-3).
5. Provide a durable `RunLock` (the file lock is a single-host interim; use a
   distributed lock for multi-host).

## Core invariants enforced here (spec §3, §5-§7)

- **Idempotent by `(kind, naturalKey)`** — re-asserting a resource overwrites in place;
  env-scoped kinds compose `environmentId` so cross-environment names never collide.
- **Watermark-free reconcile** — no per-run `RunId`; at crawl completion the skill reports
  the natural keys it observed per scope, and the server retires Discovered/Active rows in
  those scopes whose key was not observed.
- **Completeness gates reconcile** — a scope reconciles only if it enumerated fully with
  no fatal error; a partial or crashed run never triggers reconcile (recrawl instead).
- **Tenant-root exemption** — a subset-of-environments run never marks tenant-root kinds
  complete.

## Local inventory mirror (`local_store.py`)

The server (WeveNova) is authoritative, but each run also merges its results into a durable
local mirror (the kit writes it to `.local/inventory.json`). `build_document()` is pure and
applies the **same per-scope reconcile the server would**, keyed off
`RunSummary.completed_scopes` (which already encodes the tenant-root exemption):

- **Reconciled scope** (in `completed_scopes`) — observed items are refreshed and drift
  (prior keys not observed) is retired. Retired rows are kept for **one** run
  (`state:"Retired"` + `retiredAt`), then pruned.
- **Complete-but-exempt scope** (fully enumerated, no error, but tenant-root during a subset
  crawl) — observed items are refreshed; prior items are **kept**, never retired.
- **Incomplete / not-crawled scope** — prior items are preserved untouched, so a partial
  crawl never wipes the mirror.

Each record carries `firstSeenAt` (carried forward across runs) and `lastSeenAt`. All file
I/O and the `.local/config.json` pointer live in the kit bridge, not in this module.

