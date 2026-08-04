<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->
# Setup Step 6 — Baseline Readiness

Mark the step in progress and read the selected starter matrix:

```text
python scripts/setup_state.py update-step --step SETUP-06 --status in-progress
python scripts/setup_state.py show
```

For each selected installed starter:

1. Open it in Copilot Studio.
2. Confirm it can be edited.
3. Confirm **Configure** and **Topics** are reachable.
4. Confirm the agent shell and starter content footprint are present.

Use automation when available. Otherwise show these exact checks and require explicit
manual attestation for each starter. A listed starter that cannot be opened is a
failure, not a partial pass.

Persist readiness independently:

```text
python scripts/setup_state.py set-product-readiness \
  --product "{da.esshr|da.essit|cea.esshr|cea.essit}" --ready
```

Record:

- `SETUP-READINESS-001` — Configure/Topics reachable;
- `SETUP-READINESS-002` — shell and starter content footprint present;
- `SETUP-READINESS-003` — persisted starter matrix matches observed state.

Complete only after every selected starter passes:

```text
python scripts/setup_state.py update-step --step SETUP-06 --status done
```
