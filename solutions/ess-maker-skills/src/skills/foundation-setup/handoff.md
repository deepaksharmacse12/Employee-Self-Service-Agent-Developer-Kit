<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->
# Setup Step 7 — Connect Handoff

Load canonical state:

```text
python scripts/setup_state.py show --view report
```

Build the completion report only from that output. Include:

- locked environment name, type, and endpoint;
- approved capacity model and governance status;
- preferred solution and publisher prefix, or `Not configured (skipped)`;
- HR and IT installed/ready matrix;
- open issues;
- statement that ISV connection and topic work were not performed.

Ask the maker to confirm the report is accurate.

After the maker confirms the report, persist one consolidated handoff result:

```text
python scripts/setup_state.py record-step-result \
  --step SETUP-07 \
  --mode manual-attested
```

Run the final bundle by invoking:

```text
python scripts/setup_state.py finalize
```

The domain service requires every prior step to be complete with a persisted
step result. On failure it blocks `SETUP-07` with the returned causes and stops.

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
