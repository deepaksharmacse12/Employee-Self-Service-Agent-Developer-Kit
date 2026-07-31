# Test / Debug Topic Skill

Guides a maker through debugging a topic they just created or updated — driving it, confirming the reply is real, and localizing a fault to either the flow it calls or the topic's own internal state.

**This is the self-serve step that gets a topic to meet its evals.** The topic's **evaluation cases** (authored via the `evaluations/create` skill, stored as `EvaluationData` under `{agent.folder}/evaluations/`) describe the customer-facing behavior the topic must exhibit — especially failure handling (backend down, record missing, connection unauthorized). This skill exercises the topic against those scenarios and debugs it until it behaves. Once it does, the topic is ready for the **completion gate** — the automated eval runner that grades the same `EvaluationData` cases to gate completion (see Step 5). If the topic has no evaluation cases yet, author them with `evaluations/create` first — they are the standard you are debugging toward.

This skill **drives the topic automatically** — it launches (or attaches to) an InPrivate browser on the agent's test pane, sends the scenario, and captures the reply — then runs deterministic tools over that reply so you are not debugging on a phantom reply or guessing at hidden state:

- **`scripts/drive_topic.py`** — drive a scenario against the test pane and classify the reply in one step. It attaches to an already-open CDP browser, or launches Edge InPrivate on the test pane and prompts a single sign-in, then drives. InPrivate is deliberate: it signs in as a *test* account, not the ambient corp account.
- **`scripts/reply_signal.py`** — the classifier `drive_topic` uses (also runnable standalone on a pasted reply): real answer vs consent gate / timeout / empty.
- **`scripts/flow_run_inspect.py`** — for flow-backed topics, read the flow's per-action run history (did the connector run, which action failed, why is the reply generic). Interpret it with `src/reference/ess-docs/operations/flow-run-inspection.md`.
- **`scripts/plant_debug.py`** / **`scripts/strip_debug.py`** — for topic-internal silent-state bugs, plant a temporary DBG node that projects a topic variable into the transcript, re-drive, read it, then strip it.
- **`scripts/topic_checker_capture.py`** — for an **unexplained** "something went wrong" reply, surface the Copilot Studio authoring-canvas **Topic checker** (PowerFx / card errors that local diagnostics and a runtime drive both miss). Read-only; escalates command bar → 'More' overflow → tells you to check manually if the panel can't be surfaced.

## Rules

- **Always strip.** A DBG node planted with `plant_debug.py` is a live mutation of the deployed topic. It MUST be removed with `strip_debug.py` before you finish — never leave debug noise in a shipped topic. If you plant, you strip, even if the diagnosis fails.
- **Classify before you trust a reply.** Do not diagnose topic logic on a reply until `reply_signal.py` says it is `ok`. A consent gate or empty reply will make any conclusion vacuous.
- **Drive is automated, with a manual fallback.** `drive_topic.py` sends the turn and captures the full reply (all bubbles — a card plus a separate DBG bubble is one reply). If it cannot reach a signed-in test pane, it **warns and tells you how to fix it** (launch/sign-in) rather than failing silently; only then paste the reply into `reply_signal.py` by hand.
- **Read-only where possible.** Flow inspection only reads run history. Only the DBG plant path mutates the topic, and it is byte-reversible.
- **TRACK PROGRESS.** Use the todo list tool to track the steps below so the maker can see where you are.

## Step 1: Classify the topic

Read the topic file and decide which fault surface applies — it drives which tool you reach for:

- **Flow-backed** — the topic calls a shared system topic (`BeginDialog` to `...System...`) or an `InvokeFlowAction`. Faults here are usually in the flow / connector path → **Step 3**.
- **Topic-only** — the topic branches on its own variables (a `ConditionGroup`, a parsed table, a count) with no backend call, or the backend call succeeded but a downstream branch/variable is wrong → **Step 4**.

Most real topics are both; work outward — confirm the drive (Step 2), then the flow (Step 3), then the topic's internal state (Step 4).

## Step 2: Drive and confirm the reply is real

