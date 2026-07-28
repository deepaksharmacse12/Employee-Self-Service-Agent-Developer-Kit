# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
Install an ESS extension pack (application package) into the environment.

Setup **action** (it mutates the Power Platform environment). It performs the
install the Copilot Studio "Customize" gallery / AppSource performs when a maker
clicks **Install**, but headlessly via the Power Platform Application Management
(``appmanagement``) API — no ``pac`` CLI, no browser gallery. It closes the
``SN-PKG-001`` / ``WD-PKG-001`` gap where installing the extension pack was the
only manual step.

Flow (validated live against the appmanagement API):

  1. **List** the environment's application packages
     (``GET .../applicationPackages``) to read each pack's ``state``.
  2. **Guard** — the target extension pack depends on its parent ESS solution
     (HR: ``msdyn_CopilotForEmployeeSelfServiceHR``); if the parent is not
     ``Installed`` we stop rather than trigger a doomed install.
  3. **Idempotency** — if the target's ``state`` is already ``Installed`` we do
     nothing (the install POST is NOT idempotent server-side — re-posting starts
     a fresh operation — so this check is load-bearing).
  4. **Install** — ``POST .../applicationPackages/{uniqueName}/install`` returns
     ``202`` with an ``operationId``.
  5. **Poll** — ``GET .../operations/{operationId}`` until a terminal status
     (``Succeeded`` / ``Failed`` / ``Canceled``). Polling emits a heartbeat to
     **stderr** each interval so a multi-minute install is not mistaken for a
     hang (stdout stays a clean ``--json`` document).

Package selection **fails closed**: only products explicitly selected in the
config ``scope`` (``hrsd`` / ``itsm``) are installed. An empty/all-false scope
installs NOTHING (exit 4, ``no_targets``) rather than silently installing every
pack the maker did not request.

**Fire-and-poll modes** (so a caller can animate a long install instead of
blocking on one silent call):

  * ``--start`` fires the install(s) and prints the ``operationId``(s) without
    polling. Installs continue server-side.
  * ``--status --operation-id <id>`` polls one operation once and reports
    ``status`` / ``terminal`` / ``succeeded`` / ``percentComplete``. Exit 0 while
    running or succeeded, exit 6 on a terminal non-success — the caller loops on
    the JSON ``terminal``/``succeeded`` flags, posting a chat update each cycle.

  Omitting both keeps the original **blocking** behavior (POST then poll to
  terminal, with a stderr heartbeat).

Authentication uses the Power Platform CLI ("pac") public client via
``pp_env_client`` (interactive browser sign-in on the action path, cached
thereafter). The kit's default Azure CLI client is not pre-authorized for the
``https://api.powerplatform.com`` resource.

Exit codes:
  0  installed (now or already).
  3  the parent ESS solution is not installed (install it first).
  4  the target package is not found / not entitled, or no product is selected
     in scope (nothing to install).
  5  could not resolve the Power Platform environment id.
  6  the install operation failed, was canceled, or timed out.
  1  unexpected error.

Usage:
    python scripts/install_extension_pack.py [--connector servicenow]
        [--package UNIQUE_NAME ...] [--env-url URL] [--environment-id ID]
        [--timeout SECONDS] [--dry-run] [--json]
        [--start | --status --operation-id ID]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

import auth  # noqa: E402
import connect_state  # noqa: E402
from connect_and_share import bot_schema  # noqa: E402
from pp_env_client import (  # noqa: E402
    PPEnvClient,
    find_application_package,
    operation_is_terminal,
    operation_succeeded,
    package_is_installed,
)

# Parent ESS solution each persona's extension packs depend on.
PARENT_BY_PERSONA = {
    "hr": "msdyn_CopilotForEmployeeSelfServiceHR",
    "it": "msdyn_CopilotForEmployeeSelfServiceIT",
}

# ServiceNow extension pack unique names, keyed by persona then product.
# (Future work: promote this to a data-driven catalog per parent solution.)
SERVICENOW_PACK_CATALOG = {
    "hr": {
        "hrsd": "msdyn_EssHRServiceNowHRSD",
        "itsm": "msdyn_EssHRServiceNowITSM",
    },
    "it": {
        "hrsd": "msdyn_EssITServiceNowHRSD",
        "itsm": "msdyn_EssITServiceNowITSM",
    },
}

_POLL_INTERVAL_SECONDS = 15


