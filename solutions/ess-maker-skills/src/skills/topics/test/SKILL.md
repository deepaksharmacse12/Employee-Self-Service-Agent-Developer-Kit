# Test / Debug Topic Skill

Guides a maker through debugging a topic they just created or updated — driving it, confirming the reply is real, checking the fix doesn't break the intent the eval cases encode, and localizing a fault to either the flow it calls or the topic's own internal state.

**Eval cases are the read-only intent guardrail.** A topic's **evaluation cases** (authored upstream via the `evaluations/create` skill, stored as `EvaluationData` under `{agent.folder}/evaluations/`) encode the intended customer-facing behaviour — especially failure handling (backend down, record missing, connection unauthorized). This skill treats them as a **guardrail: a fix must not break the intent they encode.** It reads that intent; it does not grade the topic against the cases and it never edits them. If a topic has no eval cases yet, that is a signal to author them with `evaluations/create` — not work this skill does. Absent evals, drive a representative trigger phrase instead.

## The debug-and-validate loop

Debugging a topic is one loop, repeated per probe until the behaviour is right. It is not a heavyweight process — it is the loop you already run naturally — but the moves are:

1. **Pick a probe** — the input you drive the topic with. It is one of two kinds, and you usually use both in a session:
   - an **eval-case input** — the `input` of an `EvaluationData` case, driven to check a fix still preserves the intent that case encodes; or
   - an **exploratory probe** — an ad-hoc prompt you drive to build or harden behaviour that no eval case covers yet (what you do while a topic is still coming together).

   Drive the failure-handling probes first — backend error, missing record, unauthorized — they are the hardest and the highest-value.
2. **Drive and capture** the full reply.
3. **Confirm the reply is real** — classify it (`ok` vs consent gate / timeout / empty) so you never diagnose a phantom reply.
4. **Check the behaviour against intent** — by the kind of probe: where an eval case covers this behaviour, confirm the fix doesn't break the intent it encodes; on an exploratory probe, judge against your own intent for the behaviour you are building. Use deterministic substring checks (`--expect` / `--reject`) as a quick sanity signal, and a **best-effort LLM judge over the capture** where intent is a matter of judgement (does the failure message actually help the user, is the tone right, is anything missing). This is a guardrail on your fix, not a grade of the topic — both signals are advisory and inform your diagnosis.
5. **If it diverges, localize the fault** — flow run history, the Topic checker, or a planted DBG node — and **fix the topic, or its flow / template config.**
6. **Re-drive the same probe** until the behaviour holds, then move to the next.

Both kinds run in the **same loop** and usually coexist — the common state is a topic with some eval cases plus behaviour still being built. Existing eval cases are a **standing regression guardrail** the whole time: even while you drive exploratory probes on new ground, a fix must not break the intent the existing cases encode. When an exploratory probe's behaviour stabilizes, capture it as a case with `evaluations/create` so the new ground becomes guardrail too.

**The one invariant:** every fix lands on the **topic** (or its flow / template config) — never on the eval cases. The eval cases are the fixed intent you check against; changing them to make a topic behave defeats the guardrail.

This skill **drives the topic automatically** — it launches (or attaches to) an InPrivate browser on the agent's test pane, sends the probe, and captures the reply — then runs deterministic tools over that reply so you are not debugging on a phantom reply or guessing at hidden state:

- **`scripts/drive_topic.py`** — drive a probe against the test pane and classify the reply in one step. It attaches to an already-open CDP browser, or launches Edge InPrivate on the test pane and prompts a single sign-in, then drives. InPrivate is deliberate: it signs in as a *test* account, not the ambient corp account.
- **`scripts/reply_signal.py`** — the classifier `drive_topic` uses (also runnable standalone on a pasted reply): real answer vs consent gate / timeout / empty.
- **`scripts/flow_run_inspect.py`** — for flow-backed topics, read the flow's per-action run history (did the connector run, which action failed, why is the reply generic). Interpret it with `src/reference/ess-docs/operations/flow-run-inspection.md`.
- **`scripts/plant_debug.py`** / **`scripts/strip_debug.py`** — for topic-internal silent-state bugs, plant a temporary DBG node that projects a topic variable into the transcript, re-drive, read it, then strip it.
- **`scripts/topic_checker_capture.py`** — for an **unexplained** "something went wrong" reply, surface the Copilot Studio authoring-canvas **Topic checker** (PowerFx / card errors that local diagnostics and a runtime drive both miss). Read-only; escalates command bar → 'More' overflow → tells you to check manually if the panel can't be surfaced.

## Rules

