"""§10: per-kind mapping + natural-key composition."""

from __future__ import annotations

import json

import pytest

from tenant_inventory_discovery.mapping import map_resource, to_request_body
from tenant_inventory_discovery.models import Kind
from tenant_inventory_discovery.schemas import AttributeValidationError, schema_for


def test_every_kind_maps_required_keys_only_allowed_camelcase():
    samples = {
        Kind.ENVIRONMENT: {"environmentId": "e1", "displayName": "Prod"},
        Kind.ENTRA_APP: {"appId": "a1", "displayName": "App"},
        Kind.CONNECTOR: {"connectorId": "c1", "displayName": "SN"},
        Kind.CONNECTION: {"environmentId": "e1", "connectionId": "x1", "connectorId": "c1"},
        Kind.SHAREPOINT_SITE: {"siteUrl": "https://s", "siteId": "s1"},
        Kind.KNOWLEDGE_SOURCE: {"environmentId": "e1", "botId": "b1", "sourceId": "k1"},
        Kind.EXTENSION_PACK: {"environmentId": "e1", "packName": "P"},
        Kind.SCENARIO_TEMPLATE: {"environmentId": "e1", "uniqueName": "U"},
    }
    for kind, attrs in samples.items():
        item = map_resource(kind, attrs)
        schema = schema_for(kind)
        assert schema.required <= item.attributes.keys()
        assert item.attributes.keys() <= schema.allowed


def test_missing_required_key_fails_item():
    with pytest.raises(AttributeValidationError):
        map_resource(Kind.ENVIRONMENT, {"environmentId": "e1"})  # missing displayName


def test_unlisted_key_rejected():
    with pytest.raises(AttributeValidationError):
        map_resource(
            Kind.CONNECTOR,
            {"connectorId": "c1", "displayName": "SN", "bogus": "x"},
        )


def test_env_scoped_natural_key_composes_environment_id():
    a = map_resource(
        Kind.CONNECTION,
        {"environmentId": "envA", "connectionId": "c-1", "connectorId": "k"},
    )
    b = map_resource(
        Kind.CONNECTION,
        {"environmentId": "envB", "connectionId": "c-1", "connectorId": "k"},
    )
    # Same connectionId in two environments must not collide (spec §4).
    assert a.natural_key != b.natural_key
    assert a.natural_key == "envA|c-1"
    assert b.natural_key == "envB|c-1"


def test_multipart_key_for_knowledge_source():
    item = map_resource(
        Kind.KNOWLEDGE_SOURCE,
        {"environmentId": "e1", "botId": "b1", "sourceId": "k1"},
    )
    assert item.natural_key == "e1|b1|k1"


def test_connection_carries_connector_edge():
    item = map_resource(
        Kind.CONNECTION,
        {"environmentId": "e1", "connectionId": "c-1", "connectorId": "cat-9"},
    )
    assert item.connector_id == "cat-9"  # reference edge -> Connector (§5.5)
    assert item.environment_id == "e1"  # containment edge (§5.5)


def test_tenant_root_has_no_environment_id():
    item = map_resource(Kind.CONNECTOR, {"connectorId": "c1", "displayName": "SN"})
    assert item.environment_id is None


def test_request_body_serializes_attributes_as_json_string():
    item = map_resource(Kind.ENVIRONMENT, {"environmentId": "e1", "displayName": "Prod"})
    body = to_request_body(item)
    assert body["kind"] == "Environment"
    assert body["naturalKey"] == "e1"
    assert "runId" not in body  # no per-run watermark on the wire
    # attributes travels as a JSON-object *string* on the wire (spec §8).
    assert isinstance(body["attributes"], str)
    assert json.loads(body["attributes"]) == {"environmentId": "e1", "displayName": "Prod"}
    # Skill never stamps provenance/audit/concurrency (spec §4.1).
    for forbidden in ("source", "submittedById", "state", "createdAt", "version"):
        assert forbidden not in body


def test_caps_enforced():
    from tenant_inventory_discovery.schemas import AttributeCaps

    caps = AttributeCaps(max_value_length=3)
    with pytest.raises(AttributeValidationError):
        map_resource(
            Kind.ENVIRONMENT,
            {"environmentId": "e1", "displayName": "way-too-long"},
            caps=caps,
        )
