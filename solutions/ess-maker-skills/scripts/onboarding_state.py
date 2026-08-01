# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Persist partial /setup progress across Copilot conversations."""

import argparse
import json
import os
import sys
from urllib.parse import urlparse


STATE_PATH = os.path.join(".local", "onboarding.json")
MCP_CONFIG_PATH = os.path.join(".vscode", "mcp.json")
STATE_VERSION = 1
INSTALLATION_STATUSES = {
    "installing",
    "manual-required",
    "automatic-complete",
    "verified",
}


def _normalize_environment_url(url):
    normalized = url.rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("Environment URL must be a valid HTTPS URL.")
    return normalized


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"Could not read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return value


def _environment_from_mcp_config():
    config = _read_json(MCP_CONFIG_PATH)
    if not config:
        return None

    servers = config.get("servers") or config.get("mcpServers") or {}
    if not isinstance(servers, dict):
        return None

    for server in servers.values():
        if not isinstance(server, dict):
            continue
        url = server.get("url")
        if not isinstance(url, str):
            continue
        for suffix in ("/api/mcp", "/api/mcp_preview"):
            if url.rstrip("/").endswith(suffix):
                return _normalize_environment_url(
                    url.rstrip("/")[:-len(suffix)]
                )
    return None


def load_state(*, recover_from_mcp=True):
    state = _read_json(STATE_PATH) or {}
    if state and state.get("version") != STATE_VERSION:
        raise ValueError(
            f"Unsupported onboarding state version: {state.get('version')}"
        )

    if state.get("environmentUrl"):
        state["environmentUrl"] = _normalize_environment_url(
            state["environmentUrl"]
        )
    elif recover_from_mcp:
        try:
            recovered_url = _environment_from_mcp_config()
        except ValueError as exc:
            state["recoveryWarning"] = str(exc)
        else:
            if recovered_url:
                state = {
                    **state,
                    "version": STATE_VERSION,
                    "environmentUrl": recovered_url,
                }
    return state


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    state = {**state, "version": STATE_VERSION}
    tmp_path = STATE_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)
        handle.write("\n")
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass
    os.replace(tmp_path, STATE_PATH)
    return state


def save_environment(url):
    state = load_state(recover_from_mcp=False)
    state["environmentUrl"] = _normalize_environment_url(url)
    return save_state(state)


def save_agent(url, bot_id, name, schema_name, is_managed):
    state = load_state(recover_from_mcp=False)
    state["environmentUrl"] = _normalize_environment_url(url)
    state["agent"] = {
        "botId": bot_id,
        "name": name,
        "schemaName": schema_name,
        "isManaged": is_managed,
    }
    return save_state(state)


def save_installation(url, experience, vertical, status):
    if status not in INSTALLATION_STATUSES:
        raise ValueError(f"Unsupported installation status: {status}")
    state = load_state(recover_from_mcp=False)
    state["environmentUrl"] = _normalize_environment_url(url)
    state["installation"] = {
        "experience": experience,
        "vertical": vertical,
        "status": status,
    }
    return save_state(state)


def clear_state():
    try:
        os.remove(STATE_PATH)
    except FileNotFoundError:
        return


def _print_state(state):
    print(f"ONBOARDING_STATE_JSON:{json.dumps(state)}")


def main():
    parser = argparse.ArgumentParser(
        description="Read or update partial ESS onboarding state"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("show", help="Show saved onboarding state")

    environment_parser = subparsers.add_parser(
        "save-environment", help="Save the selected environment"
    )
    environment_parser.add_argument("--url", required=True)

    agent_parser = subparsers.add_parser(
        "save-agent", help="Save the selected environment and agent"
    )
    agent_parser.add_argument("--url", required=True)
    agent_parser.add_argument("--bot-id", required=True)
    agent_parser.add_argument("--name", required=True)
    agent_parser.add_argument("--schema", required=True)
    agent_parser.add_argument("--managed", action="store_true")

    installation_parser = subparsers.add_parser(
        "save-installation", help="Save ESS application installation progress"
    )
    installation_parser.add_argument("--url", required=True)
    installation_parser.add_argument(
        "--experience", required=True, choices=("da", "cea")
    )
    installation_parser.add_argument(
        "--vertical", required=True, choices=("hr", "it")
    )
    installation_parser.add_argument(
        "--status", required=True, choices=sorted(INSTALLATION_STATUSES)
    )

    subparsers.add_parser("clear", help="Clear partial onboarding state")
    args = parser.parse_args()

    try:
        if args.command == "show":
            _print_state(load_state())
        elif args.command == "save-environment":
            _print_state(save_environment(args.url))
        elif args.command == "save-agent":
            _print_state(save_agent(
                args.url,
                args.bot_id,
                args.name,
                args.schema,
                args.managed,
            ))
        elif args.command == "save-installation":
            _print_state(save_installation(
                args.url,
                args.experience,
                args.vertical,
                args.status,
            ))
        else:
            clear_state()
            print("ONBOARDING_STATE_CLEARED")
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
