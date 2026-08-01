# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Install an Employee Self-Service app with the Power Platform API."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

from auth import discover_tenant
from flightcheck.powerplatform_client import PowerPlatformClient
from flightcheck.pp_admin_client import PPAdminClient


CATALOG_PATH = (
    Path(__file__).resolve().parents[1] / "src" / "reference"
    / "solution-catalog.md"
)

EXPERIENCE_STATUS = {
    "da": "Active (DA bundle)",
    "cea": "Active (CEA bundle)",
}

VERTICAL_PACKAGE = {
    "hr": "Employee Self-Service HR",
    "it": "Employee Self-Service IT",
}


def load_parent_schemas(catalog_path: Path = CATALOG_PATH) -> dict[tuple[str, str], str]:
    """Read DA/CEA and HR/IT parent schema mappings from the solution catalog."""
    mappings: dict[tuple[str, str], str] = {}
    in_parents = False

    for line in catalog_path.read_text(encoding="utf-8").splitlines():
        if line == "## Parents":
            in_parents = True
            continue
        if in_parents and line.startswith("## "):
            break
        if not in_parents or not line.startswith("|"):
            continue

        columns = [column.strip() for column in line.strip("|").split("|")]
        if len(columns) != 5 or not columns[0].isdigit():
            continue

        package = columns[1]
        schema = columns[2].strip("`")
        status = columns[3]

        experience = next(
            (
                key for key, expected_status in EXPERIENCE_STATUS.items()
                if status == expected_status
            ),
            None,
        )
        vertical = next(
            (
                key for key, expected_package in VERTICAL_PACKAGE.items()
                if package == expected_package
            ),
            None,
        )
        if experience and vertical:
            mappings[(experience, vertical)] = schema

    expected = {
        (experience, vertical)
        for experience in EXPERIENCE_STATUS
        for vertical in VERTICAL_PACKAGE
    }
    missing = expected - mappings.keys()
    if missing:
        missing_labels = ", ".join(
            f"{experience.upper()}/{vertical.upper()}"
            for experience, vertical in sorted(missing)
        )
        raise ValueError(
            f"Solution catalog is missing parent schema mappings: {missing_labels}"
        )

    return mappings


INSTALLED_STATES = {"Installed", "TemplateInstalled"}
IN_PROGRESS_STATES = {
    "InstallRequested",
    "Installing",
    "InstallScheduled",
    "InstallRetrying",
}
FAILED_STATES = {"InstallFailed"}


def _find_package(packages: list[dict], schema_name: str) -> dict | None:
    """Find the entitled application whose unique name matches the catalog."""
    target = schema_name.casefold()
    for package in packages:
        names = (
            package.get("uniqueName"),
            package.get("applicationName"),
        )
        if any(
            isinstance(name, str) and name.casefold() == target
            for name in names
        ):
            return package
    return None


def _error_message(response: dict, default: str) -> str:
    """Return a concise API error without dumping the full response."""
    error = (
        response.get("error")
        or response.get("errorDetails")
        or response.get("lastError")
        or {}
    )
    if isinstance(error, dict):
        message = error.get("message") or error.get("errorName")
        if message:
            return str(message)
    message = response.get("statusMessage")
    return str(message) if message else default


def _list_packages(client: PowerPlatformClient, environment_id: str) -> list[dict]:
    packages = client.list_environment_application_packages(environment_id)
    if isinstance(packages, dict) and packages.get("_error"):
        raise RuntimeError(
            "Your account cannot read Marketplace applications for this "
            "environment. Use a Power Platform or Dynamics 365 administrator "
            "account."
        )
    return packages


