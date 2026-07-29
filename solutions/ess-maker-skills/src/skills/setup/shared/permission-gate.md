# Permission Gate (Shared)

A reusable **role check → specific named error → stop** routine. Every Workday
setup skill applies this fragment before it performs role-restricted work, so no
skill duplicates inline role logic.

Every **Message** block is the exact text to show the user. Copy it verbatim. Do
not rephrase, add commentary, or tell the user what tools you are calling.

**Inputs from the calling file:**
- `REQUIRED_ROLE` — the human-readable role name to require (e.g.
  `"Workday Administrator"`, `"Power Platform Administrator"`,
  `"Application Administrator"`).
- `GATE_MODE` — `"programmatic"` or `"attested"` (see "Choosing a mode" below).
- `STEP_ID` — the master-checklist Step ID this gate protects (e.g. `"S4.1"`),
  used only to record evidence.
- `ROLE_QUERY` — *(programmatic mode only)* the command/check that proves the
  caller holds the role (the calling file supplies it; examples below).

**Outputs to the calling file:**
- `GATE_RESULT` — `"pass"` or `"stop"`. On `"stop"`, the calling file must halt
  **unless** it explicitly handles a specific `GATE_REASON` (see below).
- `GATE_REASON` — *(set only when `GATE_RESULT = "stop"`)* why the gate stopped, so
  a caller can tailor the follow-up:
  - `"acquiring_role"` — the user will obtain the role themselves and re-run.
  - `"delegated"` — another admin will perform this role-gated step.
  - `"declined"` — no path forward (no role, no delegate, or attestation refused).

  When `GATE_REASON = "delegated"`, a caller MAY, instead of halting, hand the
  delegated administrator the full manual runbook for the step and resume once the
  admin returns the resulting identifier (e.g. an Application (client) ID). A
  caller that does not implement such a runbook simply halts on any `"stop"`.
- `GATE_EVIDENCE` — an object recording how the gate was satisfied; the caller
  persists it under `setupStatus["{STEP_ID}"].verifiedBy` in
  `.local/connect/workday/config.json` (see `config-schema.md`):
  - `verifiedBy` ∈ `"programmatic"` \| `"attested"`.
  - `note` — short free text (e.g. the role-query result, or the user's
    attestation timestamp/identity).

---

## Choosing a mode

The gating mechanism differs by role because not every role has a queryable
directory:

| Role family | Mode | How verified |
|-------------|------|--------------|
| Entra roles (App Admin, Cloud App Admin, Global Admin, Priv Role Admin) | `programmatic` | Microsoft Graph role / privilege query |
| Power Platform Admin | `programmatic` | Power Platform admin API |
| Dataverse maker / system roles | `programmatic` | Dataverse security-role query |
| **Workday Administrator** | `attested` | No directory here → explicit named-role attestation + captured evidence |
| **InfoSec / IT** (firewall allowlisting) | `attested` | No directory here → explicit named-role attestation + captured evidence |

The calling file picks `GATE_MODE` from this table. **Never** silently pass an
attested role — always require the explicit confirmation in section G.2.

---

## G.1 — Programmatic gate

Use when `GATE_MODE` is `"programmatic"`.

Run the `ROLE_QUERY` the calling file supplied. Examples of what a caller passes:

- **Entra role (Graph):**
  ```
  az rest --method GET --url "https://graph.microsoft.com/v1.0/me/memberOf?%24select=displayName" --query "value[].displayName" -o json
  ```
  (OData options are percent-encoded — `%24select` not `$select` — so the URL
  survives PowerShell/bash `$`-expansion and runs first-try on every shell.)
  Pass if the result contains a directory role that grants `REQUIRED_ROLE`
  (e.g. `Application Administrator`, `Cloud Application Administrator`,
  `Global Administrator`).
- **Power Platform Admin / Dataverse role:** the caller supplies the specific
  admin-API or Dataverse query and the expected value.

**If the query proves the role is held:**
- Set `GATE_RESULT = "pass"`.
- Set `GATE_EVIDENCE = { "verifiedBy": "programmatic", "note": "<matched role/query result>" }`.
- Return to the calling file.

**If the query proves the role is NOT held** (or returns an
`Insufficient privileges` / `Authorization_RequestDenied` error — mirror the
existing pattern in `connect/azure/app-registration.md` section B.2):

If `REQUIRED_ROLE` is an Entra app/admin role (for example
`Application Administrator`, `Cloud Application Administrator`,
`Privileged Role Administrator`, `Global Administrator`), run this triage
sequence before deciding whether to stop:

