# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.filterwarnings("ignore:Field 'lifespan'.*")

_MCP_ROOT = (
    Path(__file__).resolve().parents[1]
    / "solutions"
    / "ess-maker-skills"
    / "src"
    / "mcp"
)


def _load_server(folder: str, alias: str):
    """Import one of the kit's MCP servers by folder name.

    Each server does a sibling-relative ``sys.path.insert`` for ``client`` /
    ``auth``, so its own directory has to be importable while it executes.
    """
    server_path = _MCP_ROOT / folder / "server.py"
    sys.path.insert(0, str(_MCP_ROOT / folder))
    try:
        spec = importlib.util.spec_from_file_location(alias, server_path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


def _load_adk_server():
    """The server that hosts the bridge — i.e. the one serving widget resources.

    The bridge moved from the `adk` server to `agentconfig`: MCP Apps only lets
    a widget call tools on the SAME server connection it was loaded from, and
    the widget resources live on `agentconfig`.
    """
    return _load_server("agentconfig", "agentconfig_mcp_server_under_test")


def test_bridge_is_colocated_with_the_widget_resources():
    """The bridge must live on whichever server serves the widgets.

    This is the invariant whose absence shipped a silently-broken bridge: the
    tool was registered on the `adk` server while the widgets were served from
    `agentconfig`, so every widget call failed with

        Tool not found on server: report_client_events

    and the Vorpal queue swallowed it by design, so no telemetry arrived and
    nothing surfaced an error. Every test passed, because they all called the
    `adk` server directly and never crossed the connection boundary the widget
    actually uses.
    """
    agentconfig = _load_server("agentconfig", "agentconfig_colocation_check")
    adk = _load_server("adk", "adk_colocation_check")

    def has_widgets(mod) -> bool:
        return any(
            str(getattr(t, "uri", "")).startswith("ui://widget/")
            for t in mod.mcp._resource_manager._resources.values()
        )

    def has_bridge(mod) -> bool:
        return "report_client_events" in mod.mcp._tool_manager._tools

    # The server with widgets has the bridge; the one without must not.
    assert has_widgets(agentconfig) and has_bridge(agentconfig)
    assert not has_widgets(adk) and not has_bridge(adk)


def test_report_client_events_tool_is_app_only():
    server = _load_adk_server()

    tool = server.mcp._tool_manager._tools["report_client_events"]

    # ["app"] only — NOT ["model", "app"] like update_agent_config, so the host
    # keeps the bridge out of the model-visible tool list.
    assert tool.meta == {"ui": {"visibility": ["app"]}}
    assert tool.annotations.readOnlyHint is False
    assert tool.annotations.destructiveHint is False
    assert tool.annotations.idempotentHint is False
    assert tool.annotations.openWorldHint is False


def test_report_client_events_tool_returns_structured_bridge_result(monkeypatch):
    server = _load_adk_server()
    captured = {}

    def _fake_report_client_events(envelope):
        captured["envelope"] = envelope
        return {"status": "accepted", "acceptedEventCount": len(envelope["events"])}

    monkeypatch.setattr(server.adk_telemetry, "report_client_events", _fake_report_client_events)

    result = asyncio.run(
        server.report_client_events(
            schemaVersion=1,
            correlationId="corr-test",
            mountId="mount-test",
            appName="AgentIcon",
            buildEnvironment="dev",
            buildNumber="0",
            toolCallId="tool-test",
            events=[{"eventName": "WidgetReady", "timeSinceAppStart": 1}],
        )
    )

    assert captured["envelope"] == {
        "schemaVersion": 1,
        "correlationId": "corr-test",
        "mountId": "mount-test",
        "appName": "AgentIcon",
        "buildEnvironment": "dev",
        "buildNumber": "0",
        "toolCallId": "tool-test",
        "events": [{"eventName": "WidgetReady", "timeSinceAppStart": 1}],
    }
    assert result.structuredContent == {"status": "accepted", "acceptedEventCount": 1}
    assert result.isError is False


def test_report_client_events_tool_marks_rejection_as_error(monkeypatch):
    server = _load_adk_server()

    monkeypatch.setattr(
        server.adk_telemetry,
        "report_client_events",
        lambda envelope: {
            "status": "rejected",
            "acceptedEventCount": 0,
            "rejectedReason": "invalid_event_shape",
        },
    )

    result = asyncio.run(
        server.report_client_events(
            schemaVersion=1,
            correlationId="corr-test",
            mountId="mount-test",
            appName="AgentIcon",
            buildEnvironment="dev",
            buildNumber="0",
            events=[],
        )
    )

    assert result.structuredContent == {
        "status": "rejected",
        "acceptedEventCount": 0,
        "rejectedReason": "invalid_event_shape",
    }
    assert result.isError is True


def _valid_tool_args():
    return {
        "schemaVersion": 1,
        "correlationId": "corr-test",
        "mountId": "mount-test",
        "appName": "AgentIcon",
        "buildEnvironment": "dev",
        "buildNumber": "0",
        "events": [{"eventName": "WidgetReady", "timeSinceAppStart": 1}],
    }


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        ({"events": [1, 2]}, "invalid_event_shape"),
        ({"correlationId": 123}, "invalid_correlation_id"),
        ({"mountId": 123}, "invalid_mount_id"),
        ({"schemaVersion": "2"}, "unsupported_schema_version"),
        ({"buildNumber": None}, "invalid_event_shape"),
        ({"events": None}, "empty_batch"),
    ],
)
def test_malformed_envelopes_return_a_structured_rejection(mutation, expected_reason):
    """A malformed envelope must never surface as a bare tool error.

    FastMCP validates the tool signature with Pydantic BEFORE the body runs, so
    a narrowly-typed signature turns an out-of-contract envelope into a
    ToolError with no ``structuredContent``. Vorpal reads a result without a
    ``status`` as a transient ``invalid_result`` and MAY RETRY it — and since
    the envelope is permanently malformed, it would retry forever. Going
    through the real ``call_tool`` path is the point of this test: calling the
    function directly would bypass the Pydantic layer that caused the bug.
    """
    server = _load_adk_server()
    args = {**_valid_tool_args(), **mutation}

    result = asyncio.run(server.mcp.call_tool("report_client_events", args))

    assert result.structuredContent == {
        "status": "rejected",
        "acceptedEventCount": 0,
        "rejectedReason": expected_reason,
    }


