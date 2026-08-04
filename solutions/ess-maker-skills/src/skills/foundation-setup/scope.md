<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->
# Setup Step 1 — Scope

Use `vscode_askQuestions` to collect one target environment and one ESS product.

1. First ask how the maker wants to provide the environment:

   ```json
   [
     {
       "header": "Environment setup",
       "question": "How would you like to choose your Power Platform environment?",
       "options": [
         {
           "label": "Yes, list my environments",
           "description": "Sign in and browse available environments"
         },
         {
           "label": "No, I'll enter the URL manually",
           "description": "I already know my environment URL"
         },
         {
           "label": "Create a new environment",
           "description": "Create an environment with Dataverse in the Power Platform admin center"
         }
       ],
       "allowFreeformInput": false
     }
   ]
   ```

2. If the maker chooses to list environments:
   - Run `python scripts/discover.py --list-environments`.
   - Present the returned environments with name, URL, and platform type.
   - Ask the maker to select one.
   - Run `python scripts/discover.py --list-environments --select {NUMBER}`.
   - Parse `SELECTED_ENV_JSON:` for the environment ID, name, type, and URL.

3. If the maker chooses manual entry, ask:

   ```json
   [
     {
       "header": "Environment URL",
       "question": "What's your Power Platform environment URL? Example: `https://yourorg.crm.dynamics.com`. Find it in the Power Platform admin center."
     }
   ]
   ```

   Strip the trailing slash from the supplied URL.

4. If the maker chooses to create an environment, explain that a Power Platform
   or Dynamics 365 administrator and at least 1 GB of available database
   capacity are required. Then show:

   1. Open the [Power Platform admin center](https://admin.powerplatform.microsoft.com).
   2. Select `Manage` → `Environments`.
   3. Select `New`.
   4. Enter the environment name, region, type, and purpose.
   5. Set `Add a Dataverse data store` to `Yes`.
   6. Keep the release cycle standard by not enabling early features.
   7. Select `Next`, then choose the language, unique URL, currency, and security
      group.
   8. Select `Save` and wait until provisioning finishes.
   9. In the new environment, open `Settings` → `Users + permissions` →
      `Users`, select the setup user, and assign both **Environment Maker** and
      **System Administrator**.

   Link to the
   [Microsoft environment creation instructions](https://learn.microsoft.com/power-platform/admin/create-environment?tabs=new#create-an-environment-with-a-database).
   After the maker confirms creation is complete, run
   `python scripts/discover.py --list-environments`, present the returned
   environments, ask them to select the newly created environment, and run
   `python scripts/discover.py --list-environments --select {NUMBER}`. Parse
   `SELECTED_ENV_JSON:` for its ID, name, type, and URL. Never assume creation
   succeeded without rediscovering the environment.

5. As soon as an environment URL is selected, entered, or obtained after
   creation, verify the maker's
   role in that environment:

   ```text
   python scripts/check_environment_roles.py --url "{ENVIRONMENT_URL}"
   ```

   Parse `ENVIRONMENT_ROLE_ACCESS_JSON:`:

   - When `eligible` is true, continue immediately without asking for
     confirmation.
   - When `eligible` is false, do not lock the environment. Show:

     Your account needs both the **Environment Maker** and **System
     Administrator** roles in **{environment name}** to use this environment for
     ESS setup. Missing roles: **{missing role names}**. Ask your Power Platform
     administrator to assign the missing roles, or select a different
     environment.

     Then return to environment selection.
   - If the command fails, show the exact error and stop. Do not treat an
     unavailable role result as successful access.

   The check must include roles assigned directly and through team membership.
6. For manual entry, resolve the environment metadata without displaying the
   tenant's environment list:

   ```text
   python scripts/discover.py \
     --resolve-environment-url "{ENVIRONMENT_URL}"
   ```

   Parse `SELECTED_ENV_JSON:` for the environment ID, name, type, and URL. If
   the URL cannot be resolved, show the exact error and stop.
7. Use the environment type returned in `SELECTED_ENV_JSON:` as
   `ENVIRONMENT_PLATFORM_TYPE`. Do not ask the maker to classify the
   environment.
8. Discover the supported ESS agents already installed in the selected
   environment and the remaining catalog installations:

   ```text
   python scripts/discover.py \
     --url "{ENVIRONMENT_URL}" \
     --inventory-only
   ```

   Parse `ESS_AGENT_DISCOVERY_JSON:`. Treat `agents` as installed and
   `availableInstallations` as the only products available to install. Do not
   offer an installed product as an installation option.

   If `agents` is nonempty, show each installed agent distinctly:

   ```text
   Installed: **{agent 1 name}**; **{agent 2 name}**
   ```

   Build the same `vscode_askQuestions` picker used by onboarding:

   - Add one option for each `availableInstallations` entry, preserving catalog
     order and using its `label` and `description`.
   - If `agents` is nonempty, append **Customize an installed agent**.
   - Do not mark any option as recommended in the tool metadata and do not
     preselect an option. The `(Recommended)` text in a catalog label is
     informational only.
   - Allow exactly one selection.

   If the maker selects an available installation, use its `configKey` as
   `PRODUCT_ID`.

   If the maker selects **Customize an installed agent**, ask them to select one
   entry from `agents`. Use its `configKey` as `PRODUCT_ID` and retain its
   `schemaname` as `INSTALLED_SCHEMA_NAME`. This adopts the existing installed
   product into foundation state; it does not reinstall it.

   If both `agents` and `availableInstallations` are empty, show the exact
   discovery result and stop.
9. Persist `PRODUCT_ID`, which must be exactly `da.esshr`, `da.essit`,
   `cea.esshr`, or `cea.essit`.

Do not collapse the choices into HR, IT, or both. DA and CEA are separate
installable products with independent lifecycle state. Install one product per
foundation cycle. After setup completes, onboarding uses `add-product` to offer
one remaining product at a time while preserving the installed product.

Persist the locked scope:

```text
python scripts/setup_state.py set-scope \
  --environment-id "{ENVIRONMENT_ID}" \
  --environment-name "{ENVIRONMENT_NAME}" \
  --environment-type "{ENVIRONMENT_PLATFORM_TYPE}" \
  --tenant-endpoint "{ENVIRONMENT_URL}" \
  --product "{PRODUCT_ID}"
```

The command records `SETUP-SCOPE-001`, `SETUP-SCOPE-002`, and
`SETUP-SCOPE-003`, then completes the step atomically.

If an installed agent was selected for customization, immediately record its
existing installation:

```text
python scripts/setup_state.py set-product-status \
  --product "{PRODUCT_ID}" \
  --status installed \
  --schema-name "{INSTALLED_SCHEMA_NAME}"
```

**Message:**

Setup is locked to **{environment name}** for the **{selected product labels}**.

**End message.**
