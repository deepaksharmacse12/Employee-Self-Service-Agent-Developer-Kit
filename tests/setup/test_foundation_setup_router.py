# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Structural guards for the isolated foundation setup flow."""

from __future__ import annotations

import re
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SOLUTION = _REPO_ROOT / "solutions" / "ess-maker-skills"
_FOUNDATION = (
    _SOLUTION / "src" / "skills" / "foundation-setup" / "SKILL.md"
)
_SCOPE = _SOLUTION / "src" / "skills" / "foundation-setup" / "scope.md"
_PREREQUISITES = (
    _SOLUTION / "src" / "skills" / "foundation-setup" / "prerequisites.md"
)
_ENVIRONMENT = (
    _SOLUTION / "src" / "skills" / "foundation-setup" / "environment.md"
)
_ONBOARDING_STEP1 = _SOLUTION / "src" / "skills" / "onboarding" / "step1.md"
_ONBOARDING_STEP1B = (
    _SOLUTION / "src" / "skills" / "onboarding" / "step1b.md"
)
_ONBOARDING = _SOLUTION / "src" / "skills" / "onboarding" / "SKILL.md"
_INSTALL_STARTERS = (
    _SOLUTION
    / "src"
    / "skills"
    / "foundation-setup"
    / "install-starters.md"
)
_INSTALLATION_CATALOG = (
    _SOLUTION / "src" / "reference" / "ess-agent-installation" / "config.json"
)
_UI_FORMATTING_GUIDELINES = (
    _SOLUTION / "src" / "reference" / "ui-formatting-guidelines.md"
)
_WORKDAY = _SOLUTION / "src" / "skills" / "setup" / "SKILL.md"
_CONNECT_STEP1 = _SOLUTION / "src" / "skills" / "connect" / "step1.md"
_INSTRUCTIONS = _SOLUTION / ".github" / "copilot-instructions.md"
_PATH_RE = re.compile(r"`(src/skills/[^`]+?\.md)`")


def test_public_setup_routes_to_foundation_module() -> None:
    instructions = _INSTRUCTIONS.read_text(encoding="utf-8")

    assert "src/skills/foundation-setup/SKILL.md" in instructions


def test_workday_routing_remains_unchanged() -> None:
    step1 = _CONNECT_STEP1.read_text(encoding="utf-8")

    assert "src/skills/setup/SKILL.md" in step1
    assert "src/skills/foundation-setup/SKILL.md" not in step1
    assert _WORKDAY.is_file()


def test_foundation_dispatches_all_playbooks() -> None:
    text = _FOUNDATION.read_text(encoding="utf-8")
    expected = {
        "src/skills/foundation-setup/scope.md",
        "src/skills/foundation-setup/prerequisites.md",
        "src/skills/foundation-setup/environment.md",
        "src/skills/foundation-setup/alm-baseline.md",
        "src/skills/foundation-setup/install-starters.md",
        "src/skills/foundation-setup/readiness.md",
        "src/skills/foundation-setup/handoff.md",
    }

    assert expected <= set(_PATH_RE.findall(text))
    assert ".local/connect/workday/config.json" not in text


def test_foundation_router_paths_resolve() -> None:
    referenced = set(_PATH_RE.findall(_FOUNDATION.read_text(encoding="utf-8")))
    missing = [
        path
        for path in sorted(referenced)
        if not (_SOLUTION / path).is_file()
    ]

    assert not missing


def test_foundation_does_not_prompt_for_non_decisions() -> None:
    router = _FOUNDATION.read_text(encoding="utf-8")
    scope = _SCOPE.read_text(encoding="utf-8")

    assert "ask the maker to confirm resuming" not in router
    assert "Picking up at:" not in router
    assert "Confirm that this run covers" not in scope


def test_dataverse_mcp_enablement_is_checked_automatically() -> None:
    prerequisites = _PREREQUISITES.read_text(encoding="utf-8")
    onboarding = _ONBOARDING_STEP1.read_text(encoding="utf-8")

    assert "scripts/check_dataverse_mcp.py" in prerequisites
    assert "scripts/check_dataverse_mcp.py" in onboarding
    assert "without asking the maker anything" in prerequisites
    assert "Type **done**" not in onboarding