def test_valid_envelope_is_accepted_through_the_real_call_tool_path():
    server = _load_adk_server()

    result = asyncio.run(server.mcp.call_tool("report_client_events", _valid_tool_args()))

    # The autouse conftest guard opts telemetry out, so nothing is transmitted —
    # but the batch is still acknowledged in full, because a count that doesn't
    # account for every sent event makes Vorpal retry the batch.
    assert result.structuredContent == {"status": "accepted", "acceptedEventCount": 1}
    assert result.isError is False


def test_unknown_event_field_survives_the_real_call_tool_path():
    """The asymmetry that made the closed event key set reachable.

    Unknown *envelope* keys never reach the body — FastMCP's arg model is
    ``extra='ignore'``. Unknown *event* keys do: ``events`` is typed ``Any``, so
    Pydantic passes the nested dict through untouched. That made the old
    ``set(event) - _CLIENT_EVENTS_EVENT_KEYS`` check live, and it rejected the
    whole batch. Asserting this through ``call_tool`` rather than against the
    bridge directly is the point — the direct call cannot tell the two cases
    apart, which is how the envelope twin was mistaken for a real guarantee.
    """
    server = _load_adk_server()
    args = {
        **_valid_tool_args(),
        "events": [{"eventName": "WidgetReady", "timeSinceAppStart": 1, "someFutureField": "x"}],
    }

    result = asyncio.run(server.mcp.call_tool("report_client_events", args))

    assert result.structuredContent == {"status": "accepted", "acceptedEventCount": 1}
    assert result.isError is False


def test_bridge_is_total_so_the_wrapper_needs_no_guard(monkeypatch):
    """The wrapper carries no try/except because the bridge never raises.

    These two tests previously monkeypatched `report_client_events` to raise,
    which proved only that a redundant guard caught a simulated fault. The
    guarantee that actually matters is that `adk_telemetry.report_client_events`
    is total: it traps internal faults itself and always answers with a
    contract-shaped verdict. So this induces a REAL internal fault instead, and
    asserts the tool still returns a verdict with the full sent count.
    """
    server = _load_adk_server()

    def _boom(*_args, **_kwargs):
        raise OSError("state dir gone")

    monkeypatch.setattr(server.adk_telemetry, "common_dimensions", _boom)

    args = {
        **_valid_tool_args(),
        "events": [{"eventName": "E", "timeSinceAppStart": 1} for _ in range(20)],
    }
    result = asyncio.run(server.report_client_events(**args))

    assert result.structuredContent == {"status": "accepted", "acceptedEventCount": 20}
    assert result.isError is False
