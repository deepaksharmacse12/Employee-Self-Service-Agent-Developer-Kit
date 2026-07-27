# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for solutions/ess-maker-skills/scripts/pp_env_client.py.

Pure-logic coverage of the host derivation and the user-connections parsing
helpers. No network, no MSAL.
"""

from __future__ import annotations

import pytest

import pp_env_client as ppe


SN_CONNECTOR = "/providers/Microsoft.PowerApps/apis/shared_service-now"
DV_CONNECTOR = "/providers/Microsoft.PowerApps/apis/shared_commondataserviceforapps"


def test_env_api_host_matches_captured_shape():
    assert (
        ppe.env_api_host("11a02d3a-172c-ef48-8b74-8e2975c2fb05")
        == "11a02d3a172cef488b748e2975c2fb.05.environment.api.powerplatform.com"
    )


def test_env_api_host_rejects_garbage():
    with pytest.raises(ValueError):
        ppe.env_api_host("")


def test_connector_short_name():
    assert ppe.connector_short_name(SN_CONNECTOR) == "shared_service-now"
    assert ppe.connector_short_name(SN_CONNECTOR + "/") == "shared_service-now"
    assert ppe.connector_short_name("") == ""


def _nested(flow_id, connectors):
    return {"flowBindings": {flow_id: {"connectors": connectors}}}


def test_iter_flow_connectors_nested_shape():
    data = _nested("f1", [{"connectorId": SN_CONNECTOR, "connectionId": "c1"}])
    pairs = list(ppe.iter_flow_connectors(data))
    assert pairs == [("f1", {"connectorId": SN_CONNECTOR, "connectionId": "c1"})]


def test_iter_flow_connectors_direct_array_shape():
    data = {"flowBindings": {"f1": [{"connectorId": SN_CONNECTOR}]}}
    assert list(ppe.iter_flow_connectors(data)) == [("f1", {"connectorId": SN_CONNECTOR})]


def test_iter_flow_connectors_tolerates_empty():
    assert list(ppe.iter_flow_connectors({})) == []
    assert list(ppe.iter_flow_connectors({"flowBindings": None})) == []


def test_find_connector_flows_filters_by_name():
    data = {
        "flowBindings": {
            "f1": {"connectors": [
                {"connectorId": SN_CONNECTOR, "connectionId": "c1"},
                {"connectorId": DV_CONNECTOR, "connectionId": "d1"},
            ]},
            "f2": {"connectors": [{"connectorId": DV_CONNECTOR}]},
        }
    }
    got = ppe.find_connector_flows(data, "shared_service-now")
    assert [fid for fid, _ in got] == ["f1"]


def test_connector_is_connected():
    assert ppe.connector_is_connected({"connectionId": "c1", "status": "Connected"})
    assert ppe.connector_is_connected({"connectionId": "c1", "status": "connected"})
    assert not ppe.connector_is_connected({"connectionId": None, "status": "Connected"})
    assert not ppe.connector_is_connected({"connectionId": "c1", "status": "NotConnected"})
    assert not ppe.connector_is_connected({"connectionId": "c1"})
