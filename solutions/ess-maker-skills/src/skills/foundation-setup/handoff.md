<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->
# Setup Step 7 — Connect Handoff

Load canonical state:

```text
python scripts/setup_state.py show
```

Build the completion report only from that output. Include:

- locked environment name, type, and endpoint;
- approved capacity model and governance status;
- preferred solution and publisher prefix;
- HR and IT installed/ready matrix;
- open issues;
- statement that ISV connection and topic work were not performed.

Ask the maker to confirm the report is accurate.

Record:

- `SETUP-HANDOFF-001` — report matches persisted state;
- `SETUP-HANDOFF-002` — all required setup gates passed;
- `SETUP-HANDOFF-003` — rerun will resume at this boundary.

Use `src/skills/foundation-setup/shared/validation.md` to persist all three. Handoff summary
confirmation is manual-attested; readiness and deterministic resume are automated.
`SETUP-HANDOFF-002` evidence must list the required setup modules that passed.

Run the final bundle by invoking:

```text
python scripts/setup_state.py finalize
```

The domain service requires every prior step and every check in
`SETUP-FINAL-BUNDLE`. On failure it records `SETUP-FINAL-001`, blocks `SETUP-07`
with the returned causes, and stops.

If it passes, record the final module result in the displayed report. The command
sets setup to done, records the completion timestamp, and marks `/connect` ready.

**Message:**

Your ESS foundation is ready. I'll now initialize the local ADK workspace from the
installed starter so the integration and authoring commands can use it.

**End message.**

Read `src/skills/onboarding/SKILL.md` and follow it. The onboarding flow is a common
local workspace bootstrap, not integration setup. When it completes:

**Message:**

Your ESS foundation and local workspace are complete and ready for integrations.

Run `/connect` and choose the system you want to connect. Topic creation remains a
separate `/create` workflow.

**End message.**
