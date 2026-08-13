"""Shared fixtures: a populated fake tenant across all eight kinds."""

from __future__ import annotations

import pytest

from tenant_inventory_discovery.fake_inventory import FakeInventoryClient
from tenant_inventory_discovery.platform_clients import FakePlatform

ENV_A = "env-aaaa"
ENV_B = "env-bbbb"


def build_platform() -> FakePlatform:
    """A two-environment tenant with at least one resource of every kind."""
    return FakePlatform(
        environments=[
            {"environmentId": ENV_A, "displayName": "Prod"},
            {"environmentId": ENV_B, "displayName": "Test"},
        ],
        entra_apps=[{"appId": "app-1", "displayName": "ESS Agent App"}],
        connectors=[{"connectorId": "conn-catalog-1", "displayName": "ServiceNow"}],
        sharepoint_sites=[
            {"siteUrl": "https://contoso.sharepoint.com/hr", "siteId": "site-1"}
        ],
        connections={
            ENV_A: [
                {
                    "environmentId": ENV_A,
                    "connectionId": "c-1",
                    "connectorId": "conn-catalog-1",
                }
            ],
            ENV_B: [
                {
                    "environmentId": ENV_B,
                    "connectionId": "c-1",  # same id, different env -> must not collide
                    "connectorId": "conn-catalog-1",
                }
            ],
        },
        knowledge_sources={
            ENV_A: [
                {
                    "environmentId": ENV_A,
                    "botId": "bot-1",
                    "sourceId": "ks-1",
                    "sourceType": "SharePoint",
                }
            ],
        },
        extension_packs={
            ENV_A: [{"environmentId": ENV_A, "packName": "ESS.HRSD", "version": "1.2.3"}],
        },
        scenario_templates={
            ENV_A: [
                {
                    "environmentId": ENV_A,
                    "uniqueName": "GetPayslip",
                    "scenarioName": "Get Payslip",
                }
            ],
        },
    )


@pytest.fixture
def platform() -> FakePlatform:
    return build_platform()


@pytest.fixture
def inventory() -> FakeInventoryClient:
    return FakeInventoryClient()
