<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->
# Skill 6 — Install the ServiceNow Extension Pack
Role: **Environment Maker**. This skill installs the ServiceNow extension pack(s),
binds ServiceNow and Dataverse connections, sets the portal URL used by returned
links, verifies the ServiceNow cloud flows, and records rows **S6.1 through S6.6**.
Depends on skills 1–5 as applicable: the environment, Dataverse, ESS base agent,
ServiceNow connection basics, and the selected ServiceNow sign-in path must already
exist. Read the selected path from `.local/connect/servicenow/config.json` as
`authType`.
Every **Message** block is the exact text to show the user. Copy it verbatim. Do
not rephrase, add commentary, or tell the user what tools you are calling or what
files you are reading.
**Do not show internal variable names, Step IDs, checkpoint IDs, hidden checklist
comments, or config file paths to the user.** User-facing text is limited to Message
blocks, checkpoint-result tables rendered by the shared checklist updater, manual
verification details, and question prompts.
**Secrets:** never ask for ServiceNow passwords, client secrets, certificate private
keys, certificate passwords, PFX bytes, or any other secret through chat. For the
certificate path, use only the saved certificate file path and tell the maker to use
the password they received when the certificate was generated. Do not write that
password to any config file.
**Files this skill owns or updates:**
- Config: `.local/connect/servicenow/config.json`
- Working checklist: `.local/setup/servicenow/tasks.md`
- Checklist template: `src/skills/setup/servicenow/tasks.md`
**Files this skill may read, but not own:**
- `.local/config.json` — read-only source for `dataverseEndpoint` and agent details;
  write only the legacy `connections.ServiceNow` summary described in P6.3d.
**Checkpoints this skill drives (run each in isolation):**
| Step | Checkpoint | Gate |
|------|------------|------|
| S6.1 | `SN-PKG-001` — ServiceNow extension pack(s) installed | prog when installed automatically; manual when installed by the maker |
| S6.2 | `bind_connections.py` exit 0 — ServiceNow connection reference bound (live health confirmed by `SN-FLOWCONN-001` at S6.4) | prog; auth type may require attestation |
| S6.2 | `SN-DV-CONN-001` — Dataverse connection reference active | prog |
| S6.3 | `SN-FLOW-*` — ServiceNow cloud flows enabled | prog |
| S6.4 | `SN-FLOWCONN-001` — ServiceNow flow invoker connection connected | prog |
| S6.5 | `connect_and_share.py` — connection parameters shared onto the portal-owned reference (verified live by `SN-FLOWCONN-001`) | prog |
| S6.6 | `SN-BASEURL-001` — portal base URL present | prog; else attest |
Run any individually-registered checkpoint by itself:
```
python scripts/flightcheck/cli.py --checkpoint <ID>
```
Only `SN-FLOWCONN-001`, `SN-DV-CONN-001`, and the `SN-ENTRA-*` checks are registered as
standalone `--checkpoint` IDs. `SN-PKG-001`, `SN-FLOW-*`, and `SN-BASEURL-001` are
emitted **only** by the ServiceNow scope run — get them with
`python scripts/flightcheck/cli.py --scope servicenow --no-open` and read the matching
row(s) from the results. **If a `--checkpoint <ID>` call returns "unknown checkpoint",
that ID is scope-emitted: switch to the scope run and read the row. Never treat an
"unknown checkpoint" message as a setup blocker, and never stop the flow because of
it — the underlying action (install, bind, activate) is still what you must run.**
**After every checkpoint run, show its result in chat first.** As soon as a
`--checkpoint` run returns, render the result to the user per
[`../shared/checklist-updater.md`](../shared/checklist-updater.md) §U.0–U.0a — the
compact result table and, for any `MANUAL` / `Warning` / `NotConfigured` row, its
full verification steps — before you show any later Message block or ask any
attestation question. Single-checkpoint runs never open the HTML report, so this
in-chat render is the only place the user sees manual steps; never ask a user to
attest to steps they have not been shown.
`SN-FLOW-*` is a data-driven family. Expand S6.3 into one checkbox per emitted flow
result, using the checkpoint description as the visible flow label, and update each
generated row immediately. Do not batch the flow updates.
**Build order and resume.** Always run P6.0 first, then run P6.2's pack lookup
before the first incomplete row. Both are idempotent and rehydrate state used by the
connection, flow, and portal URL checks. After that, skip any row whose
`setupStatus` state is already `done` — **except P6.3's connection bind, which you
always run (it is idempotent): a pack reinstall can silently unbind a reference, so
never trust a recorded S6.2 without re-running `bind_connections.py` and confirming
exit 0.**
The connection/flow chain runs in this order: **install (P6.2) → bind connection
references (P6.3) → turn on flows (P6.4) → connect and share (P6.5) → portal URL
(P6.6)**. A cloud flow can only hold activation once its connection references are
bound, and the flow invoker binding must land on the activated flow definition, so
this order is deliberate — do not reorder it.

