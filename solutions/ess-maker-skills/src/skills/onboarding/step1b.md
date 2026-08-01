# Step 1b: Discover Agent

Every **Message** block is the exact text to show the user. Copy it verbatim.
Do not rephrase, add commentary, or tell the user what tools you are calling.

Reload the saved environment selection:

```
python scripts/onboarding_state.py show
```

Find the line starting with `ONBOARDING_STATE_JSON:` and parse the JSON after
the colon. Save `environmentUrl` as ENV_URL and inspect the optional
`installation` object.

If `environmentUrl` is missing, read `src/skills/onboarding/step1.md` and
follow it from section 0.9. Do not ask for an environment here.

- If `installation.vertical` is `it` but `installation.connectionName` is
  missing, load EXPERIENCE and VERTICAL and go to section 1.8f regardless of
  installation status. This reconciles durable IT installations created by
  older setup versions before connection selection was stored. The installer
  will detect an already-installed or in-progress package without reinstalling
  it and then persist the selected connection.
- If `installation.status` is `installing`, load EXPERIENCE and VERTICAL from
  the object, load its optional `connectionName` as CONNECTION_NAME, and go
  directly to section 1.8d. This state exists only after Power Platform
  accepted or reported an in-progress installation.
- If `installation.status` is `manual-required`, load EXPERIENCE and VERTICAL
  from the object and go directly to section 1.8c.
- If `installation.status` is `automatic-complete` or `verified`, load
  EXPERIENCE and VERTICAL and go directly to section 1.8g.

---

## 1.4 — Run the discovery script

**Message (do NOT wait for user response — continue immediately):**

Looking for ESS agents in your environment — this takes a few
seconds...

**End message.**

Run this command in the terminal (substitute ENV_URL):

```
python scripts/discover.py --url "{ENV_URL}" --sync-config
```

A browser window will open for sign-in. Wait for the script to finish.

**Check the terminal output:**

- Find `ESS_AGENT_DISCOVERY_JSON:` and parse the JSON after the colon as
  DISCOVERY. It contains `agents`, `installedInstallationKeys`, and
  `availableInstallations`.
- The command also synchronizes every detected supported agent into
  `.local/config.json`. Installed but unselected agents have
  `installation.status` set to `installed` and `extraction.status` set to
  `not-started`.
- **DISCOVERY contains agents and available installations → go to step 1.4a.**
- **DISCOVERY contains agents and no available installations → go to step
  1.5.**
- **Script printed "No supported ESS agents found" and no installation
  completed during this
  setup run → go to step 1.8.**
- **Script printed "No supported ESS agents found" after installation completed
  during this setup run → go to step 1.8b.**
- **Script failed with an auth/connection error → go to step 1.9.**

---

## 1.4a — Offer another supported ESS agent

Build INSTALLED_AGENT_NAMES from `DISCOVERY.agents[*].name`.

**Message:**

This environment already has these supported ESS agents:

{INSTALLED_AGENT_NAMES as a Markdown bullet list}

You can install another supported agent before choosing which one to customize.

**End message.**

Use `vscode_askQuestions`. Build one option for every entry in
`DISCOVERY.availableInstallations`, preserving its order and using its `label`
and `description`. Append the continue option last:

```json
[
  {
    "header": "Install another agent",
    "question": "Would you like to install another ESS agent?",
    "options": [
      {
        "label": "{available installation label}",
        "description": "{available installation description}"
      },
      {
        "label": "Continue with installed agents",
        "description": "Choose an existing agent to customize."
      }
    ],
    "allowFreeformInput": false
  }
]
```

- If the user selects **Continue with installed agents**, go to step 1.5.
- Otherwise, map the selected option to its `experience` and `vertical` values,
  save them as EXPERIENCE and VERTICAL, and go to step 1.8f. Every installation
  route, including installing another agent, must pass connection preflight
  before installation state is persisted or the installer is started.

---

## 1.5 — Ask the user to pick an agent

Build options from the discovery script's agent table. Each row becomes an
option with the agent name as the label and any extra details (schema name,
managed/unmanaged) as the description.

Use the `vscode_askQuestions` tool:

```json
[
  {
    "header": "Select agent",
    "question": "Which agent do you want to customize?",
    "options": [
      { "label": "{agent 1 name}", "description": "{schema name, managed/unmanaged}" },
      { "label": "{agent 2 name}", "description": "{schema name, managed/unmanaged}" }
    ],
    "allowFreeformInput": false
  }
]
```

Map the selected agent name back to its row number from the discovery output.

---

## 1.6 — Confirm selection

Run the selection command in the terminal:

```
python scripts/discover.py --url "{ENV_URL}" --select {NUMBER}
```

Find the line starting with `SELECTED_AGENT_JSON:` in the output. Parse the
JSON after the colon to get BOT_ID (`botid`), BOT_NAME (`name`),
SCHEMA_NAME (`schemaname`), and IS_MANAGED (`ismanaged`).

Persist the selected agent:

```
python scripts/onboarding_state.py save-agent --url "{ENV_URL}" --bot-id "{BOT_ID}" --name "{BOT_NAME}" --schema "{SCHEMA_NAME}" {--managed if IS_MANAGED is true}
```

