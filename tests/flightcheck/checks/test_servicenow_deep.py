# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Unit tests for the ServiceNow checks that lacked coverage:
``_check_flow_status`` (SN-FLOW-*), ``_check_template_configs``
(SN-CFG-*), and ``_check_local_topics`` (SN-LOCAL-*).

The connection helper (SN-CONN-*) is covered separately in
``test_servicenow_connections.py``. Flow status and local topics are
pure-logic (flow dicts / local files); template configs reads Dataverse
via ``query_all``, which is stubbed here (the Dataverse contract itself
is exercised by the connection/env tests).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.mocks import pp_admin as pp


@pytest.fixture(autouse=True)
def _scripts_on_path():
    repo_root = Path(__file__).resolve().parents[3]
    scripts_dir = repo_root / "solutions" / "ess-maker-skills" / "scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        yield
    finally:
        try:
            sys.path.remove(str(scripts_dir))
        except ValueError:
            pass


def _by_id(results, cid):
    matches = [r for r in results if r.checkpoint_id == cid]
    assert len(matches) == 1, [r.checkpoint_id for r in results]
    return matches[0]


# --------------------------------------------------------------------------
# _check_flow_status — SN-FLOW-000 summary + SN-FLOW-NNN per flow
# --------------------------------------------------------------------------

def test_flow_status_all_enabled():
    from flightcheck.checks.servicenow import _check_flow_status
    flows = [
        pp.flow(display_name="ServiceNow HRSD Create Case", state="Started"),
        pp.flow(display_name="ServiceNow ITSM Create Ticket", state="Started"),
    ]
    results = _check_flow_status(SimpleNamespace(), flows)

    summary = _by_id(results, "SN-FLOW-000")
    assert summary.status == "Passed"
    assert "2 enabled, 0 disabled" in summary.result

    first = _by_id(results, "SN-FLOW-001")
    assert first.status == "Passed"
    assert "Enabled" in first.result


def test_flow_status_one_disabled_warns_and_fails_row():
    from flightcheck.checks.servicenow import _check_flow_status
    flows = [
        pp.flow(display_name="ServiceNow HRSD Create Case", state="Started"),
        pp.flow(display_name="ServiceNow ITSM Create Ticket", state="Stopped"),
    ]
    results = _check_flow_status(SimpleNamespace(), flows)

    summary = _by_id(results, "SN-FLOW-000")
    assert summary.status == "Warning"
    assert "1 enabled, 1 disabled" in summary.result
    assert "enable them in Power Automate" in summary.remediation

    disabled = _by_id(results, "SN-FLOW-002")
    assert disabled.status == "Failed"
    assert "Enable" in disabled.remediation


# --------------------------------------------------------------------------
# _check_template_configs — SN-CFG-001 + per-pack SN-CFG-010 / SN-CFG-020
# --------------------------------------------------------------------------

_ALL_SCENARIOS = [
    "ServiceNowHRSDCreateCase", "ServiceNowHRSDGetCaseDetails",
    "ServiceNowHRSDGetCasesList",
    "ServiceNowITSMCreateTicket", "ServiceNowITSMGetTicketDetails",
    "ServiceNowITSMGetUserTickets", "ServiceNowITSMUpdateTicket",
]


def test_template_configs_all_present(monkeypatch):
    import auth
    monkeypatch.setattr(
        auth, "query_all",
        lambda *a, **kw: [{"msdyn_name": s} for s in _ALL_SCENARIOS],
    )
    from flightcheck.checks.servicenow import _check_template_configs
    runner = SimpleNamespace(env_url="https://org.crm.dynamics.com", dv_token="t")
    results = _check_template_configs(runner)

    cfg = _by_id(results, "SN-CFG-001")
    assert cfg.status == "Passed"
    assert "7 ServiceNow template config(s)" in cfg.result
    # Per-pack completeness rows.
    assert _by_id(results, "SN-CFG-010").status == "Passed"
    assert _by_id(results, "SN-CFG-020").status == "Passed"


