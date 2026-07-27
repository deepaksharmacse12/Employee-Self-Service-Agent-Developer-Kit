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
     (``Succeeded`` / ``Failed`` / ``Canceled``).

Authentication uses the Power Platform CLI ("pac") public client via
``pp_env_client`` (interactive browser sign-in on the action path, cached
thereafter). The kit's default Azure CLI client is not pre-authorized for the
``https://api.powerplatform.com`` resource.

Exit codes:
  0  installed (now or already).
  3  the parent ESS solution is not installed (install it first).
  4  the target package is not found / not entitled in this environment.
  5  could not resolve the Power Platform environment id.
  6  the install operation failed, was canceled, or timed out.
  1  unexpected error.

Usage:
    python scripts/install_extension_pack.py [--connector servicenow]
        [--package UNIQUE_NAME ...] [--env-url URL] [--environment-id ID]
        [--timeout SECONDS] [--dry-run] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

import auth  # noqa: E402
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
    When ``scope`` is falsy/empty, both products are targeted.
    """
    catalog = SERVICENOW_PACK_CATALOG.get(persona or "", {})
    scope = scope or {}
    want_any = bool(scope.get("hrsd") or scope.get("itsm"))
    packages = []
    for product, unique_name in catalog.items():
        if not want_any or scope.get(product):
            packages.append(unique_name)
    return packages


def parent_package(persona: str | None) -> str | None:
    """Return the parent ESS solution unique name for a persona."""
    return PARENT_BY_PERSONA.get(persona or "")


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


def _poll_install(client: PPEnvClient, operation_id: str, timeout: int) -> dict:
    """Poll an install operation until terminal or ``timeout`` seconds elapse."""
    deadline = time.monotonic() + timeout
    last = {"status": None}
    while time.monotonic() < deadline:
        last = client.get_operation(operation_id)
        status = last.get("status")
        if operation_is_terminal(status):
            return last
        time.sleep(_POLL_INTERVAL_SECONDS)
    return {"status": "Timeout", "statusMessage": last.get("statusMessage")}


def _install_one(client, packages, unique_name, parent, *, dry_run, timeout):
    """Install a single package; return a per-package result dict."""
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
    final = _poll_install(client, operation_id, timeout)
    status = final.get("status")
    if operation_succeeded(status):
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


def run(
    env_url: str,
    *,
    config: dict,
    environment_id: str | None,
    packages: list[str] | None,
    timeout: int,
    dry_run: bool,
) -> dict:
    """Install the resolved extension pack(s) into the environment."""
    schema = bot_schema(config)
    persona = resolve_persona(schema)

    targets = list(packages or [])
    parent = None
    if not targets:
        targets = servicenow_packages(persona, config.get("scope"))
        parent = parent_package(persona)
        if not targets:
            return {
                "action": "no_targets", "exit_code": 4,
                "message": (
                    "No extension packs resolved. Pass --package <uniqueName>, "
                    "or set config scope (hrsd/itsm) and a resolvable persona."
                ),
            }

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
    results = [
        _install_one(client, packages_list, name, parent,
                     dry_run=dry_run, timeout=timeout)
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
            packages=args.packages,
            timeout=args.timeout,
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
