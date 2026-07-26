# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
Auto-bind the ServiceNow connection reference to an existing connection.

Setup **action** (it mutates Dataverse) invoked by the ServiceNow setup
orchestrator's S6.2 step (``install-servicenow-extension-pack.md`` P6.3) before
it falls back to manual "go bind it in Copilot Studio" instructions.

The problem it solves: a maker who created the ServiceNow connection during
installation (the interactive Entra consent already happened) is still told to
manually wire it to the extension pack's connection *reference*. Binding a
reference to an already-created connection is **not** an OAuth flow — it is a
single Dataverse write setting ``connectionreferences.connectionid`` to the
connection's id (the same field ``push.py`` writes when it mirrors a
flow-scoped connref). This script performs that write automatically.

Scope: **ServiceNow only.** Dataverse and other connectors are intentionally
out of scope (the base agent install owns the Dataverse binding).

Resolution order for the connection to bind:
  1. **Sibling reuse** — another ServiceNow connection reference that is already
     bound to an active connection; reuse its ``connectionid`` (safest — same
     maker, proven-active connection, no guessing).
  2. **BAP discovery** — list the environment's connections, keep the ones on
     the ServiceNow connector that are ``Connected``. Zero → cannot auto-bind
     (the maker must create a connection first); exactly one → bind it; more
     than one → bind the **most recently created** and report which was chosen
     (and its owner) so the maker can veto.

Exit codes (consumed by the playbook):
  0  bound now, or already bound — proceed to verify with SN-CONN-001.
  3  no ServiceNow connection reference found (extension pack not installed).
  4  no active ServiceNow connection exists — fall back to manual create.
  1  unexpected error.

Usage:
    python scripts/bind_connections.py [--env-url URL] [--environment-id ID]
                                       [--dry-run] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import auth  # noqa: E402

# The Power Platform ServiceNow connector's apiId suffix. A connection
# reference's ``connectorid`` looks like
# ``/providers/Microsoft.PowerApps/apis/shared_service-now``; a BAP connection's
# ``properties.apiId`` ends the same way.
_SERVICENOW_CONNECTOR_KEYWORDS = ("service-now", "servicenow")

_REF_ENTITY = "connectionreferences"
_REF_SELECT = (
    "connectionreferenceid,connectionreferencelogicalname,"
    "connectionreferencedisplayname,connectorid,connectionid,statuscode"
)


# ─────────────────────────────────────────────────────────────────────
# Pure helpers (no I/O — unit-testable).
# ─────────────────────────────────────────────────────────────────────


def _matches_servicenow(text: str) -> bool:
    low = (text or "").lower()
    return any(kw in low for kw in _SERVICENOW_CONNECTOR_KEYWORDS)


def select_servicenow_refs(rows: list[dict]) -> list[dict]:
    """Return the connection references whose connector is ServiceNow."""
    return [r for r in rows if _matches_servicenow(r.get("connectorid", ""))]


def ref_is_bound(ref: dict) -> bool:
    """A reference is usable when it points at a connection (``connectionid``
    set) and Dataverse marks the row active (``statuscode == 1``)."""
    return bool(ref.get("connectionid")) and ref.get("statuscode") == 1


def sibling_connection_id(refs: list[dict], target: dict) -> str | None:
    """Return the ``connectionid`` of another ServiceNow reference that is
    already bound to an active connection, or ``None``."""
    target_id = target.get("connectionreferenceid")
    for r in refs:
        if r.get("connectionreferenceid") == target_id:
            continue
        if ref_is_bound(r):
            return r.get("connectionid")
    return None


def _connection_status(conn: dict) -> str:
    statuses = (conn.get("properties") or {}).get("statuses") or []
    if isinstance(statuses, list) and statuses:
        return statuses[0].get("status", "Unknown")
    return "Unknown"


def _connection_owner(conn: dict) -> str:
    created_by = (conn.get("properties") or {}).get("createdBy") or {}
    return (
        created_by.get("userPrincipalName")
        or created_by.get("displayName")
        or "(unknown owner)"
    )


def _created_time(conn: dict) -> str:
    return (conn.get("properties") or {}).get("createdTime") or ""


def filter_servicenow_connections(conns: list[dict]) -> list[dict]:
    """Keep only ``Connected`` connections on the ServiceNow connector."""
    out = []
    for c in conns:
        props = c.get("properties") or {}
        haystack = f"{props.get('apiId', '')}{props.get('displayName', '')}"
        if _matches_servicenow(haystack) and _connection_status(c) == "Connected":
            out.append(c)
    return out


def pick_connection(conns: list[dict]) -> tuple[dict | None, int]:
    """Choose the connection to bind from active ServiceNow connections.

    Returns ``(chosen, total)``. When more than one candidate exists the most
    recently created wins (ISO-8601 ``createdTime`` sorts correctly as text);
    the total is returned so the caller can report the disambiguation.
    """
    if not conns:
        return None, 0
    ordered = sorted(conns, key=_created_time, reverse=True)
    return ordered[0], len(conns)


