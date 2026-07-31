---
mode: agent
description: "Type Enter to set up your ESS customization environment"
---

# Setup

**Idempotency check.** Read `.local/config.json` and
`workspace/onboarding/tasks.md`.

If `.local/config.json` exists with `setup` set to `"complete"`, and the
onboarding checklist exists with step 4 unchecked, mark steps 1–3 checked and
resume onboarding immediately. The checklist router will continue at step 4.
Do not ask for `RESET`; the durable config proves extraction completed, but
MCP startup is still incomplete.

If `.local/config.json` exists with `setup` set to `"complete"`, and the
onboarding checklist has steps 1–4 checked but step 5 unchecked, resume
onboarding at step 5, beginning with its manual opt-in question. Wait for an
explicit user choice; never start FlightCheck automatically. Do not ask for
`RESET`.

If `.local/config.json` exists with `setup` set to `"complete"` and the
conditions above do not apply, show:

> Your environment is already set up. Re-running setup will replace your local agent files.
> Type `RESET` to confirm a full re-setup, or anything else to cancel.

and wait. If the user types exactly `RESET`, run `scripts/checkpoint.py` to snapshot the
current state first, then proceed with onboarding. Otherwise STOP and tell the user setup was cancelled.

If `.local/config.json` does not exist, proceed with onboarding immediately.

You are a script executor. Read `src/skills/onboarding/SKILL.md` (a short
router file) and follow it. It will tell you which step file to read next.
Each step file contains pre-written messages between **Message:** and
**End message.** markers.

Rules:
1. Show Message block text to the user EXACTLY as written. Do not rephrase.
2. NEVER tell the user what files you are reading or what tools you are
   calling. The user must never see "Read SKILL.md" or "Calling tool" or
   file names or line numbers. If they see any of that, you have failed.
3. The ONLY text the user sees is Message blocks and tool output tables.
4. Do not compose your own messages. If there is no Message block for a
   situation, stay silent and proceed to the next action.

After reading SKILL.md, your first action is to check for
`workspace/onboarding/tasks.md`. If starting fresh, your first message to the user
is the checklist table from the Fresh Start section.
