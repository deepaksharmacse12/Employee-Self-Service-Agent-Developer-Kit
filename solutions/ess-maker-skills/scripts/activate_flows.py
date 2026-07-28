# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
Turn on (activate) the ServiceNow cloud flows an extension pack installed.

Setup **action** (it mutates Dataverse) invoked by the ServiceNow setup
orchestrator's "turn on flows" step (``install-servicenow-extension-pack.md``
P6.5) before it falls back to manual "go turn them on in Power Automate"
instructions.

The problem it solves: installing an extension pack lands its cloud flows in
**Draft** (``statecode == 0``). Copilot Studio will not invoke a draft flow, so
the maker is told to open Power Platform → Cloud flows and switch each one on by
hand. Activating a flow is a single Dataverse write setting
``workflows.statecode = 1`` / ``workflows.statuscode = 2`` (the same write
``push.py`` performs when it activates a created flow). This script performs that
write automatically for every ServiceNow flow that is not already on.

Ordering matters: a cloud flow can only hold activation once its connection
references are bound, so this step must run **after** ``bind_connections.py`` and
**before** ``connect_and_share.py`` (the invoker binding lands on the final,
activated flow definition and will not go stale).

Scope: **ServiceNow only.** Flows are matched by display name containing
``ServiceNow`` (the same pattern the ``SN-FLOW-*`` FlightCheck uses); other
connectors' flows are left untouched.

Exit codes (consumed by the playbook):
  0  every ServiceNow flow is on now, or was already on — proceed to connect.
  3  no ServiceNow cloud flow found (extension pack not installed / not landed).
  1  unexpected error.

Usage:
    python scripts/activate_flows.py [--connector servicenow]
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
import connect_state  # noqa: E402

# ServiceNow cloud flows carry "ServiceNow" in their display name (e.g.
# "ESS HR ServiceNow HRSD Common Orchestrator"). This mirrors
# flightcheck.checks.external_systems.SERVICENOW_PATTERNS.
_SERVICENOW_FLOW_KEYWORDS = ("servicenow", "service-now")

_WF_ENTITY = "workflows"
_WF_SELECT = "workflowid,name,category,type,statecode,statuscode"
# category 5 = Modern (cloud) flow; type 1 = Definition (not an activation copy).
_WF_FILTER = "category eq 5 and type eq 1"

# Activated = statecode 1 (Activated) + statuscode 2 (Activated).
_ACTIVE_STATECODE = 1
_ACTIVE_STATUSCODE = 2


# ─────────────────────────────────────────────────────────────────────
# Pure helpers (no I/O — unit-testable).
# ─────────────────────────────────────────────────────────────────────


def _matches_servicenow(text: str) -> bool:
    low = (text or "").lower()
    return any(kw in low for kw in _SERVICENOW_FLOW_KEYWORDS)


def select_servicenow_flows(rows: list[dict]) -> list[dict]:
    """Return the cloud-flow rows whose display name is ServiceNow's."""
    return [r for r in rows if _matches_servicenow(r.get("name", ""))]


def flow_is_activated(flow: dict) -> bool:
    """A flow is on when ``statecode == 1`` and ``statuscode == 2``."""
    return (
        flow.get("statecode") == _ACTIVE_STATECODE
        and flow.get("statuscode") == _ACTIVE_STATUSCODE
    )


def flows_to_activate(flows: list[dict]) -> list[dict]:
    """Return the ServiceNow flows that are not already activated."""
    return [f for f in flows if not flow_is_activated(f)]


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


def run(env_url: str, *, environment_id: str | None, dry_run: bool) -> dict:
    """Resolve and (unless ``dry_run``) activate every off ServiceNow flow.

    Returns a result dict with an ``action`` in {``already_on``, ``activated``,
    ``would_activate``, ``no_flows``} and an ``exit_code``.
    """
    dv_token = auth.authenticate(env_url)
    rows = auth.query_all(env_url, dv_token, _WF_ENTITY, _WF_SELECT,
                          filter_expr=_WF_FILTER)
    sn_flows = select_servicenow_flows(rows)

    if not sn_flows:
        return {
            "action": "no_flows",
            "exit_code": 3,
            "connector": "servicenow",
            "message": (
                "No ServiceNow cloud flow found in this environment. Install the "
                "ServiceNow extension pack first (S6.1), then re-run."
            ),
        }

    pending = flows_to_activate(sn_flows)
    if not pending:
        return {
            "action": "already_on",
            "exit_code": 0,
            "connector": "servicenow",
            "flow_count": len(sn_flows),
            "activated_flows": [],
            "message": (
                f"All {len(sn_flows)} ServiceNow cloud flow(s) are already on."
            ),
        }

    activated_names: list[str] = []
    for flow in pending:
        if not dry_run:
            auth.update_record(
                env_url, dv_token, _WF_ENTITY, flow.get("workflowid"),
                {"statecode": _ACTIVE_STATECODE, "statuscode": _ACTIVE_STATUSCODE},
            )
        activated_names.append(flow.get("name"))

    verb = "Would turn on" if dry_run else "Turned on"
    return {
        "action": "would_activate" if dry_run else "activated",
        "exit_code": 0,
        "connector": "servicenow",
        "flow_count": len(sn_flows),
        "activated_flows": activated_names,
        "message": (
            f"{verb} {len(activated_names)} of {len(sn_flows)} ServiceNow cloud "
            f"flow(s): {', '.join(activated_names)}."
        ),
    }


def _persist_activate_state(args, result: dict) -> None:
    """On confirmed flow activation, record ``flows`` + ``S6.3`` (never raises)."""
    try:
        if args.dry_run or result.get("exit_code") != 0:
            return
        if result.get("action") not in ("activated", "already_on"):
            return
        connect_state.merge("servicenow", {"flows": {"state": "enabled"}})
        connect_state.record_setup_step(
            "servicenow", "S6.3", "SN-FLOW-000..004",
            note="Turn on the background cloud flows that carry requests between "
                 "the agent and ServiceNow.")
    except Exception:  # noqa: BLE001 — persistence must never change exit code
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Turn on the ServiceNow cloud flows an extension pack installed."
    )
    parser.add_argument("--connector", default="servicenow",
                        choices=["servicenow"],
                        help="Connector whose flows to turn on (only 'servicenow').")
    parser.add_argument("--env-url", default=None,
                        help="Dataverse environment URL (default: .local/config.json).")
    parser.add_argument("--environment-id", default=None,
                        help="Power Platform environment id (accepted for parity; "
                             "activation uses the Dataverse URL).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report which flows would be turned on without writing.")
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

    _persist_activate_state(args, result)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result.get("message", result.get("action", "")))

    return int(result.get("exit_code", 1))


if __name__ == "__main__":
    sys.exit(main())
