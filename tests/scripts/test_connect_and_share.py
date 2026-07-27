# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for solutions/ess-maker-skills/scripts/connect_and_share.py.

Covers the pure resolution/binding-body helpers and the ``run()`` orchestration
across its branches: no reference, unbound reference, already-connected no-op,
bind-and-share, dry-run (no writes), and idempotent share. All network/auth is
monkeypatched — no live calls.
"""

from __future__ import annotations

import json

import pytest

import connect_and_share as cs


SN_CONNECTOR = "/providers/Microsoft.PowerApps/apis/shared_service-now"
DV_CONNECTOR = "/providers/Microsoft.PowerApps/apis/shared_commondataserviceforapps"
CONN_ID = "0fdad2b7f5454ab1b9d41bb8058ed13d"
REF_ID = "c359730a-ad8d-442e-baf4-9ea7857c5537"
SCHEMA = "msdyn_copilotforemployeeselfservicehr"
FLOW_ID = "a1f4c28d-6b7c-49b9-a32e-55d8f19c7a03"

CONFIG = {"agents": [{"slug": "ess", "schemaName": SCHEMA}], "activeAgent": "ess"}


def _ref(*, connectionid=CONN_ID, statuscode=1, connector=SN_CONNECTOR,
         config=None):
    return {
        "connectionreferenceid": REF_ID,
        "connectionreferencelogicalname": "msdyn.bot.shared_service-now",
        "connectorid": connector,
        "connectionid": connectionid,
        "statuscode": statuscode,
        "connectionparametersetconfig": config,
    }


def _user_connections(connection_id, status="Connected"):
    return {
        "flowBindings": {
            FLOW_ID: {
                "connectors": [
                    {
                        "connectorId": SN_CONNECTOR,
                        "connectionId": connection_id,
                        "connectionName": "shared_service-now",
                        "status": status,
                    },
                    {
                        "connectorId": DV_CONNECTOR,
                        "connectionId": "dv-conn",
                        "connectionName": "shared_commondataserviceforapps",
                        "status": "Connected",
                    },
                ]
            }
        }
    }


def _connection():
    return {
        "properties": {
            "connectionParametersSet": {
                "name": "entraIDUserLogin",
                "values": {
                    "token:ResourceUri": {"value": "92065ada-app"},
                    "token:InstanceName": {"value": "dev184242"},
                },
            }
        }
    }


PARAM_CONFIG = {
    "name": "entraIDUserLogin",
    "values": {
        "token:ResourceUri": {"value": "92065ada-app"},
        "token:InstanceName": {"value": "dev184242"},
    },
}


# ── pure helpers ─────────────────────────────────────────────────────
def test_select_servicenow_refs():
    rows = [_ref(), _ref(connector=DV_CONNECTOR)]
    assert len(cs.select_servicenow_refs(rows)) == 1


def test_pick_bound_ref_prefers_active():
    inactive = _ref(statuscode=2)
    active = _ref(statuscode=1)
    assert cs.pick_bound_ref([inactive, active])["statuscode"] == 1


def test_pick_bound_ref_none_when_unbound():
    assert cs.pick_bound_ref([_ref(connectionid=None)]) is None


def test_bot_schema_active_agent():
    assert cs.bot_schema(CONFIG) == SCHEMA


def test_bot_schema_fallbacks():
    assert cs.bot_schema({"schemaName": "top"}) == "top"
    assert cs.bot_schema({"agents": [{"schemaName": "first"}]}) == "first"
    assert cs.bot_schema({}) is None
    assert cs.bot_schema(None) is None


def test_build_flow_bindings_sets_target_and_preserves_others():
    data = _user_connections(None)
    bindings, changed = cs.build_flow_bindings(data, "shared_service-now", CONN_ID)
    assert changed == [FLOW_ID]
    connectors = bindings[FLOW_ID]
    sn = next(c for c in connectors if c["connectorId"] == SN_CONNECTOR)
    dv = next(c for c in connectors if c["connectorId"] == DV_CONNECTOR)
    assert sn["connectionId"] == CONN_ID          # set to target
    assert dv["connectionId"] == "dv-conn"        # preserved untouched


def test_build_flow_bindings_no_change_when_already_target():
    data = _user_connections(CONN_ID)
    bindings, changed = cs.build_flow_bindings(data, "shared_service-now", CONN_ID)
    assert changed == []
    assert FLOW_ID in bindings  # still emits the body, just unchanged


def test_build_flow_bindings_rebinds_stale_even_when_id_matches():
    # A pack install can leave the binding with the right connectionId but a
    # 'Stale' status; it must be re-POSTed to become active again.
    data = _user_connections(CONN_ID, status="Stale")
    bindings, changed = cs.build_flow_bindings(data, "shared_service-now", CONN_ID)
    assert changed == [FLOW_ID]
    sn = next(c for c in bindings[FLOW_ID] if c["connectorId"] == SN_CONNECTOR)
    assert sn["connectionId"] == CONN_ID


def test_build_param_config_reduces_to_value_only():
    assert cs.build_param_config(_connection()) == PARAM_CONFIG


def test_build_param_config_none_when_absent():
    assert cs.build_param_config({"properties": {}}) is None


def test_config_equal_tolerates_formatting():
    stored = json.dumps(PARAM_CONFIG)  # different separators/whitespace
    assert cs._config_equal(stored, PARAM_CONFIG)
    assert not cs._config_equal(None, PARAM_CONFIG)
    assert not cs._config_equal("not json", PARAM_CONFIG)


# ── run() orchestration ──────────────────────────────────────────────
class _FakeClient:
    def __init__(self, user_connections, connection):
        self._uc = user_connections
        self._conn = connection
        self.posted = []
        self.authed = None

    def authenticate(self, interactive=True):
        self.authed = interactive
        return "pac-token"

    def get_user_connections(self, schema):
        return self._uc

    def set_user_connections(self, schema, flow_bindings):
        self.posted.append((schema, flow_bindings))
        return 204

    def get_connection(self, connector, connection_id):
        return self._conn


@pytest.fixture
def patched(monkeypatch):
    calls = {"update": []}
    monkeypatch.setattr(cs.auth, "authenticate", lambda env: "dvtok")
    monkeypatch.setattr(cs.auth, "discover_tenant", lambda env: "tenant")
    monkeypatch.setattr(
        cs.auth, "update_record",
        lambda env, tok, ent, rid, data: calls["update"].append((ent, rid, data)),
    )
    return calls


def _install_client(monkeypatch, user_connections, connection):
    client = _FakeClient(user_connections, connection)
    monkeypatch.setattr(cs, "PPEnvClient", lambda tenant, env_id: client)
    return client


def _run(monkeypatch, rows, **kw):
    monkeypatch.setattr(cs.auth, "query_all", lambda *a, **k: rows)
    return cs.run(
        "https://org.crm.dynamics.com",
        config=CONFIG,
        environment_id="env-guid",
        dry_run=kw.get("dry_run", False),
    )


def test_run_no_reference(patched, monkeypatch):
    _install_client(monkeypatch, _user_connections(None), _connection())
    result = _run(monkeypatch, [_ref(connector=DV_CONNECTOR)])
    assert result["action"] == "no_reference"
    assert result["exit_code"] == 3


def test_run_unbound_reference(patched, monkeypatch):
    _install_client(monkeypatch, _user_connections(None), _connection())
    result = _run(monkeypatch, [_ref(connectionid=None)])
    assert result["action"] == "no_binding"
    assert result["exit_code"] == 4


def test_run_binds_and_shares(patched, monkeypatch):
    client = _install_client(monkeypatch, _user_connections(None), _connection())
    result = _run(monkeypatch, [_ref(config=None)])
    assert result["exit_code"] == 0
    assert result["action"] == "connected"
    assert result["flow_binding"] == "bound"
    assert result["share"] == "shared"
    # POST issued once, PATCH issued once.
    assert len(client.posted) == 1
    assert len(patched["update"]) == 1
    ent, rid, data = patched["update"][0]
    assert ent == "connectionreferences"
    assert rid == REF_ID
    assert data["connectionid"] == CONN_ID
    assert json.loads(data["connectionparametersetconfig"]) == PARAM_CONFIG


def test_run_already_connected_and_shared_no_writes(patched, monkeypatch):
    stored = json.dumps(PARAM_CONFIG, separators=(",", ":"))
    client = _install_client(monkeypatch, _user_connections(CONN_ID), _connection())
    result = _run(monkeypatch, [_ref(config=stored)])
    assert result["exit_code"] == 0
    assert result["flow_binding"] == "already_connected"
    assert result["share"] == "already_shared"
    assert client.posted == []
    assert patched["update"] == []


def test_run_dry_run_writes_nothing(patched, monkeypatch):
    client = _install_client(monkeypatch, _user_connections(None), _connection())
    result = _run(monkeypatch, [_ref(config=None)], dry_run=True)
    assert result["action"] == "would_connect"
    assert result["flow_binding"] == "would_bind"
    assert result["share"] == "would_share"
    assert client.posted == []
    assert patched["update"] == []


def test_run_no_flow_connector(patched, monkeypatch):
    empty = {"flowBindings": {FLOW_ID: {"connectors": [
        {"connectorId": DV_CONNECTOR, "connectionId": "dv", "status": "Connected"},
    ]}}}
    _install_client(monkeypatch, empty, _connection())
    result = _run(monkeypatch, [_ref()])
    assert result["action"] == "no_flow_connector"
    assert result["exit_code"] == 4