- **Always strip.** A DBG node planted with `plant_debug.py` is a live mutation of the deployed topic. It MUST be removed with `strip_debug.py` before you finish — never leave debug noise in a shipped topic. If you plant, you strip, even if the diagnosis fails.
- **Classify before you trust a reply.** Do not diagnose topic logic on a reply until `reply_signal.py` says it is `ok`. A consent gate or empty reply will make any conclusion vacuous.
- **Drive is automated, with a manual fallback.** `drive_topic.py` sends the turn and captures the full reply (all bubbles — a card plus a separate DBG bubble is one reply). If it cannot reach a signed-in test pane, it **warns and tells you how to fix it** (launch/sign-in) rather than failing silently; only then paste the reply into `reply_signal.py` by hand.
- **Read-only where possible.** Flow inspection only reads run history. Only the DBG plant path mutates the topic, and it is byte-reversible.
- **Check, don't assume.** An `ok` reply is a real turn, not a correct one — a `400` error reply is also `ok`. Check the content against intent: deterministic `--expect` / `--reject` substrings as a sanity signal, plus a best-effort LLM judge over the capture when intent needs judgement. The eval cases are the intent you check against and are **read-only here** — a fix must not break them, and you never edit them to make a topic behave.
- **TRACK PROGRESS.** Use the todo list tool to track the loop so the maker can see where you are.

## Classify the topic — which fault surface?

Read the topic file and decide which fault surface applies — it drives which tool you reach for:

- **Flow-backed** — the topic calls a shared system topic (`BeginDialog` to `...System...`) or an `InvokeFlowAction`. Faults here are usually in the flow / connector path → **Inspect the flow run**.
- **Topic-only** — the topic branches on its own variables (a `ConditionGroup`, a parsed table, a count) with no backend call, or the backend call succeeded but a downstream branch/variable is wrong → **Plant a DBG node**.

Most real topics are both; work outward — confirm the drive, then the flow, then the topic's internal state.

## Drive, confirm, and validate the reply

