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
`not-required`. The command rereads Dataverse after binding and persists the
product as `bound`. On failure, keep the product-specific error and stop that
product without changing successful products.

Record:

- `SETUP-INSTALL-001` — every selected starter appears;
- `SETUP-INSTALL-002` — every selected starter opens;
- `SETUP-INSTALL-003` — every selected product is independently `bound`.

Automated verification may be supplemented by manual-attested starter-specific
evidence because `ESS-SOLN-001` covers the solution family rather than uniquely
identifying both starter experiences.

Complete only after all selected starters pass:

```text
python scripts/setup_state.py update-step --step SETUP-05 --status done
```