def test_template_configs_none_found(monkeypatch):
    import auth
    monkeypatch.setattr(auth, "query_all", lambda *a, **kw: [])
    from flightcheck.checks.servicenow import _check_template_configs
    runner = SimpleNamespace(env_url="https://org.crm.dynamics.com", dv_token="t")
    cfg = _by_id(_check_template_configs(runner), "SN-CFG-001")
    assert cfg.status == "NotConfigured"
    assert "No ServiceNow template configs" in cfg.result
    assert "extension pack" in cfg.remediation


def test_template_configs_skipped_without_token():
    from flightcheck.checks.servicenow import _check_template_configs
    runner = SimpleNamespace(env_url="", dv_token="")
    cfg = _by_id(_check_template_configs(runner), "SN-CFG-001")
    assert cfg.status == "Skipped"
    assert "Dataverse token not available" in cfg.result


# --------------------------------------------------------------------------
# _check_local_topics — SN-LOCAL-001/002/003
# --------------------------------------------------------------------------

def _make_agent(tmp_path, files: dict[str, str]):
    agent = tmp_path / "workspace" / "agents" / "ess-hr"
    topics = agent / "topics"
    topics.mkdir(parents=True)
    for name, content in files.items():
        (topics / name).write_text(content, encoding="utf-8")


def test_local_topics_hrsd_and_itsm_present(tmp_path, monkeypatch):
    _make_agent(tmp_path, {
        "servicenowhrsdcreatecase.mcs.yml": "kind: x\nServiceNow case",
        "servicenowitsmcreateticket.mcs.yml": "kind: x\nServiceNow ticket",
    })
    monkeypatch.chdir(tmp_path)
    from flightcheck.checks.servicenow import _check_local_topics
    results = _check_local_topics(SimpleNamespace())

    assert _by_id(results, "SN-LOCAL-001").status == "Passed"
    assert _by_id(results, "SN-LOCAL-002").status == "Passed"   # HRSD
    assert _by_id(results, "SN-LOCAL-003").status == "Passed"   # ITSM


def test_local_topics_none_found_not_configured(tmp_path, monkeypatch):
    _make_agent(tmp_path, {"weather.mcs.yml": "kind: x\nno integration here"})
    monkeypatch.chdir(tmp_path)
    from flightcheck.checks.servicenow import _check_local_topics
    r = _by_id(_check_local_topics(SimpleNamespace()), "SN-LOCAL-001")
    assert r.status == "NotConfigured"
    assert "No ServiceNow topics found" in r.result


# --------------------------------------------------------------------------
# _check_dataverse_connection — SN-DV-CONN-001 (connector-generic Dataverse
# reference binding; sibling of the Workday DV-CONN-001 but matched by
# connector instead of the ..._92b66 logical-name suffix).
# --------------------------------------------------------------------------

_DV_CONNECTOR = (
    "/providers/Microsoft.PowerApps/apis/shared_commondataserviceforapps"
)


def _dv_ref(logical, *, connectionid="c1", statuscode=1, connector=_DV_CONNECTOR):
    return {
        "connectionreferencelogicalname": logical,
        "connectionreferencedisplayname": logical,
        "connectorid": connector,
        "connectionid": connectionid,
        "statuscode": statuscode,
    }


def _dv_runner():
    return SimpleNamespace(
        env_url="https://org.crm.dynamics.com", dv_token="t",
        pp_admin=None, env_id="env-1",
    )


def test_dataverse_connection_all_bound_active(monkeypatch):
    import auth
    monkeypatch.setattr(auth, "query_all", lambda *a, **kw: [
        _dv_ref("msdyn_Dataverse"),
        _dv_ref("new_sharedcommondataserviceforapps_41c83"),
        # A non-Dataverse ref that must be ignored.
        _dv_ref("msdyn_service_now",
                connector="/providers/x/apis/shared_service-now"),
    ])
    from flightcheck.checks.servicenow import _check_dataverse_connection
    r = _by_id(_check_dataverse_connection(_dv_runner()), "SN-DV-CONN-001")
    assert r.status == "Passed"
    assert "All 2 Dataverse connection reference(s)" in r.result


