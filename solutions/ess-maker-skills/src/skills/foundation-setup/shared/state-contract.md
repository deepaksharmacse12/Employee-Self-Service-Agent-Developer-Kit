<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->
# Setup State Contract

`scripts/setup_state.py` is the only writer for `.local/setup/config.json`.

## Responsibilities

- `SetupState` owns the versioned domain data.
- `SetupWorkflow` enforces transitions, deterministic resume, and readiness.
- `JsonSetupStateRepository` owns atomic JSON persistence.
- `LegacyWorkdayStateMigrator` imports only common environment/base-install progress.
- `SetupStateService` coordinates repository and migration behavior.

The state intent is fixed to `prereqs + base ESS install only`. Integration data must
never be added to this file.

## Step state

The seven canonical steps support `pending`, `in-progress`, `blocked`, and `done`.
Only one step may be `in-progress`. `active_step` always resolves to the first step
that is not `done`.

## Validation result

Every check is recorded with:

```text
check_id
status: pass | fail
mode: automated | manual-attested
evidence: object
cause_codes: list
recorded_at: UTC timestamp
```

A failed or unknown mandatory check cannot be converted into success-shaped state.
Manual completion requires an explicit positive attestation and evidence.