# ─────────────────────────────────────────────────────────────────────
# Orchestration.
# ─────────────────────────────────────────────────────────────────────


def _load_env_url(cli_value: str | None) -> str | None:
    if cli_value:
        return cli_value
    config_path = os.path.join(".local", "config.json")
    if os.path.exists(config_path):
        with open(config_path, encoding="utf-8") as f:
            return json.load(f).get("dataverseEndpoint")
    return None


def _discover_connections(env_url: str, dv_token: str, environment_id: str | None):
    """List the environment's connections via the BAP admin API. Returns a list
    (possibly empty). Raises on auth/lookup failure so ``main`` can report it."""
    from auth import discover_tenant
    from flightcheck.pp_admin_client import PPAdminClient, derive_environment_id

    tenant_id = discover_tenant(env_url)
    pp = PPAdminClient(tenant_id)
    pp.authenticate()
    env_id = environment_id or derive_environment_id(env_url, dv_token, pp_admin=pp)
    if not env_id:
        raise RuntimeError("Could not resolve the Power Platform environment id.")
    conns = pp.get_connections(env_id)
    if isinstance(conns, dict) and "_error" in conns:
        raise RuntimeError(f"Unable to list connections: {conns['_error']}")
    return conns if isinstance(conns, list) else []


def run(env_url: str, *, environment_id: str | None, dry_run: bool) -> dict:
    """Resolve and (unless ``dry_run``) perform the ServiceNow bind.

    Returns a result dict with an ``action`` in {``already_bound``,
    ``bound``, ``would_bind``, ``no_reference``, ``no_connection``} and an
    ``exit_code``.
    """
    dv_token = auth.authenticate(env_url)
    rows = auth.query_all(env_url, dv_token, _REF_ENTITY, _REF_SELECT)
    sn_refs = select_servicenow_refs(rows)

    if not sn_refs:
        return {
            "action": "no_reference",
            "exit_code": 3,
            "message": (
                "No ServiceNow connection reference found in this environment. "
                "Install the ServiceNow extension pack first (S6.1), then re-run."
            ),
        }

    already = [r for r in sn_refs if ref_is_bound(r)]
    unbound = [r for r in sn_refs if not ref_is_bound(r)]

    if not unbound:
        ref = already[0]
        return {
            "action": "already_bound",
            "exit_code": 0,
            "reference": ref.get("connectionreferencelogicalname"),
            "connection_id": ref.get("connectionid"),
            "message": "ServiceNow connection reference is already bound and active.",
        }

    target = unbound[0]
    ref_name = target.get("connectionreferencelogicalname")
    ref_id = target.get("connectionreferenceid")

    # 1) Sibling reuse.
    source = "sibling reference"
    chosen_id = sibling_connection_id(sn_refs, target)
    owner = None
    total = None

    # 2) BAP discovery.
    if not chosen_id:
        conns = _discover_connections(env_url, dv_token, environment_id)
        candidates = filter_servicenow_connections(conns)
        chosen, total = pick_connection(candidates)
        if chosen is None:
            return {
                "action": "no_connection",
                "exit_code": 4,
                "reference": ref_name,
                "message": (
                    "No active ServiceNow connection exists to bind. Create the "
                    "ServiceNow connection in Copilot Studio (Connections), then "
                    "re-run this step."
                ),
            }
        chosen_id = chosen.get("name")
        owner = _connection_owner(chosen)
        source = "environment connection"

    result = {
        "action": "would_bind" if dry_run else "bound",
        "exit_code": 0,
        "reference": ref_name,
        "connection_id": chosen_id,
        "source": source,
        "owner": owner,
        "candidate_count": total,
    }

    note = ""
    if total and total > 1:
        note = (
            f" ({total} active ServiceNow connections found — bound the most "
            f"recently created one, owner {owner})"
        )
    result["message"] = (
        f"{'Would bind' if dry_run else 'Bound'} ServiceNow connection reference "
        f"'{ref_name}' to connection {chosen_id} (via {source}){note}."
    )

    if not dry_run:
        auth.update_record(
            env_url, dv_token, _REF_ENTITY, ref_id, {"connectionid": chosen_id}
        )

    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Auto-bind the ServiceNow connection reference."
    )
    parser.add_argument("--connector", default="servicenow",
                        choices=["servicenow"],
                        help="Connector to bind (only 'servicenow' is supported).")
    parser.add_argument("--env-url", default=None,
                        help="Dataverse environment URL (default: .local/config.json).")
    parser.add_argument("--environment-id", default=None,
                        help="Power Platform environment id (default: derived).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be bound without writing.")
    parser.add_argument("--json", action="store_true",
                        help="Emit the result as JSON.")
    args = parser.parse_args(argv)

    env_url = _load_env_url(args.env_url)
    if not env_url:
        print("ERROR: No Dataverse environment URL (.local/config.json missing "
              "dataverseEndpoint, and --env-url not given).")
        return 1

    try:
        result = run(env_url, environment_id=args.environment_id,
                     dry_run=args.dry_run)
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
