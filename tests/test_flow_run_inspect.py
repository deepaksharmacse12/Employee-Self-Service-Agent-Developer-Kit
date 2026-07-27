# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for the pure run-action interpreter (``summarize_actions``).

Feeds a recorded run-action list — including the G23 cascade shape (a failure
handler that Succeeded while the containing scope Failed, then a catch-all
Response) — through the interpreter and pins the ``{name, status, statusCode}``
contract the caller reasons about. The REST GET helpers are thin and live-only;
this suite covers the offline consumer contract.
"""
from __future__ import annotations

from flow_run_inspect import _extract_status_code, summarize_actions

# A recorded cascade mirroring the G23 trap: the connector call Failed with a
# 400, its runAfter:[Failed] handler Succeeded (it set the raw error into a
# body), the containing Switch scope is nonetheless marked Failed, a Skipped
# success branch shows the path not taken, and a catch-all Response returns a
# generic message. statusCode lives in each action's fetched outputs.
_G23_ACTIONS = [
    {"name": "Invoke_ServiceNow", "status": "Failed", "outputs": {"statusCode": 400}},
    {"name": "Set_error_body", "status": "Succeeded", "outputs": {"statusCode": 200}},
    {"name": "Switch_on_result", "status": "Failed", "outputs": None},
    {"name": "Success_Response", "status": "Skipped", "outputs": None},
    {"name": "CatchAll_Response", "status": "Succeeded", "outputs": {"statusCode": 500}},
]


def test_summary_preserves_name_and_status_in_order():
    summary = summarize_actions(_G23_ACTIONS)
    assert [a["name"] for a in summary] == [
        "Invoke_ServiceNow", "Set_error_body", "Switch_on_result",
        "Success_Response", "CatchAll_Response",
    ]
    assert [a["status"] for a in summary] == [
        "Failed", "Succeeded", "Failed", "Skipped", "Succeeded",
    ]


def test_summary_surfaces_the_g23_signal():
    # The interpreter must expose that the connector Failed (400) even though a
    # downstream handler Succeeded and the final Response Succeeded (500). This
    # is the exact data the skill-doc teaches the agent to read.
    summary = summarize_actions(_G23_ACTIONS)
    by_name = {a["name"]: a for a in summary}
    assert by_name["Invoke_ServiceNow"]["status"] == "Failed"
    assert by_name["Invoke_ServiceNow"]["statusCode"] == 400
    assert by_name["Switch_on_result"]["status"] == "Failed"
    # The catch-all Response "Succeeded" but carries the generic 500 — a reply
    # reader alone would see only this.
    assert by_name["CatchAll_Response"]["statusCode"] == 500


def test_skipped_action_has_null_status_code():
    summary = summarize_actions([
        {"name": "Skipped_branch", "status": "Skipped", "outputs": None},
    ])
    assert summary[0]["statusCode"] is None


def test_empty_actions_yields_empty_summary():
    assert summarize_actions([]) == []


def test_extract_status_code_handles_missing_and_non_int():
    assert _extract_status_code(None) is None
    assert _extract_status_code({}) is None
    assert _extract_status_code({"statusCode": 200}) == 200
    # A non-int statusCode (malformed outputs) must not leak through as truthy.
    assert _extract_status_code({"statusCode": "200"}) is None
    assert _extract_status_code("not-a-dict") is None


def test_missing_keys_do_not_raise():
    # Defensive: a partial action dict must not KeyError the interpreter.
    summary = summarize_actions([{"status": "Succeeded"}])
    assert summary == [{"name": None, "status": "Succeeded", "statusCode": None}]
