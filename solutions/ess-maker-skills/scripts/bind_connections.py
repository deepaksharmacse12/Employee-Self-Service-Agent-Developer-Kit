# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
Auto-bind extension-pack connection references to existing connections.

Setup **action** (it mutates Dataverse) invoked by the ServiceNow setup
orchestrator's S6.2 step (``install-servicenow-extension-pack.md`` P6.3) before
it falls back to manual "go bind it in Copilot Studio" instructions.

The problem it solves: installing an extension pack **creates** connection
references but leaves them unbound (``connectionid == null``). A maker who
created the connection during installation (the interactive Entra consent
already happened) is still told to manually wire it to the extension pack's
connection *reference*. Binding a reference to an already-created connection is
**not** an OAuth flow — it is a single Dataverse write setting
``connectionreferences.connectionid`` to the connection's id (the same field
``push.py`` writes when it mirrors a flow-scoped connref). This script performs
that write automatically for every unbound reference on the target connector.

Supported connectors (``--connector``):
  * ``servicenow`` — the ServiceNow HR/ITSM connection reference.
  * ``dataverse``  — the Microsoft Dataverse connection reference(s) a pack
    creates (there are usually two). Reuses an existing ``Connected`` Dataverse
    connection; it does **not** mint a new one.
  * ``all``        — bind every supported connector in one pass.

Resolution order for the connection to bind (per connector):
  1. **Sibling reuse** — another reference on the same connector that is already
     bound to an active connection; reuse its ``connectionid`` (safest — same
     maker, proven-active connection, no guessing).
  2. **BAP discovery** — list the environment's connections, keep the ones on
     the target connector that are ``Connected``. Zero → cannot auto-bind
     (the maker must create a connection first); exactly one → bind it; more
     than one → bind the **most recently created** and report which was chosen
     (and its owner) so the maker can veto.

Every unbound reference on the connector is bound to the resolved connection.

Exit codes (consumed by the playbook):
  0  bound now, or already bound — proceed to verify (SN-CONN-001 / DV-CONN-001).
  3  no connection reference found for the connector (extension pack not installed).
  4  no active connection exists for the connector — fall back to manual create.
  1  unexpected error.

Usage:
    python scripts/bind_connections.py [--connector servicenow|dataverse|all]
                                       [--env-url URL] [--environment-id ID]
                                       [--dry-run] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import auth  # noqa: E402

# Per-connector matching keywords. A connection reference's ``connectorid`` and
# a BAP connection's ``properties.apiId`` share the ``shared_<connector>`` suffix
# (e.g. ``.../apis/shared_service-now`` or ``.../apis/shared_commondataserviceforapps``).
_CONNECTOR_KEYWORDS = {
    "servicenow": ("service-now", "servicenow"),
    "dataverse": ("commondataserviceforapps",),
}
_CONNECTOR_LABELS = {
    "servicenow": "ServiceNow",
    "dataverse": "Dataverse",
}
# Order used by ``--connector all``.
_ALL_CONNECTORS = ("servicenow", "dataverse")

# Backward-compatible alias for callers/tests that reference the ServiceNow set.
_SERVICENOW_CONNECTOR_KEYWORDS = _CONNECTOR_KEYWORDS["servicenow"]

_REF_ENTITY = "connectionreferences"
_REF_SELECT = (
    "connectionreferenceid,connectionreferencelogicalname,"
    "connectionreferencedisplayname,connectorid,connectionid,statuscode"
)


# ─────────────────────────────────────────────────────────────────────
# Pure helpers (no I/O — unit-testable).
# ─────────────────────────────────────────────────────────────────────


def _matches(text: str, keywords: tuple[str, ...]) -> bool:
    low = (text or "").lower()
    return any(kw in low for kw in keywords)


def _matches_servicenow(text: str) -> bool:
    return _matches(text, _CONNECTOR_KEYWORDS["servicenow"])


def select_refs(rows: list[dict], keywords: tuple[str, ...]) -> list[dict]:
    """Return the connection references whose connector matches ``keywords``."""
    return [r for r in rows if _matches(r.get("connectorid", ""), keywords)]


def select_servicenow_refs(rows: list[dict]) -> list[dict]:
    """Return the connection references whose connector is ServiceNow."""
    return select_refs(rows, _CONNECTOR_KEYWORDS["servicenow"])


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


def filter_connections(conns: list[dict], keywords: tuple[str, ...]) -> list[dict]:
    """Keep only ``Connected`` connections on the connector matching ``keywords``."""
    out = []
    for c in conns:
        props = c.get("properties") or {}
        haystack = f"{props.get('apiId', '')}{props.get('displayName', '')}"
        if _matches(haystack, keywords) and _connection_status(c) == "Connected":
            out.append(c)
    return out


def filter_servicenow_connections(conns: list[dict]) -> list[dict]:
    """Keep only ``Connected`` connections on the ServiceNow connector."""
    return filter_connections(conns, _CONNECTOR_KEYWORDS["servicenow"])


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


