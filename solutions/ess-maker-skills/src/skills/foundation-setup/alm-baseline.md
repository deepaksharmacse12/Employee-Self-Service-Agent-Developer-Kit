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

If `solutions` is empty, show:

1. Open [Power Apps](https://make.powerapps.com).
2. Select the `{ENVIRONMENT_NAME}` environment.
3. Open `Solutions`.
4. Choose `New solution`.
5. Enter a `Display name`, `Name`, and `Version`.
6. Select or create a custom `Publisher`.
7. Choose `Create`, then select **Check again** here.

This is the only manual creation path.

After the maker selects a solution, configure it:

```text
python scripts/preferred_solution.py \
  --url "{ENVIRONMENT_URL}" \
  select --solution-id "{SOLUTION_ID}"
```

The command must not trust the picker label. After the maker answers, it
rediscovers the selected solution and reads `GetPreferredSolution()`. If the
selected solution is not preferred, it invokes `SetPreferredSolution`. It then
always reads `GetPreferredSolution()` again and continues only when the selected
ID is retained. This final verification is mandatory even when the solution was
already marked **(Current preferred)**. It then persists the solution ID, unique
name, publisher prefix, and version to setup state.

Parse `PREFERRED_SOLUTION_JSON:` and show a concise confirmation. Do not ask for
another confirmation.

If Dataverse rejects the cached session with HTTP 401, the command clears only
the local token cache, prompts for sign-in, and retries once. If the retry also
fails, show the final error once and keep `SETUP-04` in progress. Do not repeat
the command error in a second setup message.

Record:

- `SETUP-ALM-001` — unmanaged solution exists;
- `SETUP-ALM-002` — preferred solution id equals the target solution id;
- `SETUP-ALM-003` — required metadata is stored.

Record all three checks in automated mode from the verified selection result.
Complete only after all three checks pass:

```text
python scripts/setup_state.py update-step --step SETUP-04 --status done
```
