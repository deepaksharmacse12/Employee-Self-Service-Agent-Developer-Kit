# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
ESS Maker Kit - Agent Discovery Script

Authenticates to Dataverse via MSAL and lists available agents (bots).
Designed to be called by the onboarding flow so that any model — including
less-capable ones — can complete setup by running a terminal command instead
of navigating MCP tool calls.

Usage:
    # List all environments in the tenant (no URL required)
    python scripts/discover.py --list-environments

    # Select environment #2 and output JSON
    python scripts/discover.py --list-environments --select 2

    # List agents in the environment
    python scripts/discover.py --url https://org.crm.dynamics.com

    # Select agent #2 and output JSON for the next step
    python scripts/discover.py --url https://org.crm.dynamics.com --select 2
"""

import argparse
import json
import sys
import os
from pathlib import Path
from xml.etree import ElementTree as ET

# Add scripts/ to path so we can import auth
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from auth import authenticate, query_all
from http_errors import APIError
from install_ess_agent import (
    build_installation_options,
    load_installation_config,
)


GITHUB_COPILOT_MCP_CLIENT_ID = "aebc6443-996d-45c2-90f0-388ff96faa56"
LOCAL_CONFIG_PATH = Path(".local") / "config.json"


def discover_agents(env_url, token):
    """Query Dataverse for all bots and return the list."""
    raw = query_all(
        env_url, token,
        entity_set="bots",
        select="botid,name,schemaname,ismanaged",
    )
    agents = []
    for r in raw:
        agents.append({
            "botid": r.get("botid"),
            "name": r.get("name"),
            "schemaname": r.get("schemaname"),
            "ismanaged": r.get("ismanaged", False),
        })
    return agents


def build_ess_agent_inventory(agents, config):
    """Classify discovered bots and return missing supported installations."""
    options = build_installation_options(config)
    options_by_schema = {
        option["schemaName"].casefold(): option
        for option in options
    }
    ess_agents = []
    installed_keys = set()

    for agent in agents:
        schema_name = agent.get("schemaname")
        if not isinstance(schema_name, str):
            continue
        option = options_by_schema.get(schema_name.casefold())
        if not option:
            continue
        ess_agents.append({
            **agent,
            "installationKey": option["key"],
            "configKey": option["configKey"],
        })
        installed_keys.add(option["key"])

    return {
        "agents": ess_agents,
        "installedInstallationKeys": sorted(installed_keys),
        "availableInstallations": [
            option for option in options
            if option["key"] not in installed_keys
        ],
    }


def sync_installed_agents(
    env_url,
    inventory,
    config_path=LOCAL_CONFIG_PATH,
):
    """Persist every detected supported ESS agent in config schema v2."""
    path = Path(config_path)
    existing = {}
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get("configVersion") not in (None, 2):
            return existing

    config = existing if existing.get("configVersion") == 2 else {
        "configVersion": 2,
        "setup": "in-progress",
        "common": {},
        "agents": {},
    }
    config.setdefault("common", {})["dataverseEndpoint"] = env_url.rstrip("/")
    grouped_agents = config.setdefault("agents", {})

    detected_keys = set()
    for agent in inventory["agents"]:
        config_key = agent["configKey"]
        experience, agent_section = config_key.split(".", 1)
        detected_keys.add(config_key)
        existing_agent = (
            grouped_agents.get(experience, {}).get(agent_section, {})
        )
        extraction = existing_agent.get("extraction") or {
            "status": "not-started"
        }
        grouped_agents.setdefault(experience, {})[agent_section] = {
            **existing_agent,
            "name": agent["name"],
            "botId": agent["botid"],
            "schemaName": agent["schemaname"],
            "isManaged": agent["ismanaged"],
            "installation": {"status": "installed"},
            "extraction": extraction,
        }

    for experience, experience_agents in grouped_agents.items():
        if not isinstance(experience_agents, dict):
            continue
        for agent_section, agent in experience_agents.items():
            config_key = f"{experience}.{agent_section}"
            if (
                isinstance(agent, dict)
                and config_key not in detected_keys
                and agent.get("installation")
            ):
                agent["installation"]["status"] = "not-detected"

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(config, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp_path, path)
    return config


def check_mcp_config(env_url, token):
    """Return the GA Dataverse MCP and GitHub Copilot allow-list state."""
    organizations = query_all(
        env_url,
        token,
        entity_set="organizations",
        select="orgdborgsettings",
    )
    org_settings = (
        organizations[0].get("orgdborgsettings") if organizations else None
    )
    root = ET.fromstring(org_settings or "<OrgSettings />")
    mcp_value = (root.findtext("IsMCPEnabled") or "").strip().lower()

    # Dataverse MCP is enabled by default when IsMCPEnabled is omitted.
    server_enabled = mcp_value != "false"

    clients = query_all(
        env_url,
        token,
        entity_set="allowedmcpclients",
        select="applicationid,isenabled,statecode",
        filter_expr=(
            f"applicationid eq '{GITHUB_COPILOT_MCP_CLIENT_ID}'"
        ),
    )
    client_enabled = any(
        client.get("isenabled") is True
        and client.get("statecode", 0) == 0
        for client in clients
    )

    return {
        "configured": server_enabled and client_enabled,
        "serverEnabled": server_enabled,
        "githubCopilotEnabled": client_enabled,
    }


def print_agent_table(agents):
    """Print a numbered table of agents to stdout."""
    # Calculate column widths
    name_width = max((len(a["name"] or "") for a in agents), default=10)
    schema_width = max((len(a["schemaname"] or "") for a in agents), default=11)
    name_width = max(name_width, 10)
    schema_width = max(schema_width, 11)

    header = f"  {'#':<4} {'Agent Name':<{name_width}}  {'Schema Name':<{schema_width}}  {'Managed'}"
    sep = f"  {'-'*4} {'-'*name_width}  {'-'*schema_width}  {'-'*7}"
    print()
    print(header)
    print(sep)
    for i, a in enumerate(agents, 1):
        managed = "Yes" if a["ismanaged"] else "No"
        print(f"  {i:<4} {a['name'] or '':<{name_width}}  {a['schemaname'] or '':<{schema_width}}  {managed}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Discover agents in a Dataverse environment")
    parser.add_argument("--url",
                        help="Power Platform environment URL")
    parser.add_argument("--list-environments", action="store_true",
                        help="List all environments in the tenant (no URL needed)")
    parser.add_argument("--check-mcp-config", action="store_true",
                        help="Check Dataverse MCP and GitHub Copilot enablement")
    parser.add_argument("--select", type=int, default=None,
                        help="Select agent by number and output JSON")
    parser.add_argument(
        "--sync-config",
        action="store_true",
        help="Persist detected supported agents in .local/config.json",
    )
    args = parser.parse_args()

    # --- Environment listing mode ---
    if args.list_environments:
        from list_environments import (
            get_dataverse_environments,
            print_environment_table,
        )

        dv_environments, excluded = get_dataverse_environments()

        print(f"Found {len(dv_environments)} Dataverse-linked environment(s).")
        if excluded:
            print(f"  ({excluded} environment(s) without Dataverse were excluded.)")

        if not dv_environments:
            print("ERROR: No environments with linked Dataverse found.")
            print("ESS requires a Dataverse-enabled environment.")
            sys.exit(1)

        print_environment_table(dv_environments)

        if args.select is not None:
            idx = args.select
            if idx < 1 or idx > len(dv_environments):
                print(f"ERROR: Invalid selection '{idx}'. "
                      f"Choose a number between 1 and {len(dv_environments)}.")
                sys.exit(1)
            selected = dv_environments[idx - 1]
            print(f"SELECTED_ENV_JSON:{json.dumps(selected)}")
            sys.exit(0)

        return

    # --- Agent discovery mode (requires --url) ---
    if not args.url:
        parser.error("--url is required when not using --list-environments")

    env_url = args.url.rstrip("/")

    print("Authenticating to Dataverse...")
    token = authenticate(env_url)
    print("Authenticated.\n")

    if args.check_mcp_config:
        print("Checking Dataverse MCP configuration...")
        try:
            state = check_mcp_config(env_url, token)
        except (APIError, ET.ParseError) as e:
            if isinstance(e, APIError):
                print(e.format_for_terminal())
            else:
                print("ERROR: Dataverse returned invalid environment settings.")
            sys.exit(1)
        print(f"MCP_CONFIG_JSON:{json.dumps(state)}")
        return

    print("Discovering agents...")
    try:
        discovered_agents = discover_agents(env_url, token)
        inventory = build_ess_agent_inventory(
            discovered_agents,
            load_installation_config(),
        )
    except APIError as e:
        print(e.format_for_terminal())
        sys.exit(1)
    except (OSError, ValueError) as e:
        print(f"ERROR: Could not load ESS installation catalog: {e}")
        sys.exit(1)

    print(f"ESS_AGENT_DISCOVERY_JSON:{json.dumps(inventory)}")
    if args.sync_config:
        sync_installed_agents(env_url, inventory)
    agents = inventory["agents"]

    if not agents:
        print("No supported ESS agents found in this environment.")
        sys.exit(1)

    print(f"Found {len(agents)} supported ESS agent(s):")
    print_agent_table(agents)

    if args.select is not None:
        idx = args.select
        if idx < 1 or idx > len(agents):
            print(f"ERROR: Invalid selection '{idx}'. "
                  f"Choose a number between 1 and {len(agents)}.")
            sys.exit(1)
        selected = agents[idx - 1]
        # Output JSON on a clearly marked line for easy parsing
        print(f"SELECTED_AGENT_JSON:{json.dumps(selected)}")
        sys.exit(0)


if __name__ == "__main__":
    main()
