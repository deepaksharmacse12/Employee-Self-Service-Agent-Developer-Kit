# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for solutions/ess-maker-skills/scripts/bind_connections.py.

Covers the pure resolution helpers and the ``run()`` orchestration across
every branch: already-bound (no-op), sibling-reuse, single active connection,
most-recent-wins disambiguation, zero active connections, missing reference,
and dry-run (no write).

All network/auth is monkeypatched — no live calls.
"""

from __future__ import annotations

import pytest

import bind_connections as bc


SN_CONNECTOR = "/providers/Microsoft.PowerApps/apis/shared_service-now"
OTHER_CONNECTOR = "/providers/Microsoft.PowerApps/apis/shared_commondataserviceforapps"


def _ref(*, rid, connectionid=None, statuscode=1, connector=SN_CONNECTOR,
         name="msdyn_x.bot.shared_service-now"):
    return {
        "connectionreferenceid": rid,
        "connectionreferencelogicalname": name,
        "connectorid": connector,
        "connectionid": connectionid,
        "statuscode": statuscode,
    }


def _conn(*, name, status="Connected", created="2026-01-01T00:00:00Z",
          owner="maker@contoso.com", api=SN_CONNECTOR, display="ServiceNow"):
    return {
        "name": name,
        "properties": {
            "apiId": api,
            "displayName": display,
            "statuses": [{"status": status}],
            "createdTime": created,
            "createdBy": {"userPrincipalName": owner},
        },
    }


# ── pure helpers ─────────────────────────────────────────────────────

def test_select_servicenow_refs_filters_by_connector():
    rows = [
        _ref(rid="a", connector=SN_CONNECTOR),
        _ref(rid="b", connector=OTHER_CONNECTOR),
    ]
    got = bc.select_servicenow_refs(rows)
    assert [r["connectionreferenceid"] for r in got] == ["a"]


def test_ref_is_bound_requires_connectionid_and_active():
    assert bc.ref_is_bound(_ref(rid="a", connectionid="c1", statuscode=1))
    assert not bc.ref_is_bound(_ref(rid="a", connectionid=None, statuscode=1))
    assert not bc.ref_is_bound(_ref(rid="a", connectionid="c1", statuscode=2))


def test_sibling_connection_id_returns_bound_peer():
    target = _ref(rid="t", connectionid=None)
    peer = _ref(rid="p", connectionid="conn-9")
    assert bc.sibling_connection_id([target, peer], target) == "conn-9"


def test_sibling_connection_id_ignores_self_and_unbound():
    target = _ref(rid="t", connectionid=None)
    other = _ref(rid="o", connectionid=None)
    assert bc.sibling_connection_id([target, other], target) is None


def test_filter_servicenow_connections_keeps_only_connected_sn():
    conns = [
        _conn(name="sn-ok"),
        _conn(name="sn-down", status="Error"),
        _conn(name="dv", api=OTHER_CONNECTOR, display="Dataverse"),
    ]
    got = bc.filter_servicenow_connections(conns)
    assert [c["name"] for c in got] == ["sn-ok"]


def test_pick_connection_most_recent_wins():
    conns = [
        _conn(name="old", created="2026-01-01T00:00:00Z"),
        _conn(name="new", created="2026-06-01T00:00:00Z"),
        _conn(name="mid", created="2026-03-01T00:00:00Z"),
    ]
    chosen, total = bc.pick_connection(conns)
    assert chosen["name"] == "new"
    assert total == 3


def test_pick_connection_empty():
    assert bc.pick_connection([]) == (None, 0)


# ── run() orchestration ──────────────────────────────────────────────

@pytest.fixture
def patched(monkeypatch):
    """Patch auth + discovery on the module; capture any PATCH write."""
    calls = {"update": []}

    monkeypatch.setattr(bc.auth, "authenticate", lambda env: "tok")
    monkeypatch.setattr(
        bc.auth, "update_record",
        lambda env, tok, ent, rid, data: calls["update"].append((ent, rid, data)),
    )
    return calls


def test_run_already_bound_no_write(patched, monkeypatch):
    monkeypatch.setattr(
        bc.auth, "query_all",
        lambda *a, **k: [_ref(rid="t", connectionid="c1", statuscode=1)],
    )
    res = bc.run("https://x", environment_id=None, dry_run=False)
    assert res["action"] == "already_bound"
    assert res["exit_code"] == 0
    assert patched["update"] == []


def test_run_no_reference(patched, monkeypatch):
    monkeypatch.setattr(
        bc.auth, "query_all",
        lambda *a, **k: [_ref(rid="d", connector=OTHER_CONNECTOR)],
    )
    res = bc.run("https://x", environment_id=None, dry_run=False)
    assert res["action"] == "no_reference"
    assert res["exit_code"] == 3
    assert patched["update"] == []


def test_run_sibling_reuse_binds_without_bap(patched, monkeypatch):
    refs = [
        _ref(rid="t", connectionid=None),
        _ref(rid="p", connectionid="conn-sib"),
    ]
    monkeypatch.setattr(bc.auth, "query_all", lambda *a, **k: refs)

    def _boom(*a, **k):
        raise AssertionError("BAP discovery should not run when a sibling exists")

    monkeypatch.setattr(bc, "_discover_connections", _boom)
    res = bc.run("https://x", environment_id=None, dry_run=False)
    assert res["action"] == "bound"
    assert res["connection_id"] == "conn-sib"
    assert res["source"] == "sibling reference"
    assert patched["update"] == [
        ("connectionreferences", "t", {"connectionid": "conn-sib"})
    ]


def test_run_binds_single_active_connection(patched, monkeypatch):
    monkeypatch.setattr(
        bc.auth, "query_all", lambda *a, **k: [_ref(rid="t", connectionid=None)]
    )
    monkeypatch.setattr(
        bc, "_discover_connections", lambda *a, **k: [_conn(name="only-conn")]
    )
    res = bc.run("https://x", environment_id=None, dry_run=False)
    assert res["action"] == "bound"
    assert res["connection_id"] == "only-conn"
    assert patched["update"] == [
        ("connectionreferences", "t", {"connectionid": "only-conn"})
    ]


def test_run_binds_most_recent_and_reports(patched, monkeypatch):
    monkeypatch.setattr(
        bc.auth, "query_all", lambda *a, **k: [_ref(rid="t", connectionid=None)]
    )
    monkeypatch.setattr(
        bc, "_discover_connections",
        lambda *a, **k: [
            _conn(name="old", created="2026-01-01T00:00:00Z", owner="a@x"),
            _conn(name="new", created="2026-06-01T00:00:00Z", owner="b@x"),
        ],
    )
    res = bc.run("https://x", environment_id=None, dry_run=False)
    assert res["connection_id"] == "new"
    assert res["candidate_count"] == 2
    assert res["owner"] == "b@x"
    assert "most recently created" in res["message"]
    assert patched["update"][0][2] == {"connectionid": "new"}


def test_run_no_active_connection(patched, monkeypatch):
    monkeypatch.setattr(
        bc.auth, "query_all", lambda *a, **k: [_ref(rid="t", connectionid=None)]
    )
    monkeypatch.setattr(bc, "_discover_connections", lambda *a, **k: [])
    res = bc.run("https://x", environment_id=None, dry_run=False)
    assert res["action"] == "no_connection"
    assert res["exit_code"] == 4
    assert patched["update"] == []


def test_run_dry_run_does_not_write(patched, monkeypatch):
    monkeypatch.setattr(
        bc.auth, "query_all", lambda *a, **k: [_ref(rid="t", connectionid=None)]
    )
    monkeypatch.setattr(
        bc, "_discover_connections", lambda *a, **k: [_conn(name="c1")]
    )
    res = bc.run("https://x", environment_id=None, dry_run=True)
    assert res["action"] == "would_bind"
    assert res["exit_code"] == 0
    assert res["connection_id"] == "c1"
    assert patched["update"] == []
    assert res["message"].startswith("Would bind")


# ── Dataverse + multi-connector ──────────────────────────────────────

def test_select_refs_matches_dataverse_connector():
    rows = [
        _ref(rid="sn", connector=SN_CONNECTOR),
        _ref(rid="dv", connector=OTHER_CONNECTOR),
    ]
    got = bc.select_refs(rows, bc._CONNECTOR_KEYWORDS["dataverse"])
    assert [r["connectionreferenceid"] for r in got] == ["dv"]


def test_filter_connections_keeps_only_connected_dataverse():
    conns = [
        _conn(name="dv-ok", api=OTHER_CONNECTOR, display="Dataverse"),
        _conn(name="dv-down", api=OTHER_CONNECTOR, display="Dataverse", status="Error"),
        _conn(name="sn", api=SN_CONNECTOR, display="ServiceNow"),
    ]
    got = bc.filter_connections(conns, bc._CONNECTOR_KEYWORDS["dataverse"])
    assert [c["name"] for c in got] == ["dv-ok"]


def test_run_dataverse_binds_all_unbound_refs(patched, monkeypatch):
    refs = [
        _ref(rid="dv1", connector=OTHER_CONNECTOR, name="pack.bot.dv1"),
        _ref(rid="dv2", connector=OTHER_CONNECTOR, name="pack.bot.dv2"),
        _ref(rid="sn", connector=SN_CONNECTOR),  # different connector, ignored
    ]
    monkeypatch.setattr(bc.auth, "query_all", lambda *a, **k: refs)
    monkeypatch.setattr(
        bc, "_discover_connections",
        lambda *a, **k: [_conn(name="dv-conn", api=OTHER_CONNECTOR, display="Dataverse")],
    )
    res = bc.run("https://x", environment_id=None, dry_run=False,
                 connector="dataverse")
    assert res["action"] == "bound"
    assert res["connection_id"] == "dv-conn"
    assert set(res["bound_references"]) == {"pack.bot.dv1", "pack.bot.dv2"}
    assert patched["update"] == [
        ("connectionreferences", "dv1", {"connectionid": "dv-conn"}),
        ("connectionreferences", "dv2", {"connectionid": "dv-conn"}),
    ]
    assert "2 Dataverse connection references" in res["message"]


def test_run_dataverse_no_reference(patched, monkeypatch):
    monkeypatch.setattr(
        bc.auth, "query_all", lambda *a, **k: [_ref(rid="sn", connector=SN_CONNECTOR)]
    )
    res = bc.run("https://x", environment_id=None, dry_run=False,
                 connector="dataverse")
    assert res["action"] == "no_reference"
    assert res["exit_code"] == 3
    assert patched["update"] == []


def test_run_all_binds_both_connectors(patched, monkeypatch):
    refs = [
        _ref(rid="sn", connector=SN_CONNECTOR, name="pack.bot.sn"),
        _ref(rid="dv1", connector=OTHER_CONNECTOR, name="pack.bot.dv1"),
    ]
    monkeypatch.setattr(bc.auth, "query_all", lambda *a, **k: refs)

    def _disco(env, tok, env_id):
        return [
            _conn(name="sn-conn", api=SN_CONNECTOR, display="ServiceNow"),
            _conn(name="dv-conn", api=OTHER_CONNECTOR, display="Dataverse"),
        ]

    monkeypatch.setattr(bc, "_discover_connections", _disco)
    res = bc.run("https://x", environment_id=None, dry_run=False, connector="all")
    assert res["action"] == "multi"
    assert res["exit_code"] == 0
    actions = {r["connector"]: r["action"] for r in res["results"]}
    assert actions == {"servicenow": "bound", "dataverse": "bound"}
    assert ("connectionreferences", "sn", {"connectionid": "sn-conn"}) in patched["update"]
    assert ("connectionreferences", "dv1", {"connectionid": "dv-conn"}) in patched["update"]


def test_run_all_tolerates_missing_reference(patched, monkeypatch):
    # Only ServiceNow refs present; Dataverse absent -> 'all' still succeeds.
    monkeypatch.setattr(
        bc.auth, "query_all",
        lambda *a, **k: [_ref(rid="sn", connector=SN_CONNECTOR)],
    )
    monkeypatch.setattr(
        bc, "_discover_connections", lambda *a, **k: [_conn(name="sn-conn")]
    )
    res = bc.run("https://x", environment_id=None, dry_run=False, connector="all")
    assert res["action"] == "multi"
    assert res["exit_code"] == 0
    actions = {r["connector"]: r["action"] for r in res["results"]}
    assert actions["servicenow"] == "bound"
    assert actions["dataverse"] == "no_reference"


def test_run_all_fails_when_no_connection(patched, monkeypatch):
    monkeypatch.setattr(
        bc.auth, "query_all",
        lambda *a, **k: [_ref(rid="dv1", connector=OTHER_CONNECTOR)],
    )
    monkeypatch.setattr(bc, "_discover_connections", lambda *a, **k: [])
    res = bc.run("https://x", environment_id=None, dry_run=False, connector="all")
    assert res["action"] == "multi"
    assert res["exit_code"] == 4
    assert patched["update"] == []