def _progress(msg: str) -> None:
    """Emit a progress/heartbeat line to stderr.

    Install operations are long-running and polled silently; without a heartbeat
    the run is indistinguishable from a hang and gets killed prematurely. Progress
    goes to **stderr** so ``--json`` stdout stays a single clean document.
    """
    print(msg, file=sys.stderr, flush=True)


# ─────────────────────────────────────────────────────────────────────
# Pure helpers (no I/O — unit-testable).
# ─────────────────────────────────────────────────────────────────────
def resolve_persona(schema: str | None) -> str | None:
    """Derive the ESS persona ("hr"/"it") from the agent's Dataverse schema.

    ``msdyn_copilotforemployeeselfservicehr`` -> ``hr``
    ``msdyn_copilotforemployeeselfserviceit`` -> ``it``
    """
    low = (schema or "").lower()
    if low.endswith("hr"):
        return "hr"
    if low.endswith("it"):
        return "it"
    return None


def servicenow_packages(persona: str | None, scope: dict | None) -> list[str]:
    """Resolve the ServiceNow extension pack unique names for a persona + scope.

    ``scope`` is the config ``scope`` object (``{"hrsd": bool, "itsm": bool}``).
    Only products explicitly selected (truthy) in ``scope`` are targeted. This
    fails **closed**: if no product is selected (empty/all-false scope), NOTHING
    is targeted — installing every pack when the maker picked none would silently
    install products they never requested. Callers turn an empty result into a
    ``no_targets`` stop.
    """
    catalog = SERVICENOW_PACK_CATALOG.get(persona or "", {})
    scope = scope or {}
    return [
        unique_name
        for product, unique_name in catalog.items()
        if scope.get(product)
    ]


def parent_package(persona: str | None) -> str | None:
    """Return the parent ESS solution unique name for a persona."""
    return PARENT_BY_PERSONA.get(persona or "")


# ─────────────────────────────────────────────────────────────────────
# Orchestration.
# ─────────────────────────────────────────────────────────────────────
def _read_json(path: str) -> dict:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _load_config() -> dict:
    """Load the installer's effective config.

    The root ``.local/config.json`` is the ESS-package config: it provides the
    Dataverse endpoint and the agent schema used to derive the persona (hr/it).
    Product ``scope`` (``hrsd`` / ``itsm``), however, is **ServiceNow-specific**
    and is captured by setup skill 3 (S3.1) into the ServiceNow connect config
    ``.local/connect/servicenow/config.json`` — not the root ESS config. Read
    scope from there and overlay it, so an automated install run (S6.1) resolves
    the products the maker actually selected. A root-level ``scope`` is honored
    only as a back-compat fallback when the ServiceNow config has none.
    """
    config = _read_json(os.path.join(".local", "config.json"))
    sn_config = _read_json(
        os.path.join(".local", "connect", "servicenow", "config.json")
    )
    sn_scope = sn_config.get("scope")
    if sn_scope is not None:
        config["scope"] = sn_scope
    return config


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


def _percent(operation: dict) -> int | None:
    """Best-effort percent-complete from an operation body (``None`` if absent)."""
    for key in ("percentComplete", "percentageComplete", "progress"):
        val = operation.get(key)
        if isinstance(val, (int, float)):
            return int(val)
    return None


def _precheck(packages, unique_name, parent) -> dict | None:
    """Return an early per-package result, or ``None`` when an install is needed.

    Shared by the blocking (``_install_one``) and fire-and-poll (``_start_one``)
    paths so both apply the same not-found / already-installed / parent-missing
    guards before any non-idempotent install POST.
    """
    pkg = find_application_package(packages, unique_name)
    if pkg is None:
        return {
            "package": unique_name, "action": "not_found", "exit_code": 4,
            "message": (
                f"Package '{unique_name}' is not available in this environment "
                "(not entitled, or wrong unique name)."
            ),
        }
    if package_is_installed(pkg):
        return {"package": unique_name, "action": "already_installed", "exit_code": 0}
    if parent is not None:
        parent_pkg = find_application_package(packages, parent)
        if not package_is_installed(parent_pkg):
            return {
                "package": unique_name, "action": "parent_missing", "exit_code": 3,
                "message": (
                    f"The parent ESS solution '{parent}' is not installed; "
                    "install it before the extension pack."
                ),
            }
    return None


