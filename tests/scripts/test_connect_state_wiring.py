# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for the per-script connect-state persistence hooks.

Each action script's ``main()`` calls a ``_persist_*`` helper on confirmed
success. These tests exercise the gating logic directly (no Dataverse/network)
by feeding fabricated ``args``/``result`` objects and asserting what lands in
``.local/connect/servicenow/config.json`` under a ``tmp_path`` sandbox.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import activate_flows
import bind_connections
import connect_and_share
import connect_state
import install_extension_pack as iep


def _sn():
    with open(connect_state.config_path("servicenow"), encoding="utf-8") as f:
        return json.load(f)


def _args(**kw):
    kw.setdefault("dry_run", False)
    kw.setdefault("start", False)
    return SimpleNamespace(**kw)


# ── install ─────────────────────────────────────────────────────────────
def test_install_persists_packs_and_step_on_success(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    iep._persist_install_state(
        {"scope": {"hrsd": True, "itsm": False}}, _args(),
        {"exit_code": 0, "action": "install"})
    data = _sn()
    assert data["packs"] == {"hrsd": "installed"}
    # Install is per-product now: S6.1 lands under productStatus.<product>,
    # not the shared flat setupStatus block.
    assert data["productStatus"]["hrsd"]["S6.1"]["checkpoint"] == "SN-001"
    assert "S6.1" not in data.get("setupStatus", {})


def test_install_status_succeeded_persists(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    iep._persist_install_state(
        {"scope": {"hrsd": True}}, _args(),
        {"exit_code": 0, "action": "succeeded"})
    assert _sn()["packs"] == {"hrsd": "installed"}


def test_install_dry_run_persists_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    iep._persist_install_state(
        {"scope": {"hrsd": True}}, _args(dry_run=True),
        {"exit_code": 0, "action": "install"})
    assert not os.path.exists(connect_state.config_path("servicenow"))


def test_install_start_only_persists_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    iep._persist_install_state(
        {"scope": {"hrsd": True}}, _args(start=True),
        {"exit_code": 0, "action": "start"})
    assert not os.path.exists(connect_state.config_path("servicenow"))


def test_install_running_poll_persists_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    iep._persist_install_state(
        {"scope": {"hrsd": True}}, _args(),
        {"exit_code": 0, "action": "running"})
    assert not os.path.exists(connect_state.config_path("servicenow"))


# ── bind ────────────────────────────────────────────────────────────────
def test_bind_multi_records_only_exit0_connectors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bind_connections._persist_bind_state(_args(), {
        "action": "multi",
        "results": [
            {"connector": "servicenow", "exit_code": 0},
            {"connector": "dataverse", "exit_code": 3},  # tolerated miss
        ],
    })
    conns = _sn()["connections"]
    assert conns["servicenow"]["state"] == "bound"
    assert "dataverse" not in conns  # exit 3 → not marked bound
    assert _sn()["setupStatus"]["S6.2"]["state"] == "done"


def test_bind_single_servicenow_records_step(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bind_connections._persist_bind_state(
        _args(), {"connector": "servicenow", "exit_code": 0, "action": "bound"})
    assert _sn()["connections"]["servicenow"]["state"] == "bound"
    assert "S6.2" in _sn()["setupStatus"]


def test_bind_attaches_authtype_from_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    connect_state.merge("servicenow", {"authType": "entra_user"})
    bind_connections._persist_bind_state(
        _args(), {"connector": "servicenow", "exit_code": 0})
    assert _sn()["connections"]["servicenow"]["authType"] == "entra_user"


def test_bind_dry_run_persists_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bind_connections._persist_bind_state(
        _args(dry_run=True), {"connector": "servicenow", "exit_code": 0})
    assert not os.path.exists(connect_state.config_path("servicenow"))


# ── activate ────────────────────────────────────────────────────────────
def test_activate_records_flows_and_step(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    activate_flows._persist_activate_state(
        _args(), {"exit_code": 0, "action": "activated"})
    data = _sn()
    assert data["flows"]["state"] == "enabled"
    assert data["setupStatus"]["S6.3"]["checkpoint"] == "SN-FLOW-000..004"


def test_activate_no_flows_persists_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    activate_flows._persist_activate_state(
        _args(), {"exit_code": 3, "action": "no_flows"})
    assert not os.path.exists(connect_state.config_path("servicenow"))


# ── connect_and_share ───────────────────────────────────────────────────
def test_connect_records_status_and_legacy_summary(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs(".local", exist_ok=True)
    with open(os.path.join(".local", "config.json"), "w", encoding="utf-8") as f:
        json.dump({"dataverseEndpoint": "https://x"}, f)
    connect_state.merge("servicenow", {
        "authType": "entra_user", "instanceName": "dev184242",
        "instanceUrl": "https://dev184242.service-now.com", "usage": "hrsd",
    })
    connect_and_share._persist_connect_state(
        _args(), {"exit_code": 0, "action": "connected",
                  "flow_binding": "bound", "share": "shared"})

    sn = _sn()
    assert sn["connections"]["servicenow"] == {
        "state": "active", "flowBinding": "connected",
        "verifiedBy": "programmatic", "authType": "entra_user",
    }
    assert sn["status"] == "connected"
    assert sn["setupStatus"]["S6.4"]["checkpoint"] == "SN-FLOWCONN-001"
    # Share stage recorded as its own checkpoint (S6.5).
    assert sn["setupStatus"]["S6.5"]["checkpoint"] == "SN-FLOWCONN-001"
    assert sn["parameterSharing"] == "shared"

    with open(os.path.join(".local", "config.json"), encoding="utf-8") as f:
        root = json.load(f)
    assert root["dataverseEndpoint"] == "https://x"  # preserved
    assert root["connections"]["ServiceNow"]["instanceName"] == "dev184242"


def test_connect_dry_run_persists_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    connect_and_share._persist_connect_state(
        _args(dry_run=True), {"exit_code": 0, "action": "connected"})
    assert not os.path.exists(connect_state.config_path("servicenow"))


# ── per-product connect-chain recording (S6.2–S6.5 into productStatus) ────
#
# The action scripts additionally attribute each connect-chain step to the
# ServiceNow product(s) whose installed pack owns the touched artifacts, so an
# incrementally installed agent (e.g. HR added, then IT) resumes per product.
# These use an injected ``query`` (no network) that models an HR-persona env
# whose only installed pack is ITSM.

def _fake_query(*, sn_bound=True, flow_state=1):
    """Fake Dataverse ``query`` over an HR-persona env where only the ITSM pack
    is installed. ``sn_bound`` toggles whether the owned ServiceNow reference is
    bound; ``flow_state`` is the owned flow's ``statecode`` (1 = on)."""
    tables = {
        "solutions": [
            {"solutionid": "sid-itsm", "uniquename": "msdyn_EssHRServiceNowITSM"},
        ],
        "connectionreferences": [
            {"connectionreferenceid": "ref-sn",
             "connectorid": "/x/shared_service-now",
             "connectionid": "conn-sn" if sn_bound else None,
             "statuscode": 1},
            {"connectionreferenceid": "ref-dv",
             "connectorid": "/x/shared_commondataserviceforapps",
             "connectionid": "conn-dv", "statuscode": 1},
        ],
        "workflows": [
            {"workflowid": "wf-1", "name": "ESS HR ServiceNow ITSM Get Tickets",
             "category": 5, "statecode": flow_state},
        ],
        "solutioncomponents": [
            {"componenttype": 10038, "objectid": "ref-sn"},
            {"componenttype": 10038, "objectid": "ref-dv"},
            {"componenttype": 29, "objectid": "wf-1"},
        ],
    }

    def query(env_url, token, entity, select, filter_expr=None):
        return tables[entity]

    return query


def _seed_persona_scope(scope):
    """Write the on-disk config ``persona_and_scope`` reads: root agent schema
    (HR persona) + the connector's product ``scope``."""
    os.makedirs(".local", exist_ok=True)
    with open(os.path.join(".local", "config.json"), "w", encoding="utf-8") as f:
        json.dump({"schemaName": "msdyn_copilotforemployeeselfservicehr"}, f)
    connect_state.merge("servicenow", {"scope": scope})


def _tok(_env_url):
    return "tok"


def test_bind_records_product_step_when_sn_ref_bound(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_persona_scope({"itsm": True})
    bind_connections._persist_product_bind_state(
        "https://env", _args(),
        {"connector": "servicenow", "exit_code": 0, "action": "bound"},
        query=_fake_query(sn_bound=True), authenticate=_tok)
    assert _sn()["productStatus"]["itsm"]["S6.2"]["checkpoint"] == "SN-CONN-001"


def test_bind_product_not_recorded_when_sn_ref_unbound(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_persona_scope({"itsm": True})
    bind_connections._persist_product_bind_state(
        "https://env", _args(),
        {"connector": "servicenow", "exit_code": 0, "action": "bound"},
        query=_fake_query(sn_bound=False), authenticate=_tok)
    assert "itsm" not in _sn().get("productStatus", {})


def test_bind_product_not_recorded_when_servicenow_failed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_persona_scope({"itsm": True})
    bind_connections._persist_product_bind_state(
        "https://env", _args(),
        {"connector": "servicenow", "exit_code": 1, "action": "error"},
        query=_fake_query(sn_bound=True), authenticate=_tok)
    assert "itsm" not in _sn().get("productStatus", {})


def test_bind_product_not_recorded_for_uninstalled_product(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # HRSD is in scope but its pack is not installed in the fake env -> skipped.
    _seed_persona_scope({"hrsd": True, "itsm": True})
    bind_connections._persist_product_bind_state(
        "https://env", _args(),
        {"connector": "servicenow", "exit_code": 0, "action": "bound"},
        query=_fake_query(sn_bound=True), authenticate=_tok)
    products = _sn().get("productStatus", {})
    assert "hrsd" not in products
    assert products["itsm"]["S6.2"]["state"] == "done"


def test_bind_product_dry_run_persists_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_persona_scope({"itsm": True})
    before = _sn()
    bind_connections._persist_product_bind_state(
        "https://env", _args(dry_run=True),
        {"connector": "servicenow", "exit_code": 0, "action": "bound"},
        query=_fake_query(sn_bound=True), authenticate=_tok)
    assert "productStatus" not in before or "itsm" not in before.get("productStatus", {})


def test_activate_records_product_step_when_flows_on(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_persona_scope({"itsm": True})
    activate_flows._persist_product_activate_state(
        "https://env", _args(), {"exit_code": 0, "action": "activated"},
        query=_fake_query(flow_state=1), authenticate=_tok)
    assert _sn()["productStatus"]["itsm"]["S6.3"]["checkpoint"] == "SN-FLOW-000..004"


def test_activate_product_not_recorded_when_flow_off(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_persona_scope({"itsm": True})
    activate_flows._persist_product_activate_state(
        "https://env", _args(), {"exit_code": 0, "action": "activated"},
        query=_fake_query(flow_state=0), authenticate=_tok)
    assert "itsm" not in _sn().get("productStatus", {})


def test_connect_records_product_s64_and_s65(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_persona_scope({"itsm": True})
    connect_and_share._persist_product_connect_state(
        "https://env", _args(),
        {"exit_code": 0, "flow_binding": "bound", "share": "shared"},
        query=_fake_query(), authenticate=_tok)
    itsm = _sn()["productStatus"]["itsm"]
    assert itsm["S6.4"]["checkpoint"] == "SN-FLOWCONN-001"
    assert itsm["S6.5"]["checkpoint"] == "SN-FLOWCONN-001"


def test_connect_records_only_s64_when_share_pending(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_persona_scope({"itsm": True})
    connect_and_share._persist_product_connect_state(
        "https://env", _args(),
        {"exit_code": 6, "flow_binding": "bound", "share": "failed"},
        query=_fake_query(), authenticate=_tok)
    itsm = _sn()["productStatus"]["itsm"]
    assert "S6.4" in itsm
    assert "S6.5" not in itsm


def test_connect_product_dry_run_persists_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_persona_scope({"itsm": True})
    connect_and_share._persist_product_connect_state(
        "https://env", _args(dry_run=True),
        {"exit_code": 0, "flow_binding": "bound", "share": "shared"},
        query=_fake_query(), authenticate=_tok)
    assert "productStatus" not in _sn() or "itsm" not in _sn().get("productStatus", {})
