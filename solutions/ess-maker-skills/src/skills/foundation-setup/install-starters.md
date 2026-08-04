<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->
# Setup Step 5 — Install ESS Starters

Mark the step in progress and read `starter_scope`:

```text
python scripts/setup_state.py update-step --step SETUP-05 --status in-progress
python scripts/setup_state.py show
```

Install in this deterministic order:

1. HR, when scope is HR or both.
2. IT, when scope is IT or both.

For each selected starter:

1. Check whether it is already present.
2. If absent, guide the maker through the managed-agent/AppSource installation in
   the locked environment.
3. Run `python scripts/flightcheck/cli.py --checkpoint ESS-SOLN-001`.
4. Confirm the specific HR or IT starter appears in the agent list.
5. Confirm the maker can open that starter.
6. Persist its independent result:

```text
python scripts/setup_state.py set-starter \
  --starter "{HR|IT}" --installed --no-ready
```

When both is selected, never overwrite the first result while installing the second.
If an installation fails, preserve successful starter state and block with a
starter-specific cause.

Record:

- `SETUP-INSTALL-001` — every selected starter appears;
- `SETUP-INSTALL-002` — every selected starter opens;
- `SETUP-INSTALL-003` — sequential independent outcomes match the selected scope.

Automated verification may be supplemented by manual-attested starter-specific
evidence because `ESS-SOLN-001` covers the solution family rather than uniquely
identifying both starter experiences.

Complete only after all selected starters pass:

```text
python scripts/setup_state.py update-step --step SETUP-05 --status done
```
