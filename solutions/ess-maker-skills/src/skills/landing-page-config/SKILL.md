---
name: landing-page-config
description: >-
  Configure an ESS landing page through the AgentConfiguration MCP server.
  Use for branding and accent colors, quick links, starter prompts, Stay Up
  to Date, Quick Access, reading the agent name or icon, and any call to the
  ess-landing-page-config MCP server.
---

# Landing Page Configuration

Orchestrate landing-page configuration through the AgentConfiguration MCP
server. Guide the maker through complete, safe section updates without relying
only on individual tool descriptions.

## Hard rules

1. Route every call to the `ess-landing-page-config` MCP server through this
   skill.
2. Use AgentConfiguration MCP tools for server access. Do not call the backing
   REST/OData API directly.
3. Use a `titleId` supplied by the maker when available. Otherwise, use the
   active agent's `titleId` from `.local/config.json`. Never substitute its
   Dataverse `botId`.
4. When the maker asks to update branding/accent colors, quick links, or starter
   prompts, call that surface's `open_*` tool immediately after resolving
   `titleId`. Do not read with `get_agent_config` first or apply the requested
   change through a chat-driven write.
5. Call `get_agent_config` before a chat-driven update only when the requested
   surface has no `open_*` editor.
6. Treat every provided config section as a bulk replacement:
   - An omitted section remains unchanged.
   - A provided section replaces the complete section.
   - An empty section resets or clears that section.
7. Merge add/remove/reorder/toggle requests into the current section and submit
   the complete resulting section.
8. Set the matching update flag to `true` for every provided section. A section
   with a false or omitted update flag is ignored by the backend.
9. Before an agent-initiated branding update containing colors, run
   `python scripts/validate_branding.py` for each changed theme.
10. Treat failed contrast validation as advisory. Warn the maker, show the
   result, and require explicit confirmation before submitting that color.
11. Send only `name` and `accentColor` for each theme. The server derives
    `hoverColor` and `activeColor`.
12. Confirm section clears, branding resets, and full-list replacements before
    writing.
13. Use the `update_agent_config` response as the operation result. It contains
    the updated configuration and success information, so do not perform a
    follow-up read.
14. Open at most one widget per turn. A widget owns its Publish operation; do
    not issue a duplicate write after opening it.
15. Treat the agent name and icon as read-only. You may report the name and
    display the icon, but never include either in an update payload or imply
    that the AgentConfiguration server can edit them.
16. Treat a 404 from `get_agent_config` or any `open_*` tool as an
    uninitialized landing-page configuration and follow the initialization
    flow below.

## Resolve the target

1. Use a `titleId` supplied explicitly by the maker.
2. Otherwise, when `.local/config.json` is available, resolve the active agent
   from `agent` and use `agent.titleId`.
3. When the `titleId` is still unknown, call `list_agent_configs` and match its
   configured agents against the active or maker-supplied agent name.
4. When there is no configured match, call `search_agents` with a distinctive
   substring of the agent name. The server does not require a three-character
   minimum.
5. Use an unambiguous matching result's `titleId`. When multiple candidates
   match, ask the maker to choose. When none match, explain that the agent could
   not be found and ask the maker to verify its name.

Do not guess the identifier from the agent name, schema name, `botId`, Teams
app ID, or manifest ID.

## Start a guided configuration

When the maker asks to configure or set up the landing page:

1. Resolve `titleId`.
2. Call `get_agent_config`.
3. Present the current state:

   | Area | Show |
   |---|---|
   | Branding | Configured light/dark accent colors, or defaults |
   | Quick links | Link count and ordered labels |
   | Starter prompts | Pivot count and prompts per pivot |
   | Stay Up to Date | Enabled or disabled |
   | Quick Access | Enabled or disabled |
   | Agent identity | Read-only name and whether an icon is available |

4. Ask which area the maker wants to configure.
5. Complete one area at a time.

## Route the request

| Intent | Tool flow |
|---|---|
| View or summarize current configuration | `get_agent_config` |
| View the agent name | `get_agent_config`, then report the read-only name |
| Show the agent icon | `get_agent_config`, decode, then open the read-only PNG |
| Update branding or an accent color | Resolve `titleId` -> call `open_accent_color` immediately |
| Update quick links | Resolve `titleId` -> call `open_quick_links` immediately |
| Update starter prompts | Resolve `titleId` -> call `open_starter_prompts` immediately |
| Update insight cards or another surface without an editor | `get_agent_config` -> merge and validate complete affected section(s) -> `update_agent_config` |
| Update the agent name or icon | Explain that the field is read-only and do not call an update tool |

The maker does not need to ask for an editor or widget explicitly. Requests
such as "change the accent color," "add a quick link," or "update a starter
prompt" always open the corresponding editing surface. Let the widget read,
validate, and publish its own section.

## Initialize missing configuration

When `get_agent_config` or any `open_*` tool returns 404:

1. Explain that the agent's landing page has not been configured yet and must
   be initialized.
2. Resolve the `titleId`:
   - When the attempted tool call used the correct known `titleId`, keep it.
   - Otherwise, call `list_agent_configs` and match by agent name. If a match
     exists, use its `titleId` and retry the original tool because that agent
     already has a configuration.
   - If no configured agent matches, call `search_agents` with a distinctive
     substring of the agent name. Use an unambiguous matching result's
     `titleId`; ask the maker to choose when multiple candidates match.
3. When the agent is absent from `list_agent_configs`, call
   `create_agent_config` with the resolved `titleId`. If the maker's original
   request was read-only, first explain that initialization creates a
   configuration and get confirmation. A request to set up, configure, or
   update the landing page already authorizes initialization.
