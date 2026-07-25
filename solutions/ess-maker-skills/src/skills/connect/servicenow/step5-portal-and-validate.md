# ServiceNow Step 5: Portal Base URL, End-to-End Validation & Topic Handoff

Every **Message** block is the exact text to show the user. Copy it verbatim.
Do not rephrase, add commentary, or tell the user what tools you are calling.

**Do NOT show internal variable names or assignments to the user.**

> Covers spec Steps 5c, 5b (OBO), 5e, and Step 6. Completion is by FlightCheck
> checkpoint where coverage exists, otherwise by **maker attestation** — and the
> agent must **never fabricate a pass** (spec §1.3).

Restore INSTANCE_NAME, `scope`, and INSTANCE_URL from
`.local/connect/servicenow/config.json`.

---

## 5.1 — Configure the ServiceNow Portal Base URL (spec 5c)

Without this, ServiceNow links returned to employees don't resolve to a working
portal. Set `ServiceNowPortalBaseURI = <instance url>/sp` on the managed solution
object for each pack in scope.

**Message:**

One important setting the packs don't fill in for you: the **ServiceNow Portal
Base URL**. It's what turns case/ticket references into working links for your
employees. I'll walk you through it.

For each ServiceNow pack you installed, do this in Copilot Studio:

1. Go to **…** → **Solutions** → **Managed**
2. Open **{for HRSD: "ServiceNow HR Solution" → Objects → `msdyn_ServiceNowHRSD`}**
   **{for ITSM: "ServiceNow IT Solution" → Objects → `msdyn_ServiceNowITSM`}**
3. Set this value:

   ```json
   { "ServiceNowPortalBaseURI": "https://{INSTANCE_NAME}.service-now.com/sp" }
   ```

Use the **same URL** for both packs. Type **done** when you've set it for every
pack you installed.

**End message.**

Wait for the user. Only prompt for the packs that are `true` in `scope`.

When they confirm, persist `portalBaseUrl` in config.json
(`https://{INSTANCE_NAME}.service-now.com/sp`) and verify:

```
python scripts/flightcheck/cli.py --checkpoint SN-BASEURL-001
```

- **`PASSED`** (presence confirmed) → record
  `stepStatus.P5c = { "state": "done", "verifiedBy": "programmatic" }`. Continue.
- **No checkpoint result / partial coverage** → ask the maker to attest they set the
  value on every in-scope pack; on confirmation record `verifiedBy: "attested"`.
  Never mark done without one of these.

> ⚠️ **This resets after every pack update.** Tell the user they must reconfigure
> the Portal Base URL after updating either extension pack.

---

## 5.2 — Share connection parameters (OBO) (spec 5b)

**Message:**

Last connection housekeeping: **share the connection parameters** for your
ServiceNow connection. This stops your employees from being asked to authenticate
the first time they use a ServiceNow feature.

In the Power Apps maker portal → **Connections** → open your ServiceNow connection
→ **Share** → add your employees (or a group) with **Can use** access.

Type **done** when you've shared it.

**End message.**

Wait for the user. There is **no reliable automated validation** for OBO sharing
(spec 5b) — record `stepStatus.P5b = { "state": "done", "verifiedBy": "attested" }`
on the maker's confirmation.

---

## 5.3 — End-to-end validation (spec 5e) — the true proof

This is the real test: drive the actual employee prompts and confirm live data +
working links.

**Message:**

Now the moment of truth — let's prove it end to end. In a chat with your ESS
agent (Copilot Studio **Test** pane, or the published channel), send the prompt(s)
below for the areas you connected and tell me what comes back.

{if `scope.hrsd`}: **"Show my HR cases"** — should trigger *ServiceNow HRSD Get
User Cases* and return your real HR cases.
{if `scope.itsm`}: **"Show my IT tickets"** — should trigger *ServiceNow ITSM Get
User Tickets* and return your real IT tickets.

For each: do you see **real ServiceNow data**, and do the **links open** the right
record in ServiceNow?

**End message.**

Wait for the user. Interpret their answer:

- **Real data + working links (maker attests)** → record
  `stepStatus.P5e = { "state": "done", "verifiedBy": "attested" }` and each
  in-scope entry in `connections` as active. This is the E2E proof — proceed to 5.4.
- **401 / 403** → authentication / user-mapping problem. Send them back to the
  ServiceNow OIDC provider + user-mapping work (Step 4 /
  `step2-*`/`step3-*` for the chosen auth type). Do not mark done.
- **Empty results** → likely no records for that user, or a user-matching failure.
  Have them try a user known to have cases/tickets; if still empty, revisit the
  OIDC user-claim → ServiceNow user-field mapping.
- **Broken links** → the Portal Base URL is wrong or missing. Return to **5.1**.

Do not proceed to the topic handoff until the maker attests a successful E2E result
(or explicitly chooses to stop and troubleshoot later).

---

## 5.4 — Topic-creation handoff (spec Step 6)

The connect skill does **not** author topics — it hands off.

Use `vscode_askQuestions`:

```json
[
  {
    "header": "Custom topic",
    "question": "Do you want to create a new custom ServiceNow topic now?",
    "options": [
      { "label": "Yes — create a custom topic" },
      { "label": "No — I'm done for now" }
    ],
    "allowFreeformInput": false
  }
]
```

- **Yes** → hand off to the topic-creation skill (do **not** author the topic here).
  Read `src/skills/topics/create/SKILL.md` (or run `/create`) and follow it.
- **No** → show the closing message below.

**Message:**

✅ ServiceNow is connected end to end.

| # | Task | Status |
|---|------|--------|
| 1 | Instance configured | ✅ |
| 2 | Connection secured | ✅ |
| 3 | Extension installed | ✅ |
| 4 | Connection verified | ✅ |
| 5 | Portal URL + live test | ✅ |

Your out-of-the-box ServiceNow topics are ready to use. When you're set, **publish**
your agent in Copilot Studio so employees can use it. Here's what else you can do:

| Command | What it does |
|---------|-------------|
| `/create` | Create a new topic that uses ServiceNow |
| `/scan` | Check your agent for any errors |
| `/menu` | See all available commands |

**End message.**

Stop here.
