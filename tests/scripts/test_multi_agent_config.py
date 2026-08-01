# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for the nested multi-agent .local/config.json schema."""

import json

import auth
import setup as setup_script


def _agent_info(schema, name, bot_id):
    return {
        "url": "https://org.crm.dynamics.com",
        "botId": bot_id,
        "name": name,
        "schema": schema,
        "managed": True,
    }


def test_normalize_v2_config_exposes_active_runtime_view():
    raw = {
        "configVersion": 2,
        "setup": "complete",
        "activeAgent": "da.esshr",
        "common": {
            "dataverseEndpoint": "https://org.crm.dynamics.com",
            "environmentSku": "Sandbox",
        },
        "agents": {
            "da": {
                "esshr": {
                    "name": "ESS HR",
                    "botId": "bot-hr",
                    "schemaName": "schema-hr",
                    "slug": "ess-hr",
                    "folder": "workspace/agents/ess-hr",
                    "extraction": {"workflowCount": 3},
                }
            }
        },
    }

    normalized = auth.normalize_config(raw)

    assert normalized["dataverseEndpoint"] == (
        "https://org.crm.dynamics.com"
    )
    assert normalized["environmentSku"] == "Sandbox"
    assert normalized["activeAgentKey"] == "da.esshr"
    assert normalized["activeAgent"] == "ess-hr"
    assert normalized["agent"]["botId"] == "bot-hr"
    assert normalized["agents"][0]["configKey"] == "da.esshr"
    assert normalized["workflowCount"] == 3


def test_load_config_reads_v2_file_and_returns_runtime_aliases(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    local = tmp_path / ".local"
    local.mkdir()
    (local / "config.json").write_text(json.dumps({
        "configVersion": 2,
        "setup": "complete",
        "activeAgent": "cea.essit",
        "common": {
            "dataverseEndpoint": "https://org.crm.dynamics.com",
        },
        "agents": {
            "cea": {
                "essit": {
                    "name": "ESS IT",
                    "botId": "bot-it",
                    "schemaName": "schema-it",
                    "slug": "ess-it",
                    "folder": "workspace/agents/ess-it",
                }
            }
        },
    }), encoding="utf-8")

    config = auth.load_config()

    assert config["activeAgentKey"] == "cea.essit"
    assert config["activeAgent"] == "ess-it"
    assert config["agent"]["schemaName"] == "schema-it"
    assert config["dataverseEndpoint"] == "https://org.crm.dynamics.com"


def test_write_config_creates_nested_agents_and_shared_common_state(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)

    setup_script.write_config(
        _agent_info(
            "msdyn_CopilotForEmployeeSelfServiceDAHR",
            "Employee Self-Service HR",
            "bot-hr",
        ),
        "employee-self-service-hr",
        "workspace/agents/employee-self-service-hr",
        True,
        template_config_count=4,
        workflow_count=5,
        evaluation_count=6,
    )

    config = json.loads(
        (tmp_path / ".local" / "config.json").read_text(encoding="utf-8")
    )
    agent = config["agents"]["da"]["esshr"]

    assert config["configVersion"] == 2
    assert config["activeAgent"] == "da.esshr"
    assert config["common"] == {
        "dataverseEndpoint": "https://org.crm.dynamics.com"
    }
    assert "agent" not in config
    assert agent["botId"] == "bot-hr"
    assert agent["extraction"] == {
        "status": "complete",
        "templateConfigsDiscovered": True,
        "templateConfigCount": 4,
        "workflowCount": 5,
        "evaluationCount": 6,
    }


def test_write_config_adds_second_agent_without_repeating_common_state(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    local = tmp_path / ".local"
    local.mkdir()
    (local / "config.json").write_text(json.dumps({
        "configVersion": 2,
        "setup": "complete",
        "activeAgent": "da.esshr",
        "common": {
            "dataverseEndpoint": "https://org.crm.dynamics.com",
            "connections": {"ServiceNow": {"status": "connected"}},
        },
        "agents": {
            "da": {
                "esshr": {
                    "name": "Employee Self-Service HR",
                    "botId": "bot-hr",
                    "schemaName": (
                        "msdyn_CopilotForEmployeeSelfServiceDAHR"
                    ),
                    "slug": "employee-self-service-hr",
                    "folder": "workspace/agents/employee-self-service-hr",
                }
            }
        },
    }), encoding="utf-8")

    setup_script.write_config(
        _agent_info(
            "msdyn_CopilotForEmployeeSelfServiceDAIT",
            "Employee Self-Service IT",
            "bot-it",
        ),
        "employee-self-service-it",
        "workspace/agents/employee-self-service-it",
        False,
    )

    config = json.loads(
        (local / "config.json").read_text(encoding="utf-8")
    )

    assert config["activeAgent"] == "da.essit"
    assert config["agents"]["da"]["esshr"]["botId"] == "bot-hr"
    assert config["agents"]["da"]["essit"]["botId"] == "bot-it"
    assert config["common"]["connections"]["ServiceNow"]["status"] == (
        "connected"
    )


def test_write_config_migrates_v1_agent_array(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    local = tmp_path / ".local"
    local.mkdir()
    (local / "config.json").write_text(json.dumps({
        "configVersion": 1,
        "setup": "complete",
        "activeAgent": "employee-self-service-hr",
        "dataverseEndpoint": "https://old.crm.dynamics.com",
        "environmentSku": "Sandbox",
        "agents": [{
            "name": "Employee Self-Service HR",
            "botId": "bot-hr",
            "schemaName": "msdyn_CopilotForEmployeeSelfServiceDAHR",
            "slug": "employee-self-service-hr",
            "folder": "workspace/agents/employee-self-service-hr",
        }],
        "workflowCount": 2,
    }), encoding="utf-8")

    setup_script.write_config(
        _agent_info(
            "msdyn_CopilotForEmployeeSelfServiceDAIT",
            "Employee Self-Service IT",
            "bot-it",
        ),
        "employee-self-service-it",
        "workspace/agents/employee-self-service-it",
        False,
    )

    config = json.loads(
        (local / "config.json").read_text(encoding="utf-8")
    )

    assert config["configVersion"] == 2
    assert config["agents"]["da"]["esshr"]["extraction"] == {
        "status": "complete",
        "workflowCount": 2
    }
    assert config["agents"]["da"]["essit"]["botId"] == "bot-it"
    assert config["common"]["environmentSku"] == "Sandbox"
