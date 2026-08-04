<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->
# Setup Step 3 — Environment Binding

Mark the step in progress and read the locked environment:

```text
python scripts/setup_state.py update-step --step SETUP-03 --status in-progress
python scripts/setup_state.py show
```

Show the environment name, classification, and URL. Ask the maker to confirm this is
still the intended target. If it changed, block with cause `ENVIRONMENT_DRIFT`; do
not silently rewrite the locked scope.

Run:

```text
python scripts/flightcheck/cli.py --checkpoint ENV-001
python scripts/flightcheck/cli.py --checkpoint ENV-002
```

Record:

- `SETUP-ENV-001` when active context handles resolve to the locked environment;
- `SETUP-ENV-002` when environment id, name, tenant endpoint, type, and timestamp
  are present;
- `SETUP-ENV-003` when both prior checks pass and the next step may be unlocked.

No manual pass is allowed for context drift. Dataverse verification may use the
existing documented manual fallback only when the API is unavailable.

Complete only after all three checks pass:

```text
python scripts/setup_state.py update-step --step SETUP-03 --status done
```
