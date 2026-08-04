<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->
# Setup Step 2 — Prerequisites

Mark the step in progress:

```text
python scripts/setup_state.py update-step --step SETUP-02 --status in-progress
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
must pass. Record both checks in automated mode:

- `SETUP-PREREQ-ACCESS-001` — selected environment ID, name, and URL match the
  discovered environment;
- `SETUP-PREREQ-DV-001` — `ENV-002` confirms Dataverse is provisioned.

Continue immediately when both checks pass. Do not ask whether the maker can
open the environment in Power Platform or Copilot Studio. If discovery or
`ENV-002` fails, show the exact command error and block the prerequisite step;
do not replace failed automated evidence with manual attestation.

## Dataverse MCP client

Check the documented Allowed MCP Client record for Microsoft GitHub Copilot:

```text
python scripts/check_dataverse_mcp.py --url "{ENVIRONMENT_URL}"
```

Parse `DATAVERSE_MCP_STATUS_JSON:`:

- `enabled`: record `SETUP-PREREQ-MCP-001` as `pass` with the application ID,
  record ID, and active/enabled flags as automated evidence. Continue
  immediately without asking the maker anything.
- `disabled` or `missing`: record `SETUP-PREREQ-MCP-001` as `fail`, show the
  following guidance, then offer **Check again**:

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
`prepaid`. Record `SETUP-PREREQ-CAP-001` with the selected model in evidence.
The capacity checkpoint may use manual-attested fallback.

## Governance

Ask for explicit status of:

- DLP allowlisting;
- firewall/outbound allowlisting required for planned integrations;
- organization approvals.

Record `SETUP-PREREQ-GOV-001`. A required item that is pending is a failure.

## Blocking guard

If any mandatory prerequisite failed or remains unknown:

1. Record `SETUP-PREREQ-BLOCK-001` as `fail`.
2. Set `SETUP-02` to `blocked` with one normalized cause per missing item.
3. Show the missing items and stop.

If all five prerequisite checks pass, record `SETUP-PREREQ-BLOCK-001` as `pass`
with evidence that no mandatory item remains, then complete the step:

```text
python scripts/setup_state.py update-step --step SETUP-02 --status done
```
