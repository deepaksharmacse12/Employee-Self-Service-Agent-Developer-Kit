# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for solutions/ess-maker-skills/scripts/install_extension_pack.py.

Covers the pure resolution helpers and the ``run()`` orchestration across its
branches: already-installed no-op, parent-dependency missing, not-found /
not-entitled, successful install (POST + poll), install failure, and dry-run
(no writes). All network/auth is monkeypatched — no live calls.
"""

from __future__ import annotations

import pytest

import install_extension_pack as iep

SCHEMA_HR = "msdyn_copilotforemployeeselfservicehr"
SCHEMA_IT = "msdyn_copilotforemployeeselfserviceit"
CONFIG_HR = {"agents": [{"slug": "ess", "schemaName": SCHEMA_HR}], "activeAgent": "ess"}
PARENT_HR = "msdyn_CopilotForEmployeeSelfServiceHR"
SN_HRSD_HR = "msdyn_EssHRServiceNowHRSD"
SN_ITSM_HR = "msdyn_EssHRServiceNowITSM"


# ── pure helpers ─────────────────────────────────────────────────────
def test_resolve_persona():
    assert iep.resolve_persona(SCHEMA_HR) == "hr"
    assert iep.resolve_persona(SCHEMA_IT) == "it"
    assert iep.resolve_persona(None) is None
    assert iep.resolve_persona("something_else") is None


def test_servicenow_packages_scope_filters():
    assert iep.servicenow_packages("hr", {"hrsd": True, "itsm": False}) == [SN_HRSD_HR]
    assert set(iep.servicenow_packages("hr", {"hrsd": True, "itsm": True})) == {
        SN_HRSD_HR, SN_ITSM_HR,
    }


def test_servicenow_packages_empty_scope_fails_closed():
    # Fail closed: no product selected -> install NOTHING (never silently both).
    assert iep.servicenow_packages("hr", None) == []
    assert iep.servicenow_packages("hr", {}) == []
    assert iep.servicenow_packages("hr", {"hrsd": False, "itsm": False}) == []


def test_servicenow_packages_only_selected_products():
    assert iep.servicenow_packages("hr", {"hrsd": True, "itsm": False}) == [SN_HRSD_HR]
    assert iep.servicenow_packages("hr", {"hrsd": False, "itsm": True}) == [SN_ITSM_HR]


def test_servicenow_packages_unknown_persona():
    assert iep.servicenow_packages(None, {"hrsd": True}) == []


def test_parent_package():
    assert iep.parent_package("hr") == PARENT_HR
    assert iep.parent_package("it") == "msdyn_CopilotForEmployeeSelfServiceIT"
    assert iep.parent_package(None) is None


# ── _install_one branches ────────────────────────────────────────────
def _pkgs(target_state="None", parent_state="Installed"):
    return [
        {"uniqueName": SN_HRSD_HR, "state": target_state},
        {"uniqueName": PARENT_HR, "state": parent_state},
    ]


class _FakeClient:
    def __init__(self, poll_statuses=None, operation_id="op-1"):
        self._poll = list(poll_statuses or ["Succeeded"])
        self._operation_id = operation_id
        self.installed = []

    def install_application_package(self, unique_name):
        self.installed.append(unique_name)
        return {"status_code": 202, "operation_id": self._operation_id, "body": {}}

    def get_operation(self, operation_id):
        status = self._poll.pop(0) if self._poll else "Succeeded"
        return {"status": status}


def test_install_one_already_installed_no_post():
    client = _FakeClient()
    res = iep._install_one(client, _pkgs(target_state="Installed"), SN_HRSD_HR,
                           PARENT_HR, dry_run=False, timeout=60)
    assert res["action"] == "already_installed"
    assert res["exit_code"] == 0
    assert client.installed == []


def test_install_one_not_found():
    client = _FakeClient()
    res = iep._install_one(client, _pkgs(), "msdyn_NoSuchPack", PARENT_HR,
                           dry_run=False, timeout=60)
    assert res["action"] == "not_found"
    assert res["exit_code"] == 4


def test_install_one_parent_missing():
    client = _FakeClient()
    res = iep._install_one(client, _pkgs(parent_state="None"), SN_HRSD_HR,
                           PARENT_HR, dry_run=False, timeout=60)
    assert res["action"] == "parent_missing"
    assert res["exit_code"] == 3
    assert client.installed == []


def test_install_one_dry_run_no_post():
    client = _FakeClient()
    res = iep._install_one(client, _pkgs(), SN_HRSD_HR, PARENT_HR,
                           dry_run=True, timeout=60)
    assert res["action"] == "would_install"
    assert res["exit_code"] == 0
    assert client.installed == []


def test_install_one_success(monkeypatch):
    monkeypatch.setattr(iep.time, "sleep", lambda s: None)
    client = _FakeClient(poll_statuses=["NotStarted", "Running", "Succeeded"])
    res = iep._install_one(client, _pkgs(), SN_HRSD_HR, PARENT_HR,
                           dry_run=False, timeout=60)
    assert res["action"] == "installed"
    assert res["exit_code"] == 0
    assert client.installed == [SN_HRSD_HR]


def test_install_one_failure(monkeypatch):
    monkeypatch.setattr(iep.time, "sleep", lambda s: None)
    client = _FakeClient(poll_statuses=["Running", "Failed"])
    res = iep._install_one(client, _pkgs(), SN_HRSD_HR, PARENT_HR,
                           dry_run=False, timeout=60)
    assert res["action"] == "install_failed"
    assert res["exit_code"] == 6
    assert res["status"] == "Failed"


def test_install_one_no_operation_id():
    class _NoOpClient(_FakeClient):
        def install_application_package(self, unique_name):
            self.installed.append(unique_name)
            return {"status_code": 202, "operation_id": None, "body": {}}

    res = iep._install_one(_NoOpClient(), _pkgs(), SN_HRSD_HR, PARENT_HR,
                           dry_run=False, timeout=60)
    assert res["action"] == "no_operation"
    assert res["exit_code"] == 6


def test_poll_install_times_out(monkeypatch):
    # monotonic returns past the deadline immediately on the second call.
    times = iter([0.0, 100.0, 200.0])
    monkeypatch.setattr(iep.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(iep.time, "sleep", lambda s: None)

    class _NeverDone:
        def get_operation(self, op):
            return {"status": "Running"}

    out = iep._poll_install(_NeverDone(), "op", timeout=1)
    assert out["status"] == "Timeout"


def test_poll_install_emits_heartbeat(monkeypatch):
    clock = iter(float(n) for n in range(0, 100))
    monkeypatch.setattr(iep.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(iep.time, "sleep", lambda s: None)

    class _RunningThenDone:
        def __init__(self):
            self.calls = 0

        def get_operation(self, op):
            self.calls += 1
            return {"status": "Running" if self.calls == 1 else "Succeeded"}

    beats: list[str] = []
    out = iep._poll_install(_RunningThenDone(), "op", timeout=60,
                            progress=beats.append)
    assert out["status"] == "Succeeded"
    # At least one heartbeat emitted while the operation was still Running.
    assert beats and "still installing" in beats[0]


# ── run() orchestration ──────────────────────────────────────────────
@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(iep.auth, "authenticate", lambda env: "dvtok")
    monkeypatch.setattr(iep.auth, "discover_tenant", lambda env: "tenant")
    monkeypatch.setattr(iep, "_resolve_environment_id",
                        lambda env_url, tok, env_id, tid: env_id or "env-guid")


def _install_client(monkeypatch, packages, poll_statuses=None):
    client = _FakeClient(poll_statuses=poll_statuses)
    client._packages = packages

    def _auth(interactive=True):
        return "pac-token"

    client.authenticate = _auth
    client.list_application_packages = lambda: packages
    monkeypatch.setattr(iep, "PPEnvClient", lambda tenant, env_id: client)
    return client


def test_run_already_installed(patched, monkeypatch):
    _install_client(monkeypatch, _pkgs(target_state="Installed"))
    result = iep.run("https://org.crm.dynamics.com", config=CONFIG_HR,
                     environment_id="env-guid", packages=[SN_HRSD_HR],
                     timeout=60, dry_run=False)
    assert result["exit_code"] == 0
    assert result["results"][0]["action"] == "already_installed"


def test_run_installs_via_scope(patched, monkeypatch):
    monkeypatch.setattr(iep.time, "sleep", lambda s: None)
    client = _install_client(monkeypatch, _pkgs(), poll_statuses=["Succeeded"])
    config = dict(CONFIG_HR, scope={"hrsd": True, "itsm": False})
    result = iep.run("https://org.crm.dynamics.com", config=config,
                     environment_id="env-guid", packages=None,
                     timeout=60, dry_run=False)
    assert result["exit_code"] == 0
    assert client.installed == [SN_HRSD_HR]
    assert result["persona"] == "hr"


def test_run_parent_missing(patched, monkeypatch):
    _install_client(monkeypatch, _pkgs(parent_state="None"))
    config = dict(CONFIG_HR, scope={"hrsd": True})
    result = iep.run("https://org.crm.dynamics.com", config=config,
                     environment_id="env-guid", packages=None,
                     timeout=60, dry_run=False)
    assert result["exit_code"] == 3


def test_run_no_targets(patched, monkeypatch):
    _install_client(monkeypatch, _pkgs())
    result = iep.run("https://org.crm.dynamics.com",
                     config={"schemaName": "unknown"},
                     environment_id="env-guid", packages=None,
                     timeout=60, dry_run=False)
    assert result["exit_code"] == 4
    assert result["action"] == "no_targets"


def test_run_no_targets_when_scope_all_false(patched, monkeypatch):
    # Regression: a valid HR persona with NO product selected must install
    # nothing (fail closed), not silently install both HRSD and ITSM.
    client = _install_client(monkeypatch, _pkgs())
    config = dict(CONFIG_HR, scope={"hrsd": False, "itsm": False})
    result = iep.run("https://org.crm.dynamics.com", config=config,
                     environment_id="env-guid", packages=None,
                     timeout=60, dry_run=False)
    assert result["exit_code"] == 4
    assert result["action"] == "no_targets"
    assert "no servicenow product is selected" in result["message"].lower()
    assert client.installed == []


def test_run_dry_run_no_install(patched, monkeypatch):
    client = _install_client(monkeypatch, _pkgs())
    result = iep.run("https://org.crm.dynamics.com", config=CONFIG_HR,
                     environment_id="env-guid", packages=[SN_HRSD_HR],
                     timeout=60, dry_run=True)
    assert result["exit_code"] == 0
    assert client.installed == []
    assert result["results"][0]["action"] == "would_install"
