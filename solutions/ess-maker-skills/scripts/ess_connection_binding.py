# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Validate and bind connections required by ESS agent installations."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from auth import authenticate, discover_tenant, query_all, update_record
from flightcheck.pp_admin_client import PPAdminClient, derive_environment_id
from install_ess_agent import CONFIG_PATH, load_installation_config


PREFLIGHT_MARKER = "ESS_CONNECTION_PREFLIGHT_JSON:"
BINDING_MARKER = "ESS_CONNECTION_BINDING_JSON:"
LOCAL_CONFIG_PATH = Path(".local") / "config.json"


def _connector_api_name(value: str | None) -> str:
    return (value or "").rstrip("/").rsplit("/", 1)[-1].casefold()


def _connection_status(connection: dict) -> str:
    statuses = (connection.get("properties") or {}).get("statuses") or []
    if not statuses or not isinstance(statuses[0], dict):
        return "Unknown"
    return str(statuses[0].get("status") or "Unknown")


def connection_option(connection: dict) -> dict:
    properties = connection.get("properties") or {}
    created_by = properties.get("createdBy") or {}
    return {
        "name": connection.get("name"),
        "displayName": properties.get("displayName") or connection.get("name"),
        "accountName": (
            properties.get("accountName")
            or created_by.get("userPrincipalName")
            or created_by.get("displayName")
        ),
        "status": _connection_status(connection),
    }


def matching_connections(connections: list[dict], connector_api_name: str) -> list[dict]:
    matches = []
    expected = connector_api_name.casefold()
    for connection in connections:
        properties = connection.get("properties") or {}
        if _connector_api_name(properties.get("apiId")) != expected:
            continue
        if _connection_status(connection).casefold() != "connected":
            continue
        if connection.get("name"):
            matches.append(connection_option(connection))
    return matches


def build_preflight_result(
    installation: dict,
    connections: list[dict],
    environment_id: str,
) -> dict:
    requirement = installation.get("requiredConnection")
    if requirement is None:
        return {
            "required": False,
            "status": "not-required",
            "environmentId": environment_id,
            "connections": [],
        }

    options = matching_connections(
        connections,
        requirement["connectorApiName"],
    )
    status = (
        "missing" if not options
        else "ready" if len(options) == 1
        else "selection-required"
    )
    return {
        "required": True,
        "status": status,
        "environmentId": environment_id,
        "connectorApiName": requirement["connectorApiName"],
        "displayName": requirement["displayName"],
        "referenceLogicalName": requirement["referenceLogicalName"],
        "creationGuidance": requirement["creationGuidance"],
        "connections": options,
        "selectedConnection": options[0] if len(options) == 1 else None,
    }


def _installation(config: dict, experience: str, vertical: str) -> dict:
    return config["installations"][f"{experience}.{vertical}"]


