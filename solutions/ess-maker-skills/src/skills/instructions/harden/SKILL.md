# Harden Instructions Skill

This skill reviews an agent's **system instructions** (the `instructions:` block in `agent.mcs.yml`) and
proposes changes that reduce two failure modes:

- **Ungrounded answers** — the agent states something its knowledge sources do not support.
- **Over-committing answers** — the agent offers an action, service, or referral that nothing authorized.

It also always checks the instructions for **internal contradictions**, which are worth fixing even when
the agent is behaving well: a rule contradicted elsewhere is not in force, however firmly it is written.

> **Advisory and diff-based.** Every change is shown as exact before-and-after text and applied only after
> the maker approves. Instructions govern every answer the agent gives, so an unreviewed edit here is far
> more dangerous than an unreviewed edit to a single topic.

## Rules

- **Never rewrite the instructions wholesale.** Propose the smallest set of line-level changes that address
  what was actually found. A rewrite is unreviewable — the maker cannot tell an intended change from an
  incidental one — and it discards wording their organization may have chosen deliberately.
- **Do not tighten just because you were invoked.** If the review finds nothing and the maker reports no
  problem, say so and stop. Adding prohibitions "to be safe" causes the agent to refuse questions its
  sources fully answer, which is a real regression traded for a hypothetical one.
- **Quote before proposing.** Every finding and every proposed change names the exact line it applies to.
  Findings you cannot anchor to a line do not get reported.
- **Never propose a rule that blocks a supported action.** Check the agent's configured topics and tools
  before prohibiting anything the agent might legitimately do (see `INSTR-032`).
- **Never apply changes without a checkpoint** (Step 7).
- **Do not push.** This skill writes locally. Pushing is the maker's separate, explicit decision via `/push`.
- **Run the analysis silently.** Steps 3–5 are internal. Do not narrate which files you are reading, which
  rule ids matched, or what you are about to check. The maker sees Step 6 onward.
- **Speak the maker's language.** Never show `INSTR-*` ids, rule-pack filenames, or the words "detector",
  "rule pack", or "probe". Describe each finding in plain language and quote the maker's own text. Their
  instruction wording is *their* language and should be shown in full.
- **TRACK PROGRESS**: use the todo list tool to track the steps below so the maker can see where you are.

## What this checks and what it does not

This skill reads **only the instructions text**, plus the agent's topic and tool inventory for the
capability checks. It **cannot** tell you whether the agent actually produces a bad answer — instructions
are one input to that, and retrieval quality, knowledge-source coverage, and the underlying model matter at
least as much.

Say this plainly when it is relevant. A maker whose agent gives ungrounded answers because a knowledge
source is not being retrieved will get no benefit from tighter instructions, and letting them believe
otherwise costs them the time they should have spent on retrieval. Two signals that instructions are the
wrong lever:

- the agent answers correctly when the maker pastes the source content into the chat, but not otherwise;
- the agent says it cannot find information that the maker knows is in an attached source.

Both point at knowledge-source configuration. Route those makers to `/flightcheck` and `/troubleshoot`
rather than editing instructions.

## Step 1: Resolve the agent

Read `.local/config.json` for the agent folder. The instructions live at
`workspace/agents/{agent.folder}/agent.mcs.yml` in the `instructions:` block.

If the file is missing, tell the maker their agent has not been extracted yet and to run `/setup`, then STOP.

If the `instructions:` block is missing or empty, say so and STOP — there is nothing to harden, and this
usually means the agent's instructions were never configured rather than that they are safe.

## Step 2: Ask what the maker has actually seen

Ask before analyzing. What the maker has observed is better evidence than anything derivable from the text,
and it determines whether Step 6 proposes changes or only reports.

> Before I look at your instructions — have you seen specific answers from your agent that you didn't like?
>
> If you can paste one or two, that helps most: the exact question and what the agent said. Otherwise, tell
> me the kind of answer you want to prevent — for example, making claims your documents don't cover, or
> offering to do things the agent can't actually do.
>
> If nothing specific has gone wrong, that's fine too — say so and I'll check the instructions for
> contradictions and gaps and tell you what I find.

Record their answer. Do not paraphrase a vague answer into a specific complaint — if they said "it makes
things up sometimes" without an example, you have a **theme**, not a case, and Step 6 treats those
differently.

## Step 3: Read the instructions and the rule pack

Read the full `instructions:` value and the rule pack at
[`instruction-rules.md`](src/reference/ess-docs/hardening/instruction-rules.md).

Split the instructions into numbered lines so every finding can be anchored precisely. Keep the maker's
original wording, spelling, and casing exactly — you will be quoting it back and later diffing against it.

## Step 4: Contradiction pass (always runs)

Apply Part 1 of the rule pack (`INSTR-001` … `INSTR-005`). This pass runs regardless of the maker's answer
in Step 2.

For each contradiction, record **both** conflicting lines. Do not decide which one is "right" — the maker
knows which behavior they intended, and guessing produces a fix that removes a guardrail they wanted.

## Step 5: Grounding and over-commitment pass

Apply Parts 2 and 3 of the rule pack (`INSTR-010` … `INSTR-022`).

Where the maker gave specific bad responses in Step 2, work backward from each one: identify which
instruction *permitted* it, or which instruction that would have prevented it is **absent**. An absence is a
valid finding as long as you can state the specific behavior that is unconstrained.

