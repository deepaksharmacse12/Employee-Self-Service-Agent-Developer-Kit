<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->
# Setup Steps 2.1 and 2.2 — Prerequisites

Mark the access and Dataverse substep in progress:

```text
python scripts/setup_state.py update-step --step SETUP-02.1 --status in-progress
```

Read the locked environment from `python scripts/setup_state.py show`.

## Access and Dataverse

Run:

```text
python scripts/discover.py --list-environments
python scripts/flightcheck/cli.py \
  --checkpoint ENV-002 \
  --environment-url "{ENVIRONMENT_URL}" \
  --environment-id "{ENVIRONMENT_ID}"
```

The selected environment must be present in environment discovery and `ENV-002`
must pass.

Continue immediately when both checks pass. Do not ask whether the maker can
open the environment in Power Platform or Copilot Studio. If discovery or
`ENV-002` fails, show the exact command error and block the prerequisite step;
do not replace a failed automated result with manual attestation.

Persist and complete the first substep:

```text
python scripts/setup_state.py record-step-result \
  --step SETUP-02.1 \
  --checkpoint ENV-002 \
  --mode automated
python scripts/setup_state.py update-step --step SETUP-02.1 --status done
python scripts/setup_state.py update-step --step SETUP-02.2 --status in-progress
```

## Dataverse MCP client

Check the documented Allowed MCP Client record for Microsoft GitHub Copilot:

```text
python scripts/check_dataverse_mcp.py --url "{ENVIRONMENT_URL}"
```

Parse `DATAVERSE_MCP_STATUS_JSON:`:

- `enabled`: continue immediately without asking the maker anything.
- `disabled` or `missing`: show the following guidance, then offer **Check
  again**:

  1. Open [Power Platform admin center](https://admin.powerplatform.microsoft.com/environments).
  2. Select the `{ENVIRONMENT_NAME}` environment.
  3. Open `Settings` → `Product` → `Features`.
  4. Turn on **Allow MCP clients to interact with Dataverse MCP server**.
  5. Open `Advanced Settings`.
  6. Open **Microsoft GitHub Copilot** and set `Is Enabled` to `Yes`.
  7. Choose `Save & Close`, then select **Check again** here.

  Rerun the command when selected.
- command failure: show the exact error and stop. Do not replace an unavailable
  API result with manual attestation.

The setup must not ask whether MCP is already enabled. Dataverse is the source
of truth.

## Capacity and billing

Run:

```text
python scripts/flightcheck/cli.py \
  --checkpoint ENV-CAPACITY-001 \
  --environment-url "{ENVIRONMENT_URL}" \
  --environment-id "{ENVIRONMENT_ID}"
```

Ask the maker to select exactly one approved model: `licensed users`, `PayG`, or
`prepaid`. Persist the selected model with `set-prerequisite`.
The capacity checkpoint may use manual-attested fallback.

## Governance

Ask for explicit status of:

- DLP allowlisting;
- firewall/outbound allowlisting required for planned integrations;
- organization approvals.

A required item that is pending is a failure. Persist each answer with
`set-prerequisite`.

## Blocking guard

If any mandatory prerequisite failed or remains unknown:

1. Set `SETUP-02.2` to `blocked` with one normalized cause per missing item.
2. Show the missing items and stop.

If all five prerequisite checks pass, persist one consolidated step result:

```text
python scripts/setup_state.py record-step-result \
  --step SETUP-02.2 \
  --checkpoint ENV-CAPACITY-001 \
  --mode manual-attested
```

Then complete the step:

```text
python scripts/setup_state.py update-step --step SETUP-02.2 --status done
```