def test_dataverse_connection_none_found_not_configured(monkeypatch):
    import auth
    monkeypatch.setattr(auth, "query_all", lambda *a, **kw: [
        _dv_ref("msdyn_service_now",
                connector="/providers/x/apis/shared_service-now"),
    ])
    from flightcheck.checks.servicenow import _check_dataverse_connection
    r = _by_id(_check_dataverse_connection(_dv_runner()), "SN-DV-CONN-001")
    assert r.status == "NotConfigured"
    assert "shared_commondataserviceforapps" in r.result
    assert "extension pack" in r.remediation


def test_dataverse_connection_unbound_fails(monkeypatch):
    import auth
    monkeypatch.setattr(auth, "query_all", lambda *a, **kw: [
        _dv_ref("msdyn_Dataverse"),
        _dv_ref("new_sharedcommondataserviceforapps_41c83", connectionid=None),
    ])
    from flightcheck.checks.servicenow import _check_dataverse_connection
    r = _by_id(_check_dataverse_connection(_dv_runner()), "SN-DV-CONN-001")
    assert r.status == "Failed"
    assert "unbound" in r.result
    assert "new_sharedcommondataserviceforapps_41c83" in r.result


def test_dataverse_connection_inactive_fails(monkeypatch):
    import auth
    monkeypatch.setattr(auth, "query_all", lambda *a, **kw: [
        _dv_ref("new_sharedcommondataserviceforapps_41c83", statuscode=2),
    ])
    from flightcheck.checks.servicenow import _check_dataverse_connection
    r = _by_id(_check_dataverse_connection(_dv_runner()), "SN-DV-CONN-001")
    assert r.status == "Failed"
    assert "inactive" in r.result


def test_dataverse_connection_skipped_without_token():
    from flightcheck.checks.servicenow import _check_dataverse_connection
    runner = SimpleNamespace(env_url="", dv_token="")
    r = _by_id(_check_dataverse_connection(runner), "SN-DV-CONN-001")
    assert r.status == "Skipped"
    assert "Dataverse token not available" in r.result