Update `workspace/onboarding/tasks.md` — change both step 1 and step 2 from
`- [ ]` to `- [x]`.

**Message:**

✅ Selected **{BOT_NAME}**.

| # | Task | Status |
|---|------|--------|
| 1 | Dataverse configured | ✅ |
| 2 | Agent discovered | ✅ |
| 3 | Agent extracted | ⬜ |
| 4 | MCP server started | ⬜ |
| 5 | Readiness check (optional) | ⬜ |

Extracting your agent now. This takes a few seconds...

**End message.**

Now read `src/skills/onboarding/step2.md` and follow it.

---

## 1.8 — No agents found

Use `DISCOVERY.availableInstallations` from step 1.4. It contains all four
supported agents on a fresh environment, already ordered with both DA options
first and both CEA options last. Use `vscode_askQuestions` and wait for the
user's response. The question should be equivalent to:

```json
[
  {
    "header": "Install ESS agent",
    "question": "Which Employee Self-Service agent do you want to install?",
    "options": [
      {
        "label": "DA : Employee Self-Service HR",
        "description": "Install the declarative Human Resources agent."
      },
      {
        "label": "DA : Employee Self-Service IT",
        "description": "Install the declarative Information Technology agent."
      },
      {
        "label": "CEA : Employee Self-Service HR",
        "description": "Install the custom engine Human Resources agent."
      },
      {
        "label": "CEA : Employee Self-Service IT",
        "description": "Install the custom engine Information Technology agent."
      }
    ],
    "allowFreeformInput": false
  }
]
```

Map the selected option to its `experience` and `vertical` values and save them
as EXPERIENCE and VERTICAL.

The installer resolves the composite `{EXPERIENCE}.{VERTICAL}` key from
`src/reference/ess-agent-installation/config.json` and validates its application
and solution unique names against `src/reference/solution-catalog.md`. Do not
hard-code a schema name in the onboarding instructions.

First continue to section 1.8f. Do not persist the selected agent or preflight
result.

### 1.8f — Validate required connections before installation

**Message (do NOT wait for user response — continue immediately):**

Checking required connections for the selected Employee Self-Service agent...

**End message.**

Run:

```
python scripts/ess_connection_binding.py inspect --url "{ENV_URL}" --experience {EXPERIENCE} --vertical {VERTICAL}
```

Parse the JSON after `ESS_CONNECTION_PREFLIGHT_JSON:`.

- If `status` is `not-required`, continue directly to section 1.8d.

- If `status` is `ready`, save
  `selectedConnection.name` as the in-memory CONNECTION_NAME and continue
  directly to section 1.8d. Do not write it to onboarding state yet.

  **Message (do NOT wait for user response — continue immediately):**

  ✅ Found an active **{displayName}** connection. Installation can proceed.

  **End message.**

- If `status` is `selection-required`, use `vscode_askQuestions` to show one
  option for every item in `connections`. Build a unique label from
  `displayName` plus `accountName` (or `name` when no account is available),
  and include the stable connection `name` in the description. Do not
  auto-select among multiple connections. Save the selected item's `name` as
  the in-memory CONNECTION_NAME and continue directly to section 1.8d. Do not
  write it to onboarding state yet.

- If `status` is `missing`:
  **Message:**

  The selected agent requires an active **{displayName}** connection before it
  can be installed.

  {creationGuidance}

  Create the connection in this environment, then choose **Check again**.

  **End message.**

  Ask with `vscode_askQuestions`:

  - **Check again** — rerun section 1.8f.
  - **Not yet** — stop without saving the agent selection or connection
    preflight. The next `/setup` run must rediscover agents and ask what to
    install.

Do not run the installer unless this preflight returns `not-required`, `ready`,
or the user explicitly selects one of multiple connected matches. The installer
owns installation-state persistence and writes it only after the Power Platform
API confirms that installation has started or is already complete.

### 1.8d — Run or resume automatic installation

**Message (do NOT wait for user response — continue immediately):**

Installing the selected Employee Self-Service agent in your environment.
A browser window may open for Power Platform administrator sign-in.
I'll check the installation status every 20 seconds for up to 10 minutes...

**End message.**

Run this command in the terminal:

```
python scripts/install_ess_agent.py --url "{ENV_URL}" --experience {EXPERIENCE} --vertical {VERTICAL}
```

When connection preflight selected CONNECTION_NAME, append:

```
--connection-name "{CONNECTION_NAME}"
```

The installer independently repeats the required-connection validation and
will refuse to start the package installation if the preflight was bypassed,
the connection became unhealthy, or multiple matches exist without an explicit
selection.

- If the command prints `INSTALLED_ESS_AGENT_JSON:`, continue immediately.
- If the command prints `ESS_AGENT_INSTALLATION_TIMEOUT_JSON:`, persist the
  timeout state emitted by the installer and go to step 1.8c.

- If the command fails, go to step 1.8a.

**Message (do NOT wait for user response — continue immediately):**

✅ Employee Self-Service installation completed. Binding its required
connection...

**End message.**

Continue to section 1.8g. Do not run discovery until required connection
binding is verified.