**State persistence.** The action scripts (`install_extension_pack.py`,
`bind_connections.py`, `activate_flows.py`, `connect_and_share.py`) now self-record
their confirmed outcome into `.local/connect/servicenow/config.json` on success —
the factual `packs` / `connections` / `status` artifacts plus the `setupStatus`
step(s) they own (S6.1–S6.5, where `connect_and_share.py` records **both** S6.4
connect and S6.5 share independently), via `connect_state.py`. This keeps a headless or
script-first drive resumable even if the row-recording Message steps below are
skipped. The recorders are merge-only and never change a script's exit code, so
the explicit config merges in each step remain the source of truth for gate
evidence and stay safe to run.
---
## P6.0 — Role gate (Environment Maker)
Apply the shared [`permission-gate.md`](../shared/permission-gate.md) before any
extension-pack or connection work, with:
- `REQUIRED_ROLE` = `"Environment Maker"`
- `GATE_MODE` = `"programmatic"`
- `STEP_ID` = `"S6.1"`
- `ROLE_QUERY` = a Dataverse security-role membership check for the signed-in user.
  Read `dataverseEndpoint` from `.local/config.json`; call it `{ENV_URL}`.
Resolve the caller and roles:
```
az rest --method GET --resource "{ENV_URL}" --url "{ENV_URL}/api/data/v9.2/WhoAmI" --query "UserId" -o tsv
```
```
az rest --method GET --resource "{ENV_URL}" --url "{ENV_URL}/api/data/v9.2/systemusers%28{USER_ID}%29/systemuserroles_association?%24select=name" --query "value[].name" -o json
```
The role is held if the returned names include **Environment Maker**, **System
Customizer**, or **System Administrator**. Treat insufficient privilege, forbidden,
or authorization-denied responses as "role not held". If the query errors for an
unrelated reason, follow the shared gate's retry-then-attest fallback — never assume
pass. If `GATE_RESULT` is `"stop"`, halt. Otherwise carry `GATE_EVIDENCE` forward
and record it when rows are updated.
---
## P6.1 — Restore setup state and derive product/auth values
Read `.local/connect/servicenow/config.json`. If it is missing, stop and route back
to the ServiceNow connection basics skill; this skill cannot infer the instance,
product scope, or sign-in method.
Restore:
- `instanceName` and `instanceUrl`; derive one from the other if needed.
- `usage` and `scope`. `itsm` means ITSM only, `hrsd` means HRSD only, and `both`
  means ITSM then HRSD. Prefer explicit `scope` when present.
- `authType`, which must be `entra_user` or `entra_certificate`.
- `entra.appClientId` for `entra_user`.
- `certificate.tenantId`, `certificate.appAClientId`, `certificate.appBClientId`,
  and `certificate.certPfxPath` for `entra_certificate`.
- Existing `packs`, `connections`, `portalBaseUrl`, `setupStatus`, and
  `productStatus` (per-product install / portal state, keyed by `hrsd` / `itsm`).
If `authType` is missing or not supported, stop and route back to the selected
sign-in setup skill. Do not offer legacy auth in this playbook.
Build the in-scope pack list in this order: ITSM when `scope.itsm` is true, then
HRSD when `scope.hrsd` is true. Use **ServiceNow IT** and **ServiceNow HR** as the
user-facing names. Ensure each in-scope `packs.<product>` exists with at least
`"pending"`, unless it already records a more advanced state. Merge only; never drop
fields written by earlier setup skills.
Read `.local/config.json` read-only for `dataverseEndpoint` and `agent` details. Do
not write ServiceNow setup fields into that file except for the legacy connection
summary in P6.3d.
---
## P6.2 — Install the extension pack and verify it landed (SN-PKG-001) *(completes S6.1)*
First check whether the ServiceNow pack content is already installed, so resume does
not ask the maker to reinstall.
**Message:**

First, let me check whether the ServiceNow extension pack is already installed in
your agent.

**End message.**
```
python scripts/flightcheck/cli.py --scope servicenow --no-open
```
Read the `SN-PKG-001` row from the results before continuing (`SN-PKG-001` is emitted
by the ServiceNow scope run, not a standalone `--checkpoint` ID). Later steps own the
S6.2–S6.6 rows, so ignore those rows here.
- `PASSED` → the expected pack content is present; go to **Record S6.1**.
- `FAILED`, `NotConfigured`, or a manual install finding → choose an install mode
  (below), install each in-scope pack, then re-run the checkpoint.

### P6.2 — Choose the install mode
Only the pack **install** differs by mode; every later step (bind connections, turn
on flows, connect and share, portal URL) is automated in both cases.
If `packs.installMode` is already recorded in `.local/connect/servicenow/config.json`,
reuse it silently (do not re-ask on resume). Otherwise ask with the question tool so
the maker gets selectable options (do not enumerate the choices as a bulleted list in
the message body — that renders as plain text instead of a picker):
**Message:**

