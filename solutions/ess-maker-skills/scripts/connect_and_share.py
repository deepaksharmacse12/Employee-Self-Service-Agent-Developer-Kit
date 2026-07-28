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
  3. **Share the parameters** — write ``connectionparametersetconfig`` onto the
     Copilot Studio *shared-connector* references, one per ServiceNow invoker
     flow, named ``{schema}.{flowId}.shared_service-now`` (flowId = the flow's
     Dataverse workflowid). The portal creates these on demand when the maker
     shares; this script finds each and updates it, or creates it when absent.
     The name is derived from the flow id (NOT random) so the portal correlates
     it back to the flow — a random name shows as "not shared". (The
     solution-shipped ``.cr.<short>`` reference is NOT what the portal reflects.)

Scope: **ServiceNow only.** Resolves the connection to connect dynamically —
the live most-recently-created Connected ServiceNow connection (BAP discovery,
same as ``bind_connections``) — rather than trusting a stored ``connectionid``.
This script does not create or authenticate connections.

Authentication: the environment API (steps 1–2) uses the Power Platform CLI
("pac") public client via ``pp_env_client`` — the kit's default Azure CLI client
is not pre-authorized for that resource. The Dataverse PATCH (step 3) uses the
normal ``auth.authenticate`` token.

Exit codes:
  0  connected (now or already) — proceed to verify with SN-FLOWCONN-001.
  3  no ServiceNow connection reference found (extension pack not installed).
  4  no active ServiceNow connection found to connect (create + bind first).
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
import connect_state  # noqa: E402
from bind_connections import (  # noqa: E402
    _discover_connections as _discover_env_connections,
    filter_servicenow_connections,
    pick_connection,
)
from pp_env_client import (  # noqa: E402
    PPEnvClient,
    connector_is_connected,
    connector_short_name,
    find_connector_flows,
    iter_flow_connectors,
)

_SERVICENOW_CONNECTOR_KEYWORDS = ("service-now", "servicenow")
_CONNECTOR_NAME = "shared_service-now"
_CONNECTOR_ID = "/providers/Microsoft.PowerApps/apis/shared_service-now"

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


def _is_shared_connector_ref(ref: dict) -> bool:
    """True when the reference is a Copilot Studio *shared-connector* reference.

    Copilot Studio's "share connection parameters with users" feature does not
    read the solution-shipped reference (logical name ``{schema}.cr.<short>``).
    Instead it owns a reference named ``{schema}.<flowId>.shared_service-now``
    — one per ServiceNow invoker flow — and *creates it on demand* when the maker
    shares. Those are the only references the portal's sharing state reflects;
    patching the solution reference leaves the portal showing "not shared".
    """
    ln = (ref.get("connectionreferencelogicalname") or "").lower()
    return ln.endswith("." + _CONNECTOR_NAME)


def pick_shared_ref(refs: list[dict]) -> dict | None:
    """Return the first portal-owned ``.shared_service-now`` reference, if any."""
    for r in refs:
        if _is_shared_connector_ref(r):
            return r
    return None


def shared_ref_logical_name(schema: str, flow_id: str) -> str:
    """Build a Copilot Studio shared-connector reference logical name.

    Mirrors the portal's convention **exactly**: ``{schema}.{flowId}.\
shared_service-now`` where ``flowId`` is the invoker flow's Dataverse
    ``workflowid``. The GUID is NOT random — the portal derives the reference
    name from the flow id so it can correlate the shared parameters back to that
    flow. Using a random uuid here produced a reference the portal could not find
    (it still showed "not shared"); keying by flow id is what makes sharing take.
    """
    return f"{schema}.{flow_id}.{_CONNECTOR_NAME}"


def find_shared_ref_by_name(refs: list[dict], logical_name: str) -> dict | None:
    """Return the reference whose logical name matches ``logical_name`` (case-
    insensitive), or ``None``."""
    target = (logical_name or "").lower()
    for r in refs:
        if (r.get("connectionreferencelogicalname") or "").lower() == target:
            return r
    return None


