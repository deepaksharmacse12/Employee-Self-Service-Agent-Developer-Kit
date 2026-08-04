<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->
# Setup Step 4 — ALM Baseline

Mark the step in progress:

```text
python scripts/setup_state.py update-step --step SETUP-04 --status in-progress
```

Discover eligible unmanaged solutions in the locked environment:

```text
python scripts/preferred_solution.py --url "{ENVIRONMENT_URL}" list
```

Parse `UNMANAGED_SOLUTIONS_JSON:`. Build one `vscode_askQuestions` option per
entry in `solutions`:

- Label: `displayName`, adding **(Current preferred)** when `isPreferred` is
  true.
- Description: unique name, version, publisher name, and publisher prefix.
- Add **Default publisher — not recommended** when `publisherIsDefault` is
  true.

Do not ask the maker to type a solution ID, unique name, publisher prefix, or
version. These values come from Dataverse.

If `solutions` is empty, guide the maker to create an unmanaged solution with a
custom publisher in Power Apps, then offer **Check again**. This is the only
manual creation path.

After the maker selects a solution, configure it:

```text
python scripts/preferred_solution.py \
  --url "{ENVIRONMENT_URL}" \
  select --solution-id "{SOLUTION_ID}"
```

The command detects whether the selected solution is already preferred. If so,
it does not write to Dataverse. Otherwise it invokes `SetPreferredSolution`,
rereads `GetPreferredSolution()`, and continues only when the selected ID is
retained. It then persists the solution ID, unique name, publisher prefix, and
version to setup state.

Parse `PREFERRED_SOLUTION_JSON:` and show a concise confirmation. Do not ask for
another confirmation.

Record:

- `SETUP-ALM-001` — unmanaged solution exists;
- `SETUP-ALM-002` — preferred solution id equals the target solution id;
- `SETUP-ALM-003` — required metadata is stored.

Record all three checks in automated mode from the verified selection result.
Complete only after all three checks pass:

```text
python scripts/setup_state.py update-step --step SETUP-04 --status done
```
