# Step 1b: Discover Agent

Every **Message** block is the exact text to show the user. Copy it verbatim.
Do not rephrase, add commentary, or tell the user what tools you are calling.

Reload the saved environment selection:

```
python scripts/onboarding_state.py show
```

Find the line starting with `ONBOARDING_STATE_JSON:` and parse the JSON after
the colon. Save `environmentUrl` as ENV_URL.

If `environmentUrl` is missing, read `src/skills/onboarding/step1.md` and
follow it from section 0.9. Do not ask for an environment here.

---

## 1.4 — Run the discovery script

**Message (do NOT wait for user response — continue immediately):**

Looking for ESS agents in your environment — this takes a few
seconds...

**End message.**

Run this command in the terminal (substitute ENV_URL):

```
python scripts/discover.py --url "{ENV_URL}"
```

A browser window will open for sign-in. Wait for the script to finish.

**Check the terminal output:**

- **Script printed a table of agents → go to step 1.5.**
- **Script printed "No agents found" and no installation completed during this
  setup run → go to step 1.8.**
- **Script printed "No agents found" after installation completed during this
  setup run → go to step 1.8b.**
- **Script failed with an auth/connection error → go to step 1.9.**

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

Use the `vscode_askQuestions` tool and wait for the user's response:

```json
[
  {
    "header": "Select experience",
    "question": "Which Employee Self-Service experience do you want to install?",
    "options": [
      {
        "label": "ESS as DA (Recommended)",
        "description": "Install the declarative agent experience."
      },
      {
        "label": "ESS as CEA",
        "description": "Install the custom engine agent experience."
      }
    ],
    "allowFreeformInput": false
  }
]
```

Map **ESS as DA (Recommended)** to EXPERIENCE=`da` and
**ESS as CEA** to EXPERIENCE=`cea`.

After the user answers, use the `vscode_askQuestions` tool and wait for the
second response:

```json
[
  {
    "header": "Select vertical",
    "question": "Which Employee Self-Service vertical do you want to install?",
    "options": [
      {
        "label": "Employee Self-Service HR",
        "description": "Install the Human Resources agent."
      },
      {
        "label": "Employee Self-Service IT",
        "description": "Install the Information Technology agent."
      }
    ],
    "allowFreeformInput": false
  }
]
```

Map **Employee Self-Service HR** to VERTICAL=`hr` and
**Employee Self-Service IT** to VERTICAL=`it`.

The installer resolves the selected application's schema name from
`src/reference/solution-catalog.md`. Do not hard-code a schema name in the
onboarding instructions.

**Message (do NOT wait for user response — continue immediately):**

Installing the selected Employee Self-Service agent in your environment.
A browser window may open for Power Platform administrator sign-in.
Installation can take several minutes...

**End message.**

Run this command in the terminal:

```
python scripts/install_ess_agent.py --url "{ENV_URL}" --experience {EXPERIENCE} --vertical {VERTICAL}
```

- If the command prints `INSTALLED_ESS_AGENT_JSON:`, continue immediately.
- If the command fails, go to step 1.8a.

**Message (do NOT wait for user response — continue immediately):**

✅ Employee Self-Service installation completed. Discovering the new agent...

**End message.**

Return to step 1.4 and run discovery again. Remember that installation
completed during this setup run so a delayed agent registration does not
trigger another installation.

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