1. Pick a scenario to drive. Prefer the topic's **evaluation cases** (`{agent.folder}/evaluations/`) — each `EvaluationData` case's `input` is a scenario the topic must handle, and the failure-handling cases (backend error, missing record, unauthorized) are the highest-value ones to exercise here, since they are what the completion gate will grade. Absent evals, use a representative trigger phrase.
2. Drive it and classify the reply in one step:

   ```
   python scripts/drive_topic.py --prompt "<scenario input>"
   ```

   - **First run** (no browser attached yet): the tool launches Edge InPrivate on the agent's test pane and asks you to **sign in once with your test account**, then drives. Pass `--env <env-guid> --bot <bot-guid>` if they can't be read from the workspace config. Subsequent drives re-attach to that same browser automatically.
   - It prints the `signal`, a one-line remediation, and the captured reply.
   - **If it can't reach a signed-in test pane**, it warns and tells you exactly what to do (launch / sign in) — it does **not** fail silently. Fix that and re-run, or fall back to a manual drive: send the prompt in the Test pane, copy the full reply, and run `python scripts/reply_signal.py "<pasted reply>"`.
   - **After a publish** (e.g. you just planted a DBG node or edited the topic), add `--new-session` so the drive starts a fresh test conversation — otherwise stale routing from the pre-publish session can answer the turn.
   - **Grade deterministically** with `--expect "<text the reply must contain>"` and/or `--reject "<text it must not>"` (both repeatable). A failed assertion returns a non-zero exit — this is the axis that separates a real success from a `400`/runtime error, since **both are `ok` turns** (a real error reply is a real turn). Prefer wiring the eval case's `expectedOutput` substrings here.

3. Act on the signal:
   - **`consent_gate`** — the backend never ran; the reply is a "Connect to continue" / connection-manager prompt. Authorize the connection in the test pane, then re-drive. Do NOT diagnose logic on this reply.
   - **`timeout`** — re-drive; a hibernating backend may need a warm-up call first.
   - **`empty`** — confirm the topic actually triggered (right trigger phrase, no conflicting topic), then re-drive.
   - **`ok`** — a real reply. Note that a genuine backend error reply (e.g. `Error code: 400`) is also `ok` — it is a real turn, so the tool prints an **`advisory: reply is error-shaped`** line and you must assert on it, not assume success. Compare it against the eval case's `expectedOutput` (use `--expect`/`--reject` above to make this a pass/fail): does the topic's failure handling match the standard? If yes, continue to the next scenario; if not, the divergence is your fault to localize (Steps 3–4).

Only proceed past this step on an `ok` reply.

## Step 3: Inspect the flow run (flow-backed topics)

When the reply is a real answer but wrong (generic error, missing data), read the flow's run history — the decisive "why" surface.

1. Get the **flow id** (GUID) of the flow the topic calls. The **environment id** is resolved automatically from the active agent's Dataverse org URL — pass `--environment <env-guid>` only to override. The tool acquires a Flow-scoped token automatically via the kit's sign-in (set `FLOW_API_TOKEN` only to override with your own).
2. Dump the latest run's action cascade:

   ```
   python scripts/flow_run_inspect.py --flow <flow-guid>
   ```

   Add `--environment <env-guid>` to target a specific environment, or `--run <run-guid>` to inspect a specific run.

3. Interpret the cascade using `src/reference/ess-docs/operations/flow-run-inspection.md`. The key trap: a `runAfter:[Failed]` handler that shows **Succeeded** does NOT mean the flow succeeded — the containing scope can still be **Failed** and a catch-all Response can discard its output, masking (say) a connector **400** as a generic **500**. The **first `Failed` action + its statusCode** is usually the real fault.

