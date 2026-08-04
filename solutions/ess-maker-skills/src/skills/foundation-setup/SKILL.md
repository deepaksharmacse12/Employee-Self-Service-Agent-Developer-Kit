<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->
# ESS Foundation Setup

Every **Message** block is exact user-facing text. Do not expose internal step IDs,
checkpoint IDs, state paths, or tool narration.

Follow `src/reference/ui-formatting-guidelines.md` for every user-facing
instruction in this flow. Resolve its examples with the actual environment,
agent, product, and connector names before displaying them.

This is the single integration-neutral `/setup` entry point. It owns only:

- Power Platform and Copilot Studio prerequisites;
- environment selection and binding;
- preferred unmanaged solution configuration;
- HR and/or IT ESS starter installation;
- baseline readiness;
- the handoff to `/connect`.

Workday, ServiceNow, SAP SuccessFactors, authentication, extension packs, and topics
are explicitly outside this skill.

---

## Start

Record anonymous usage telemetry best-effort:

```text
python scripts/emit_capability.py setup
```

Initialize or load state:

```text
python scripts/setup_state.py init
```

The command validates the state schema, imports only reusable S1/S2 foundation
progress from a legacy Workday run, and prints the canonical state. If it fails,
show the specific error and stop. Never recreate or overwrite corrupt state silently.

If `connect_ready` is true, inspect `.local/config.json`:

- If its `setup` value is `"complete"`, show that foundation and workspace setup
  are complete and direct the maker to `/connect`.
- Otherwise read `src/skills/onboarding/SKILL.md` and follow it to initialize the
  local ADK workspace from the installed starter. Onboarding must reuse
  `environment.tenant_endpoint` from this locked foundation state and must not
  ask the maker to select the environment again. When onboarding completes,
  return here and show the completed handoff.

Render the checklist from `src/skills/foundation-setup/tasks.md` using the states returned by
the command:

- `done` = ✅
- `in-progress` = 🔄
- `blocked` = ⛔
- `pending` = ⬜

**Message:**

Here's your ESS foundation setup:

- {marker} Choose and lock the target environment and ESS products
- {marker} Confirm access, MCP, capacity, billing, and governance prerequisites
- {marker} Verify the environment and Dataverse
- {marker} Configure the preferred unmanaged solution
- {marker} Install and bind the selected ESS products
- {marker} Verify baseline agent readiness
- {marker} Confirm the setup-to-connect handoff

**End message.**

The persisted `active_step` is authoritative. Dispatch to it immediately.
Resumption does not require user input.

---

## Dispatch

| Active step | Playbook |
|---|---|
| `SETUP-01` | `src/skills/foundation-setup/scope.md` |
| `SETUP-02` | `src/skills/foundation-setup/prerequisites.md` |
| `SETUP-03` | `src/skills/foundation-setup/environment.md` |
| `SETUP-04` | `src/skills/foundation-setup/alm-baseline.md` |
| `SETUP-05` | `src/skills/foundation-setup/install-starters.md` |
| `SETUP-06` | `src/skills/foundation-setup/readiness.md` |
| `SETUP-07` | `src/skills/foundation-setup/handoff.md` |

Read and follow the playbook for the active step. After it returns, restart at
**Start** so the state is reloaded and the next step is resolved deterministically.

Never route from `/setup` into an integration or topic playbook.