def test_environment_access_does_not_require_redundant_attestation() -> None:
    prerequisites = _PREREQUISITES.read_text(encoding="utf-8")

    assert "both Power Platform and Copilot Studio" not in prerequisites
    assert "Do not ask whether the maker can" in prerequisites
    assert "Record both checks in automated mode" in prerequisites
    assert "do not replace failed automated evidence" in prerequisites


def test_locked_environment_is_not_reconfirmed() -> None:
    environment = _ENVIRONMENT.read_text(encoding="utf-8")
    normalized = " ".join(environment.split())

    assert "Do not ask whether it is still intended" in normalized
    assert "do not show a confirmation popup" in normalized
    assert "Ask the maker to confirm" not in environment
    assert "ENVIRONMENT_DRIFT" in environment
    assert environment.count('--environment-url "{ENVIRONMENT_URL}"') == 2
    assert environment.count('--environment-id "{ENVIRONMENT_ID}"') == 2
    assert "Do not read `.local/config.json`" in environment


def test_foundation_flightchecks_use_locked_environment_context() -> None:
    prerequisites = _PREREQUISITES.read_text(encoding="utf-8")

    assert prerequisites.count(
        '--environment-url "{ENVIRONMENT_URL}"'
    ) == 2
    assert prerequisites.count(
        '--environment-id "{ENVIRONMENT_ID}"'
    ) == 2


def test_onboarding_reuses_locked_foundation_environment() -> None:
    onboarding = _ONBOARDING_STEP1.read_text(encoding="utf-8")

    assert "python scripts/setup_state.py show" in onboarding
    assert "environment.tenant_endpoint" in onboarding
    assert "Do not list environments" in onboarding


def test_onboarding_offers_install_or_customize_for_existing_agents() -> None:
    discovery = _ONBOARDING_STEP1B.read_text(encoding="utf-8")

    assert "install another ESS agent or customize" in discovery
    assert "Customize an installed agent" in discovery
    assert "setup_state.py add-product" in discovery


def test_onboarding_guidance_uses_precise_markdown_formatting() -> None:
    router = _ONBOARDING.read_text(encoding="utf-8")
    foundation = _FOUNDATION.read_text(encoding="utf-8")
    installation = _INSTALL_STARTERS.read_text(encoding="utf-8")
    catalog = _INSTALLATION_CATALOG.read_text(encoding="utf-8")
    guidelines = _UI_FORMATTING_GUIDELINES.read_text(encoding="utf-8")

    assert "src/reference/ui-formatting-guidelines.md" in router
    assert "src/reference/ui-formatting-guidelines.md" in foundation
    assert "Use a numbered list for any sequence of UI actions." in guidelines
    assert "Do not use bold for portal controls" in guidelines
    assert "Never show unresolved placeholders" in guidelines
    assert "Do not add complete popup messages" in guidelines
    assert "Verify an installed agent connection" not in guidelines
    assert "[Power Apps](https://make.powerapps.com)" in installation
    assert "`Connections`" in installation
    assert "`New connection`" in installation
    assert "**{displayName}**" in installation
    assert "Select the `{ENVIRONMENT_NAME}` environment." in installation
    assert "connection-attestation-required" in installation
    assert "attest-product-connection" in installation
    assert "connectionSettingsUrl" in installation
    assert "There is no skip option." in installation
    assert "`Connection settings`" in installation
    assert (
        "Is **{CONNECTION_DISPLAY_NAME}** connected"
        in installation
    )
    assert r"\`{AGENT_NAME}\` agent?" in installation
    assert "In the `Manage` column, choose `See details`." in installation
    assert "Open `Connection parameters`." in installation
    assert (
        "If parameters are available, enable sharing for the parameters"
        in installation
    )
    assert "`Save`." in installation
    assert "In make.powerapps.com, select the target environment" not in catalog