If the run history localizes the fault to the flow, fix the flow (see the workflow skills) — not the topic. If the flow ran clean but the topic still behaves wrong, the fault is topic-internal → Step 4. If the flow ran clean (or the topic isn't flow-backed) **and the error is generic and unexplained** — "something went wrong" with no status code, table, or field named — the surface may be a publish-time authoring defect the runtime never articulates → **Step 3b**.

## Step 3b: Surface the Topic checker (unexplained authoring-canvas errors)

A generic, unexplained error reply — no status code, no named table/field — is frequently the runtime surface of a **publish-time authoring defect** (an invalid Adaptive Card JSON, a broken PowerFx expression) that a runtime drive and flow inspection both miss, because the defect never runs cleanly enough to produce a specific fault. `reply_signal.py`'s error-shaped advisory tells you the reply is an error; when it carries **no actionable detail**, reach for the Topic checker.

```powershell
python scripts/topic_checker_capture.py --topic-id <GUID> --json
```

The tool follows an escalation ladder against the authoring canvas and reports exactly which rung it reached:

1. **Command bar** — clicks the Topic checker button when it's directly visible, captures each error (message + linked node/field where available).
2. **'More' overflow** — when the button is hidden, opens the '…' overflow menu and clicks it there.
3. **Manual fallback** — the panel has additional rendering conditions that can't be automated. If neither rung surfaces it, the tool reports `not run` (NOT `clean`) and advises you to **open the Topic checker manually in Copilot Studio and read the errors yourself** — a run that never happened is never reported as a passing check.

Each captured error names a `componentId` (a GUID) rather than the topic's display name — resolve it via `.component-map.json`. Fix the named card/expression, republish, and re-drive. If the tool reports `not run`, do not conclude the topic is clean — surface the checker manually before moving on.



When the reply looks plausible and the flow run shows nothing wrong, but a branch fired wrong or a field came back blank, make the deciding topic state visible.

1. Pick the **action id** to instrument (the action that *populates* the variable you doubt) and the variable(s) to print. The DBG node must land **after** the populating action, or it reads a not-yet-set value.
2. Plant and publish. You **own the consent decision in chat**: this mutates the deployed topic, so confirm with the user first, then pass `--yes` — the script otherwise prompts on `input()`, which a non-interactive subprocess cannot answer and which reads as a hang.

   ```
   python scripts/plant_debug.py --topic <topic> --after <action-id> --activity "DBG branch={Topic.SomeVar} count={Topic.SomeCount}" --yes
   ```

   `--topic` accepts the friendly file stem (e.g. `servicenow-hrsd-get-cases-by-status`), the display name, or the full schemaname — it resolves to the immutable schemaname via `.component-map.json`. This PATCHes the topic, records provenance to `.local/.dbg_provenance.json`, and publishes (retrying transient publish throttling automatically). It refuses to double-plant.

3. **Re-drive** the topic with a fresh session so the just-published change is what answers (`python scripts/drive_topic.py --prompt "<same scenario>" --new-session`). The DBG line renders as its own bubble and is captured with the rest of the reply; read the `DBG ...` values from the output.
4. Interpret: an empty value where you expected data, or a branch tag that does not match the path you thought fired, is your fault.
5. **Strip — always:**

   ```
   python scripts/strip_debug.py --yes
   ```

   This restores the topic byte-identically, publishes, and clears the provenance. Run it even if the diagnosis was inconclusive. (`--yes` because you already owned the plant/strip consent above; the bare command prompts and would hang as a subprocess.)

## Step 5: Report

Summarize for the maker:

- The drive-outcome signal (Step 2) and, if it was not `ok`, what unblocked it.
- If flow-inspected: the first failing action + statusCode and whether the fault is in the flow or the topic.
- If DBG-planted: the decisive variable/branch value and what it revealed — and confirm the plant was stripped.
- The concrete fix and where it belongs (topic YAML, flow, template config).

If you planted a DBG node at any point, confirm `strip_debug.py` ran and `.local/.dbg_provenance.json` is gone before you finish.

## Step 6: Hand off to the completion gate

Once the topic behaves correctly on its evaluation scenarios — the failure-handling cases included — it has met the standard this skill exists to reach. Hand off:

- If the topic has evaluation cases, tell the maker it is ready for the **completion gate**: the automated eval runner grades the same `EvaluationData` cases to decide whether the topic can be marked complete. (That runner is a separate workstream; this skill's job ends at "the topic meets its evals.")
- If the topic has **no** evaluation cases yet, point the maker at the `evaluations/create` skill to author them — the topic cannot be gated on a standard that does not exist, and the cases you just debugged against should be captured there so the gate can enforce them.
- Optionally validate the eval set's quality first with `python scripts/evaluate_evals.py` (this grades whether the *eval cases themselves* are realistic and well-scoped — it does not drive the topic).