def _start_one(client, packages, unique_name, parent) -> dict:
    """Fire a single install and return its operation id **without polling**.

    The fire-and-poll counterpart to :func:`_install_one`: the caller (the
    playbook) polls each returned ``operation_id`` via ``--status`` so it can post
    a chat update every cycle instead of blocking on one long call.
    """
    early = _precheck(packages, unique_name, parent)
    if early is not None:
        return early
    resp = client.install_application_package(unique_name)
    operation_id = resp.get("operation_id")
    if not operation_id:
        return {
            "package": unique_name, "action": "no_operation", "exit_code": 6,
            "message": (
                f"Install POST for '{unique_name}' returned "
                f"{resp.get('status_code')} without an operation id."
            ),
        }
    return {
        "package": unique_name, "action": "started", "exit_code": 0,
        "operation_id": operation_id,
    }


def _poll_install(
    client: PPEnvClient,
    operation_id: str,
    timeout: int,
    *,
    progress=None,
) -> dict:
    """Poll an install operation until terminal or ``timeout`` seconds elapse.

    ``progress`` is an optional callback invoked each poll with a heartbeat
    string so the caller can show the operation is still running.
    """
    start = time.monotonic()
    deadline = start + timeout
    last = {"status": None}
    while time.monotonic() < deadline:
        last = client.get_operation(operation_id)
        status = last.get("status")
        if operation_is_terminal(status):
            return last
        if progress is not None:
            elapsed = int(time.monotonic() - start)
            progress(
                f"  ...still installing (status: {status or 'Running'}; "
                f"{elapsed}s elapsed, waiting up to {timeout}s)"
            )
        time.sleep(_POLL_INTERVAL_SECONDS)
    return {"status": "Timeout", "statusMessage": last.get("statusMessage")}


def _install_one(client, packages, unique_name, parent, *, dry_run, timeout,
                 progress=None):
    """Install a single package (blocking: POST then poll to terminal)."""
    early = _precheck(packages, unique_name, parent)
    if early is not None:
        return early

    if dry_run:
        return {"package": unique_name, "action": "would_install", "exit_code": 0}

    resp = client.install_application_package(unique_name)
    operation_id = resp.get("operation_id")
    if not operation_id:
        return {
            "package": unique_name, "action": "no_operation", "exit_code": 6,
            "message": (
                f"Install POST for '{unique_name}' returned "
                f"{resp.get('status_code')} without an operation id."
            ),
        }
    if progress is not None:
        progress(
            f"[install] {unique_name}: operation {operation_id} started; "
            f"polling every {_POLL_INTERVAL_SECONDS}s (up to {timeout}s). "
            "This can take several minutes — the install continues on the "
            "server even if this run is interrupted."
        )
    final = _poll_install(client, operation_id, timeout, progress=progress)
    status = final.get("status")
    if operation_succeeded(status):
        if progress is not None:
            progress(f"[install] {unique_name}: completed (status: {status}).")
        return {
            "package": unique_name, "action": "installed", "exit_code": 0,
            "operation_id": operation_id,
        }
    return {
        "package": unique_name, "action": "install_failed", "exit_code": 6,
        "operation_id": operation_id, "status": status,
        "message": (
            f"Install of '{unique_name}' did not succeed (status: {status}"
            + (f"; {final.get('statusMessage')}" if final.get("statusMessage") else "")
            + ")."
        ),
    }


def _resolve_targets(config: dict, packages: list[str] | None):
    """Resolve install targets.

    Returns ``(targets, parent, persona)`` on success, or a ``no_targets`` result
    dict when nothing is selected. Shared by the blocking and fire-and-poll paths.
    """
    persona = resolve_persona(bot_schema(config))
    targets = list(packages or [])
    parent = None
    if not targets:
        targets = servicenow_packages(persona, config.get("scope"))
        parent = parent_package(persona)
        if not targets:
            scope = config.get("scope") or {}
            selected = [p for p in ("hrsd", "itsm") if scope.get(p)]
            return {
                "action": "no_targets", "exit_code": 4,
                "message": (
                    "No extension packs resolved — nothing to install. "
                    + (
                        f"Persona '{persona}' could not be derived from the agent "
                        "schema."
                        if not persona else
                        "No ServiceNow product is selected in config scope "
                        f"(hrsd/itsm both off; selected={selected})."
                    )
                    + " Select a product in "
                    ".local/connect/servicenow/config.json "
                    "(set scope.hrsd or scope.itsm) or pass "
                    "--package <uniqueName>. Nothing was installed."
                ),
            }
    return targets, parent, persona


