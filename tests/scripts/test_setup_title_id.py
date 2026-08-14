# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for landing-page title ID setup persistence."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import fetch_and_setup
import setup


def _agent_info(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "name": "Mock ESS Agent",
        "botId": "bot-1",
        "titleId": None,
        "schema": "msdyn_copilotforemployeeselfservicehr",
        "managed": True,
        "url": "https://example.crm.dynamics.com",
    }
    values.update(overrides)
    return values


def _write_config(
    tmp_path: Path,
    agent_info: dict[str, object],
    slug: str = "mock-ess-agent",
) -> dict:
    setup.write_config(
        agent_info,
        slug,
        f"workspace/agents/{slug}",
        False,
    )
    return json.loads(
        (tmp_path / ".local" / "config.json").read_text(encoding="utf-8")
    )


def test_write_config_persists_supplied_title_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    config = _write_config(tmp_path, _agent_info(titleId="title-1"))

    assert config["agent"]["titleId"] == "title-1"
    assert config["agents"][0]["titleId"] == "title-1"


def test_write_config_preserves_title_id_when_refresh_omits_it(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path, _agent_info(titleId="title-1"))

    config = _write_config(tmp_path, _agent_info(name="Renamed Agent"))

    assert config["agent"]["titleId"] == "title-1"
    assert config["agent"]["name"] == "Renamed Agent"


def test_write_config_replaces_renamed_agent_by_bot_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path, _agent_info(titleId="title-1"))

    config = _write_config(
        tmp_path,
        _agent_info(name="Renamed Agent"),
        slug="renamed-agent",
    )

    assert len(config["agents"]) == 1
    assert config["agent"]["slug"] == "renamed-agent"
    assert config["agent"]["titleId"] == "title-1"


def test_write_config_does_not_transfer_title_id_to_new_bot_with_same_slug(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    _write_config(tmp_path, _agent_info(titleId="title-1"))

    config = _write_config(tmp_path, _agent_info(botId="bot-2"))

    assert len(config["agents"]) == 1
    assert config["agent"]["botId"] == "bot-2"
    assert "titleId" not in config["agent"]


def test_write_config_omits_unresolved_title_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    config = _write_config(tmp_path, _agent_info())

    assert "titleId" not in config["agent"]


def test_run_setup_forwards_title_id() -> None:
    completed = SimpleNamespace(returncode=0)
    with patch("fetch_and_setup.subprocess.run", return_value=completed) as run:
        result = fetch_and_setup.run_setup(
            "https://example.crm.dynamics.com",
            "bot-1",
            "Mock ESS Agent",
            "msdyn_copilotforemployeeselfservicehr",
            True,
            {"components": "components.json"},
            title_id="title-1",
        )

    assert result == 0
    command = run.call_args.args[0]
    assert command[command.index("--title-id") + 1] == "title-1"


def test_refresh_prefers_supplied_title_id() -> None:
    agent_config = {"titleId": "old-title"}

    result = fetch_and_setup.resolve_title_id("new-title", agent_config)

    assert result == "new-title"


def test_refresh_preserves_configured_title_id_when_omitted() -> None:
    agent_config = {"titleId": "old-title"}

    result = fetch_and_setup.resolve_title_id(None, agent_config)

    assert result == "old-title"