def flow_id_from_shared_ref_name(schema: str, logical_name: str) -> str | None:
    """Extract the invoker flow id from a ``.shared_service-now`` reference name.

    Inverse of :func:`shared_ref_logical_name`: given ``{schema}.{flowId}.\
shared_service-now`` returns ``flowId``. Returns ``None`` when the name does not
    match the portal's shared-reference convention (used only for reporting).
    """
    ln = logical_name or ""
    prefix = f"{schema}."
    suffix = f".{_CONNECTOR_NAME}"
    low = ln.lower()
    if low.startswith(prefix.lower()) and low.endswith(suffix.lower()):
        return ln[len(prefix): len(ln) - len(suffix)]
    return None


def resolve_connection_id(
    env_url: str,
    dv_token: str,
    environment_id: str | None,
    sn_refs: list[dict],
) -> tuple[str | None, str | None]:
    """Resolve the ServiceNow connection id to connect and share, dynamically.

    Fool-proof, and consistent with ``bind_connections``: prefer the live
    **most-recently-created Connected** ServiceNow connection (discovered via the
    BAP admin API) rather than trusting a value stored on a reference — a stored
    ``connectionid`` can be stale if the maker re-created the connection. Falls
    back to an already-bound reference's ``connectionid`` only when live
    discovery is unavailable (e.g. missing BAP permissions).

    Returns ``(connection_id, source)`` or ``(None, None)`` when neither a live
    connection nor a bound reference is found.
    """
    try:
        conns = _discover_env_connections(env_url, dv_token, environment_id)
        chosen, _total = pick_connection(filter_servicenow_connections(conns))
        if chosen and chosen.get("name"):
            return chosen["name"], "latest environment connection"
    except Exception:
        # Discovery is best-effort; fall through to the bound-reference fallback.
        pass
    bound = pick_bound_ref(sn_refs)
    if bound and bound.get("connectionid"):
        return bound["connectionid"], "bound reference"
    return None, None


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

    connection_id, conn_source = resolve_connection_id(
        env_url, dv_token, environment_id, sn_refs
    )
    if not connection_id:
        return {
            "action": "no_connection",
            "exit_code": 4,
            "message": (
                "No active ServiceNow connection was found to connect. Create "
                "the ServiceNow connection in Copilot Studio (Connections) and "
                "bind it (`python scripts/bind_connections.py --connector "
                "servicenow`), then re-run."
            ),
        }

    bound_ref = pick_bound_ref(sn_refs)
    ref_name = (bound_ref or {}).get("connectionreferencelogicalname")

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

    # --- Step 3: share parameters onto the portal-owned shared reference(s) ---
    #
    # Copilot Studio's "share connection parameters" feature reads its *own*
    # references named ``{schema}.{flowId}.shared_service-now`` — one per
    # ServiceNow invoker flow — NOT the solution-shipped ``.cr.<short>``
    # reference, and NOT a randomly-named one. It creates them on demand when the
    # maker shares. Mirror the portal: for every ServiceNow invoker flow, find
    # the flow-id-keyed shared reference and update it, or create it if absent.
    shared_records: list[dict] = []
    if param_config is not None:
        desired = json.dumps(param_config, separators=(",", ":"))
        # Build the unified set of shared references to configure: one per
        # ServiceNow invoker flow registered on the agent, UNION every
        # portal-owned ``.shared_service-now`` reference already present in
        # Dataverse. Relying on the agent's registered flows alone misses
        # solution-shipped shared references — e.g. a second extension pack's
        # orchestrator (HRSD installed alongside ITSM) ships its own
        # ``{schema}.{flowId}.shared_service-now`` reference whose flow may not
        # yet be registered on the agent, yet the portal still reads it for the
        # connection's sharing state. Missing it leaves that pack "not shared".
        targets: dict[str, tuple[str, str | None]] = {}
        for flow_id, _connector in sn_flows:
            name = shared_ref_logical_name(schema, flow_id)
            targets[name.lower()] = (name, flow_id)
        for r in sn_refs:
            if _is_shared_connector_ref(r):
                name = r.get("connectionreferencelogicalname") or ""
                if name:
                    targets.setdefault(
                        name.lower(),
                        (name, flow_id_from_shared_ref_name(schema, name)),
                    )
        for name, flow_id in targets.values():
            existing = find_shared_ref_by_name(sn_refs, name)
            if existing is not None:
                already = (
                    _config_equal(
                        existing.get("connectionparametersetconfig"), param_config
                    )
                    and existing.get("connectionid") == connection_id
                )
                if already:
                    act = "already_shared"
                elif dry_run:
                    act = "would_share"
                else:
                    auth.update_record(
                        env_url,
                        dv_token,
                        _REF_ENTITY,
                        existing["connectionreferenceid"],
                        {
                            "connectionparametersetconfig": desired,
                            "connectionid": connection_id,
                        },
                    )
                    act = "shared"
            elif dry_run:
                act = "would_create_shared_ref"
            else:
                auth.create_record(
                    env_url,
                    dv_token,
                    _REF_ENTITY,
                    {
                        "connectionreferencelogicalname": name,
                        "connectionreferencedisplayname": name,
                        "connectorid": _CONNECTOR_ID,
                        "connectionid": connection_id,
                        "connectionparametersetconfig": desired,
                    },
                )
                act = "created_shared_ref"
            shared_records.append(
                {"flow": flow_id, "reference": name, "action": act}
            )

    share_action = _aggregate_share(shared_records)
    shared_ref_name = shared_records[0]["reference"] if shared_records else None

    verb = "Would connect" if dry_run else "Connected"
    return {
        "action": "would_connect" if dry_run else "connected",
        "exit_code": 0,
        "reference": ref_name,
        "shared_reference": shared_ref_name,
        "shared_references": shared_records,
        "connection_id": connection_id,
        "connection_source": conn_source,
        "flow_binding": bind_action,
        "changed_flows": changed_flows,
        "share": share_action,
        "message": (
            f"{verb} the ServiceNow flow invoker connection "
            f"(flow binding: {bind_action}; parameter sharing: {share_action})."
        ),
    }