def _connect_client(env_url, environment_id, *, interactive):
    """Authenticate and return ``(client, None)`` or ``(None, early_result)``."""
    dv_token = auth.authenticate(env_url)
    from auth import discover_tenant

    tenant_id = discover_tenant(env_url)
    env_id = _resolve_environment_id(env_url, dv_token, environment_id, tenant_id)
    if not env_id:
        return None, {
            "action": "no_environment", "exit_code": 5,
            "message": (
                "Could not resolve the Power Platform environment id. Pass "
                "--environment-id <guid>."
            ),
        }
    client = PPEnvClient(tenant_id, env_id)
    if not client.authenticate(interactive=interactive):
        return None, {
            "action": "error", "exit_code": 1,
            "message": "Power Platform sign-in failed; cannot reach the install API.",
        }
    return client, None


def run(
    env_url: str,
    *,
    config: dict,
    environment_id: str | None,
    packages: list[str] | None,
    timeout: int,
    dry_run: bool,
) -> dict:
    """Install the resolved extension pack(s) into the environment (blocking)."""
    resolved = _resolve_targets(config, packages)
    if isinstance(resolved, dict):
        return resolved
    targets, parent, persona = resolved

    dv_token = auth.authenticate(env_url)
    from auth import discover_tenant

    tenant_id = discover_tenant(env_url)
    env_id = _resolve_environment_id(env_url, dv_token, environment_id, tenant_id)
    if not env_id:
        return {
            "action": "no_environment", "exit_code": 5,
            "message": (
                "Could not resolve the Power Platform environment id. Pass "
                "--environment-id <guid>."
            ),
        }

    client = PPEnvClient(tenant_id, env_id)
    if not client.authenticate(interactive=not dry_run):
        if dry_run:
            return {
                "action": "would_install", "exit_code": 0, "targets": targets,
                "message": (
                    "Would install "
                    f"{', '.join(targets)} (dry-run: no cached Power Platform "
                    "token, live state not inspected)."
                ),
            }
        return {
            "action": "error", "exit_code": 1,
            "message": "Power Platform sign-in failed; cannot install.",
        }

    packages_list = client.list_application_packages()
    emit = None if dry_run else _progress
    results = [
        _install_one(client, packages_list, name, parent,
                     dry_run=dry_run, timeout=timeout, progress=emit)
        for name in targets
    ]
    exit_code = next((r["exit_code"] for r in results if r["exit_code"] != 0), 0)
    installed = [r["package"] for r in results if r["action"] in
                 ("installed", "already_installed", "would_install")]
    verb = "Would install" if dry_run else "Installed"
    summary = (
        f"{verb}: {', '.join(installed)}." if installed and exit_code == 0
        else next((r.get("message") for r in results if r["exit_code"] != 0),
                  "No changes.")
    )
    return {
        "action": "install", "exit_code": exit_code, "persona": persona,
        "results": results, "message": summary,
    }


def run_start(
    env_url: str,
    *,
    config: dict,
    environment_id: str | None,
    packages: list[str] | None,
    timeout: int,
) -> dict:
    """Fire the install(s) and return operation id(s) **without polling**.

    The fire half of the fire-and-poll pattern: the playbook calls this once, then
    polls each returned ``operation_id`` with ``--status`` so it can post a live
    progress update every cycle instead of blocking on one multi-minute call.
    """
    resolved = _resolve_targets(config, packages)
    if isinstance(resolved, dict):
        return resolved
    targets, parent, persona = resolved

    client, early = _connect_client(env_url, environment_id, interactive=True)
    if early is not None:
        return early

    packages_list = client.list_application_packages()
    results = [_start_one(client, packages_list, name, parent) for name in targets]
    exit_code = next((r["exit_code"] for r in results if r["exit_code"] != 0), 0)
    operations = [
        {"package": r["package"], "operation_id": r["operation_id"]}
        for r in results if r["action"] == "started"
    ]
    already = [r["package"] for r in results if r["action"] == "already_installed"]
    if exit_code != 0:
        summary = next(r.get("message") for r in results if r["exit_code"] != 0)
    elif operations:
        names = ", ".join(o["package"] for o in operations)
        summary = (
            f"Started install of {names}. Poll each operation with "
            "`--status --operation-id <id>` (installs continue server-side)."
        )
    else:
        summary = f"Already installed: {', '.join(already)}." if already else "Nothing to install."
    return {
        "action": "start", "exit_code": exit_code, "persona": persona,
        "results": results, "operations": operations,
        "timeout_hint": timeout, "message": summary,
    }