### 1.8g — Bind and verify the required connection

Reload `ONBOARDING_STATE_JSON` and read
`installation.connectionName` as CONNECTION_NAME when present.

For an agent with a selected connection, run:

```
python scripts/ess_connection_binding.py bind --url "{ENV_URL}" --experience {EXPERIENCE} --vertical {VERTICAL} --connection-name "{CONNECTION_NAME}"
```

When `installation.connectionName` is absent, run the same command without
`--connection-name`; the selected agent does not require a parent connection.

Continue only when the command prints `ESS_CONNECTION_BINDING_JSON:` with
`status` equal to `bound` or `not-required`. The script updates the nested
agent state in `.local/config.json`:

- `setupStatus.S1` — package installed and verified.
- `setupStatus.S2` — required connection bound and reread from Dataverse, or
  explicitly marked not required.

Persist the resumable binding state:

```
python scripts/onboarding_state.py save-connection --url "{ENV_URL}" --experience {EXPERIENCE} --vertical {VERTICAL} --status bound --connection-name "{CONNECTION_NAME}"
```

For `not-required`, use `--status not-required` and omit `--connection-name`.
If binding fails, show the exact error and stop. Do not mark S2 done and do not
continue to discovery.

Return to step 1.4 and run discovery again after binding succeeds. Remember
that installation
completed during this setup run so a delayed agent registration does not
trigger another installation. Once the new agent appears, step 1.4a offers
only the other supported agents that are still missing.

### 1.8c — Automatic installation timed out

Read `src/reference/ess-agent-installation/config.json`. Resolve the
`{EXPERIENCE}.{VERTICAL}` installation and use its experience label, vertical
label, and `marketplaceApplication.uniqueName` in the message below.

**Message:**

Automatic installation is still not complete after 10 minutes.

Please install **{EXPERIENCE_LABEL} — {VERTICAL_LABEL}** manually:

1. Open Power Platform admin center and select this environment.
2. Go to **Resources → Dynamics 365 apps**.
3. Find and install the application with unique name
   **{MARKETPLACE_APPLICATION_UNIQUE_NAME}**.

When the installation finishes, choose **Verify installation** and I'll confirm
it with the ESS solution FlightCheck.

**End message.**

Use the `vscode_askQuestions` tool and wait for the user's response:

```json
[
  {
    "header": "Manual installation",
    "question": "Is the selected ESS application installed?",
    "options": [
      {
        "label": "Verify installation",
        "description": "Run the ESS solution FlightCheck now."
      },
      {
        "label": "Not yet",
        "description": "Keep this step pending so setup can resume later."
      }
    ],
    "allowFreeformInput": false
  }
]
```

- If the user selects **Not yet**, stop. Keep `installation.status` as
  `manual-required`.
- If the user selects **Verify installation**, run:

  ```
  python scripts/flightcheck/cli.py --checkpoint ESS-SOLN-001 --environment-url "{ENV_URL}" --expected-solution "{MARKETPLACE_APPLICATION_UNIQUE_NAME}" --output workspace/flightcheck/installation-verification --no-open --invocation-source installer
  ```

Read `workspace/flightcheck/installation-verification/results.json`. Continue
only when the `ESS-SOLN-001` row has status `Passed`. Do not treat the command's
exit code alone as confirmation.

When the checkpoint passes, persist the verification:

```
python scripts/onboarding_state.py save-installation --url "{ENV_URL}" --experience {EXPERIENCE} --vertical {VERTICAL} --status verified
```

**Message (do NOT wait for user response — continue immediately):**

✅ Employee Self-Service installation verified. Binding its required
connection...

**End message.**

Continue to section 1.8g. If the checkpoint does not pass,
show its result and offer the same **Verify installation** and **Not yet**
choices again. Do not rerun automatic installation.

### 1.8a — Installation failed

**Message:**

The Employee Self-Service agent could not be installed automatically. Review
the terminal error, confirm your account is a Power Platform or Dynamics 365
administrator who can install applications in this environment, and type
**retry** to try again.

**End message.**

Wait for the user. When they say retry, rerun the installation command with
the same ENV_URL, EXPERIENCE, and VERTICAL. Do not repeat either selection
question.

### 1.8b — Installed agent is not discoverable yet

**Message:**

The installation completed, but the new agent is not discoverable yet.
Provisioning can take a few minutes. Type **retry** to check again without
reinstalling the package.

**End message.**

Wait for the user. When they say retry, return to step 1.4. Do not rerun the
installation command or repeat either selection question.

---

## 1.9 — Script failed

**Message:**

The discovery script couldn't connect. Let's troubleshoot:

1. Check that the environment URL is correct
   (`https://yourorg.crm.dynamics.com`, not `.api.` or `make.powerapps.com`).
2. Confirm the admin steps: MCP feature flag **ON** in Power Platform admin
   center, and **Microsoft GitHub Copilot** client **enabled** in Advanced
   Settings.
3. Make sure your account has read access to the environment.

Type **retry** when ready, or run `/setup` again after fixing.

**End message.**

Wait for the user. When they say retry, go back to step 1.4 and re-run the
discovery script.
