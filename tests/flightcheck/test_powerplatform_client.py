# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for Power Platform App Management API methods."""

from __future__ import annotations

import responses


def _client():
    from flightcheck.powerplatform_client import PowerPlatformClient

    client = PowerPlatformClient("tenant")
    client._token = "REDACTED_TOKEN"  # noqa: S105 — test fixture
    return client


@responses.activate
def test_lists_environment_application_packages():
    from flightcheck.powerplatform_client import PP_API_BASE

    responses.get(
        f"{PP_API_BASE}/appmanagement/environments/env-123/applicationPackages",
        json={
            "value": [{
                "uniqueName": "msdyn_CopilotForEmployeeSelfServiceDAHR",
                "state": "None",
            }]
        },
        status=200,
    )

    packages = _client().list_environment_application_packages("env-123")

    assert packages[0]["uniqueName"] == (
        "msdyn_CopilotForEmployeeSelfServiceDAHR"
    )


@responses.activate
def test_starts_application_install_and_returns_operation_id():
    from flightcheck.powerplatform_client import PP_API_BASE

    responses.post(
        (
            f"{PP_API_BASE}/appmanagement/environments/env-123/"
            "applicationPackages/app-name/install"
        ),
        json={
            "lastOperation": {
                "operationId": "operation-123",
                "state": "InstallRequested",
            }
        },
        status=200,
    )

    result = _client().install_application_package("env-123", "app-name")

    assert result["_operationId"] == "operation-123"
    assert result["_async"] is False
    assert responses.calls[0].request.body == b'{"payloadValue": ""}'


@responses.activate
def test_accepts_async_install_without_response_body():
    from flightcheck.powerplatform_client import PP_API_BASE

    responses.post(
        (
            f"{PP_API_BASE}/appmanagement/environments/env-123/"
            "applicationPackages/app-name/install"
        ),
        body="",
        status=202,
    )

    result = _client().install_application_package("env-123", "app-name")

    assert result == {"_async": True, "_operationId": None}


@responses.activate
def test_gets_application_install_status():
    from flightcheck.powerplatform_client import PP_API_BASE

    responses.get(
        (
            f"{PP_API_BASE}/appmanagement/environments/env-123/"
            "operations/operation-123"
        ),
        json={"operationId": "operation-123", "status": "Succeeded"},
        status=200,
    )

    result = _client().get_application_package_install_status(
        "env-123",
        "operation-123",
    )

    assert result["status"] == "Succeeded"