def _resolve_connection(
    connector: str, refs: list[dict], env_url: str, dv_token: str,
    environment_id: str | None,
) -> tuple[str | None, str, str | None, int | None, dict | None]:
    """Resolve the connection id to bind the connector's unbound refs to.

    Returns ``(connection_id, source, owner, candidate_total, failure)``. When
    resolution fails, ``connection_id`` is ``None`` and ``failure`` is a result
    dict describing why (``no_connection``); otherwise ``failure`` is ``None``.
    """
    # 1) Sibling reuse — any already-bound reference on this same connector.
    for r in refs:
        if ref_is_bound(r):
            return r.get("connectionid"), "sibling reference", None, None, None

    # 2) BAP discovery.
    conns = _discover_connections(env_url, dv_token, environment_id)
    candidates = filter_connections(conns, _CONNECTOR_KEYWORDS[connector])
    chosen, total = pick_connection(candidates)
    if chosen is None:
        label = _CONNECTOR_LABELS[connector]
        failure = {
            "action": "no_connection",
            "exit_code": 4,
            "connector": connector,
            "message": (
                f"No active {label} connection exists to bind. Create the "
                f"{label} connection in Copilot Studio (Connections), then "
                "re-run this step."
            ),
        }
        return None, "environment connection", None, total, failure
    return chosen.get("name"), "environment connection", _connection_owner(chosen), total, None


def bind_connector(
    connector: str, rows: list[dict], env_url: str, dv_token: str, *,
    environment_id: str | None, dry_run: bool,
) -> dict:
    """Resolve and (unless ``dry_run``) bind every unbound reference on
    ``connector`` to a single resolved connection.

    Returns a result dict with an ``action`` in {``already_bound``, ``bound``,
    ``would_bind``, ``no_reference``, ``no_connection``} and an ``exit_code``.
    """
    keywords = _CONNECTOR_KEYWORDS[connector]
    label = _CONNECTOR_LABELS[connector]
    refs = select_refs(rows, keywords)

    if not refs:
        return {
            "action": "no_reference",
            "exit_code": 3,
            "connector": connector,
            "message": (
                f"No {label} connection reference found in this environment. "
                "Install the extension pack first (S6.1), then re-run."
            ),
        }

    unbound = [r for r in refs if not ref_is_bound(r)]
    if not unbound:
        ref = refs[0]
        return {
            "action": "already_bound",
            "exit_code": 0,
            "connector": connector,
            "reference": ref.get("connectionreferencelogicalname"),
            "connection_id": ref.get("connectionid"),
            "message": f"{label} connection reference(s) already bound and active.",
        }

    chosen_id, source, owner, total, failure = _resolve_connection(
        connector, refs, env_url, dv_token, environment_id
    )
    if failure is not None:
        failure["reference"] = unbound[0].get("connectionreferencelogicalname")
        return failure

    bound_names: list[str] = []
    for ref in unbound:
        if not dry_run:
            auth.update_record(
                env_url, dv_token, _REF_ENTITY,
                ref.get("connectionreferenceid"), {"connectionid": chosen_id},
            )
        bound_names.append(ref.get("connectionreferencelogicalname"))

    note = ""
    if total and total > 1:
        note = (
            f" ({total} active {label} connections found — bound the most "
            f"recently created one, owner {owner})"
        )
    verb = "Would bind" if dry_run else "Bound"
    if len(bound_names) == 1:
        message = (
            f"{verb} {label} connection reference '{bound_names[0]}' to "
            f"connection {chosen_id} (via {source}){note}."
        )
    else:
        message = (
            f"{verb} {len(bound_names)} {label} connection references to "
            f"connection {chosen_id} (via {source}){note}."
        )

    return {
        "action": "would_bind" if dry_run else "bound",
        "exit_code": 0,
        "connector": connector,
        "reference": bound_names[0],
        "connection_id": chosen_id,
        "source": source,
        "owner": owner,
        "candidate_count": total,
        "bound_references": bound_names,
        "message": message,
    }


def _aggregate_exit(results: list[dict]) -> int:
    """Worst-case exit for ``--connector all``: a genuine failure (error/1 or
    no-connection/4) fails the pass; a missing reference (3) for one connector is
    tolerated so binding the other still reports success."""
    codes = [r.get("exit_code", 1) for r in results]
    if any(c == 1 for c in codes):
        return 1
    if any(c == 4 for c in codes):
        return 4
    return 0


def run(env_url: str, *, environment_id: str | None, dry_run: bool,
        connector: str = "servicenow") -> dict:
    """Resolve and (unless ``dry_run``) perform the bind for ``connector``.

    For a single connector, returns that connector's result dict. For ``all``,
    returns an aggregate ``{"action": "multi", "results": [...]}``.
    """
    dv_token = auth.authenticate(env_url)
    rows = auth.query_all(env_url, dv_token, _REF_ENTITY, _REF_SELECT)

    if connector == "all":
        results = [
            bind_connector(c, rows, env_url, dv_token,
                           environment_id=environment_id, dry_run=dry_run)
            for c in _ALL_CONNECTORS
        ]
        return {
            "action": "multi",
            "exit_code": _aggregate_exit(results),
            "results": results,
            "message": " ".join(r.get("message", "") for r in results),
        }

    return bind_connector(connector, rows, env_url, dv_token,
                          environment_id=environment_id, dry_run=dry_run)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Auto-bind extension-pack connection references."
    )
    parser.add_argument("--connector", default="servicenow",
                        choices=["servicenow", "dataverse", "all"],
                        help="Connector(s) to bind (default: servicenow).")
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
                     dry_run=args.dry_run, connector=args.connector)
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
