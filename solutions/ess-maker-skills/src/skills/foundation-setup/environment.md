<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->
# Setup Step 3 — Environment Binding

Mark the step in progress and read the locked environment:

```text
python scripts/setup_state.py update-step --step SETUP-03 --status in-progress
python scripts/setup_state.py show
```

Treat the locked scope as the maker's selected target. Do not ask whether it is
still intended and do not show a confirmation popup. Verify its identity
automatically below. If observed environment identity differs from the locked
scope, block with cause `ENVIRONMENT_DRIFT`; do not silently rewrite the scope.

Run:

```text
python scripts/flightcheck/cli.py \
  --checkpoint ENV-001 \
  --environment-url "{ENVIRONMENT_URL}" \
  --environment-id "{ENVIRONMENT_ID}"
python scripts/flightcheck/cli.py \
  --checkpoint ENV-002 \
  --environment-url "{ENVIRONMENT_URL}" \
  --environment-id "{ENVIRONMENT_ID}"
```

Record:

- `SETUP-ENV-001` when FlightCheck resolves the explicitly supplied locked
  environment ID and URL;
- `SETUP-ENV-002` when environment id, name, tenant endpoint, type, and timestamp
  are present;
- `SETUP-ENV-003` when both prior checks pass and the next step may be unlocked.

Do not read `.local/config.json` or invoke environment selection during this
step; local workspace configuration is created later. No manual pass is allowed
for context drift. Dataverse verification may use the existing documented
manual fallback only when the API is unavailable.

Complete only after all three checks pass:

```text
python scripts/setup_state.py update-step --step SETUP-03 --status done
```
