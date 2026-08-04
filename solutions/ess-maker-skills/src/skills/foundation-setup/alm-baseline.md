<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->
# Setup Step 4 — ALM Baseline

Mark the step in progress:

```text
python scripts/setup_state.py update-step --step SETUP-04 --status in-progress
```

Run the existing preferred-solution check:

```text
python scripts/flightcheck/cli.py --checkpoint ENV-009
```

If no suitable unmanaged solution is selected, guide the maker to Power Apps:

1. Open **Solutions** in the locked environment.
2. Create an unmanaged solution with `display name`, `name/schema`, `publisher`,
   and `version`, or select an existing suitable solution.
3. Use a custom publisher rather than the environment Default Publisher.
4. Choose **Set preferred solution**.
5. Re-run `ENV-009`.

Capture solution id, solution name/schema, publisher prefix, and version:

```text
python scripts/setup_state.py set-alm \
  --solution-id "{SOLUTION_ID}" \
  --solution-name "{SOLUTION_NAME}" \
  --publisher-prefix "{PUBLISHER_PREFIX}" \
  --version "{VERSION}"
```

Record:

- `SETUP-ALM-001` — unmanaged solution exists;
- `SETUP-ALM-002` — preferred solution id equals the target solution id;
- `SETUP-ALM-003` — required metadata is stored.

Use automated mode for a passing `ENV-009`. If the API is unavailable, manual
attestation must include the solution id and a timestamp. Complete only after all
three checks pass:

```text
python scripts/setup_state.py update-step --step SETUP-04 --status done
```