How would you like to install the ServiceNow extension pack? **Automated** is fast
and hands-off — I install the pack content for you headlessly, no clicking through
Copilot Studio. **Manual** means you install it yourself in Copilot Studio and I
verify it; choose that if your organization requires a person to perform the install
or you want to review each step. Either way, once the pack is in I automate the rest
— connections, flows, sharing, and the portal link.

**End message.**
Use the question tool with options **Automated (recommended)** and **Manual**.
Persist the answer to `.local/connect/servicenow/config.json` as
`packs.installMode` (`"automated"` or `"manual"`) before continuing.
- **Automated** → go to **P6.2-A**.
- **Manual** → go to **P6.2-M**.

### P6.2-A — Automated headless install *(when `installMode == "automated"`)*
Install the in-scope pack content headlessly via the Power Platform appmanagement
API. This installs pack **content only** — it does not create the ServiceNow or
Dataverse connections. That is expected: P6.3 binds connections by reusing an
existing active connection, and if none exists yet it guides the maker to create one
(the graceful fallback), then continues automated.
**Scope precondition (do not skip).** The installer installs **only** the products
selected in `.local/connect/servicenow/config.json` `scope` (`hrsd` / `itsm`), and it
**fails closed** — an empty or all-`false` scope installs nothing (exit 4). Before
running it, confirm `scope` reflects exactly the product(s) the maker asked for (e.g.
`{ "hrsd": true, "itsm": false }` for HR only). If `scope` is missing or all-`false`,
re-derive it from the maker's earlier product selection (P6.1) and persist it first —
never run the install against an unset scope, or the maker may get products they did
not request.
The preview run below is the safe way to confirm the target set: it prints exactly
which pack unique name(s) would be installed. Verify that list matches the requested
product(s) before running the real install.
**Message:**

I'll install the ServiceNow extension pack content for you now — no clicks needed.

**End message.**
First preview the target set (this prints exactly which pack unique name(s) would be
installed — confirm they match the requested product(s) before the real install):
```
python scripts/install_extension_pack.py --connector servicenow --dry-run --json
```

#### P6.2-A.1 — Set expectations and open the live status block
Before the install runs, set expectations and render the shared status block so the
maker sees continuous motion instead of a silent multi-minute wait.
**Message:**

Installing the ServiceNow extension pack now. This usually takes **2–5 minutes** (up
to 10). You don't have to wait here — keep working and I'll keep the checklist below
updated and tell you the moment each step is done.

**End message.**
Then render the **live setup status block** — one compact checklist, covering the whole
connect chain, that you rewrite **in place** on each update (edit/replace the same
block; never post a fresh one each cycle):
```
Setting up ServiceNow {product}…
[~] Install extension pack — installing… (0s; ~2–5 min)
[ ] Bind connections (ServiceNow + Dataverse)
[ ] Turn on the ServiceNow flows
[ ] Connect the flow invoker connection (in Copilot Studio)
[ ] Share the connection parameters
[ ] Set the Portal Base URL
```
`{product}` is the in-scope product label (e.g. `HR`). Row icons: `[x]` done (with
elapsed, e.g. `— done (2m10s)`), `[~]` in progress (short status + elapsed), `[ ]`
pending. The six rows map to P6.2→P6.6 (the **connect** and **share** rows are both
driven by P6.5's single `connect_and_share.py` run but track the two separate
checkpoints S6.4 and S6.5), and each later step updates **its own row in this same
block** — never start a second block.

#### P6.2-A.2 — Fire the install and poll it (keeps the block moving)
Fire the install and poll it yourself so the install row animates. **Do not** use the
blocking `--json` form here — it hides all progress until the whole install returns.
```
python scripts/install_extension_pack.py --connector servicenow --start --json
```
Read the result:
- **Exit 3** (`parent_missing`) — the parent ESS solution is not installed. Stop and
  route back to the ESS solution import step; the extension pack cannot install first.
- **Exit 4** (`no_targets`) — no product is selected in `scope`; fix the scope
  precondition above and re-run. (`not_found` means no matching pack — fall back to
  manual.)
- **Exit 5** (`no_environment`) — the environment could not be resolved. Surface the
  message and stop; do not fall back.
- **Exit 1** (`error`) — surface the message and offer the fallback below.
- **Exit 0** — read `operations[]`. If it is empty and every result is
  `already_installed`, the pack is already in: mark the install row `[x] done` and go
  to **Record S6.1**. Otherwise each entry has an `operation_id` to poll.

Poll each `operation_id` about every 15s, rewriting the install row from each result,
until it is terminal:
```
python scripts/install_extension_pack.py --connector servicenow --status --operation-id <id> --json
```
- `running` (`"terminal": false`) → keep the row `[~] Install extension pack —
  installing… (Ns)`. Show `percentComplete` when the payload has it; otherwise show
  elapsed against the typical **2–5 min** (never the 10-min timeout, so a normal
  install never looks broken). Surface any `statusMessage` as the sub-status.
