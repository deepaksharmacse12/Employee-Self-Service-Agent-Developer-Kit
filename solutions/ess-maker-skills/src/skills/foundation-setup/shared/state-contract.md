<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->
# Setup State Contract

`scripts/setup_state.py` is the only writer for `.local/setup/config.json`.

## Responsibilities

- `SetupState` owns the versioned domain data.
- `SetupWorkflow` enforces transitions, deterministic resume, and readiness.
- `JsonSetupStateRepository` owns atomic JSON persistence.
- `LegacyWorkdayStateMigrator` imports only common environment/base-install progress.
- `SetupStateService` coordinates repository and migration behavior.
- `ProductInstallationRecord` owns one product's installation, connection, and
  readiness outcome.

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

## Product installation state

`selected_products` contains one or more catalog IDs:

```text
da.esshr
da.essit
cea.esshr
cea.essit
```

`products` always contains an independent record for all four IDs. Selected
products transition through `pending`, `connection-required`, `ready`,
`installing`, `manual-required`, `installed`, `bound`, or `failed`. Unselected
products remain `not-selected`.

Installation and binding commands may update only their own product record.
Successful products must remain durable when another product is blocked or
fails. Readiness can pass only after that product reaches `bound`; `bound`
also covers products whose catalog declares that no connection is required.
