# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Unit tests for ``scripts/pack_catalog.py``.

Covers the persona/scope catalog helpers and the per-product artifact resolver.
The resolver's Dataverse reads are injected via a fake ``query`` so the tests are
pure-logic (no network) — the fabricated rows mirror the real shapes observed in
a live HR-persona environment (see the ``msdyn_EssHRServiceNow*`` packs and the
``…hr.cr.w2LCWZTZ`` / ``new_sharedcommondataserviceforapps_41c83`` references).
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "solutions" / "ess-maker-skills" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import pack_catalog as pc  # noqa: E402


# ── persona / scope catalog helpers ──────────────────────────────────
def test_resolve_persona():
    assert pc.resolve_persona("msdyn_copilotforemployeeselfservicehr") == "hr"
    assert pc.resolve_persona("msdyn_copilotforemployeeselfserviceit") == "it"
    assert pc.resolve_persona("") is None
    assert pc.resolve_persona(None) is None


def test_servicenow_packages_scope_gated():
    assert pc.servicenow_packages("hr", {"hrsd": True, "itsm": True}) == [
        "msdyn_EssHRServiceNowHRSD", "msdyn_EssHRServiceNowITSM",
    ]
    assert pc.servicenow_packages("hr", {"itsm": True}) == ["msdyn_EssHRServiceNowITSM"]
    assert pc.servicenow_packages("it", {"hrsd": True}) == ["msdyn_EssITServiceNowHRSD"]
    # Fails closed: empty/all-false scope targets nothing.
    assert pc.servicenow_packages("hr", {}) == []
    assert pc.servicenow_packages("hr", {"hrsd": False, "itsm": False}) == []
    assert pc.servicenow_packages(None, {"hrsd": True}) == []


def test_parent_package():
    assert pc.parent_package("hr") == "msdyn_CopilotForEmployeeSelfServiceHR"
    assert pc.parent_package("it") == "msdyn_CopilotForEmployeeSelfServiceIT"
    assert pc.parent_package(None) is None


# ── pure resolution helpers ──────────────────────────────────────────
def test_solution_by_product_maps_installed_and_missing():
    solutions = [
        {"solutionid": "sid-itsm", "uniquename": "msdyn_EssHRServiceNowITSM"},
        {"solutionid": "sid-other", "uniquename": "msdyn_Unrelated"},
    ]
    out = pc.solution_by_product("hr", {"hrsd": True, "itsm": True}, solutions)
    assert out["itsm"]["solutionid"] == "sid-itsm"
    # HRSD is in scope but its pack is not installed -> None (the drift case).
    assert out["hrsd"] is None
    # Out-of-scope products are omitted entirely.
    assert set(out) == {"hrsd", "itsm"}


def test_solution_by_product_omits_out_of_scope():
    solutions = [
        {"solutionid": "sid-itsm", "uniquename": "msdyn_EssHRServiceNowITSM"},
    ]
    out = pc.solution_by_product("hr", {"itsm": True}, solutions)
    assert set(out) == {"itsm"}


def test_owned_object_ids_lowercases_and_skips_blank():
    comps = [
        {"componenttype": 29, "objectid": "AAA-BBB"},
        {"componenttype": 10038, "objectid": "ccc-ddd"},
        {"componenttype": 1, "objectid": None},
        {"componenttype": 1},
    ]
    assert pc.owned_object_ids(comps) == {"aaa-bbb", "ccc-ddd"}


def test_owned_rows_matches_by_id_case_insensitive():
    rows = [
        {"connectionreferenceid": "AAA-BBB", "connectionreferencelogicalname": "x"},
        {"connectionreferenceid": "eee-fff", "connectionreferencelogicalname": "y"},
    ]
    owned = {"aaa-bbb"}
    got = pc.owned_rows(rows, "connectionreferenceid", owned)
    assert [r["connectionreferencelogicalname"] for r in got] == ["x"]


# ── resolver (with injected query) ───────────────────────────────────
def _fake_env():
    """Return a fake ``query`` callable over a small in-memory Dataverse.

    Models an HR-persona env where only the ITSM pack is installed (solution
    ``sid-itsm``) owning one ServiceNow ref, one shared Dataverse ref, and one
    flow; plus a stray ref/flow owned by an unrelated solution.
    """
    tables = {
        "solutions": [
            {"solutionid": "sid-itsm", "uniquename": "msdyn_EssHRServiceNowITSM"},
        ],
        "connectionreferences": [
            {"connectionreferenceid": "ref-sn", "connectorid": "/x/shared_service-now",
             "connectionreferencelogicalname": "msdyn_copilotforemployeeselfservicehr.cr.w2LCWZTZ",
             "connectionid": None, "statuscode": 1},
            {"connectionreferenceid": "ref-dv", "connectorid": "/x/shared_commondataserviceforapps",
             "connectionreferencelogicalname": "new_sharedcommondataserviceforapps_41c83",
             "connectionid": "conn-dv", "statuscode": 1},
            {"connectionreferenceid": "ref-stray", "connectorid": "/x/shared_service-now",
             "connectionreferencelogicalname": "unrelated", "connectionid": None,
             "statuscode": 1},
        ],
        "workflows": [
            {"workflowid": "wf-1", "name": "ESS HR ServiceNow ITSM Get Tickets List",
             "category": 5, "statecode": 0},
            {"workflowid": "wf-stray", "name": "Other", "category": 5, "statecode": 1},
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


def test_resolve_product_artifacts_attributes_owned_artifacts():
    got = pc.resolve_product_artifacts(
        "https://env", "tok", "hr", {"itsm": True}, query=_fake_env())
    itsm = got["itsm"]
    assert itsm["solutionUniqueName"] == "msdyn_EssHRServiceNowITSM"
    assert itsm["solutionId"] == "sid-itsm"
    # Only the two refs the solution owns — the stray ref is excluded.
    logicals = {r["connectionreferencelogicalname"] for r in itsm["connectionRefs"]}
    assert logicals == {
        "msdyn_copilotforemployeeselfservicehr.cr.w2LCWZTZ",
        "new_sharedcommondataserviceforapps_41c83",
    }
    # Only the owned flow — the stray flow is excluded.
    assert [w["workflowid"] for w in itsm["workflows"]] == ["wf-1"]


def test_resolve_product_artifacts_none_when_pack_not_installed():
    # HRSD is in scope but not installed in the fake env -> None.
    got = pc.resolve_product_artifacts(
        "https://env", "tok", "hr", {"hrsd": True, "itsm": True},
        query=_fake_env())
    assert got["hrsd"] is None
    assert got["itsm"] is not None