- `succeeded` (`"terminal": true, "succeeded": true`, exit 0) → flip the row to
  `[x] Install extension pack — done (Ns)`, then confirm with the `SN-PKG-001` pack
  check below and go to **Record S6.1**; set the Bind row to `[~]` as you enter P6.3.
- `failed` (`"terminal": true, "succeeded": false`, exit 6) → surface `statusMessage`
  and offer the fallback below.

The install continues **server-side** even if you stop polling. If ~10 minutes pass
without a terminal status, do **not** declare it stuck: re-run `--start` (it no-ops and
returns `already_installed` once the pack has landed) or the `SN-PKG-001` scope check to
confirm, then continue.

**Fallback (on exit 1 / exit 6):**
**Message:**

The automated install didn't complete. Would you like to install the pack manually
in Copilot Studio instead? I'll verify it and then continue automating the rest.

**End message.**
Use the question tool with options **Install manually** and **Retry automated**. On
**Install manually**, set `packs.installMode="manual"` and go to **P6.2-M**. On
**Retry automated**, first re-check state with `--start` / `SN-PKG-001` (the prior
operation may have completed server-side); only re-fire the install if the pack is
still missing.

After a successful automated install, re-run:
```
python scripts/flightcheck/cli.py --scope servicenow --no-open
```
Read the `SN-PKG-001` row and loop until it passes, then go to **Record S6.1**.

### P6.2-M — Manual portal install *(when `installMode == "manual"`)*
Guide the maker through installing each in-scope pack in Copilot Studio using the
values for the selected auth type below.
#### P6.2a — User sign-in install values
Use only when `authType == "entra_user"`. Require `entra.appClientId`; if missing,
route back to the user sign-in playbook.
For each in-scope pack, show this message with `{PACK_NAME}` set to **ServiceNow
IT** or **ServiceNow HR**.
**Message:**

Time to install the ServiceNow integration in Copilot Studio.