def _wait_for_install(
    client: PowerPlatformClient,
    environment_id: str,
    unique_name: str,
    operation_id: str | None,
    *,
    timeout_seconds: int,
    poll_interval_seconds: int,
    sleep,
) -> None:
    """Poll the operation endpoint, falling back to package state if needed."""
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        if operation_id:
            status_response = client.get_application_package_install_status(
                environment_id,
                operation_id,
            )
            if status_response.get("_error"):
                raise RuntimeError(
                    "Your account cannot read application installation status."
                )
            status = status_response.get("status")
            if status == "Succeeded":
                return
            if status in {"Failed", "Canceled"}:
                raise RuntimeError(
                    _error_message(
                        status_response,
                        f"Application installation ended with status {status}.",
                    )
                )
        else:
            package = _find_package(
                _list_packages(client, environment_id),
                unique_name,
            )
            if package:
                state = package.get("state")
                if state in INSTALLED_STATES:
                    return
                if state in FAILED_STATES:
                    raise RuntimeError(
                        _error_message(
                            package,
                            f"Application installation ended with state {state}.",
                        )
                    )

        sleep(poll_interval_seconds)

    raise RuntimeError(
        f"Application installation did not finish within "
        f"{timeout_seconds // 60} minutes."
    )


def install_agent(
    env_url: str,
    experience: str,
    vertical: str,
    *,
    catalog_path: Path = CATALOG_PATH,
    pp_admin_client_factory=PPAdminClient,
    powerplatform_client_factory=PowerPlatformClient,
    timeout_seconds: int = 1800,
    poll_interval_seconds: int = 20,
    sleep=time.sleep,
) -> str:
    """Authenticate and install the selected ESS application through REST APIs."""
    env_url = env_url.rstrip("/")
    schema_name = load_parent_schemas(catalog_path)[(experience, vertical)]
    tenant_id = discover_tenant(env_url)

    pp_admin = pp_admin_client_factory(tenant_id)
    pp_admin.authenticate(include_flow=False)
    environment_id = pp_admin.find_environment_id_by_dataverse_url(env_url)
    if not environment_id:
        raise RuntimeError(
            "Could not resolve the selected environment's Power Platform ID."
        )

    client = powerplatform_client_factory(tenant_id)
    client.authenticate()
    package = _find_package(
        _list_packages(client, environment_id),
        schema_name,
    )
    if not package:
        raise RuntimeError(
            f"The Marketplace application '{schema_name}' is not available "
            "for this tenant or environment."
        )

    unique_name = package.get("uniqueName") or schema_name
    state = package.get("state")
    if state in INSTALLED_STATES:
        return schema_name

    if state in IN_PROGRESS_STATES:
        operation_id = None
    else:
        result = client.install_application_package(
            environment_id,
            unique_name,
        )
        if result.get("_error"):
            raise RuntimeError(
                "Your account cannot install Marketplace applications in this "
                "environment. Use a Power Platform or Dynamics 365 "
                "administrator account."
            )
        operation_id = result.get("_operationId")
        last_state = result.get("lastOperation", {}).get("state")
        if last_state in INSTALLED_STATES:
            return schema_name
        if last_state == "InstallFailed":
            raise RuntimeError(
                _error_message(result.get("lastOperation", {}), "Installation failed.")
            )

    _wait_for_install(
        client,
        environment_id,
        unique_name,
        operation_id,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        sleep=sleep,
    )
    return schema_name


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Install an Employee Self-Service agent application"
    )
    parser.add_argument("--url", required=True, help="Dataverse environment URL")
    parser.add_argument(
        "--experience",
        required=True,
        choices=sorted(EXPERIENCE_STATUS),
        help="Agent experience: da or cea",
    )
    parser.add_argument(
        "--vertical",
        required=True,
        choices=sorted(VERTICAL_PACKAGE),
        help="Agent vertical: hr or it",
    )
    args = parser.parse_args()

    try:
        schema_name = install_agent(
            args.url,
            args.experience,
            args.vertical,
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)

    result = {
        "environmentUrl": args.url.rstrip("/"),
        "experience": args.experience,
        "vertical": args.vertical,
        "schemaName": schema_name,
    }
    print(f"INSTALLED_ESS_AGENT_JSON:{json.dumps(result)}")


if __name__ == "__main__":
    main()
