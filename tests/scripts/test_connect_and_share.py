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
SHARED_NAME = f"{SCHEMA}.{FLOW_ID}.shared_service-now"

CONFIG = {"agents": [{"slug": "ess", "schemaName": SCHEMA}], "activeAgent": "ess"}


def _ref(*, connectionid=CONN_ID, statuscode=1, connector=SN_CONNECTOR,
         config=None, logicalname=SHARED_NAME, refid=REF_ID):
    return {
        "connectionreferenceid": refid,
        "connectionreferencelogicalname": logicalname,
        "connectorid": connector,
        "connectionid": connectionid,
        "statuscode": statuscode,
        "connectionparametersetconfig": config,
    }


def _bap_conn(name, created, *, api=SN_CONNECTOR, status="Connected"):
    """A BAP admin-API connection object (as bind_connections discovers them)."""
    return {
        "name": name,
        "properties": {
            "apiId": api,
            "displayName": "ServiceNow",
            "createdTime": created,
            "statuses": [{"status": status}],
        },
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


def test_is_shared_connector_ref():
    assert cs._is_shared_connector_ref(_ref(logicalname="s.guid.shared_service-now"))
    assert not cs._is_shared_connector_ref(_ref(logicalname="s.cr.w2LCWZTZ"))


def test_pick_shared_ref_prefers_shared_over_solution():
    solution = _ref(logicalname="s.cr.w2LCWZTZ")
    shared = _ref(logicalname="s.guid.shared_service-now")
    assert cs.pick_shared_ref([solution, shared]) is shared
    assert cs.pick_shared_ref([solution]) is None


def test_shared_ref_logical_name_shape():
    name = cs.shared_ref_logical_name(SCHEMA, FLOW_ID)
    assert name == f"{SCHEMA}.{FLOW_ID}.shared_service-now"
    # Deterministic: derived from the flow id, NOT random.
    assert cs.shared_ref_logical_name(SCHEMA, FLOW_ID) == name


def test_find_shared_ref_by_name_case_insensitive():
    name = f"{SCHEMA}.{FLOW_ID}.shared_service-now"
    ref = _ref(logicalname=name)
    assert cs.find_shared_ref_by_name([ref], name.upper()) is ref
    assert cs.find_shared_ref_by_name([ref], "other.name") is None


def test_flow_id_from_shared_ref_name_roundtrips():
    name = cs.shared_ref_logical_name(SCHEMA, FLOW_ID)
    assert cs.flow_id_from_shared_ref_name(SCHEMA, name) == FLOW_ID
    # Non-shared / mismatched names yield None.
    assert cs.flow_id_from_shared_ref_name(SCHEMA, "msdyn.cr.w2LCWZTZ") is None
    assert cs.flow_id_from_shared_ref_name(SCHEMA, "") is None


def test_resolve_connection_id_prefers_latest(monkeypatch):
    older = _bap_conn("old", "2024-01-01T00:00:00Z")
    newer = _bap_conn("new", "2025-06-01T00:00:00Z")
    monkeypatch.setattr(cs, "_discover_env_connections", lambda *a, **k: [older, newer])
    cid, src = cs.resolve_connection_id("env", "tok", None, [_ref(connectionid="stale")])
    assert cid == "new"
    assert src == "latest environment connection"


def test_resolve_connection_id_falls_back_to_bound_ref(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("no BAP permissions")

    monkeypatch.setattr(cs, "_discover_env_connections", _boom)
    cid, src = cs.resolve_connection_id("env", "tok", None, [_ref(connectionid="c1")])
    assert cid == "c1"
    assert src == "bound reference"


def test_resolve_connection_id_none_when_nothing(monkeypatch):
    monkeypatch.setattr(cs, "_discover_env_connections", lambda *a, **k: [])
    cid, src = cs.resolve_connection_id("env", "tok", None, [_ref(connectionid=None)])
    assert cid is None
    assert src is None


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
    calls = {"update": [], "create": []}
    monkeypatch.setattr(cs.auth, "authenticate", lambda env: "dvtok")
    monkeypatch.setattr(cs.auth, "discover_tenant", lambda env: "tenant")
    monkeypatch.setattr(
        cs.auth, "update_record",
        lambda env, tok, ent, rid, data: calls["update"].append((ent, rid, data)),
    )
    monkeypatch.setattr(
        cs.auth, "create_record",
        lambda env, tok, ent, data: (
            calls["create"].append((ent, data)) or "new-ref-id"
        ),
    )
    return calls


def _install_client(monkeypatch, user_connections, connection):
    client = _FakeClient(user_connections, connection)
    monkeypatch.setattr(cs, "PPEnvClient", lambda tenant, env_id: client)
    return client


def _run(monkeypatch, rows, *, connections=(), dry_run=False):
    monkeypatch.setattr(cs.auth, "query_all", lambda *a, **k: rows)
    # Dynamic connection discovery (BAP) is monkeypatched: by default it returns
    # no live connections so run() exercises the bound-reference fallback; tests
    # can pass ``connections`` to exercise the latest-connection resolution path.
    monkeypatch.setattr(
        cs, "_discover_env_connections", lambda *a, **k: list(connections)
    )
    return cs.run(
        "https://org.crm.dynamics.com",
        config=CONFIG,
        environment_id="env-guid",
        dry_run=dry_run,
    )


def test_run_no_reference(patched, monkeypatch):
    _install_client(monkeypatch, _user_connections(None), _connection())
    result = _run(monkeypatch, [_ref(connector=DV_CONNECTOR)])
    assert result["action"] == "no_reference"
    assert result["exit_code"] == 3


def test_run_no_connection_found(patched, monkeypatch):
    # Unbound reference AND no live connection discovered -> nothing to connect.
    _install_client(monkeypatch, _user_connections(None), _connection())
    result = _run(monkeypatch, [_ref(connectionid=None)])
    assert result["action"] == "no_connection"
    assert result["exit_code"] == 4


def test_run_binds_and_shares(patched, monkeypatch):
    # The flow-id-keyed shared reference already exists (unshared) -> UPDATE it.
    client = _install_client(monkeypatch, _user_connections(None), _connection())
    result = _run(monkeypatch, [_ref(logicalname=SHARED_NAME, config=None)])
    assert result["exit_code"] == 0
    assert result["action"] == "connected"
    assert result["flow_binding"] == "bound"
    assert result["share"] == "shared"
    # POST issued once, PATCH issued once (the existing shared reference).
    assert len(client.posted) == 1
    assert len(patched["update"]) == 1
    assert patched["create"] == []
    ent, rid, data = patched["update"][0]
    assert ent == "connectionreferences"
    assert rid == REF_ID
    assert data["connectionid"] == CONN_ID
    assert json.loads(data["connectionparametersetconfig"]) == PARAM_CONFIG


def test_run_creates_shared_ref_when_absent(patched, monkeypatch):
    # Only the solution-shipped ``.cr.<short>`` reference exists (no flow-id-keyed
    # ``.shared_service-now`` reference) -> the share must CREATE one named after
    # the flow id, exactly like the portal.
    client = _install_client(monkeypatch, _user_connections(CONN_ID), _connection())
    solution_ref = _ref(logicalname="msdyn.cr.w2LCWZTZ")
    result = _run(monkeypatch, [solution_ref])
    assert result["exit_code"] == 0
    assert result["share"] == "created_shared_ref"
    assert patched["update"] == []
    assert len(patched["create"]) == 1
    ent, data = patched["create"][0]
    assert ent == "connectionreferences"
    assert data["connectionid"] == CONN_ID
    assert data["connectorid"] == cs._CONNECTOR_ID
    # Name derived from the flow id (deterministic, portal-matching).
    assert data["connectionreferencelogicalname"] == SHARED_NAME
    assert data["connectionreferencedisplayname"] == SHARED_NAME
    assert json.loads(data["connectionparametersetconfig"]) == PARAM_CONFIG
    assert result["shared_reference"] == SHARED_NAME
    assert result["shared_references"] == [
        {"flow": FLOW_ID, "reference": SHARED_NAME, "action": "created_shared_ref"}
    ]


def test_run_shares_shipped_ref_not_in_user_connections(patched, monkeypatch):
    # A second extension pack (e.g. HRSD installed alongside ITSM) ships its own
    # flow-id-keyed ``.shared_service-now`` reference whose flow is NOT registered
    # in the agent's user_connections. Bind sets its connectionid but leaves it
    # unshared; the union logic must still share it (set the param config),
    # matching what the portal reads — otherwise that pack shows "not shared".
    other_flow = "7e2b1c3a-9f4a-4e2a-8b1e-2c3a9f4a8b1e"
    other_name = f"{SCHEMA}.{other_flow}.shared_service-now"
    stored = json.dumps(PARAM_CONFIG, separators=(",", ":"))
    _install_client(monkeypatch, _user_connections(CONN_ID), _connection())
    registered = _ref(logicalname=SHARED_NAME, config=stored)          # already shared
    shipped = _ref(refid="ship-ref-id", logicalname=other_name, config=None)
    result = _run(monkeypatch, [registered, shipped])
    assert result["exit_code"] == 0
    # Only the shipped ref needs a PATCH; the registered one is already shared.
    assert patched["create"] == []
    assert len(patched["update"]) == 1
    ent, rid, data = patched["update"][0]
    assert ent == "connectionreferences"
    assert rid == "ship-ref-id"
    assert data["connectionid"] == CONN_ID
    assert json.loads(data["connectionparametersetconfig"]) == PARAM_CONFIG
    # Both refs reported; the registered one already shared, the shipped one shared.
    actions = {r["reference"]: r["action"] for r in result["shared_references"]}
    assert actions[SHARED_NAME] == "already_shared"
    assert actions[other_name] == "shared"
    assert cs.flow_id_from_shared_ref_name(SCHEMA, other_name) == other_flow


def test_run_prefers_latest_discovered_connection(patched, monkeypatch):
    # Two live connections exist; the most recently created must win and its id
    # (not the reference's stored connectionid) must be shared.
    _install_client(monkeypatch, _user_connections(None), _connection())
    older = _bap_conn("stale-old-conn", "2024-01-01T00:00:00Z")
    newer = _bap_conn("latest-conn-id", "2025-01-01T00:00:00Z")
    stale_ref = _ref(logicalname="msdyn.cr.w2LCWZTZ", connectionid="stale-old-conn")
    result = _run(monkeypatch, [stale_ref], connections=[older, newer])
    assert result["exit_code"] == 0
    assert result["connection_id"] == "latest-conn-id"
    assert result["connection_source"] == "latest environment connection"
    ent, data = patched["create"][0]
    assert data["connectionid"] == "latest-conn-id"


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
