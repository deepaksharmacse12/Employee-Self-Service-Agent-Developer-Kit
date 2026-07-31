# Step 3: FlightCheck (Optional)

Every **Message** block is the exact text to show the user. Copy it verbatim.
Do not rephrase, add commentary, or tell the user what tools you are calling.

This step runs AFTER Step 2 completes (agent extracted, MCP started).

---

## 3.1 — Offer the readiness check

**MANDATORY MANUAL GATE:** Always ask this question, including when `/setup`
resumes directly at Step 5. Do not infer consent from the user running
`/setup`, from a previous answer, or from the checklist state. Invoke the
question tool and end the turn while waiting for the user's selection. Do not
run any FlightCheck command in the same turn as this question.

Use the `vscode_askQuestions` tool:

```json
[
  {
    "header": "FlightCheck",
    "question": "Want to run a quick readiness check on your environment? (~2-3 min, optional)",
    "options": [
      { "label": "Yes — run readiness check", "recommended": true },
      { "label": "Skip — remind me later" }
    ],
    "allowFreeformInput": false
  }
]
```

**If they choose "Skip":**

Do NOT update `workspace/onboarding/tasks.md`. Step 5 stays unchecked so
`/setup` will offer the readiness check again on the next run.

**Message:**

No problem — I'll keep this on your checklist. You can run
`/flightcheck` any time, or `/setup` will offer it again.

**End message.**

Stop here. Do not continue.

**Only if they explicitly choose "Yes — run readiness check":**

Proceed to 3.2.

Any response other than that explicit selection must not start FlightCheck.

---

## 3.2 — Run FlightCheck

**Message:**

Running readiness checks — this takes 1–3 minutes...

**End message.**

Run in the terminal:

```
python scripts/flightcheck/cli.py --scope full --invocation-source adk
```

Wait for the script to finish.

Update `workspace/onboarding/tasks.md` — change step 5 from `- [ ]` to `- [x]`.

---

## 3.3 — Present results

Read `workspace/flightcheck/results.json`. Present the results using the same
format as `src/skills/flightcheck/SKILL.md` Step 3 (summary banner →
category breakdown table → issues table → next steps). Follow that format
exactly.

After the results, always append the setup completion table:

| # | Task | Status |
|---|------|--------|
| 1 | Dataverse configured | ✅ |
| 2 | Agent discovered | ✅ |
| 3 | Agent extracted | ✅ |
| 4 | MCP server started | ✅ |
| 5 | Readiness check | ✅ |

Then show:

| Command | What it does |
|---------|-------------|
| `/create` | Create a new topic or workflow |
| `/connect` | Set up ServiceNow or Workday integration |
| `/scan` | Scan for compile errors |
| `/flightcheck` | Re-run readiness check after fixes |
| `/menu` | See all commands |

Stop here.