1. Open [Copilot Studio](https://copilotstudio.microsoft.com/).
2. Open your Employee Self-Service agent.
3. Go to **Settings** → **Customize**.
4. Find **{PACK_NAME}** and select **Install**.
5. When it asks for connection details, enter:

   | Field | Value |
   |-------|-------|
   | **Authentication Type** | Microsoft Entra ID User Login |
   | **Resource URI** | `{APP_CLIENT_ID}` |
   | **Instance Name** | `{INSTANCE_NAME}` |

6. Sign in with your Microsoft work account when prompted.
7. If it asks for a **Microsoft Dataverse** connection, sign in with the same maker
   account you use for this environment.

If the sign-in button hangs after authenticating, open
[Power Automate](https://make.powerautomate.com) → **Connections** and check whether
ServiceNow shows as connected. If it does, return to Copilot Studio, close the
install dialog, refresh the page, and select **Install** again so it can pick up the
existing connection.

Tell me when the install finishes, or say **help** if something went wrong.

**End message.**
#### P6.2b — Certificate install values
Use only when `authType == "entra_certificate"`. Require `certificate.tenantId`,
`certificate.appAClientId`, `certificate.appBClientId`, and
`certificate.certPfxPath`; if any are missing, route back to the certificate
playbook. Confirm the PFX file exists. If it does not, route back to regenerate it.
Before showing the install message, open File Explorer with the certificate selected:
```powershell
explorer.exe /select,"{CERT_PFX_PATH}"
```
For HRSD, show:
**Message:**

Time to install the ServiceNow HR integration in Copilot Studio.

1. Open [Copilot Studio](https://copilotstudio.microsoft.com/).
2. Open your Employee Self-Service agent.
3. Go to **Settings** → **Customize**.
4. Find **ServiceNow HR** and select **Install**.
5. When it asks for connection details, enter:

   | Field | Value |
   |-------|-------|
   | **Authentication Type** | Microsoft Entra ID OAuth using Certificate |
   | **Instance Name** | `{INSTANCE_NAME}` |
   | **Tenant ID** | `{TENANT_ID}` |
   | **Client ID** | `{APP_B_CLIENT_ID}` |
   | **Resource URI** | `{APP_A_CLIENT_ID}` |
   | **Client Secret** | Upload the `.pfx` certificate file I opened for you |
   | **Certificate password** | Use the password shown when the certificate was generated |

6. If it asks for a **Microsoft Dataverse** connection, sign in with the same maker
   account you use for this environment.

Tell me when the install finishes, or say **help** if something went wrong.

**End message.**
For ITSM, show:
**Message:**

Time to install the ServiceNow IT integration in Copilot Studio.

1. Open [Copilot Studio](https://copilotstudio.microsoft.com/).
2. Open your Employee Self-Service agent.
3. Go to **Settings** → **Customize**.
4. Find **ServiceNow IT** and select **Install**.
5. When it asks for connection details, enter:

   | Field | Value |
   |-------|-------|
   | **Authentication Type** | Use Oauth2 |
   | **Instance Name** | `{INSTANCE_NAME}` |
   | **Tenant Type** | `{TENANT_ID}` |
   | **Client Id** | `{APP_B_CLIENT_ID}` |
   | **Resource URI** | `{APP_A_CLIENT_ID}` |
   | **Client certificate secret** | Upload the `.pfx` certificate file I opened for you |
   | **Certificate password** | Use the password shown when the certificate was generated |

6. If it asks for a **Microsoft Dataverse** connection, sign in with the same maker
   account you use for this environment.

Tell me when the install finishes, or say **help** if something went wrong.

**End message.**
If the maker asks for help, mention these checks: confirm they are in the right
agent and **Settings → Customize**; check Power Automate **Connections** if sign-in
hangs; verify Resource URI / Client ID values; use the generated certificate
password or rerun certificate setup if it was lost; grant Entra admin consent if a
consent error appears. Then retry the current pack.
After each product install confirmation, continue to the next product. When all
in-scope products have been attempted, re-run:
```
python scripts/flightcheck/cli.py --scope servicenow --no-open
```
Read the `SN-PKG-001` row and loop until it passes. Keep S6.1 `in-progress` while the maker
is still installing or retrying.
### Record S6.1
When the pack checkpoint passes, merge `.local/connect/servicenow/config.json`:
- Set each in-scope `packs.<product>` to `"installed"`.
- Preserve `packs.installMode`, out-of-scope packs, and unknown fields.
Then show:
**Message:**

The ServiceNow extension pack content is installed for the products in scope.

**End message.**
`S6.1` is a **per-product** row: update it **once per in-scope product** (HRSD /
ITSM), passing `PRODUCT` (`"hrsd"` / `"itsm"`) to
[`../shared/checklist-updater.md`](../shared/checklist-updater.md) so each product's
state mirrors under `productStatus.<product>.S6.1` (not the shared `setupStatus`).
The gate depends on how the pack was installed:
- **`installMode == "automated"`** — the kit performed and verified the install, so
  this is a programmatic gate. Update S6.1 for each in-scope product with
  `GATE="prog"` and the pack checkpoint result; no separate maker acknowledgement is
  required.
- **`installMode == "manual"`** — this is a manual gate. Even with a passing
  checkpoint, ask for the explicit acknowledgement required by
  [`../shared/checklist-updater.md`](../shared/checklist-updater.md). Update S6.1 for
  each in-scope product with `GATE="manual"`, the pack checkpoint result, and
  `ACK=true` only when the maker confirms.
Persist immediately before continuing. (The `install_extension_pack.py` recorder
already writes `productStatus.<product>.S6.1` for a confirmed headless install; the
merges here remain the source of truth for gate evidence.)
---
## P6.3 — Bind the ServiceNow and Dataverse connections (SN-DV-CONN-001) *(completes S6.2)*
If the live status block (P6.2-A.1) is active, flip the **Bind connections** row to
`[~]` now, and to `[x]` when Record S6.2 (P6.3c) completes.
Verify the ServiceNow reference first.
**Message:**

Now I'll check that the ServiceNow connection is bound and active.

**End message.**

First, attempt to bind both connection references automatically. If the maker
already created the ServiceNow (and Dataverse) connection during installation,
this wires each to the extension pack's connection reference for them (a single
Dataverse write per reference — no re-authentication). The action reuses an
existing active connection per connector and self-reports which one it chose.
```
python scripts/bind_connections.py --connector all
```
Interpret the exit code and render the printed summary:
- **Exit 0** — every reference is now bound (or was already bound). If more than
  one active connection existed for a connector, the action bound the most recently
  created one and named it (with its owner) in the summary; relay that to the
  maker so they can veto. Continue to the checkpoints below.
- **Exit 4** — no active connection exists to bind for a connector. Show the manual
  message below so the maker creates one, then re-run this section.
- **Exit 3** — reported per connector when a reference is missing; if ServiceNow is
  missing, return to S6.1. A missing Dataverse reference alone does not block.
- **Exit 1** — the action errored; surface the message and fall back to the
  manual bind message below.

The `bind_connections.py` exit code above **is** the ServiceNow binding evidence for
S6.2: **exit 0** means the ServiceNow reference is bound to an active connection.
There is no standalone `SN-CONN-001` checkpoint in this workspace — do **not** run
`--checkpoint SN-CONN-001` (it returns "unknown checkpoint"); the connection's live
health is confirmed later by `SN-FLOWCONN-001` at P6.5, after the flows are on. Always
run `bind_connections.py` here even on resume (it is idempotent); never skip this step
because state was previously recorded — a pack reinstall can silently unbind the
reference. If the bind returned **exit 4** (no active connection to bind), show:
**Message:**

The ServiceNow connection isn't fully bound yet. In Copilot Studio, open your
agent's **Connections**, find the ServiceNow connection, and create, bind, or
re-authenticate it using the sign-in method we selected for this setup. Then tell
me and I'll re-check.

**End message.**
Wait, re-run `bind_connections.py`, and loop until it returns exit 0.
### P6.3a — Auth-type evidence
The successful bind (exit 0) proves the ServiceNow reference is bound to an active
connection. The Power Platform APIs do not expose a kit-verifiable auth-type
fingerprint, so ask the maker to attest the auth type after the bind result has been
rendered.
For `authType == "entra_user"`, show:
**Message:**

Please confirm the ServiceNow connection uses **Microsoft Entra ID User Login**.
Open the ServiceNow connection in Copilot Studio or Power Automate and check the
authentication type. Is it set that way?

**End message.**
Use the question tool with options **Yes** and **No / not sure**.
For `authType == "entra_certificate"`, show:
**Message:**

Please confirm the ServiceNow connection uses the Microsoft Entra certificate
sign-in method from this setup. Open the ServiceNow connection in Copilot Studio or
Power Automate and check the authentication type. Is it set that way?

**End message.**
Use the question tool with options **Yes** and **No / not sure**. If the maker
chooses **No / not sure**, leave S6.2 `in-progress`; have them re-create or re-bind
the ServiceNow connection with the selected auth type, then re-run this section.
### P6.3b — Verify Dataverse
**Message:**

Now I'll check that the Dataverse connection is bound to an active connection.

**End message.**
```
python scripts/flightcheck/cli.py --checkpoint SN-DV-CONN-001
```
Render the result. The auto-bind above already attempts the Dataverse reference
(reusing the environment's active Dataverse connection). This checkpoint matches
the Dataverse connection reference by its connector
(`shared_commondataserviceforapps`), so it validates the ServiceNow pack's own
Dataverse reference (e.g. `new_sharedcommondataserviceforapps_…`) — not the
Workday pack's `DV-CONN-001`, which keys on a Workday-specific reference suffix
and would report `NotConfigured` in a ServiceNow-only environment. On `FAILED` /
`NotConfigured`, show:
**Message:**

The Dataverse connection for the ServiceNow pack isn't bound to an active
connection yet. In Copilot Studio, open your agent's **Connections**, find the
Microsoft Dataverse connection, and bind or re-authenticate it with your maker
account. Then tell me and I'll re-check.

**End message.**
Re-run until it passes. If the result is `Skipped` because a Dataverse token is
unavailable, re-authenticate and re-run; do not complete the row on a skip.
### P6.3c — Write connection-reference state
When the ServiceNow bind returned exit 0, the `SN-DV-CONN-001` checkpoint passes, and
any auth-type attestation is satisfied, merge
`.local/connect/servicenow/config.json` to record this step:
```json
{
  "connections": {
    "servicenow": { "state": "bound", "authType": "{authType}", "verifiedBy": "programmatic-or-attested" },
    "dataverse": { "state": "bound", "verifiedBy": "programmatic" }
  }
}
```
Update S6.2 only after the ServiceNow bind returns exit 0, `SN-DV-CONN-001` passes, and
auth-type evidence is present. Use `GATE="prog"` when fully programmatic; if auth
type required maker confirmation, record the auth evidence as attested in the row
mirror while keeping the ServiceNow bind result and the passing `SN-DV-CONN-001` check.
Persist immediately.
The flow invoker binding (making Copilot Studio show the connection as
**Connected**) happens later in P6.5, after the flows are turned on.
---
## P6.4 — Turn on the ServiceNow flows (SN-FLOW-*) *(completes S6.3)*
If the live status block (P6.2-A.1) is active, flip the **Turn on the ServiceNow
flows** row to `[~]` now, and to `[x]` when this step completes.
Installing an extension pack lands its cloud flows in **Draft**. Copilot Studio will
not invoke a draft flow, so they must be turned on. This runs before the flow
invoker binding (P6.5) so the invoker connection lands on the activated flow
definition and does not go stale.
**Message:**

Now I'll turn on the ServiceNow cloud flows so your agent can use them.

**End message.**
First, turn on any off flows automatically. Activating a flow is a single Dataverse
write (`statecode`/`statuscode`); the action is ServiceNow-only, idempotent, and
self-reports which flows it switched on. A flow can only hold activation once its
connection references are bound, so this must run after P6.3.
```
python scripts/activate_flows.py --connector servicenow
```
Interpret the exit code and render the printed summary:
- **Exit 0** — every ServiceNow flow is on now, or was already on. Continue to the
  checkpoint below.
- **Exit 3** — no ServiceNow cloud flow was found; the pack may not have landed.
  Return to S6.1.
- **Exit 1** — the action errored; surface the message and fall back to the manual
  message below.

Then verify every ServiceNow cloud flow emitted by the installed packs.
```
python scripts/flightcheck/cli.py --scope servicenow --no-open
```
Read the `SN-FLOW-*` rows from the results (they are emitted by the ServiceNow scope
run, not standalone `--checkpoint` IDs; ignore rows owned by other steps here). If every emitted flow row passes, expand/update S6.3 as one
checkbox per flow result with `GATE="prog"`, persisting each generated row
immediately. If any flow row fails, show:
**Message:**

One or more ServiceNow cloud flows are turned off. In Power Platform, open the
managed ServiceNow solution, go to **Cloud flows**, and turn on any flow that is
off. Then tell me and I'll re-check.

**End message.**
Re-run the family checkpoint after the maker confirms. Leave each failing generated
row `in-progress` or `blocked` according to the checkpoint result; do not complete
the family until every emitted flow row passes. Do not invent flow names — use the
checkpoint descriptions.
When every flow row passes, merge `.local/connect/servicenow/config.json` to record
this step:
```json
{
  "flows": { "state": "on", "verifiedBy": "programmatic" }
}
```
---
## P6.5 — Connect and share the ServiceNow flow invoker connection (SN-FLOWCONN-001) *(completes S6.4 connect + S6.5 share)*
If the live status block (P6.2-A.1) is active, flip the **Connect the flow invoker
connection** row to `[~]` now. Binding the connection *reference* (P6.3) and turning
on the flows (P6.4) is necessary but not sufficient: Copilot Studio still shows the
ServiceNow connection as **Not connected** until the agent's per-flow *invoker
connection* is bound. This step performs that binding (the **connect** stage, S6.4)
and shares the connection parameters onto the portal-owned reference (the **share**
stage, S6.5), then verifies it. Because it runs after the flows are on, the binding
lands on the final, activated flow definition and will not go stale.

A single `connect_and_share.py` run drives both stages, but they are **separate
checkpoints** so a resume continues from whichever failed: if the connect succeeds
but sharing fails the action exits `6`, S6.4 is still recorded, and re-running
retries only the share (S6.5).
**Message:**

Now I'll connect the ServiceNow connection to your agent's flows so it shows as
connected in Copilot Studio.

**End message.**
Run the connect-and-share action. It is ServiceNow-only, idempotent, and
self-reports what it changed. It signs in to the Power Platform API (this may open a
browser the first time) and caches the token for the verification step.
```
python scripts/connect_and_share.py --connector servicenow
```
Interpret the exit code:
- **Exit 0** — the flow invoker connection is connected (or already was) and the
  parameters are shared. Flip both the **Connect** and **Share** status rows to `[x]`
  and continue to the checkpoint below.
- **Exit 6** — the flow invoker connection **is** connected but sharing the
  parameters failed (the message names the error). Flip the **Connect** row to `[x]`,
  leave the **Share** row `[~]`, and re-run this same action to retry only the share
  (it is idempotent — the connect is skipped as already-connected). If it keeps
  failing, fall back to the manual message below.
- **Exit 4** — the ServiceNow reference isn't bound to a connection yet, or the
  extension-pack flows don't reference the ServiceNow connector. Return to P6.3 to
  bind the reference, then re-run this step.
- **Exit 3** — the extension pack reference is missing; return to S6.1.
- **Exit 5** — the Power Platform environment id could not be resolved; re-run with
  `--environment-id <guid>` (from the Copilot Studio bot URL).
- **Exit 1** — the action errored; surface the message and fall back to the manual
  message below.

Then verify the flow invoker binding.
```
python scripts/flightcheck/cli.py --checkpoint SN-FLOWCONN-001
```
Render the result. On `FAILED` / `NotConfigured`, show:
**Message:**

The ServiceNow connection isn't connected to your agent's flows yet. In Copilot
Studio, open your agent's **Settings → Connections**, find the ServiceNow
connection, and connect it. Then tell me and I'll re-check.

**End message.**
If the checkpoint is `Skipped` because no Power Platform token is cached, re-run the
`connect_and_share.py` action above (it establishes the token), then re-check. Loop
until `SN-FLOWCONN-001` passes.
### P6.5a — Write flow invoker state (S6.4 connect) and share state (S6.5)
`connect_and_share.py` already self-records both stages on success (via
`connect_state.py`): S6.4 whenever the flow binding is confirmed connected — **even
if sharing then failed (exit 6)** — and S6.5 only when the parameters are confirmed
shared. When you record the rows manually (or the action's self-record was skipped),
mirror that split so a resume stays accurate.

**S6.4 (connect).** When `SN-FLOWCONN-001` passes, merge
`.local/connect/servicenow/config.json` (the reference-level `servicenow`/`dataverse`
state was already written in P6.3c; here we add the flow binding and overall status):
```json
{
  "connections": {
    "servicenow": { "state": "active", "authType": "{authType}", "flowBinding": "connected", "verifiedBy": "programmatic-or-attested" }
  },
  "status": "connected"
}
```
Also merge the legacy summary into `.local/config.json` so older scan/report flows
can discover the ServiceNow connection. Preserve every existing key:
```json
{
  "connections": {
    "ServiceNow": {
      "instanceName": "{INSTANCE_NAME}",
      "instanceUrl": "https://{INSTANCE_NAME}.service-now.com",
      "usage": "{usage}",
      "authType": "{authType}",
      "connectedAt": "{current ISO date}"
    }
  }
}
```
Update S6.4 only after `SN-FLOWCONN-001` passes (the connect stage). Use
`GATE="prog"`. Persist immediately.

**S6.5 (share).** Only after the action reports sharing succeeded (`share` is
`shared` / `created_shared_ref` / `already_shared`, i.e. the run exited `0`, not
`6`), merge the share artifact and record the row:
```json
{ "parameterSharing": "shared" }
```
Update S6.5 with `GATE="prog"`. If the action exited `6`, leave S6.5 `in-progress`,
keep S6.4 `done`, and re-run the action to retry the share before recording S6.5.
Persist immediately.
---
## P6.6 — Set the Portal Base URL (SN-BASEURL-001) *(completes S6.6)*
If the live status block (P6.2-A.1) is active, flip the **Set the Portal Base URL**
row to `[~]` now, and to `[x]` when this step completes — the whole block is then done.
Derive `{PORTAL_BASE_URL}` as `{instanceUrl}/sp`, for example
`https://contoso.service-now.com/sp`. Merge it into
`.local/connect/servicenow/config.json` as `portalBaseUrl` before verification.
**Message:**

One important setting the packs don't fill in for you: the **ServiceNow Portal
Base URL**. It's what turns case and ticket references into working links for your
employees.

For each ServiceNow pack you installed, do this in Copilot Studio:

1. Go to **...** → **Solutions** → **Managed**.
2. Open the managed solution for the pack:
   - For HR: **ServiceNow HR Solution** → **Objects** → `msdyn_ServiceNowHRSD`
   - For IT: **ServiceNow IT Solution** → **Objects** → `msdyn_ServiceNowITSM`
3. Set this value:

   ```json
   { "ServiceNowPortalBaseURI": "{PORTAL_BASE_URL}" }
   ```

Use the same URL for every ServiceNow pack you installed. Tell me when you've set
it for each pack in scope.

**End message.**
Only mention the HR object when HRSD is in scope and only mention the IT object when
ITSM is in scope. After confirmation, run:
```
python scripts/flightcheck/cli.py --scope servicenow --no-open
```
Read the `SN-BASEURL-001` row from the results. It is emitted by the ServiceNow
scope run and is also a registered checkpoint, so you can verify it directly with
`python scripts/flightcheck/cli.py --checkpoint SN-BASEURL-001 --no-open`.

`S6.6` is a **per-product** row: update it **once per in-scope product** (HRSD /
ITSM), passing `PRODUCT` (`"hrsd"` / `"itsm"`) to
[`../shared/checklist-updater.md`](../shared/checklist-updater.md) so each product's
state mirrors under `productStatus.<product>.S6.6`. The checkpoint verifies every
in-scope product at once and its result lists each product it found `set` vs
`empty`, so decompose it per product:
- `PASSED` → the URL is set for every in-scope product; update S6.6 for each with
  `GATE="prog"`.
- `FAILED` / `NotConfigured` / partial coverage → update S6.6 to `done` (`GATE="prog"`)
  for each product the result reports as **set**, and leave the products it reports as
  **empty** `in-progress`; requires attestation after the rendered result:
**Message:**

I can only partially verify that portal setting from the kit. Please confirm you
set the ServiceNow Portal Base URL shown above for every ServiceNow pack you
installed.

**End message.**
Use the question tool with options **Yes, it's set** and **Not yet**. On **Yes**,
update S6.6 for the pending product(s) with attested evidence and `ACK=true`. On
**Not yet**, leave those products' S6.6 `in-progress`, return to the portal setting
instructions, and re-check or re-attest after they finish.
Always include this note after S6.6 is recorded:
**Message:**

Remember: the ServiceNow Portal Base URL can reset after an extension-pack update.
If you update either ServiceNow pack later, set this value again so links keep
opening in the portal.

**End message.**
---
## Done
When S6.1 **and** S6.6 are `done` for **every in-scope product** (in
`productStatus.<product>`), and the shared S6.2, every generated S6.3 flow row, S6.4,
and S6.5 are `done` (in `setupStatus`), return
control to the setup router (`SKILL.md`) to resume at validation and handoff. Do not
run the end-to-end ServiceNow prompt test here; that belongs to the separate
`validate-and-handoff.md` playbook.
**Message:**

Your ServiceNow extension pack is installed and wired — the ServiceNow and
Dataverse connections are bound, the cloud flows are on and connected to your agent,
and the portal link setting is recorded. Next up is validating the setup end to end.

**End message.**