def test_dataverse_connection_query_error_warns(monkeypatch):
    import auth

    def _boom(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(auth, "query_all", _boom)
    from flightcheck.checks.servicenow import _check_dataverse_connection
    r = _by_id(_check_dataverse_connection(_dv_runner()), "SN-DV-CONN-001")
    assert r.status == "Warning"
    assert "boom" in r.result


def test_servicenow_dataverse_wrapper_self_contained(monkeypatch):
    """The wrapper emits SN-DV-CONN-001 with no _servicenow_flows gate."""
    import auth
    monkeypatch.setattr(auth, "query_all", lambda *a, **kw: [
        _dv_ref("new_sharedcommondataserviceforapps_41c83"),
    ])
    from flightcheck.checks.servicenow import run_servicenow_dataverse_checks
    # No _servicenow_flows attribute at all — must still emit.
    r = _by_id(run_servicenow_dataverse_checks(_dv_runner()), "SN-DV-CONN-001")
    assert r.status == "Passed"


# --------------------------------------------------------------------------
# _check_portal_base_url — SN-BASEURL-001 (portal base URL set on the
# per-product parent template-config record; P6.6 / S6.5).
# --------------------------------------------------------------------------
import json as _json


def _portal_row(name, uri=None, *, raw=None):
    """A template-config parent row. ``uri=None`` → key present but empty."""
    if raw is not None:
        value = raw
    else:
        value = _json.dumps({"ServiceNowPortalBaseURI": uri or ""})
    return {"msdyn_name": name, "msdyn_value": value}


def _portal_runner():
    return SimpleNamespace(env_url="https://org.crm.dynamics.com", dv_token="t")


def test_portal_base_url_set_passes(monkeypatch):
    import auth
    monkeypatch.setattr(auth, "query_all", lambda *a, **kw: [
        _portal_row("msdyn_ServiceNowHRSD", "https://dev184242.service-now.com/sp"),
        # Unrelated child record must be ignored.
        _portal_row("msdyn_ServiceNowHRSDGetCaseDetails", ""),
    ])
    from flightcheck.checks.servicenow import _check_portal_base_url
    r = _by_id(_check_portal_base_url(_portal_runner()), "SN-BASEURL-001")
    assert r.status == "Passed"
    assert "HRSD" in r.result
    assert "Note:" not in r.result


def test_portal_base_url_non_portal_path_passes_with_note(monkeypatch):
    import auth
    monkeypatch.setattr(auth, "query_all", lambda *a, **kw: [
        _portal_row("msdyn_ServiceNowHRSD", "https://dev184242.service-now.com"),
    ])
    from flightcheck.checks.servicenow import _check_portal_base_url
    r = _by_id(_check_portal_base_url(_portal_runner()), "SN-BASEURL-001")
    assert r.status == "Passed"
    assert "Service Portal path" in r.result


def test_portal_base_url_empty_fails(monkeypatch):
    import auth
    monkeypatch.setattr(auth, "query_all", lambda *a, **kw: [
        _portal_row("msdyn_ServiceNowHRSD", ""),
    ])
    from flightcheck.checks.servicenow import _check_portal_base_url
    r = _by_id(_check_portal_base_url(_portal_runner()), "SN-BASEURL-001")
    assert r.status == "Failed"
    assert "empty for HRSD" in r.result


def test_portal_base_url_malformed_fails(monkeypatch):
    import auth
    monkeypatch.setattr(auth, "query_all", lambda *a, **kw: [
        _portal_row("msdyn_ServiceNowITSM", "dev184242.service-now.com/sp"),
    ])
    from flightcheck.checks.servicenow import _check_portal_base_url
    r = _by_id(_check_portal_base_url(_portal_runner()), "SN-BASEURL-001")
    assert r.status == "Failed"
    assert "not a URL for ITSM" in r.result


def test_portal_base_url_no_parent_not_configured(monkeypatch):
    import auth
    monkeypatch.setattr(auth, "query_all", lambda *a, **kw: [
        _portal_row("msdyn_ServiceNowHRSDGetCaseDetails", "x"),  # child only
    ])
    from flightcheck.checks.servicenow import _check_portal_base_url
    r = _by_id(_check_portal_base_url(_portal_runner()), "SN-BASEURL-001")
    assert r.status == "NotConfigured"
    assert "not installed" in r.result


def test_portal_base_url_skipped_without_token():
    from flightcheck.checks.servicenow import _check_portal_base_url
    runner = SimpleNamespace(env_url="", dv_token="")
    r = _by_id(_check_portal_base_url(runner), "SN-BASEURL-001")
    assert r.status == "Skipped"


def test_portal_base_url_bad_json_treated_as_unset(monkeypatch):
    import auth
    monkeypatch.setattr(auth, "query_all", lambda *a, **kw: [
        _portal_row("msdyn_ServiceNowHRSD", raw="not-json"),
    ])
    from flightcheck.checks.servicenow import _check_portal_base_url
    r = _by_id(_check_portal_base_url(_portal_runner()), "SN-BASEURL-001")
    assert r.status == "Failed"
    assert "empty for HRSD" in r.result


def test_servicenow_portal_wrapper_self_contained(monkeypatch):
    """The wrapper emits SN-BASEURL-001 with no _servicenow_flows gate."""
    import auth
    monkeypatch.setattr(auth, "query_all", lambda *a, **kw: [
        _portal_row("msdyn_ServiceNowHRSD", "https://x.service-now.com/sp"),
    ])
    from flightcheck.checks.servicenow import run_servicenow_portal_checks
    r = _by_id(run_servicenow_portal_checks(_portal_runner()), "SN-BASEURL-001")
    assert r.status == "Passed"


# --------------------------------------------------------------------------
# Skill-3 capture gates — SN-CONFIG-001 / SN-PERM-001 / SN-USER-001.
# Config-only: they read .local/connect/servicenow/config.json relative to cwd.
# --------------------------------------------------------------------------
import json as _json2


def _write_sn_config(tmp_path, cfg):
    cfg_path = tmp_path / ".local" / "connect" / "servicenow" / "config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(_json2.dumps(cfg), encoding="utf-8")


_FULL_BASICS = {
    "instanceUrl": "https://dev184242.service-now.com",
    "connectorType": "powerplatform",
    "scope": {"hrsd": True, "itsm": False},
    "authType": "entra_user",
}


def test_sn_config_basics_complete_passes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_sn_config(tmp_path, dict(_FULL_BASICS))
    from flightcheck.checks.servicenow import _check_config_basics
    r = _by_id(_check_config_basics(None), "SN-CONFIG-001")
    assert r.status == "Passed"
    assert "dev184242" in r.result and "HRSD" in r.result


def test_sn_config_basics_absent_not_configured(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from flightcheck.checks.servicenow import _check_config_basics
    r = _by_id(_check_config_basics(None), "SN-CONFIG-001")
    assert r.status == "NotConfigured"


def test_sn_config_basics_missing_fields_fail(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_sn_config(tmp_path, {
        "instanceUrl": "https://example.com",   # not a service-now host
        "scope": {"hrsd": False, "itsm": False},  # nothing in scope
        "authType": "password",                  # unsupported
    })
    from flightcheck.checks.servicenow import _check_config_basics
    r = _by_id(_check_config_basics(None), "SN-CONFIG-001")
    assert r.status == "Failed"
    assert "instance URL" in r.result
    assert "HRSD or ITSM" in r.result
    assert "sign-in method" in r.result
    assert "connector" in r.result


def test_sn_perm_entra_and_snadmin_pass(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_sn_config(tmp_path, {"makerPermissions": {
        "entraAdmin": True, "serviceNowAdmin": True,
    }})
    from flightcheck.checks.servicenow import _check_maker_permissions
    r = _by_id(_check_maker_permissions(None), "SN-PERM-001")
    assert r.status == "Passed"


def test_sn_perm_no_snadmin_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_sn_config(tmp_path, {"makerPermissions": {
        "entraAdmin": True, "serviceNowAdmin": False,
    }})
    from flightcheck.checks.servicenow import _check_maker_permissions
    r = _by_id(_check_maker_permissions(None), "SN-PERM-001")
    assert r.status == "Failed"
    assert "ServiceNow administrator" in r.result


def test_sn_perm_entra_unconfirmed_manual(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_sn_config(tmp_path, {"makerPermissions": {
        "entraAdmin": False, "serviceNowAdmin": True,
        "entraAdminEvidence": "probe unavailable",
    }})
    from flightcheck.checks.servicenow import _check_maker_permissions
    r = _by_id(_check_maker_permissions(None), "SN-PERM-001")
    assert r.status == "Manual"
    assert "probe unavailable" in r.result


def test_sn_perm_snadmin_unknown_manual(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_sn_config(tmp_path, {"makerPermissions": {
        "entraAdmin": None, "serviceNowAdmin": None,
    }})
    from flightcheck.checks.servicenow import _check_maker_permissions
    r = _by_id(_check_maker_permissions(None), "SN-PERM-001")
    assert r.status == "Manual"


def test_sn_perm_not_probed_not_configured(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_sn_config(tmp_path, dict(_FULL_BASICS))  # no makerPermissions
    from flightcheck.checks.servicenow import _check_maker_permissions
    r = _by_id(_check_maker_permissions(None), "SN-PERM-001")
    assert r.status == "NotConfigured"


def test_sn_user_confirmed_passes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_sn_config(tmp_path, {"userRecord": {
        "activeUserConfirmed": True, "mappedField": "email",
    }})
    from flightcheck.checks.servicenow import _check_user_record
    r = _by_id(_check_user_record(None), "SN-USER-001")
    assert r.status == "Passed"
    assert "email" in r.result


def test_sn_user_unconfirmed_manual(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_sn_config(tmp_path, dict(_FULL_BASICS))  # no userRecord
    from flightcheck.checks.servicenow import _check_user_record
    r = _by_id(_check_user_record(None), "SN-USER-001")
    assert r.status == "Manual"


def test_capture_wrapper_emits_all_three(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_sn_config(tmp_path, dict(_FULL_BASICS))
    from flightcheck.checks.servicenow import run_servicenow_capture_checks
    ids = {r.checkpoint_id for r in run_servicenow_capture_checks(None)}
    assert ids == {"SN-CONFIG-001", "SN-PERM-001", "SN-USER-001"}

