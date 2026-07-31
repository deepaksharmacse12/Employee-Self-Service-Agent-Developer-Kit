# Step 1: Connect to Dataverse

Every **Message** block is the exact text to show the user. Copy it verbatim.
Do not rephrase, add commentary, or tell the user what tools you are calling.

---

## 0.9 — Resume a saved environment selection

Run:

```
python scripts/onboarding_state.py show
```

Find the line starting with `ONBOARDING_STATE_JSON:` and parse the JSON after
the colon.

- If it contains `environmentUrl`, save that value as ENV_URL, run
  `python scripts/onboarding_state.py save-environment --url "{ENV_URL}"` to
  persist any value recovered from an existing MCP config, and go directly to
  step 1.2.
- If it does not contain `environmentUrl`, continue to step 1.0.
- If the command fails, show its error and stop. Do not discard unreadable
  onboarding state.

## 1.0 — Ask how to provide the environment

Use the `vscode_askQuestions` tool:

```json
[
  {
    "header": "Environment setup",
    "question": "Would you like me to list all the Power Platform environments in your tenant so you can pick one?",
    "options": [
      { "label": "Yes, list my environments", "description": "Sign in and browse available environments" },
      { "label": "No, I'll enter the URL manually", "description": "I already know my environment URL" }
    ],
    "allowFreeformInput": false
  }
]
```

- If the user chose **"Yes, list my environments"** → go to step 1.1.
- If the user chose **"No, I'll enter the URL manually"** → go to step 1.1c.

---

## 1.1 — List environments and let the user pick

**Message (do NOT wait for user response — continue immediately):**

Let me find the Power Platform environments available in your tenant. A
browser window will open for sign-in...

**End message.**

Run this command in the terminal:

```
python scripts/discover.py --list-environments
```

A browser window will open for sign-in. Wait for the script to finish.

**Check the terminal output:**

- **Script printed a table of environments → go to step 1.1a.**
- **Script failed with an auth/permission error → go to step 1.1c.**

---

## 1.1a — Ask the user to pick an environment

Build options from the script's environment table. Each row becomes an
option with the environment name as the label and the URL + type as
the description.

Use the `vscode_askQuestions` tool:

```json
[
  {
    "header": "Select environment",
    "question": "Which environment is your ESS agent deployed in?",
    "options": [
      { "label": "{env 1 name}", "description": "{URL} [{type}]" },
      { "label": "{env 2 name}", "description": "{URL} [{type}]" }
    ],
    "allowFreeformInput": false
  }
]
```

Map the selected environment name back to its row number from the script
output.

---

## 1.1b — Confirm selection

Run the selection command in the terminal:

```
python scripts/discover.py --list-environments --select {NUMBER}
```

Find the line starting with `SELECTED_ENV_JSON:` in the output. Parse the
JSON after the colon to get the `instanceUrl` field. Save it as ENV_URL.
**Strip any trailing slash** from ENV_URL before using it (e.g.,
`https://org.crm.dynamics.com/` becomes `https://org.crm.dynamics.com`).

Persist the selection:

```
python scripts/onboarding_state.py save-environment --url "{ENV_URL}"
```

Go to step 1.2.

---

## 1.1c — Manual URL entry (fallback)

Use the `vscode_askQuestions` tool:

```json
[
  {
    "header": "Environment URL",
    "question": "What's your Power Platform environment URL? (e.g. https://yourorg.crm.dynamics.com — find it in the Power Platform admin center)"
  }
]
```

Save their answer as ENV_URL. **Strip any trailing
slash** from ENV_URL before using it (e.g., `https://org.crm.dynamics.com/`
becomes `https://org.crm.dynamics.com`).

Persist the selection:

```
python scripts/onboarding_state.py save-environment --url "{ENV_URL}"
```

## 1.2 — Check the environment's MCP configuration

Run this command in the terminal (substitute ENV_URL):

```
python scripts/discover.py --url "{ENV_URL}" --check-mcp-config
```

A browser window may open for sign-in. Wait for the script to finish.

Find the line starting with `MCP_CONFIG_JSON:` and parse the JSON after the
colon.

- If `configured` is `true` → go to step 1.2b.
- If `configured` is `false` → save `serverEnabled` and
  `githubCopilotEnabled`, then go to step 1.2a.
- If the script fails → show its error and stop. Do not assume the admin
  settings are missing and do not continue until the check succeeds.

## 1.2a — Show only the missing admin configuration

If `serverEnabled` is `false` and `githubCopilotEnabled` is `false`, show:

**Message:**

This environment needs a one-time admin setup for the Dataverse connector:

1. Go to [Power Platform admin center](https://admin.powerplatform.microsoft.com/environments)
   → your environment → **Settings** → **Product** → **Features**
2. Turn on **"Allow MCP clients to interact with Dataverse MCP server
   (GA version)"** and click **Save**
3. Click **"Go to Advanced Settings"** → find **"Microsoft GitHub Copilot"**
   → set **Is Enabled** to **Yes** → **Save & Close**

Type **done** when that's set up.

**End message.**

If only `serverEnabled` is `false`, show:

**Message:**

This environment needs one Dataverse connector setting enabled:

1. Go to [Power Platform admin center](https://admin.powerplatform.microsoft.com/environments)
   → your environment → **Settings** → **Product** → **Features**
2. Turn on **"Allow MCP clients to interact with Dataverse MCP server
   (GA version)"** and click **Save**

Type **done** when that's set up.

**End message.**

If only `githubCopilotEnabled` is `false`, show:

**Message:**

This environment needs GitHub Copilot enabled as a Dataverse MCP client:

1. Go to [Power Platform admin center](https://admin.powerplatform.microsoft.com/environments)
   → your environment → **Settings** → **Product** → **Features**
2. Click **"Go to Advanced Settings"** → find **"Microsoft GitHub Copilot"**
   → set **Is Enabled** to **Yes** → **Save & Close**

Type **done** when that's set up.

**End message.**

Wait for the user. When they say `done`, return to step 1.2 and re-run the
check. Do not accept `skip`; the programmatic check determines whether setup
can continue.

## 1.2b — Write the MCP config file

Build the MCP URL by appending `/api/mcp` to ENV_URL. Double-check the
result has exactly ONE slash between the domain and `api` — for example
`https://org.crm.dynamics.com/api/mcp`, NOT `https://org.crm.dynamics.com//api/mcp`.

Create `.vscode/mcp.json` with this exact content (replace the entire
`url` value with the MCP URL you just built):

```json
{
  "servers": {
    "Dataverse": {
      "type": "http",
      "url": "https://org.crm.dynamics.com/api/mcp"
    }
  }
}
```

Update `workspace/onboarding/tasks.md` — change step 1 from `- [ ]` to
`- [x]`.

Continue immediately to step 1.3. Do not show admin instructions because the
preflight check has already verified both settings.

## 1.3 — Proceed to agent discovery

The MCP config file is written and the admin configuration is verified. The
Dataverse MCP server will be started later — it's not needed for discovery or
setup.

Read `src/skills/onboarding/step1b.md` and follow it.