Use the `vscode_askQuestions` tool:

```json
[
  {
    "header": "Role check",
    "question": "Do you have the {REQUIRED_ROLE} role?",
    "options": [
      { "label": "Yes, I have this role", "recommended": true },
      { "label": "No" }
    ],
    "allowFreeformInput": false
  }
]
```

- If **Yes, I have this role**: set `GATE_RESULT = "pass"`; set
  `GATE_EVIDENCE = { "verifiedBy": "attested", "note": "user attested {REQUIRED_ROLE} for {STEP_ID} after role query did not confirm" }`; return.
- If **No**: continue to question 2.

Use the `vscode_askQuestions` tool:

```json
[
  {
    "header": "Role acquisition",
    "question": "Can you get the {REQUIRED_ROLE} role?",
    "options": [
      { "label": "Yes, I can get this role", "recommended": true },
      { "label": "No" }
    ],
    "allowFreeformInput": false
  }
]
```

- If **Yes, I can get this role**:

  **Message:**

  Great — once your admin grants **{REQUIRED_ROLE}**, come back and run this
  step again.

  **End message.**

  Set `GATE_RESULT = "stop"`, `GATE_REASON = "acquiring_role"`, and return.

- If **No**: continue to question 3.

Use the `vscode_askQuestions` tool:

```json
[
  {
    "header": "Delegation",
    "question": "Will someone else with the {REQUIRED_ROLE} role do this step?",
    "options": [
      { "label": "Yes, someone else will do this", "recommended": true },
      { "label": "No" }
    ],
    "allowFreeformInput": false
  }
]
```

- If **Yes, someone else will do this**:

  **Message:**

  Perfect — ask that administrator to complete this role-gated step, then tell me
  when it's done and I'll verify and continue.

  **End message.**

  Set `GATE_RESULT = "stop"`, `GATE_REASON = "delegated"`, and return. (A caller
  that implements a delegated-admin runbook uses this reason to hand over the full
  step list and resume on the returned identifier instead of halting.)

- If **No**:

  **Message:**

  This step requires the **{REQUIRED_ROLE}** role and can't continue without it.
  Ask your administrator for access or have an administrator run this step.

  **End message.**

  Set `GATE_RESULT = "stop"`, `GATE_REASON = "declined"`, and return.

For non-Entra roles, keep the existing stop behavior:

**Message:**

This step requires the **{REQUIRED_ROLE}** role, and your account doesn't
have it. Ask your administrator to grant this role, then come back and run
this step again.

**End message.**

- Set `GATE_RESULT = "stop"`, `GATE_REASON = "declined"`.
- Return to the calling file. **The caller must halt — do not proceed.**

**If the query itself fails** for an unrelated reason (network, not logged in):
retry once. If it still fails, **do not** assume pass — fall back to the
attestation gate in G.2 (so a check error never silently grants access),
recording `note` = the query error.

---

## G.2 — Attestation gate

Use when `GATE_MODE` is `"attested"` (Workday Administrator, InfoSec/IT), or as
the fallback when a programmatic query errored.

**Message:**

This step requires the **{REQUIRED_ROLE}** role. I can't verify that
automatically for this system, so I need you to confirm you (or the person
doing this step) hold that role before we continue.

**End message.**

Use the `vscode_askQuestions` tool:

```json
[
  {
    "header": "Confirm role",
    "question": "Do you have the {REQUIRED_ROLE} role to perform this step?",
    "options": [
      { "label": "Yes, I have this role", "recommended": true },
      { "label": "No / not sure" }
    ],
    "allowFreeformInput": false
  }
]
```

For Entra app/admin roles, use the same 3-question triage sequence from G.1
(have role -> can get role -> someone else will do this) instead of a single
yes/no attestation question.

**If the user chose "Yes, I have this role":**
- Set `GATE_RESULT = "pass"`.
- Set `GATE_EVIDENCE = { "verifiedBy": "attested", "note": "user attested {REQUIRED_ROLE} for {STEP_ID}" }`.
- Return to the calling file.

**If the user chose "No / not sure":**

**Message:**

No problem — this step needs the **{REQUIRED_ROLE}** role. Ask whoever holds
that role to run it, then come back and continue.

**End message.**

- Set `GATE_RESULT = "stop"`, `GATE_REASON = "declined"`.
- Return to the calling file. **The caller must halt — do not proceed.**

> An attested `"pass"` records that the role was **claimed**, not directory-proven.
> It satisfies the *gate*, but it does **not** by itself complete the checklist row
> — the row still needs its own captured evidence/acknowledgement per
> `checklist-updater.md`.