def _persist_setup_status(
    env_url: str,
    installation: dict,
    binding_result: dict,
    config_path: Path | None = None,
) -> None:
    config_path = config_path or LOCAL_CONFIG_PATH
    config = {}
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("configVersion") not in (None, 2):
        raise RuntimeError(
            "Cannot persist ESS connection state to an unsupported config version."
        )
    config.setdefault("configVersion", 2)
    config.setdefault("setup", "in-progress")
    config.setdefault("common", {})["dataverseEndpoint"] = env_url.rstrip("/")
    experience, section = installation["configKey"].split(".", 1)
    agent = config.setdefault("agents", {}).setdefault(
        experience, {}
    ).setdefault(section, {})
    agent["installation"] = {"status": "installed"}
    agent.setdefault("extraction", {"status": "not-started"})
    agent["setupStatus"] = {
        **(agent.get("setupStatus") or {}),
        "S1": {
            "state": "done",
            "checkpoint": "ESS-SOLN-001",
            "verifiedBy": "programmatic",
        },
        "S2": {
            "state": "done",
            "checkpoint": "ESS-CONN-001",
            "verifiedBy": "programmatic",
            "connectionName": binding_result.get("connectionName"),
            "referenceLogicalName": binding_result.get("referenceLogicalName"),
            "notRequired": not binding_result["required"],
        },
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = config_path.with_suffix(config_path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(config, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary_path, config_path)


def inspect_connections(
    env_url: str,
    experience: str,
    vertical: str,
    *,
    config_path: Path = CONFIG_PATH,
    pp_admin_client_factory=PPAdminClient,
) -> dict:
    env_url = env_url.rstrip("/")
    config = load_installation_config(config_path)
    installation = _installation(config, experience, vertical)
    if installation.get("requiredConnection") is None:
        return build_preflight_result(installation, [], "")

    tenant_id = discover_tenant(env_url)
    client = pp_admin_client_factory(tenant_id)
    client.authenticate(include_flow=False)
    environment_id = derive_environment_id(env_url, "", client)
    if not environment_id:
        raise RuntimeError(
            "Could not resolve the selected Dataverse URL to a Power Platform "
            "environment ID."
        )
    return build_preflight_result(
        installation,
        client.get_connections(environment_id),
        environment_id,
    )


def _assert_reference_in_solution(
    env_url: str,
    token: str,
    reference_id: str,
    solution_unique_name: str,
) -> None:
    components = query_all(
        env_url,
        token,
        "solutioncomponents",
        "objectid,_solutionid_value",
        filter_expr=f"objectid eq {reference_id}",
    )
    solution_ids = {
        component.get("_solutionid_value")
        for component in components
        if component.get("_solutionid_value")
    }
    if not solution_ids:
        raise RuntimeError(
            "The required connection reference is not registered in a solution."
        )

    id_filter = " or ".join(
        f"solutionid eq {solution_id}" for solution_id in sorted(solution_ids)
    )
    escaped_name = solution_unique_name.replace("'", "''")
    solutions = query_all(
        env_url,
        token,
        "solutions",
        "solutionid,uniquename",
        filter_expr=f"({id_filter}) and uniquename eq '{escaped_name}'",
    )
    if not solutions:
        raise RuntimeError(
            f"The required connection reference does not belong to solution "
            f"'{solution_unique_name}'."
        )


def bind_connection(
    env_url: str,
    experience: str,
    vertical: str,
    connection_name: str | None,
    *,
    config_path: Path = CONFIG_PATH,
    pp_admin_client_factory=PPAdminClient,
) -> dict:
    env_url = env_url.rstrip("/")
    config = load_installation_config(config_path)
    installation = _installation(config, experience, vertical)
    requirement = installation.get("requiredConnection")
    if requirement is None:
        result = {
            "required": False,
            "status": "not-required",
            "connectionName": None,
        }
        _persist_setup_status(env_url, installation, result)
        return result
    if not connection_name:
        raise ValueError("A connection name is required for this ESS agent.")

    preflight = inspect_connections(
        env_url,
        experience,
        vertical,
        config_path=config_path,
        pp_admin_client_factory=pp_admin_client_factory,
    )
    selected = next(
        (
            option for option in preflight["connections"]
            if option["name"] == connection_name
        ),
        None,
    )
    if selected is None:
        raise ValueError(
            "The selected connection is not a connected instance of "
            f"{requirement['connectorApiName']} in this environment."
        )

    token = authenticate(env_url)
    escaped_logical_name = requirement["referenceLogicalName"].replace("'", "''")
    references = query_all(
        env_url,
        token,
        "connectionreferences",
        "connectionreferenceid,connectionreferencelogicalname,connectorid,"
        "connectionid,statuscode",
        filter_expr=(
            f"connectionreferencelogicalname eq '{escaped_logical_name}'"
        ),
    )
    if len(references) != 1:
        raise RuntimeError(
            "Expected exactly one required connection reference after "
            f"installation, found {len(references)}."
        )

    reference = references[0]
    if _connector_api_name(reference.get("connectorid")) != (
        requirement["connectorApiName"].casefold()
    ):
        raise RuntimeError(
            "The installed connection reference uses an unexpected connector."
        )
    _assert_reference_in_solution(
        env_url,
        token,
        reference["connectionreferenceid"],
        installation["solution"]["parentUniqueName"],
    )

    if reference.get("connectionid") != connection_name:
        update_record(
            env_url,
            token,
            "connectionreferences",
            reference["connectionreferenceid"],
            {"connectionid": connection_name},
        )

    verified = query_all(
        env_url,
        token,
        "connectionreferences",
        "connectionreferenceid,connectionid",
        filter_expr=(
            f"connectionreferenceid eq {reference['connectionreferenceid']}"
        ),
    )
    if len(verified) != 1 or verified[0].get("connectionid") != connection_name:
        raise RuntimeError(
            "Dataverse accepted the binding request but the connection "
            "reference did not retain the selected connection."
        )

    result = {
        "required": True,
        "status": "bound",
        "connectionName": connection_name,
        "connectionDisplayName": selected["displayName"],
        "connectionAccountName": selected["accountName"],
        "referenceId": reference["connectionreferenceid"],
        "referenceLogicalName": requirement["referenceLogicalName"],
    }
    _persist_setup_status(env_url, installation, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate and bind required ESS installation connections"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("inspect", "bind"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--url", required=True)
        command_parser.add_argument("--experience", required=True, choices=("da", "cea"))
        command_parser.add_argument("--vertical", required=True, choices=("hr", "it"))
        if command == "bind":
            command_parser.add_argument("--connection-name")
    args = parser.parse_args()

    try:
        if args.command == "inspect":
            result = inspect_connections(args.url, args.experience, args.vertical)
            print(f"{PREFLIGHT_MARKER}{json.dumps(result)}")
        else:
            result = bind_connection(
                args.url,
                args.experience,
                args.vertical,
                args.connection_name,
            )
            print(f"{BINDING_MARKER}{json.dumps(result)}")
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
