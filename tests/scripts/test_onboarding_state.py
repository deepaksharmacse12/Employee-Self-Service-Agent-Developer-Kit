# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for scripts/onboarding_state.py."""

import json

import onboarding_state
import pytest


def test_environment_selection_persists(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    saved = onboarding_state.save_environment(
        "https://org.crm.dynamics.com/"
    )

    assert saved["environmentUrl"] == "https://org.crm.dynamics.com"
    assert onboarding_state.load_state() == saved


def test_agent_selection_preserves_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    saved = onboarding_state.save_agent(
        "https://org.crm.dynamics.com",
        "bot-id",
        "ESS Agent",
        "new_essagent",
        True,
    )

    assert saved["environmentUrl"] == "https://org.crm.dynamics.com"
    assert saved["agent"] == {
        "botId": "bot-id",
        "name": "ESS Agent",
        "schemaName": "new_essagent",
        "isManaged": True,
    }


def test_installation_progress_persists_for_resume(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    saved = onboarding_state.save_installation(
        "https://org.crm.dynamics.com/",
        "da",
        "hr",
        "manual-required",
    )

    assert saved["installation"] == {
        "experience": "da",
        "vertical": "hr",
        "status": "manual-required",
    }
    assert onboarding_state.load_state() == saved


def test_installation_progress_rejects_unknown_status(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match="Unsupported installation status"):
        onboarding_state.save_installation(
            "https://org.crm.dynamics.com",
            "da",
            "hr",
            "unknown",
        )


def test_recovers_environment_from_existing_mcp_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mcp_dir = tmp_path / ".vscode"
    mcp_dir.mkdir()
    (mcp_dir / "mcp.json").write_text(json.dumps({
        "servers": {
            "Dataverse": {
                "type": "http",
                "url": "https://org.crm.dynamics.com/api/mcp",
            }
        }
    }), encoding="utf-8")

    state = onboarding_state.load_state()

    assert state == {
        "version": 1,
        "environmentUrl": "https://org.crm.dynamics.com",
    }


def test_clear_removes_partial_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    onboarding_state.save_environment("https://org.crm.dynamics.com")

    onboarding_state.clear_state()

    assert not (tmp_path / ".local" / "onboarding.json").exists()


def test_explicit_environment_replaces_invalid_mcp_recovery(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    mcp_dir = tmp_path / ".vscode"
    mcp_dir.mkdir()
    (mcp_dir / "mcp.json").write_text(json.dumps({
        "servers": {
            "Dataverse": {
                "url": "http://invalid.example/api/mcp",
            }
        }
    }), encoding="utf-8")

    shown = onboarding_state.load_state()
    saved = onboarding_state.save_environment(
        "https://valid.crm.dynamics.com"
    )

    assert "recoveryWarning" in shown
    assert saved["environmentUrl"] == "https://valid.crm.dynamics.com"
    assert "recoveryWarning" not in saved
