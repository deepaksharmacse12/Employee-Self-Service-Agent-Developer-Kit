# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
Connect the ServiceNow flow invoker connection and share its parameters.

Setup **action** (it mutates the Power Platform environment and Dataverse). It
closes the gap left by ``bind_connections.py``: binding the Dataverse
``connectionreferences`` row is necessary but NOT sufficient — Copilot Studio
still shows the ServiceNow connection as "Not connected" until the per-flow
*invoker-connection binding* is set. This script performs the three writes the
Copilot Studio UI performs when a maker connects the connection:

  1. **Bind the flow invoker connection** — POST the environment's
     ``.../channels/pva-studio/user-connections`` so every flow that uses the
     ServiceNow connector points at the connection (flips status to Connected).
  2. **Read the connection parameters** — GET the live connection to capture its
     ``connectionParametersSet`` (Entra app resource uri + instance name).
  3. **Share the parameters** — PATCH the Dataverse ``connectionreferences`` row
     with ``connectionparametersetconfig`` so the parameters travel with the
     solution reference (what the portal's "share" action writes).

Scope: **ServiceNow only.** The connection reference must already be bound to a
connection (run ``bind_connections.py`` first); this script does not create or
authenticate connections.

Authentication: the environment API (steps 1–2) uses the Power Platform CLI
("pac") public client via ``pp_env_client`` — the kit's default Azure CLI client
is not pre-authorized for that resource. The Dataverse PATCH (step 3) uses the
normal ``auth.authenticate`` token.

Exit codes:
  0  connected (now or already) — proceed to verify with SN-FLOWCONN-001.
  3  no ServiceNow connection reference found (extension pack not installed).
  4  the ServiceNow reference is not bound to a connection (run bind first).
  5  could not resolve the Power Platform environment id.
  1  unexpected error.

Usage:
    python scripts/connect_and_share.py [--connector servicenow]
        [--env-url URL] [--environment-id ID] [--dry-run] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import auth  # noqa: E402
from pp_env_client import (  # noqa: E402
    PPEnvClient,
    connector_is_connected,
    connector_short_name,
    find_connector_flows,
    iter_flow_connectors,
)

_SERVICENOW_CONNECTOR_KEYWORDS = ("service-now", "servicenow")
_CONNECTOR_NAME = "shared_service-now"

_REF_ENTITY = "connectionreferences"
_REF_SELECT = (
    "connectionreferenceid,connectionreferencelogicalname,"
    "connectionreferencedisplayname,connectorid,connectionid,statuscode,"
    "connectionparametersetconfig"
)


# ─────────────────────────────────────────────────────────────────────
# Pure helpers (no I/O — unit-testable).
# ─────────────────────────────────────────────────────────────────────
def _matches_servicenow(text: str) -> bool:
    low = (text or "").lower()
    return any(kw in low for kw in _SERVICENOW_CONNECTOR_KEYWORDS)


def select_servicenow_refs(rows: list[dict]) -> list[dict]:
    return [r for r in rows if _matches_servicenow(r.get("connectorid", ""))]


def pick_bound_ref(refs: list[dict]) -> dict | None:
    """Return the ServiceNow reference bound to a connection, if any.

    Prefers an active (``statuscode == 1``) reference; falls back to any with a
    ``connectionid`` set.
    """
    active = [r for r in refs if r.get("connectionid") and r.get("statuscode") == 1]
    if active:
        return active[0]
    bound = [r for r in refs if r.get("connectionid")]
    return bound[0] if bound else None


def bot_schema(config: dict | None) -> str | None:
    """Resolve the active agent's Dataverse schema name from config.json."""
    if not isinstance(config, dict):
        return None
    agents = config.get("agents") or []
    active = config.get("activeAgent")
    if active:
        for agent in agents:
            if agent.get("slug") == active and agent.get("schemaName"):
                return agent["schemaName"]
    if config.get("schemaName"):
        return config["schemaName"]
    agent = config.get("agent") or {}
    if agent.get("schemaName"):
        return agent["schemaName"]
    for agent in agents:
        if agent.get("schemaName"):
            return agent["schemaName"]
    return None


def build_flow_bindings(
    user_connections: dict, connector_name: str, target_connection_id: str
) -> tuple[dict, list[str]]:
    """Build the ``flowBindings`` POST body pointing the connector at the target.

    Preserves every other connector on each affected flow (so re-binding
    ServiceNow does not clear a flow's Dataverse binding). Returns
    ``(flow_bindings, changed_flow_ids)`` where ``flow_bindings`` maps a flow id
    to a *direct array* of connector dicts (the write shape) and
    ``changed_flow_ids`` lists the flows whose ServiceNow binding needs a write.

    A flow needs a write when the target connector's connection id differs *or*
    the connector is not currently ``Connected`` (e.g. ``Stale`` after a pack
    install replaced the flow) — a stale binding carries the right connection id
    but must be re-POSTed to become active again.
    """
    # Group all connectors by flow id (from the nested GET shape).
    flows: dict[str, list[dict]] = {}
    for flow_id, connector in iter_flow_connectors(user_connections):
        flows.setdefault(flow_id, []).append(connector)

    bindings: dict[str, list[dict]] = {}
    changed: list[str] = []
    for flow_id, connectors in flows.items():
        has_target_connector = any(
            connector_short_name(c.get("connectorId", "")) == connector_name
            for c in connectors
        )
        if not has_target_connector:
            continue

        out = []
        flow_changed = False
        for c in connectors:
            connector_id = c.get("connectorId", "")
            short = connector_short_name(connector_id)
            connection_id = c.get("connectionId")
            if short == connector_name:
                if connection_id != target_connection_id or not connector_is_connected(c):
                    flow_changed = True
                connection_id = target_connection_id
            out.append(
                {
                    "connectorId": connector_id,
                    "connectionId": connection_id,
                    "connectionName": c.get("connectionName") or short,
                }
            )
        bindings[flow_id] = out
        if flow_changed:
            changed.append(flow_id)

    return bindings, changed


def build_param_config(connection: dict) -> dict | None:
    """Build the ``connectionparametersetconfig`` object from a connection GET.

    Reduces the live ``connectionParametersSet`` to ``{name, values}`` with each
    value stripped to ``{"value": ...}`` — the shape the portal's share action
    PATCHes onto the Dataverse reference.
    """
    props = (connection or {}).get("properties") or {}
    param_set = props.get("connectionParametersSet") or {}
    name = param_set.get("name")
    values = param_set.get("values") or {}
    if not name or not values:
        return None
    reduced = {}
    for key, entry in values.items():
        if isinstance(entry, dict) and "value" in entry:
            reduced[key] = {"value": entry["value"]}
        else:
            reduced[key] = {"value": entry}
    return {"name": name, "values": reduced}


# ─────────────────────────────────────────────────────────────────────
# Orchestration.
# ─────────────────────────────────────────────────────────────────────
def _load_config() -> dict:
    config_path = os.path.join(".local", "config.json")
    if os.path.exists(config_path):
        with open(config_path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _load_env_url(cli_value: str | None, config: dict) -> str | None:
    if cli_value:
        return cli_value
    return config.get("dataverseEndpoint")


def _resolve_environment_id(env_url, dv_token, environment_id, tenant_id):
    if environment_id:
        return environment_id
    from flightcheck.pp_admin_client import PPAdminClient, derive_environment_id

    pp = PPAdminClient(tenant_id)
    pp.authenticate()
    return derive_environment_id(env_url, dv_token, pp_admin=pp)


def run(
    env_url: str,
    *,
    config: dict,
    environment_id: str | None,
    dry_run: bool,
) -> dict:
    """Connect the ServiceNow flow invoker connection and share its parameters."""
    schema = bot_schema(config)
    if not schema:
        return {
            "action": "error",
            "exit_code": 1,
            "message": (
                "Could not resolve the agent's schema name from "
                ".local/config.json (agents[].schemaName)."
            ),
        }

    dv_token = auth.authenticate(env_url)
    rows = auth.query_all(env_url, dv_token, _REF_ENTITY, _REF_SELECT)
    sn_refs = select_servicenow_refs(rows)
    if not sn_refs:
        return {
            "action": "no_reference",
            "exit_code": 3,
            "message": (
                "No ServiceNow connection reference found. Install the "
                "ServiceNow extension pack (S6.1) first, then re-run."
            ),
        }

    ref = pick_bound_ref(sn_refs)
    if ref is None:
        return {
            "action": "no_binding",
            "exit_code": 4,
            "message": (
                "The ServiceNow connection reference is not bound to a "
                "connection. Run `python scripts/bind_connections.py "
                "--connector servicenow` first, then re-run."
            ),
        }

    connection_id = ref["connectionid"]
    ref_id = ref["connectionreferenceid"]
    ref_name = ref.get("connectionreferencelogicalname")

    from auth import discover_tenant

    tenant_id = discover_tenant(env_url)
    env_id = _resolve_environment_id(env_url, dv_token, environment_id, tenant_id)
    if not env_id:
        return {
            "action": "no_environment",
            "exit_code": 5,
            "message": (
                "Could not resolve the Power Platform environment id. Pass "
                "--environment-id <guid>."
            ),
        }

    client = PPEnvClient(tenant_id, env_id)
    if not client.authenticate(interactive=not dry_run):
        # In dry-run we still try silently; if there's no cached token we can't
        # inspect the live state, so report that rather than pretend.
        if dry_run:
            return {
                "action": "would_connect",
                "exit_code": 0,
                "reference": ref_name,
                "connection_id": connection_id,
                "message": (
                    "Would connect the ServiceNow flow invoker connection and "
                    "share its parameters (dry-run: no cached Power Platform "
                    "token, live state not inspected)."
                ),
            }
        return {
            "action": "error",
            "exit_code": 1,
            "message": "Power Platform sign-in failed; cannot connect.",
        }

    # --- Step 1: flow invoker binding ---
    user_connections = client.get_user_connections(schema)
    sn_flows = find_connector_flows(user_connections, _CONNECTOR_NAME)
    if not sn_flows:
        return {
            "action": "no_flow_connector",
            "exit_code": 4,
            "reference": ref_name,
            "message": (
                "No ServiceNow flow invoker connection was found for this agent "
                "(the extension pack flows may not reference the ServiceNow "
                "connector). Nothing to connect."
            ),
        }

    bindings, changed_flows = build_flow_bindings(
        user_connections, _CONNECTOR_NAME, connection_id
    )
    already_connected = all(connector_is_connected(c) for _, c in sn_flows)
    bind_action = "already_connected" if already_connected and not changed_flows else (
        "would_bind" if dry_run else "bound"
    )
    if changed_flows and not dry_run:
        client.set_user_connections(schema, bindings)

    # --- Step 2: read connection parameters ---
    connection = client.get_connection(_CONNECTOR_NAME, connection_id)
    param_config = build_param_config(connection)

    # --- Step 3: share parameters onto the Dataverse reference ---
    share_action = "skipped"
    if param_config is not None:
        desired = json.dumps(param_config, separators=(",", ":"))
        current = ref.get("connectionparametersetconfig")
        if _config_equal(current, param_config):
            share_action = "already_shared"
        elif dry_run:
            share_action = "would_share"
        else:
            auth.update_record(
                env_url,
                dv_token,
                _REF_ENTITY,
                ref_id,
                {
                    "connectionparametersetconfig": desired,
                    "connectionid": connection_id,
                },
            )
            share_action = "shared"

    verb = "Would connect" if dry_run else "Connected"
    return {
        "action": "would_connect" if dry_run else "connected",
        "exit_code": 0,
        "reference": ref_name,
        "connection_id": connection_id,
        "flow_binding": bind_action,
        "changed_flows": changed_flows,
        "share": share_action,
        "message": (
            f"{verb} the ServiceNow flow invoker connection "
            f"(flow binding: {bind_action}; parameter sharing: {share_action})."
        ),
    }


def _config_equal(current, desired: dict) -> bool:
    """Compare the stored connectionparametersetconfig (a JSON string) to the
    desired object, tolerating formatting/whitespace differences."""
    if not current:
        return False
    try:
        parsed = json.loads(current) if isinstance(current, str) else current
    except (TypeError, ValueError):
        return False
    return parsed == desired


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Connect the ServiceNow flow invoker connection and share "
        "its parameters."
    )
    parser.add_argument(
        "--connector", default="servicenow", choices=["servicenow"],
        help="Connector to connect (only 'servicenow' is supported).",
    )
    parser.add_argument(
        "--env-url", default=None,
        help="Dataverse environment URL (default: .local/config.json).",
    )
    parser.add_argument(
        "--environment-id", default=None,
        help="Power Platform environment id (default: derived).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report what would change without writing.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    args = parser.parse_args(argv)

    config = _load_config()
    env_url = _load_env_url(args.env_url, config)
    if not env_url:
        print("ERROR: No Dataverse environment URL (.local/config.json missing "
              "dataverseEndpoint, and --env-url not given).")
        return 1

    try:
        result = run(
            env_url,
            config=config,
            environment_id=args.environment_id,
            dry_run=args.dry_run,
        )
    except Exception as e:  # noqa: BLE001 — surface a clean message, no stack
        result = {"action": "error", "exit_code": 1,
                  "message": f"{type(e).__name__}: {e}"}

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result.get("message", result.get("action", "")))

    return int(result.get("exit_code", 1))


if __name__ == "__main__":
    sys.exit(main())
