<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->
# Setup Step 1 — Scope

Use `vscode_askQuestions` to collect one target environment and one starter scope.

1. Run `python scripts/discover.py --list-environments`.
2. Present the returned environments with name, URL, and platform type.
3. Ask the maker to classify the selected target as Dev, Test, or Prod.
4. Ask which starter to install: HR, IT, or both.
5. Confirm that this run covers prerequisites and base ESS installation only.

Do not offer DA/CA terminology. The product documentation defines HR and IT as the
current starter taxonomy.

Persist the locked scope:

```text
python scripts/setup_state.py set-scope \
  --environment-id "{ENVIRONMENT_ID}" \
  --environment-name "{ENVIRONMENT_NAME}" \
  --environment-type "{Dev|Test|Prod}" \
  --tenant-endpoint "{ENVIRONMENT_URL}" \
  --starter-scope "{HR|IT|both}"
```

The command records `SETUP-SCOPE-001`, `SETUP-SCOPE-002`, and
`SETUP-SCOPE-003`, then completes the step atomically.

**Message:**

Setup is locked to **{environment name}** ({environment type}) for the
**{starter scope}** starter scope. Integration setup remains out of scope until
this foundation is complete.

**End message.**
