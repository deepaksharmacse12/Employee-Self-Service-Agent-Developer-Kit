<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->
# Setup Step 5 — Install ESS Starters

Mark the step in progress and read `selected_products` and each product's
independent `installation_status`:

```text
python scripts/setup_state.py update-step --step SETUP-05 --status in-progress
python scripts/setup_state.py show
```

Process selected products in catalog order: `da.esshr`, `da.essit`,
`cea.esshr`, `cea.essit`. Resume from each product's persisted status; never
overwrite another product's successful result.

For each product, resolve its `experienceKey`, `verticalKey`, application,
solution, and `requiredConnection` from
`src/reference/ess-agent-installation/config.json`. Do not hard-code package
schema names in this playbook.

For products with `requiredConnection`, run preflight before installation:

```text
python scripts/ess_connection_binding.py inspect \
  --url "{ENVIRONMENT_URL}" \
  --experience "{da|cea}" \
  --vertical "{hr|it}"
```

Parse `ESS_CONNECTION_PREFLIGHT_JSON:`:

- `not-required`: mark the product `ready`.
- `ready`: retain `selectedConnection.name` and mark the product `ready`.
- `selection-required`: ask the maker to select one returned connected
  connection, retain its stable `name`, then mark the product `ready`.
- `missing`: mark the product `connection-required` and show:

  1. Open [Power Apps](https://make.powerapps.com).
  2. Select the `{ENVIRONMENT_NAME}` environment.
  3. Open `Connections`.
  4. Choose `New connection`.
  5. Search for **{displayName}**.
  6. Create the connection, then select **Check again** here.

  Rerun preflight when they choose **Check again**. Do not start installation
  until validation succeeds. Do not show `creationGuidance` as one dense
  sentence.

Persist preflight state independently:

```text
python scripts/setup_state.py set-product-status \
  --product "{PRODUCT_ID}" \
  --status "{connection-required|ready}" \
  [--connection-name "{CONNECTION_NAME}"]
```

Start or resume automatic installation through the solution-catalog schema:

```text
python scripts/install_ess_agent.py \
  --url "{ENVIRONMENT_URL}" \
  --experience "{da|cea}" \
  --vertical "{hr|it}" \
  [--connection-name "{CONNECTION_NAME}"]
```

The installer persists `installing`, `installed`, `manual-required`, or
`failed` only for that product. A failure must preserve every other product's
state. On timeout, follow the emitted manual-install guidance and verify with
`ESS-SOLN-001` before setting the product to `installed`:

```text
python scripts/setup_state.py set-product-status \
  --product "{PRODUCT_ID}" \
  --status installed \
  --schema-name "{MARKETPLACE_APPLICATION_UNIQUE_NAME}"
```

After installation, automatically bind and verify the selected connection:

```text
python scripts/ess_connection_binding.py bind \
  --url "{ENVIRONMENT_URL}" \
  --experience "{da|cea}" \
  --vertical "{hr|it}" \
  [--connection-name "{CONNECTION_NAME}"]
```

Continue only when `ESS_CONNECTION_BINDING_JSON:` reports `bound` or
`not-required`. The command rereads Dataverse after binding.

- For `not-required`, it persists the product as `bound` and continues without
  asking for connection attestation.
- For a bound connection whose catalog `runtimeSource` is not `invoker`, it
  persists the product as `bound` and continues without attestation.
- For a bound `invoker` connection, it persists the product as
  `connection-attestation-required` and returns `agentName`,
  `connectionDisplayName`, and `connectionSettingsUrl`. Show:

  Connection binding is complete. Please verify that the connection is
  available to the installed agent:

  1. Open [connection settings for `{AGENT_NAME}`]({CONNECTION_SETTINGS_URL}).
  2. Confirm the `{ENVIRONMENT_NAME}` environment is selected.
  3. Confirm the `{AGENT_NAME}` agent is open.
  4. In `Settings`, open `Connection settings`.
  5. Locate **{CONNECTION_DISPLAY_NAME}**.
  6. Confirm the connection is connected.
  7. In the `Manage` column, choose `See details`.
  8. Open `Connection parameters`.
  9. If parameters are available, enable sharing for the parameters and choose
     `Save`.

  Ask exactly one question:

  - Header: `Verify connection`
  - Question: `Is **{CONNECTION_DISPLAY_NAME}** connected, with all required connection parameters shared with the \`{AGENT_NAME}\` agent?`
  - Options:
    - `Yes, it is connected and required parameters are shared`
    - `No, it still needs attention`

  When the maker selects
  `Yes, it is connected and required parameters are shared`, persist the
  mandatory manual attestation:

  ```text
  python scripts/setup_state.py attest-product-connection \
    --product "{PRODUCT_ID}"
  ```

  Continue only after the command advances the product to `bound`. If the maker
  selects `No, it still needs attention`, keep the product at
  `connection-attestation-required`, repeat the navigation guidance, and stop
  that product. There is no skip option.

On failure, keep the product-specific error and stop that product without
changing successful products. When resuming a product already at
`connection-attestation-required`, use its persisted agent name and settings
URL to show the same mandatory attestation; do not rerun installation or
binding.

Record:

- `SETUP-INSTALL-001` — every selected starter appears;
- `SETUP-INSTALL-002` — every selected starter opens;
- `SETUP-INSTALL-003` — every selected product is independently `bound`.
- `SETUP-INSTALL-004` — every selected `invoker` product has maker-attested
  connection settings, or no selected product uses an `invoker` connection.

The attestation command records `SETUP-INSTALL-004` in `manual-attested` mode.
If no selected product uses an `invoker` connection, record it in `automated`
mode with evidence `{"attestation_required": false}`.

Automated verification may be supplemented by manual-attested starter-specific
evidence because `ESS-SOLN-001` covers the solution family rather than uniquely
identifying both starter experiences.

Complete only after all selected starters pass:

```text
python scripts/setup_state.py update-step --step SETUP-05 --status done
```
