# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json


def _connection(name, *, connector="shared_alchemy", status="Connected"):
    return {
        "name": name,
        "properties": {
            "apiId": f"/providers/Microsoft.PowerApps/apis/{connector}",
            "displayName": f"Connection {name}",
            "accountName": "maker@contoso.com",
            "statuses": [{"status": status}],
        },
    }


def test_installation_config_declares_only_it_connection_requirements():
    import install_ess_agent

    installations = install_ess_agent.load_installation_config()["installations"]

    assert installations["da.hr"]["requiredConnection"] is None
    assert installations["cea.hr"]["requiredConnection"] is None
    assert installations["da.it"]["requiredConnection"]["connectorApiName"] == (
        "shared_alchemy"
    )
    assert installations["cea.it"]["requiredConnection"]["connectorApiName"] == (
        "shared_alchemy"
    )


def test_preflight_requires_manual_selection_for_multiple_connected_matches():
    import ess_connection_binding
    import install_ess_agent

    installation = install_ess_agent.load_installation_config()["installations"][
        "da.it"
    ]
    result = ess_connection_binding.build_preflight_result(
        installation,
        [
            _connection("one"),
            _connection("two"),
            _connection("broken", status="Error"),
            _connection("wrong", connector="shared_workdaysoap"),
        ],
        "environment-id",
    )

    assert result["status"] == "selection-required"
    assert [connection["name"] for connection in result["connections"]] == [
        "one",
        "two",
    ]
    assert result["selectedConnection"] is None


def test_preflight_auto_selects_only_connected_match():
    import ess_connection_binding
    import install_ess_agent

    installation = install_ess_agent.load_installation_config()["installations"][
        "cea.it"
    ]
    result = ess_connection_binding.build_preflight_result(
        installation,
        [_connection("alchemy")],
        "environment-id",
    )

    assert result["status"] == "ready"
    assert result["selectedConnection"]["name"] == "alchemy"


def test_bind_updates_exact_solution_reference_and_persists_s_states(
    tmp_path,
    monkeypatch,
):
    import ess_connection_binding

    class FakePPAdmin:
        def __init__(self, tenant_id):
            self.tenant_id = tenant_id

        def authenticate(self, *, include_flow):
            assert include_flow is False

        def find_environment_id_by_dataverse_url(self, env_url):
            return "environment-id"

        def get_connections(self, environment_id):
            assert environment_id == "environment-id"
            return [_connection("alchemy-connection")]

    reference_id = "11111111-1111-1111-1111-111111111111"
    solution_id = "22222222-2222-2222-2222-222222222222"
    query_results = iter([
        [{
            "connectionreferenceid": reference_id,
            "connectionreferencelogicalname": (
                "msdyn_copilotforemployeeselfservicedait.shared_alchemy."
                "shared-alchemy-8262076a-e778-450b-8a35-5ae815712319"
            ),
            "connectorid": "/providers/Microsoft.PowerApps/apis/shared_alchemy",
            "connectionid": None,
            "statuscode": 1,
        }],
        [{"objectid": reference_id, "_solutionid_value": solution_id}],
        [{
            "solutionid": solution_id,
            "uniquename": "msdyn_CopilotForEmployeeSelfServiceDAIT",
        }],
        [{
            "connectionreferenceid": reference_id,
            "connectionid": "alchemy-connection",
        }],
    ])
    updates = []
    monkeypatch.setattr(ess_connection_binding, "discover_tenant", lambda url: "tenant")
    monkeypatch.setattr(ess_connection_binding, "authenticate", lambda url: "token")
    monkeypatch.setattr(
        ess_connection_binding,
        "query_all",
        lambda *args, **kwargs: next(query_results),
    )
    monkeypatch.setattr(
        ess_connection_binding,
        "update_record",
        lambda *args: updates.append(args),
    )
    monkeypatch.setattr(
        ess_connection_binding,
        "LOCAL_CONFIG_PATH",
        tmp_path / "config.json",
    )

    result = ess_connection_binding.bind_connection(
        "https://org.crm.dynamics.com",
        "da",
        "it",
        "alchemy-connection",
        pp_admin_client_factory=FakePPAdmin,
    )

    assert result["status"] == "bound"
    assert updates[0][-1] == {"connectionid": "alchemy-connection"}
    config = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    state = config["agents"]["da"]["essit"]["setupStatus"]
    assert state["S1"]["state"] == "done"
    assert state["S2"]["state"] == "done"
    assert state["S2"]["connectionName"] == "alchemy-connection"


def test_hr_binding_marks_connection_not_required(tmp_path, monkeypatch):
    import ess_connection_binding

    monkeypatch.setattr(
        ess_connection_binding,
        "LOCAL_CONFIG_PATH",
        tmp_path / "config.json",
    )

    result = ess_connection_binding.bind_connection(
        "https://org.crm.dynamics.com",
        "cea",
        "hr",
        None,
    )

    assert result["status"] == "not-required"
    config = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    state = config["agents"]["cea"]["esshr"]["setupStatus"]
    assert state["S2"]["notRequired"] is True
