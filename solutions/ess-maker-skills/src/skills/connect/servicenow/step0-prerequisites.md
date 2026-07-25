# ServiceNow Step 0: Prerequisites Gate

Every **Message** block is the exact text to show the user. Copy it verbatim.
Do not rephrase, add commentary, or tell the user what tools you are calling.

**Do NOT show internal variable names or assignments to the user.**

> **This is a gate, not a configuration step** (spec §1.1). No ServiceNow work —
> no instance capture, no Entra apps, nothing — happens until all four prerequisite
> checkpoints pass. On any failure, hand off to `/setup`, let the maker complete it,
> then re-run these checks. Do **not** proceed on a partial pass, and **never**
> fabricate a pass (spec §1.3).

---

## 0.1 — Announce the gate

**Message:**

Before we connect ServiceNow, I'll make sure your ESS foundation is ready — the
Power Platform environment, Dataverse, message capacity, and the base ESS agent.
This takes a few seconds.

**End message.**

---

## 0.2 — Run the four prerequisite checkpoints

Run each checkpoint in isolation (these are reused, ServiceNow-agnostic checks that
already exist in FlightCheck):

```
python scripts/flightcheck/cli.py --checkpoint ENV-001
python scripts/flightcheck/cli.py --checkpoint ENV-002
python scripts/flightcheck/cli.py --checkpoint ENV-CAPACITY-001
python scripts/flightcheck/cli.py --checkpoint ESS-SOLN-001
```

Read `workspace/flightcheck/results.json` after each run for the checkpoint result.

Map the outcomes (spec §4 Step 1 success criteria):

- **All four `PASSED`** (capacity may be **attested** rather than programmatically
  passing) → the gate is satisfied. Record
  `stepStatus.P0 = { "state": "done", "verifiedBy": "programmatic" }` in
  `.local/connect/servicenow/config.json` (create the file with the §2 schema shell
  if it does not exist yet — the 4-row `tasks.md` checklist is unchanged; the
  prerequisites gate is tracked in `stepStatus`, not as a checklist row). Go to **0.4**.

- **`ENV-CAPACITY-001` is `WARNING`/attestable** and the other three `PASSED` →
  ask the maker to attest capacity is provisioned. If they confirm, record
  `verifiedBy: "attested"` and proceed to **0.4**. If not, treat as failure → **0.3**.

- **Any of `ENV-001`, `ENV-002`, `ESS-SOLN-001` `FAILED`** → **0.3** (handoff).

---

## 0.3 — Handoff to `/setup` on failure

Identify which checkpoint(s) failed and name the gap plainly (spec §4 negative
cases: ESS agent missing, Dataverse missing).

**Message:**

Your environment isn't ready for ServiceNow yet — {short reason, e.g. "there's no
Dataverse database" / "the base ESS agent isn't installed"}. I'll hand you to
`/setup` to finish the foundation first. Once it's done, come back and run
`/connect servicenow` again and I'll re-check.

**End message.**

Invoke `/setup` (read `src/skills/setup/SKILL.md` and follow it). **Do not proceed**
to instance capture. When the maker returns and confirms setup is complete, re-run
**0.2**. Only continue when all four checkpoints pass.

---

## 0.4 — Prerequisites satisfied

**Message:**

✅ Foundation verified — your environment, Dataverse, capacity, and base ESS agent
are all ready. Now let's connect ServiceNow.

**End message.**

Read `src/skills/connect/servicenow/step1.md` and follow it.