def _aggregate_share(records: list[dict]) -> str:
    """Collapse per-flow share outcomes into a single status for the summary.

    Precedence (most-actionable first): an actual write (``created_shared_ref``
    / ``shared``) or its dry-run equivalent wins over ``already_shared``, which
    wins over ``skipped``. With a single flow this is just that flow's action.
    """
    if not records:
        return "skipped"
    actions = [r["action"] for r in records]
    for status in (
        "created_shared_ref",
        "shared",
        "would_create_shared_ref",
        "would_share",
    ):
        if status in actions:
            return status
    if "already_shared" in actions:
        return "already_shared"
    return "skipped"


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


def _persist_connect_state(args, result: dict) -> None:
    """On confirmed connect success, record the active ServiceNow connection,
    ``status``, the legacy root summary, and ``S6.4`` (never raises)."""
    try:
        if args.dry_run or result.get("exit_code") != 0:
            return
        if result.get("action") != "connected":
            return
        cfg = connect_state.load("servicenow")
        conn = {"state": "active", "flowBinding": "connected",
                "verifiedBy": "programmatic"}
        if cfg.get("authType"):
            conn["authType"] = cfg["authType"]
        connect_state.record_connections("servicenow", {"servicenow": conn})
        connect_state.record_status("servicenow", "connected")
        connect_state.record_setup_step(
            "servicenow", "S6.4", "SN-FLOWCONN-001",
            note="Bind the ServiceNow flow-invoker connection so Copilot Studio "
                 "shows the connection as connected to the agent.")
        if cfg.get("instanceName"):
            connect_state.record_legacy_servicenow_summary({
                "instanceName": cfg.get("instanceName"),
                "instanceUrl": cfg.get("instanceUrl"),
                "usage": cfg.get("usage"),
                "authType": cfg.get("authType"),
                "connectedAt": connect_state.today_iso(),
            })
    except Exception:  # noqa: BLE001 — persistence must never change exit code
        pass


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

    _persist_connect_state(args, result)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result.get("message", result.get("action", "")))

    return int(result.get("exit_code", 1))


if __name__ == "__main__":
    sys.exit(main())
