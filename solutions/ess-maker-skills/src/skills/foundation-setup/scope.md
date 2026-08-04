<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->
# Setup Step 1 — Scope

Use `vscode_askQuestions` to collect one target environment and one or more ESS
products.

1. Run `python scripts/discover.py --list-environments`.
2. Present the returned environments with name, URL, and platform type.
3. Ask the maker to classify the selected target as Dev, Test, or Prod.
4. Present these four products in this order:
   - **DA: Employee Self-Service HR (Recommended)**
   - **DA: Employee Self-Service IT (Recommended)**
   - **CEA: Employee Self-Service HR**
   - **CEA: Employee Self-Service IT**
5. Let the maker select one or more products. Persist the catalog IDs exactly as
   `da.esshr`, `da.essit`, `cea.esshr`, and `cea.essit`.

Do not collapse the choices into HR, IT, or both. DA and CEA are separate
installable products with independent lifecycle state.

Persist the locked scope:

```text
python scripts/setup_state.py set-scope \
  --environment-id "{ENVIRONMENT_ID}" \
  --environment-name "{ENVIRONMENT_NAME}" \
  --environment-type "{Dev|Test|Prod}" \
  --tenant-endpoint "{ENVIRONMENT_URL}" \
  --product "{PRODUCT_ID}" [--product "{ANOTHER_PRODUCT_ID}" ...]
```

The command records `SETUP-SCOPE-001`, `SETUP-SCOPE-002`, and
`SETUP-SCOPE-003`, then completes the step atomically.

**Message:**

Setup is locked to **{environment name}** ({environment type}) for the
**{selected product labels}**.

**End message.**