1. Pick a probe to drive — an **eval-case input** (`{agent.folder}/evaluations/`; each `EvaluationData` case's `input` is a behaviour the topic must handle) or an **exploratory prompt** for behaviour no case covers yet. Drive the failure-handling probes first (backend error, missing record, unauthorized) — they are the highest-value.
2. Drive it and classify the reply in one step:

   ```
   python scripts/drive_topic.py --prompt "<probe input>"
   ```

   - **First run** (no browser attached yet): the tool launches Edge InPrivate on the agent's test pane and asks you to **sign in once with your test account**, then drives. Pass `--env <env-guid> --bot <bot-guid>` if they can't be read from the workspace config. Subsequent drives re-attach to that same browser automatically.
   - It prints the `signal`, a one-line remediation, and the captured reply.
   - **If it can't reach a signed-in test pane**, it warns and tells you exactly what to do (launch / sign in) — it does **not** fail silently. Fix that and re-run, or fall back to a manual drive: send the prompt in the Test pane, copy the full reply, and run `python scripts/reply_signal.py "<pasted reply>"`.
   - **After a publish** (e.g. you just planted a DBG node or edited the topic), add `--new-session` so the drive starts a fresh test conversation — otherwise stale routing from the pre-publish session can answer the turn.
   - **Sanity-check the content** with `--expect "<text the reply must contain>"` and/or `--reject "<text it must not>"` (both repeatable). A failed assertion returns a non-zero exit — this is the axis that separates a real success from a `400`/runtime error, since **both are `ok` turns** (a real error reply is a real turn). Where an eval case names expected content, use its `expectedOutput` substrings here as the intent to hold to.

3. Act on the signal:
   - **`consent_gate`** — the backend never ran; the reply is a "Connect to continue" / connection-manager prompt. Authorize the connection in the test pane, then re-drive. Do NOT diagnose logic on this reply.
   - **`timeout`** — re-drive; a hibernating backend may need a warm-up call first.
   - **`empty`** — confirm the topic actually triggered (right trigger phrase, no conflicting topic), then re-drive.
   - **`ok`** — a real reply. Note that a genuine backend error reply (e.g. `Error code: 400`) is also `ok` — it is a real turn, so the tool prints an **`advisory: reply is error-shaped`** line and you must check it, not assume success. Check the behaviour against intent — the eval case's where one covers this probe, your own for an exploratory probe: use `--expect` / `--reject` for the substrings, and — where intent is a matter of judgement (is the failure message actually helpful, is the tone right, is anything missing) — apply a best-effort LLM judge over the capture. If the behaviour holds, move to the next probe; if it diverges, localize the fault (below) and fix the topic / flow / config.

Only proceed past this step on an `ok` reply.

## Inspect the flow run (flow-backed topics)

When the reply is a real answer but wrong (generic error, missing data), read the flow's run history — the decisive "why" surface.

1. Get the **flow id** (GUID) of the flow the topic calls. The **environment id** is resolved automatically from the active agent's Dataverse org URL — pass `--environment <env-guid>` only to override. The tool acquires a Flow-scoped token automatically via the kit's sign-in (set `FLOW_API_TOKEN` only to override with your own).
2. Dump the latest run's action cascade:

   ```
   python scripts/flow_run_inspect.py --flow <flow-guid>
   ```

   Add `--environment <env-guid>` to target a specific environment, or `--run <run-guid>` to inspect a specific run.

3. Interpret the cascade using `src/reference/ess-docs/operations/flow-run-inspection.md`. The key trap: a `runAfter:[Failed]` handler that shows **Succeeded** does NOT mean the flow succeeded — the containing scope can still be **Failed** and a catch-all Response can discard its output, masking (say) a connector **400** as a generic **500**. The **first `Failed` action + its statusCode** is usually the real fault.

If the run history localizes the fault to the flow, fix the flow (see the workflow skills) — not the topic. If the flow ran clean but the topic still behaves wrong, the fault is topic-internal → **Plant a DBG node**. If the flow ran clean (or the topic isn't flow-backed) **and the error is generic and unexplained** — "something went wrong" with no status code, table, or field named — the surface may be a publish-time authoring defect the runtime never articulates → **Surface the Topic checker**.

## Surface the Topic checker (unexplained authoring-canvas errors)

A generic, unexplained error reply — no status code, no named table/field — is frequently the runtime surface of a **publish-time authoring defect** (an invalid Adaptive Card JSON, a broken PowerFx expression) that a runtime drive and flow inspection both miss, because the defect never runs cleanly enough to produce a specific fault. `reply_signal.py`'s error-shaped advisory tells you the reply is an error; when it carries **no actionable detail**, reach for the Topic checker.

```powershell
python scripts/topic_checker_capture.py --topic-id <GUID> --json
```

The tool follows an escalation ladder against the authoring canvas and reports exactly which rung it reached:

1. **Command bar** — clicks the Topic checker button when it's directly visible, captures each error (message + linked node/field where available).
2. **'More' overflow** — when the button is hidden, opens the '…' overflow menu and clicks it there.
3. **Manual fallback** — the panel has additional rendering conditions that can't be automated. If neither rung surfaces it, the tool reports `not run` (NOT `clean`) and advises you to **open the Topic checker manually in Copilot Studio and read the errors yourself** — a run that never happened is never reported as a passing check.

Each captured error names a `componentId` (a GUID) rather than the topic's display name — resolve it via `.component-map.json`. Fix the named card/expression, republish, and re-drive. If the tool reports `not run`, do not conclude the topic is clean — surface the checker manually before moving on.

## Plant a DBG node (topic-internal silent state)

When the reply looks plausible and the flow run shows nothing wrong, but a branch fired wrong or a field came back blank, make the deciding topic state visible.

1. Pick the **action id** to instrument (the action that *populates* the variable you doubt) and the variable(s) to print. The DBG node must land **after** the populating action, or it reads a not-yet-set value.
2. Plant and publish. You **own the consent decision in chat**: this mutates the deployed topic, so confirm with the user first, then pass `--yes` — the script otherwise prompts on `input()`, which a non-interactive subprocess cannot answer and which reads as a hang.

   ```
   python scripts/plant_debug.py --topic <topic> --after <action-id> --activity "DBG branch={Topic.SomeVar} count={Topic.SomeCount}" --yes
   ```

   `--topic` accepts the friendly file stem (e.g. `servicenow-hrsd-get-cases-by-status`), the display name, or the full schemaname — it resolves to the immutable schemaname via `.component-map.json`. This PATCHes the topic, records provenance to `.local/.dbg_provenance.json`, and publishes (retrying transient publish throttling automatically). It refuses to double-plant.

3. **Re-drive** the topic with a fresh session so the just-published change is what answers (`python scripts/drive_topic.py --prompt "<same probe>" --new-session`). The DBG line renders as its own bubble and is captured with the rest of the reply; read the `DBG ...` values from the output.
4. Interpret: an empty value where you expected data, or a branch tag that does not match the path you thought fired, is your fault.
5. **Strip — always:**

   ```
   python scripts/strip_debug.py --yes
   ```

   This restores the topic byte-identically, publishes, and clears the provenance. Run it even if the diagnosis was inconclusive. (`--yes` because you already owned the plant/strip consent above; the bare command prompts and would hang as a subprocess.)

## Report

Summarize for the maker:

- The drive-outcome signal and, if it was not `ok`, what unblocked it.
- If flow-inspected: the first failing action + statusCode and whether the fault is in the flow or the topic.
- If DBG-planted: the decisive variable/branch value and what it revealed — and confirm the plant was stripped.
- The concrete fix and where it belongs (topic YAML, flow, template config).

If you planted a DBG node at any point, confirm `strip_debug.py` ran and `.local/.dbg_provenance.json` is gone before you finish.
