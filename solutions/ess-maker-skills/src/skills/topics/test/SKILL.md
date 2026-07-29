# Test / Debug Topic Skill

Guides a maker through debugging a topic they just created or updated — driving it, confirming the reply is real, and localizing a fault to either the flow it calls or the topic's own internal state.

**This is the self-serve step that gets a topic to meet its evals.** The topic's **evaluation cases** (authored via the `evaluations/create` skill, stored as `EvaluationData` under `{agent.folder}/evaluations/`) describe the customer-facing behavior the topic must exhibit — especially failure handling (backend down, record missing, connection unauthorized). This skill exercises the topic against those scenarios and debugs it until it behaves. Once it does, the topic is ready for the **completion gate** — the automated eval runner that grades the same `EvaluationData` cases to gate completion (see Step 5). If the topic has no evaluation cases yet, author them with `evaluations/create` first — they are the standard you are debugging toward.

Driving a turn is still manual (you type in the Copilot Studio **Test** pane and read the reply); this skill adds three deterministic tools around that manual drive so you are not debugging on a phantom reply or guessing at hidden state:

- **`scripts/reply_signal.py`** — classify a captured reply so you know it is a real answer, not a consent gate / timeout / empty non-reply.
- **`scripts/flow_run_inspect.py`** — for flow-backed topics, read the flow's per-action run history (did the connector run, which action failed, why is the reply generic). Interpret it with `src/reference/ess-docs/operations/flow-run-inspection.md`.
- **`scripts/plant_debug.py`** / **`scripts/strip_debug.py`** — for topic-internal silent-state bugs, plant a temporary DBG node that projects a topic variable into the transcript, re-drive, read it, then strip it.

## Rules

- **Always strip.** A DBG node planted with `plant_debug.py` is a live mutation of the deployed topic. It MUST be removed with `strip_debug.py` before you finish — never leave debug noise in a shipped topic. If you plant, you strip, even if the diagnosis fails.
- **Classify before you trust a reply.** Do not diagnose topic logic on a reply until `reply_signal.py` says it is `ok`. A consent gate or empty reply will make any conclusion vacuous.
- **Drive is manual.** You (or the maker) type the trigger phrase in the Test pane and copy the full reply — including every bubble (a card plus a separate DBG bubble is one reply). This skill does not drive the browser.
- **Read-only where possible.** Flow inspection only reads run history. Only the DBG plant path mutates the topic, and it is byte-reversible.
- **TRACK PROGRESS.** Use the todo list tool to track the steps below so the maker can see where you are.

## Step 1: Classify the topic

Read the topic file and decide which fault surface applies — it drives which tool you reach for:

- **Flow-backed** — the topic calls a shared system topic (`BeginDialog` to `...System...`) or an `InvokeFlowAction`. Faults here are usually in the flow / connector path → **Step 3**.
- **Topic-only** — the topic branches on its own variables (a `ConditionGroup`, a parsed table, a count) with no backend call, or the backend call succeeded but a downstream branch/variable is wrong → **Step 4**.

Most real topics are both; work outward — confirm the drive (Step 2), then the flow (Step 3), then the topic's internal state (Step 4).

## Step 2: Drive and confirm the reply is real

1. Pick a scenario to drive. Prefer the topic's **evaluation cases** (`{agent.folder}/evaluations/`) — each `EvaluationData` case's `input` is a scenario the topic must handle, and the failure-handling cases (backend error, missing record, unauthorized) are the highest-value ones to exercise here, since they are what the completion gate will grade. Absent evals, use a representative trigger phrase.
2. Tell the maker to open the agent in Copilot Studio, switch to the **Test** pane, and send that scenario's input. Ask them to paste back the **full** reply (all bubbles).
3. Classify it:

   ```
   python scripts/reply_signal.py "<pasted reply text>"
   ```

   Add `--timed-out` if the turn never completed.

4. Act on the signal:
   - **`consent_gate`** — the backend never ran; the reply is a "Connect to continue" / connection-manager prompt. Have the maker authorize the connection (inline consent card or the maker portal's connection manager), then re-drive. Do NOT diagnose logic on this reply.
   - **`timeout`** — re-drive; a hibernating backend may need a warm-up call first.
   - **`empty`** — confirm the topic actually triggered (right trigger phrase, no conflicting topic), then re-drive.
   - **`ok`** — a real reply. Compare it against the eval case's `expectedOutput`: does the topic's failure handling match the standard? If yes, continue to the next scenario; if not, the divergence is your fault to localize (Steps 3–4).

Only proceed past this step on an `ok` reply.

## Step 3: Inspect the flow run (flow-backed topics)

When the reply is a real answer but wrong (generic error, missing data), read the flow's run history — the decisive "why" surface.

1. Get the **environment id** (GUID) and the **flow id** (GUID) of the flow the topic calls. The tool acquires a Flow-scoped token automatically via the kit's sign-in (set `FLOW_API_TOKEN` only to override with your own).
2. Dump the latest run's action cascade:

   ```
   python scripts/flow_run_inspect.py --environment <env-guid> --flow <flow-guid>
   ```

   Add `--run <run-guid>` to inspect a specific run.

3. Interpret the cascade using `src/reference/ess-docs/operations/flow-run-inspection.md`. The key trap: a `runAfter:[Failed]` handler that shows **Succeeded** does NOT mean the flow succeeded — the containing scope can still be **Failed** and a catch-all Response can discard its output, masking (say) a connector **400** as a generic **500**. The **first `Failed` action + its statusCode** is usually the real fault.

If the run history localizes the fault to the flow, fix the flow (see the workflow skills) — not the topic. If the flow ran clean but the topic still behaves wrong, the fault is topic-internal → Step 4.

## Step 4: Plant a DBG node (topic-internal silent state)

When the reply looks plausible and the flow run shows nothing wrong, but a branch fired wrong or a field came back blank, make the deciding topic state visible.

1. Pick the **action id** to instrument (the action that *populates* the variable you doubt) and the variable(s) to print. The DBG node must land **after** the populating action, or it reads a not-yet-set value.
2. Plant and publish:

   ```
   python scripts/plant_debug.py --topic <schemaname> --after <action-id> --activity "DBG branch={Topic.SomeVar} count={Topic.SomeCount}"
   ```

   This PATCHes the topic, records provenance to `.local/.dbg_provenance.json`, and publishes (retrying transient publish throttling automatically). It refuses to double-plant.

3. Have the maker **re-drive** the topic in the Test pane and paste back the reply. The DBG line renders as its own bubble; classify the reply with `reply_signal.py` if unsure it is real, then read the `DBG ...` values.
4. Interpret: an empty value where you expected data, or a branch tag that does not match the path you thought fired, is your fault.
5. **Strip — always:**

   ```
   python scripts/strip_debug.py
   ```

   This restores the topic byte-identically, publishes, and clears the provenance. Run it even if the diagnosis was inconclusive.

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
