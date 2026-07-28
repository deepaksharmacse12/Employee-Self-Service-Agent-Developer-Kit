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
    assert data["setupStatus"]["S6.1"]["checkpoint"] == "SN-001"


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
        _args(), {"exit_code": 0, "action": "connected"})

    sn = _sn()
    assert sn["connections"]["servicenow"] == {
        "state": "active", "flowBinding": "connected",
        "verifiedBy": "programmatic", "authType": "entra_user",
    }
    assert sn["status"] == "connected"
    assert sn["setupStatus"]["S6.4"]["checkpoint"] == "SN-FLOWCONN-001"

    with open(os.path.join(".local", "config.json"), encoding="utf-8") as f:
        root = json.load(f)
    assert root["dataverseEndpoint"] == "https://x"  # preserved
    assert root["connections"]["ServiceNow"]["instanceName"] == "dev184242"


def test_connect_dry_run_persists_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    connect_and_share._persist_connect_state(
        _args(dry_run=True), {"exit_code": 0, "action": "connected"})
    assert not os.path.exists(connect_state.config_path("servicenow"))