4. Continue the maker's original request:
   - For a request to view or summarize configuration, present the
     configuration returned by `create_agent_config`.
   - For a request handled by an `open_*` editor, call the originally requested
     `open_*` tool with the initialized `titleId`.
   - For another update, use the created configuration as the current state,
     merge the requested change, and continue the normal update flow.

`create_agent_config` can initialize only a supported primary Employee
Self-Service agent: the main ESS Core, IT, or HR agent, including supported
declarative versions. If creation reports that the selected agent is not an ESS
agent, explain that landing-page configuration is available only for those
primary ESS agents. General agents and attached subagents are ineligible. For
any other creation failure, surface the actual error.

## Build update payloads

`update_agent_config` takes `titleId` and a `config` object. Include the complete
new value and update flag for each affected section.

| Section | Required update flag |
|---|---|
| `branding` | `isBrandingUpdated: true` |
| `quickLinksConfig` | `areQuickLinksUpdated: true` |
| `pivots` | `isPivotUpdated: true` |
| `insightCardsConfig` | `areInsightCardsUpdated: true` |

Omit every unaffected section and its flag.

## Branding

1. Read the current branding section.
2. Track the theme colors the maker requested to change.
3. Normalize changed colors to uppercase `#RRGGBB`.
4. Merge the changes into the complete current `theming` array.
5. Validate only the changed colors:

   ```powershell
   # Light only
   python scripts/validate_branding.py --light "#RRGGBB"

   # Dark only
   python scripts/validate_branding.py --dark "#RRGGBB"

   # Both
   python scripts/validate_branding.py --light "#RRGGBB" --dark "#RRGGBB"
   ```

6. Interpret the exit code:
   - `0`: every changed color meets WCAG AA; continue.
   - `1`: one or more changed colors have low contrast. Show the ratio,
     background, and required ratio, then ask whether to publish.
   - `2`: invalid input. Correct it before continuing.
7. Submit the complete merged section:

   ```json
   {
     "titleId": "<titleId>",
     "config": {
       "isBrandingUpdated": true,
       "branding": {
         "theming": [
           { "name": "light", "accentColor": "#RRGGBB" },
           { "name": "dark", "accentColor": "#RRGGBB" }
         ]
       }
     }
   }
   ```

The backend allows at most five theme entries and theme names up to 30
characters. This experience uses the `light` and `dark` themes.

Reset branding by submitting `branding: { "theming": [] }` with
`isBrandingUpdated: true` after confirmation. A reset does not run contrast
validation.

## Quick links

The presence of quick-link entries controls whether quick links appear.

Validate the complete replacement array before writing:

- Maximum links: 10.
- `displayText`: non-empty, maximum 300 characters.
- `address`: non-empty, maximum 2,000 characters.
- `address`: absolute HTTP or HTTPS URL.

Add, remove, and reorder operations use read-merge-write. A supplied list
replaces the complete array after confirmation. Clearing sends:

```json
{
  "titleId": "<titleId>",
  "config": {
    "areQuickLinksUpdated": true,
    "quickLinksConfig": {
      "quickLinks": []
    }
  }
}
```

## Starter prompts

Validate the complete replacement array before writing:

- Maximum pivots: 10.
- Pivot `displayName`: non-null, maximum 35 characters.
- Maximum prompts per pivot: 12.
- Prompt `title`: non-null, maximum 128 characters.
- Prompt `displayText`: non-null, maximum 4,000 characters.

Add, remove, and reorder operations use read-merge-write. Clearing sends
`pivots: []` with `isPivotUpdated: true`.

## Insight cards

The insight-card section contains both controls. Read the current section,
merge the requested toggle, and submit both values together:

```json
{
  "titleId": "<titleId>",
  "config": {
    "areInsightCardsUpdated": true,
    "insightCardsConfig": {
      "isStayUpToDateEnabled": true,
      "isQuickAccessEnabled": false
    }
  }
}
```

## Display the read-only agent icon

1. Call `get_agent_config`.
2. Read `agent.icon` from the response and require it to begin with
   `data:image/png;base64,`.
3. Take everything after `base64,` verbatim and write it to
   `.local/landing-page-config/agent-icon.b64`.
4. Run:

   ```powershell
   python scripts/decode_agent_icon.py `
     --input ".local/landing-page-config/agent-icon.b64" `
     --output ".local/landing-page-config/agent-icon.png"
   ```

5. If decoding fails, call `get_agent_config` again and rewrite the complete
   payload. Never repair the payload by adding `=`.
6. Delete the `.b64` file after a successful decode.
7. Display the PNG through a user-visible host capability. A model-only image
   inspection does not display it to the maker.

### VS Code

Invoke `run_vscode_command`:

```text
commandId: simpleBrowser.show
args: ["file:///c:/absolute/path/to/.local/landing-page-config/agent-icon.png"]
skipCheck: true
```

Use `simpleBrowser.show`, a `file:///` URI, and forward slashes. Do not use
`vscode.open` with a string URI.

### Other hosts

Use the host's user-visible file preview/open capability. If the host has none,
provide the PNG path and state that it cannot be opened automatically. Never
claim the maker can see an image rendered only to the model.

## Errors

- Authorization/403: explain that the maker needs permission to read and write
  Employee Agent configurations.
- Tenant gating: explain that landing-page configuration is unavailable for
  the tenant.
- Missing configuration/404: follow **Initialize missing configuration**.
- Ineligible agent during creation: explain that landing-page configuration is
  available only for a supported primary ESS Core, IT, or HR agent.
- Validation failure: show the field-specific server message.
- Tool failure: surface the error and do not report the update as successful.
