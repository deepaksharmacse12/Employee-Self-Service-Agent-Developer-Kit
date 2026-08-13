# Discover Skill

Tenant inventory discovery — the **admin-run crawler**. Enumerates the tenant's
shared agent resources across **eight kinds** (Environment, EntraApp, Connector,
Connection, SharePointSite, KnowledgeSource, ExtensionPack, ScenarioTemplate),
writes each as one idempotent inventory item, then triggers a scoped reconcile so
the tenant picture stays current on every re-run.

The heavy lifting lives in the standalone crawler package at
`tools/tenant-inventory-discovery/`. This skill drives it via
`scripts/discover_inventory.py` and renders the results JSON.

Every **Message** block is the exact text to show the user. Copy it verbatim.
Do not rephrase, add commentary, or tell the user what tools you are calling.

---

## Start

Read `.local/config.json` to confirm setup is complete and get the tenant/agent
context. If setup is not complete, show:

**Message:**

You need to run `/setup` first before discovering your tenant inventory.

**End message.**

Stop here.

If setup is complete, proceed.

---

## Step 1: Run the crawl

Discovery always crawls the **single environment configured during `/setup`**
(`dataverseEndpoint` in `.local/config.json`). There is no scope choice to make and
no full-tenant crawl — do not ask the user which environments to crawl.

> **What runs today.** The default crawl enumerates **all eight kinds** live for the
> configured environment, using your admin sign-in (a browser window may open the first
> time to authenticate; Microsoft Graph and Copilot Studio may each prompt a separate
> consent the first time):
>
> - **Dataverse** — `Connection`, `ExtensionPack`, `ScenarioTemplate` (and the
>   `Environment` identity, from config).
> - **Microsoft Graph** — `EntraApp` (the agent's app registration) and `SharePointSite`
>   (only the sites referenced by the agent's SharePoint knowledge sources).
> - **Power Platform (BAP) admin** — `Connector` (the distinct connectors used by the
>   environment's connections).
> - **Copilot Studio** — `KnowledgeSource` (the agent bot's knowledge sources).
>
> If any kind's platform call fails (e.g. a missing role or consent), just that kind's
> scope is reported as **Incomplete** so nothing is retired for it — the rest of the run
> still succeeds. The **write path** (WeveNova Inventory API) is also not live yet
> (Dep-1/Dep-3), so discovered items are collected in-memory and surfaced in the
> results JSON rather than persisted server-side.
>
> Use `--demo` to exercise the full eight-kind lifecycle against representative
> sample data instead of hitting Dataverse.

Run from the `solutions/ess-maker-skills` directory.

Live Dataverse crawl of the configured environment:

```
python scripts/discover_inventory.py --tenant-id {TENANT_ID} --json-out workspace/discover/results.json
```

Offline demo (no sign-in, sample data, all eight kinds):

```
python scripts/discover_inventory.py --tenant-id {TENANT_ID} --demo --json-out workspace/discover/results.json
```

The script prints a one-line status and writes the full results to
`workspace/discover/results.json`. When `mode` is `live-crawl` the JSON also carries
a `discovered` block (the actual resources read from Dataverse, grouped by kind) and
a `writePathNote` explaining the in-memory sink.

On a successful (non-aborted) run the script also updates a **durable local inventory
mirror** at `.local/inventory.json` (override with `--inventory-out`). This file is the
persistent picture of the tenant: it is merged across runs using the same per-scope
reconcile rules the server uses — completed scopes replace their items and retire drift
(kept one run as `state:"Retired"`, then pruned), incomplete or tenant-root-exempt scopes
preserve their prior items untouched, and each record carries `firstSeenAt`/`lastSeenAt`.
The server (WeveNova) remains the source of truth; this file is a local cache. An aborted
run never overwrites it. The script also drops a best-effort `inventoryPath` pointer into
`.local/config.json`.

If the status line is `"status": "aborted"`, show:

**Message:**

Discovery didn't finish, so nothing was changed in your inventory. This is safe —
the crawl only updates the inventory when it completes fully. Let's try again.

**End message.**

Then stop and offer to re-run.

---

## Step 2: Present the results

Read `workspace/discover/results.json` with your file-reading tool and format the
summary yourself, directly in the chat reply. Do NOT write or run any script to
parse the JSON.

First show the header line:

**Message:**

Here's what I found in your tenant (run `{correlationId}`):

**End message.**

Then render a table with one row per crawled scope, sorted with tenant-wide
resources first (empty `environmentId`), then grouped by environment. Map each
`kind` to a friendly label:

| Kind (JSON) | Friendly label |
|---|---|
| Environment | Environments |
| EntraApp | App registrations |
| Connector | Connectors |
| Connection | Connections |
| SharePointSite | SharePoint sites |
| KnowledgeSource | Knowledge sources |
| ExtensionPack | Extension packs |
| ScenarioTemplate | Scenario templates |

Table columns — read each from the `scopes[]` entries:

| Scope | Resource | Found | Recorded | Status |
|-------|----------|-------|----------|--------|

- **Scope** = `Tenant-wide` when `environmentId` is empty, otherwise the
  `environmentId`.
- **Resource** = friendly label for `kind`.
- **Found** = `enumerated`.
- **Recorded** = `upserted`.
- **Status** = `Complete` when `complete` is `true`; otherwise
  `Incomplete — not updated` (and include the `error` text if present).

After the table, show a one-line summary from `totals`:

**Message:**

Recorded **{totals.upserted}** resources across **{totals.kindsCrawled}** resource types.

**End message.**

If `retiredCounts` is non-empty, add:

**Message:**

I also removed **{sum of retiredCounts}** resource(s) that no longer exist in your
tenant since the last discovery.

**End message.**

---

## Step 3: Handle incomplete scopes

If any scope has `complete: false`, show:

**Message:**

Some resource types couldn't be fully read this time, so I left them untouched
rather than risk removing valid entries. Re-running discovery will pick them up.

**End message.**

Offer to re-run `/discover`.

---

## Notes for the assistant (do not show the user)

- The crawler is idempotent by `(kind, naturalKey)`: re-running over an unchanged
  tenant produces identical results and removes nothing.
- Completeness is the safety gate: a scope that fails to enumerate fully is
  reported `Incomplete` and is **not** reconciled — never present incomplete
  scopes as if they were updated.
- Never fabricate counts, environment ids, or resource names — every value comes
  from `workspace/discover/results.json`.
- `workspace/discover/results.json` is the per-run render artifact. The durable
  cross-run picture is `.local/inventory.json` (the local mirror): merged with
  server-faithful reconcile semantics, watermark-free, with `firstSeenAt`/`lastSeenAt`
  and one-run `Retired` tombstones. The server (WeveNova) stays authoritative; the
  mirror is a local cache and is only updated on a successful run.
- The default crawl reads all eight kinds live for the configured environment —
  Dataverse (Connection, ExtensionPack, ScenarioTemplate), Microsoft Graph (EntraApp,
  SharePointSite), the BAP admin API (Connector), and Copilot Studio (KnowledgeSource) —
  via the admin's delegated sign-in. Only the server-side write path is not wired yet
  (Dep-1 / Dep-3 in the crawler README). `--demo` runs the full eight-kind lifecycle
  against sample data with no network or sign-in.
