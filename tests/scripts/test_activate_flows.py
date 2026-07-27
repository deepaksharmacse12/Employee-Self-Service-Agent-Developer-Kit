# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for solutions/ess-maker-skills/scripts/activate_flows.py.

Covers the pure selection/state helpers and the ``run()`` orchestration across
every branch: already-on (no write), turn-on-some, dry-run (no write),
no-flows, and the ServiceNow name filter.

All network/auth is monkeypatched — no live calls.
"""

from __future__ import annotations

import pytest

import activate_flows as af


def _flow(*, wid, name="ESS HR ServiceNow HRSD Common Orchestrator",
          statecode=1, statuscode=2):
    return {
        "workflowid": wid,
        "name": name,
        "category": 5,
        "type": 1,
        "statecode": statecode,
        "statuscode": statuscode,
    }


# ── pure helpers ─────────────────────────────────────────────────────

def test_select_servicenow_flows_filters_by_name():
    rows = [
        _flow(wid="a", name="ESS HR ServiceNow HRSD Create Case"),
        _flow(wid="b", name="Some Workday Flow"),
        _flow(wid="c", name="ESS IT Service-Now ITSM Get Incidents"),
    ]
    got = af.select_servicenow_flows(rows)
    assert [f["workflowid"] for f in got] == ["a", "c"]


def test_flow_is_activated_requires_both_codes():
    assert af.flow_is_activated(_flow(wid="a", statecode=1, statuscode=2))
    assert not af.flow_is_activated(_flow(wid="a", statecode=0, statuscode=1))
    assert not af.flow_is_activated(_flow(wid="a", statecode=1, statuscode=1))


def test_flows_to_activate_returns_only_off():
    flows = [
        _flow(wid="on", statecode=1, statuscode=2),
        _flow(wid="off", statecode=0, statuscode=1),
    ]
    assert [f["workflowid"] for f in af.flows_to_activate(flows)] == ["off"]


# ── run() orchestration ──────────────────────────────────────────────

@pytest.fixture
def patched(monkeypatch):
    """Patch auth; capture any PATCH write."""
    calls = {"update": []}
    monkeypatch.setattr(af.auth, "authenticate", lambda env: "tok")
    monkeypatch.setattr(
        af.auth, "update_record",
        lambda env, tok, ent, rid, data: calls["update"].append((ent, rid, data)),
    )
    return calls


def test_run_no_flows(patched, monkeypatch):
    monkeypatch.setattr(
        af.auth, "query_all",
        lambda *a, **k: [_flow(wid="w", name="Some Workday Flow")],
    )
    res = af.run("https://x", environment_id=None, dry_run=False)
    assert res["action"] == "no_flows"
    assert res["exit_code"] == 3
    assert patched["update"] == []


def test_run_already_on_no_write(patched, monkeypatch):
    monkeypatch.setattr(
        af.auth, "query_all",
        lambda *a, **k: [
            _flow(wid="a", statecode=1, statuscode=2),
            _flow(wid="b", statecode=1, statuscode=2),
        ],
    )
    res = af.run("https://x", environment_id=None, dry_run=False)
    assert res["action"] == "already_on"
    assert res["exit_code"] == 0
    assert res["flow_count"] == 2
    assert patched["update"] == []


def test_run_activates_off_flows(patched, monkeypatch):
    monkeypatch.setattr(
        af.auth, "query_all",
        lambda *a, **k: [
            _flow(wid="on", name="ESS HR ServiceNow HRSD On", statecode=1, statuscode=2),
            _flow(wid="off1", name="ESS HR ServiceNow HRSD Off1", statecode=0, statuscode=1),
            _flow(wid="off2", name="ESS HR ServiceNow HRSD Off2", statecode=0, statuscode=1),
        ],
    )
    res = af.run("https://x", environment_id=None, dry_run=False)
    assert res["action"] == "activated"
    assert res["exit_code"] == 0
    assert res["flow_count"] == 3
    assert set(res["activated_flows"]) == {
        "ESS HR ServiceNow HRSD Off1", "ESS HR ServiceNow HRSD Off2"
    }
    assert patched["update"] == [
        ("workflows", "off1", {"statecode": 1, "statuscode": 2}),
        ("workflows", "off2", {"statecode": 1, "statuscode": 2}),
    ]


def test_run_dry_run_does_not_write(patched, monkeypatch):
    monkeypatch.setattr(
        af.auth, "query_all",
        lambda *a, **k: [_flow(wid="off", statecode=0, statuscode=1)],
    )
    res = af.run("https://x", environment_id=None, dry_run=True)
    assert res["action"] == "would_activate"
    assert res["exit_code"] == 0
    assert res["activated_flows"] == ["ESS HR ServiceNow HRSD Common Orchestrator"]
    assert patched["update"] == []
    assert res["message"].startswith("Would turn on")
