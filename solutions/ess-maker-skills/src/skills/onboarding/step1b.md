# Step 1b: Discover Agent

Every **Message** block is the exact text to show the user. Copy it verbatim.
Do not rephrase, add commentary, or tell the user what tools you are calling.

You should already have ENV_URL from Step 1.

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

- Parse the JSON after `ESS_AGENT_DISCOVERY_JSON:` as DISCOVERY.
- Run `python scripts/setup_state.py show` and remove any entry from
  `DISCOVERY.availableInstallations` whose `configKey` is already present in
  `selected_products`. This prevents a package that is installed but still
  provisioning its bot from being offered for installation again.
- **DISCOVERY has agents and availableInstallations → go to step 1.4a.**
- **DISCOVERY has agents and no availableInstallations → go to step 1.5.**
- **DISCOVERY has no agents → go to step 1.8.**
- **Script failed with an auth/connection error → go to step 1.9.**

---

## 1.4a — Choose whether to install or customize

Show the installed agent names from `DISCOVERY.agents`.

Use `vscode_askQuestions`. Add one option for each entry in
`DISCOVERY.availableInstallations`, preserving catalog order, followed by the
customization option:

```json
[
  {
    "header": "Next action",
    "question": "Would you like to install another ESS agent or customize one that is already installed?",
    "options": [
      {
        "label": "{available installation label}",
        "description": "{available installation description}"
      },
      {
        "label": "Customize an installed agent",
        "description": "Choose from the ESS agents already in this environment."
      }
    ],
    "allowFreeformInput": false
  }
]
```

- If the maker selects **Customize an installed agent**, continue to step 1.5.
- If the maker selects an installation, take its `configKey` as PRODUCT_ID and
  run:

  ```text
  python scripts/setup_state.py add-product --product "{PRODUCT_ID}"
  ```

  This reopens only foundation installation, readiness, and handoff while
  preserving completed products and the locked environment. Read
  `src/skills/foundation-setup/SKILL.md` and follow it immediately. Do not ask
  another confirmation. After foundation completes, onboarding runs discovery
  again and offers the remaining products or installed agents.

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

If `DISCOVERY.availableInstallations` is non-empty, ask which available ESS
agent to install using the catalog labels and descriptions. Run
`setup_state.py add-product` with its `configKey`, then follow
`src/skills/foundation-setup/SKILL.md` immediately.

If there are no available installations, explain that installed package
registration has not produced a discoverable agent yet and offer **Check
again**. Rerun step 1.4 when selected; do not reinstall any package.

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