def run_status(
    env_url: str,
    *,
    config: dict,
    environment_id: str | None,
    operation_id: str,
) -> dict:
    """Poll a single install operation once and report its state.

    The poll half of the fire-and-poll pattern. Exit code is 0 while the operation
    is running or has succeeded, and 6 if it reached a terminal non-success state,
    so the playbook can loop on the ``terminal`` / ``succeeded`` flags in the JSON.
    """
    client, early = _connect_client(env_url, environment_id, interactive=True)
    if early is not None:
        return early

    operation = client.get_operation(operation_id)
    status = operation.get("status")
    terminal = operation_is_terminal(status)
    succeeded = operation_succeeded(status)
    pct = _percent(operation)
    if terminal and not succeeded:
        exit_code, action = 6, "failed"
    elif terminal:
        exit_code, action = 0, "succeeded"
    else:
        exit_code, action = 0, "running"
    pct_txt = f" ({pct}%)" if pct is not None else ""
    detail = operation.get("statusMessage")
    message = (
        f"Operation {operation_id}: {status or 'Running'}{pct_txt}"
        + (f" — {detail}" if detail else "")
    )
    return {
        "action": action, "exit_code": exit_code, "operation_id": operation_id,
        "status": status, "terminal": terminal, "succeeded": succeeded,
        "percentComplete": pct, "statusMessage": detail, "message": message,
    }


def _persist_install_state(config: dict, args, result: dict) -> None:
    """On confirmed install success, record ``packs`` + ``S6.1`` (never raises).

    Fires for a completed blocking run (``action == "install"``) or a
    fire-and-poll poll that reached success (``action == "succeeded"``). Skips
    dry-runs, the fire-only ``--start`` step, and a still-running poll.
    """
    try:
        if result.get("exit_code") != 0 or args.dry_run or args.start:
            return
        if result.get("action") not in ("install", "succeeded"):
            return
        scope = config.get("scope") or {}
        products = [p for p in ("hrsd", "itsm") if scope.get(p)]
        if not products:
            return
        connect_state.record_packs("servicenow", products, "installed")
        connect_state.record_setup_step(
            "servicenow", "S6.1", "SN-001",
            note="Install the ServiceNow extension pack(s) for the in-scope "
                 "products (HRSD / ITSM) into the agent.")
    except Exception:  # noqa: BLE001 — persistence must never change exit code
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install an ESS extension pack (application package) "
        "headlessly via the Power Platform appmanagement API."
    )
    parser.add_argument(
        "--connector", default="servicenow", choices=["servicenow"],
        help="Connector whose extension packs to install (default: servicenow).",
    )
    parser.add_argument(
        "--package", action="append", default=None, dest="packages",
        metavar="UNIQUE_NAME",
        help="Explicit package unique name to install (repeatable). Overrides "
        "the connector/scope-based resolution.",
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
        "--timeout", type=int, default=600,
        help="Seconds to wait for each install operation (default: 600).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report what would be installed without writing.",
    )
    parser.add_argument(
        "--start", action="store_true",
        help="Fire the install(s) and print operation id(s) without polling "
        "(fire-and-poll mode; pair with --status).",
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Poll a single install operation once (requires --operation-id).",
    )
    parser.add_argument(
        "--operation-id", default=None,
        help="Install operation id to poll (with --status).",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    args = parser.parse_args(argv)

    if args.start and args.status:
        print("ERROR: --start and --status are mutually exclusive.")
        return 1
    if args.status and not args.operation_id:
        print("ERROR: --status requires --operation-id.")
        return 1
    if (args.start or args.status) and args.dry_run:
        print("ERROR: --dry-run cannot be combined with --start/--status.")
        return 1

    config = _load_config()
    env_url = _load_env_url(args.env_url, config)
    if not env_url:
        print("ERROR: No Dataverse environment URL (.local/config.json missing "
              "dataverseEndpoint, and --env-url not given).")
        return 1

    try:
        if args.status:
            result = run_status(
                env_url,
                config=config,
                environment_id=args.environment_id,
                operation_id=args.operation_id,
            )
        elif args.start:
            result = run_start(
                env_url,
                config=config,
                environment_id=args.environment_id,
                packages=args.packages,
                timeout=args.timeout,
            )
        else:
            result = run(
                env_url,
                config=config,
                environment_id=args.environment_id,
                packages=args.packages,
                timeout=args.timeout,
                dry_run=args.dry_run,
            )
    except Exception as e:  # noqa: BLE001 — surface a clean message, no stack
        result = {"action": "error", "exit_code": 1,
                  "message": f"{type(e).__name__}: {e}"}

    _persist_install_state(config, args, result)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result.get("message", result.get("action", "")))

    return int(result.get("exit_code", 1))


if __name__ == "__main__":
    sys.exit(main())