For capability findings (`INSTR-021`), read the agent's topic files (`{agent.folder}/topics/`) and tool
inventory first. You need to know what the agent *can* do before writing a rule about what it cannot.

For any finding where an existing rule already targets the reported behavior **by listing forbidden
phrases**, record that the mechanism itself failed. Phrase lists are evaded by rewording; the replacement
must prohibit the *function* — offering, asserting, referring — and say that rephrasing does not exempt it.

## Step 6: Decide what to propose

Branch on Step 2:

**A — the maker described specific responses or a specific behavior.**
Propose targeted changes for those, plus any contradictions from Step 4. Every proposed change must trace to
either a reported behavior or a contradiction. Do not append unrelated hardening because the file happened
to be open.

**B — the maker reported nothing specific.**
Propose fixes for **contradictions** (Step 4) and for findings where a rule is **internally inconsistent or
vacuous** (`INSTR-004`, `INSTR-005`). Report the grounding and over-commitment findings from Step 5 as
observations with the risk each carries, and ask whether the maker wants any of them addressed. Do not
propose those changes pre-approved.

The reason is worth stating to the maker if they push back: prohibitions have a cost. Each one makes the
agent more likely to decline a question it could have answered, and without a reported problem there is
nothing to weigh that cost against.

Before finalizing any proposal, check it against Part 4 of the rule pack (`INSTR-030` … `INSTR-033`). Any
prohibition needs a stated alternative behavior, and the proposal as a whole needs a line stating the
prohibitions restrict invention rather than helpfulness.

## Step 7: Check the character budget

Instructions have a length ceiling in Copilot Studio, and hardening only adds text. Write the proposed full
instructions to `.local/harden/candidate.txt`, then run:

```
python scripts/check_instruction_budget.py --agent {agent.folder} --candidate .local/harden/candidate.txt
```

Read the `###INSTRUCTION_BUDGET_JSON###` line. **Its verdict is authoritative — do not estimate the length
yourself and do not override it.**

- `ok` — proceed.
- `tight` — proceed, and tell the maker how little room is left.
- `over` — **do not present the proposal as-is.** Identify what to remove and propose that too. Prefer
  removing permissive or vacuous lines (`INSTR-004`, `INSTR-011`, `INSTR-022`) — removing a line that invites
  the bad behavior is usually worth more than the prohibition you were trying to add. Re-run until `ok`.

The default limit is the kit's working assumption, not a verified platform constant. If the maker knows
their real ceiling, pass it with `--limit`.

## Step 8: Present the proposal and get approval

Present, in this order:

1. **What you found**, in plain language, grouped as contradictions first, then risks. Quote the maker's
   own line for each. If the instructions came from a shipped template, say that where it applies — several
   common findings are inherited defaults, not something the maker wrote.
2. **What you propose to change**, as before-and-after pairs:

   > **Currently:** "{exact original line}"
   > **Proposed:** "{exact replacement}"
   > **Why:** {one or two sentences tied to what this prevents}

   For a removal, show the line and say it is being removed and what that changes.
3. **The length**, in one line: the new total and the remaining headroom.
4. **What this does not cover**: instructions do not fix a knowledge source the agent cannot retrieve. If
   the maker's reported examples looked like retrieval problems (see "What this checks"), say so here.

Then ask for approval. The maker may accept all, accept some, or decline. Apply exactly what they accept.

If there is nothing to propose, say so directly and stop — do not manufacture a finding to justify the run.

## Step 9: Apply

Only after explicit approval:

```
python scripts/checkpoint.py "pre-harden-instructions"
python scripts/emit_capability.py harden
```

The `emit_capability.py` line records anonymous usage telemetry (best-effort, non-blocking); it needs no
user-facing message and never fails the step.

Then edit the `instructions:` block in `workspace/agents/{agent.folder}/agent.mcs.yml`, changing only the
approved lines. Preserve the YAML block scalar style and the indentation of the surrounding file.

Preserve **exactly** any `{System.Bot.Components.Topics...}` references or other placeholder tokens in the
instructions. These are live references, not example text — rewording one silently breaks the behavior it
drives.

Re-run the budget check without `--candidate` to confirm the written file measures as expected.

Delete `.local/harden/candidate.txt` once applied.

## Step 10: Hand off to validation

Instruction changes are behavioral changes, and this skill has no way to demonstrate that the new text
produces better answers. Say that plainly and route the maker onward:

> These changes aren't verified yet — instructions affect every answer, so it's worth checking the agent
> still behaves the way you want.
>
> - `/evaluate` — turn the answers you didn't like into test cases, so you can tell whether this fixed them
> - `/test` — try the agent's behaviour directly
> - `/push` — send the change to Copilot Studio when you're ready

Where the maker gave specific bad responses in Step 2, carry them forward: those are the highest-value
evaluation rows available, and they are the only direct evidence of whether this pass worked. Offer to run
`/evaluate` with them.

Also mention, once, that a change intended to prevent a bad answer can also cause the agent to decline good
questions — and that a few normal, in-scope questions are worth testing alongside the failing ones.

## References

- [`instruction-rules.md`](src/reference/ess-docs/hardening/instruction-rules.md) — the rule pack:
  contradiction classes, grounding and over-commitment risks, over-restriction risks, and rewriting
  principles.
