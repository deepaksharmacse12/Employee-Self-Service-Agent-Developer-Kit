# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Structural tests for resumable onboarding instructions."""

from pathlib import Path


_ROOT = Path(__file__).resolve().parents[2]
_ONBOARDING = (
    _ROOT / "solutions" / "ess-maker-skills" / "src" / "skills" / "onboarding"
)
_SETUP_PROMPT = (
    _ROOT / "solutions" / "ess-maker-skills" / ".github"
    / "prompts" / "setup.prompt.md"
)
_FLIGHTCHECK_CLI = (
    _ROOT / "solutions" / "ess-maker-skills" / "scripts"
    / "flightcheck" / "cli.py"
)


def test_router_resumes_each_step_without_repeating_prior_work():
    skill = (_ONBOARDING / "SKILL.md").read_text(encoding="utf-8")

    assert "## Step 2" in skill
    assert "Read `src/skills/onboarding/step1b.md`" in skill
    assert "## Step 4" in skill
    assert "begin at section 2.2" in skill


def test_environment_and_agent_are_persisted_during_selection():
    step1 = (_ONBOARDING / "step1.md").read_text(encoding="utf-8")
    step1b = (_ONBOARDING / "step1b.md").read_text(encoding="utf-8")

    assert "onboarding_state.py save-environment" in step1
    assert "onboarding_state.py save-agent" in step1b


def test_extraction_resume_reloads_agent_and_completion_clears_state():
    step2 = (_ONBOARDING / "step2.md").read_text(encoding="utf-8")

    assert "agent.botId" in step2
    assert "onboarding_state.py clear" in step2


def test_complete_config_can_resume_mcp_startup_only():
    prompt = _SETUP_PROMPT.read_text(encoding="utf-8")
    normalized = " ".join(prompt.split())

    assert "step 4 unchecked" in prompt
    assert "mark steps 1–3 checked" in prompt
    assert "router will continue at step 4" in normalized


def test_complete_config_rechecks_available_agents_before_readiness():
    prompt = _SETUP_PROMPT.read_text(encoding="utf-8")
    normalized = " ".join(prompt.split())

    assert "steps 1–4 checked but step 5 unchecked" in prompt
    assert "resume at agent discovery before readiness" in normalized
    assert "onboarding_state.py save-environment" in prompt
    assert "src/skills/onboarding/step1b.md" in prompt
    assert "offers only missing agents before readiness" in normalized
    assert "Do not start FlightCheck directly" in prompt


def test_complete_config_rerun_does_not_require_reset():
    prompt = _SETUP_PROMPT.read_text(encoding="utf-8")
    normalized = " ".join(prompt.split())

    assert "Re-running `/setup` is how the user installs another" in normalized
    assert "Existing agent entries and workspaces are preserved" in normalized
    assert "Type `RESET` to confirm" not in prompt


def test_readiness_check_requires_explicit_manual_opt_in():
    skill = (_ONBOARDING / "SKILL.md").read_text(encoding="utf-8")
    step3 = (
        _ONBOARDING / "step3-flightcheck.md"
    ).read_text(encoding="utf-8")
    normalized_step3 = " ".join(step3.split())

    assert "The opt-in question is mandatory" in skill
    assert "MANDATORY MANUAL GATE" in step3
    assert (
        "Do not run any FlightCheck command in the same turn"
        in normalized_step3
    )
    assert 'Only if they explicitly choose "Yes' in step3


def test_readiness_check_reports_progress_and_explicit_completion():
    step3 = (
        _ONBOARDING / "step3-flightcheck.md"
    ).read_text(encoding="utf-8")
    cli = _FLIGHTCHECK_CLI.read_text(encoding="utf-8")
    normalized = " ".join(step3.split())

    assert "do not remain silent after the question tool returns" in normalized
    assert "at least once every 60 seconds" in normalized
    assert "python -u scripts/flightcheck/cli.py" in step3
    assert "FLIGHTCHECK_COMPLETE_JSON:" in step3
    assert "FLIGHTCHECK_COMPLETE_JSON:" in cli
    assert "Readiness check complete in {DURATION_SECONDS} seconds" in step3
    assert "do not mark step 5 complete" in normalized


def test_no_agent_path_offers_installation_and_resumes_discovery():
    step1b = (_ONBOARDING / "step1b.md").read_text(encoding="utf-8")
    normalized = " ".join(step1b.split())

    assert "DA : Employee Self-Service HR" in step1b
    assert "DA : Employee Self-Service IT" in step1b
    assert "CEA : Employee Self-Service HR" in step1b
    assert "CEA : Employee Self-Service IT" in step1b
    assert step1b.index("DA : Employee Self-Service IT") < step1b.index(
        "CEA : Employee Self-Service HR"
    )
    assert "Select experience" not in step1b
    assert "Select vertical" not in step1b
    assert "install_ess_agent.py" in step1b
    assert "ess-agent-installation/config.json" in step1b
    assert "src/reference/solution-catalog.md" in step1b
    assert "Return to step 1.4 and run discovery again" in normalized
    assert "without reinstalling the package" in normalized
    assert "Do not rerun the installation command" in normalized
    assert "up to 10 minutes" in normalized
    assert "ESS_AGENT_INSTALLATION_TIMEOUT_JSON:" in step1b
    assert "save-installation" in step1b
    assert "--checkpoint ESS-SOLN-001" in step1b
    assert "--expected-solution" in step1b
    assert "installation-verification/results.json" in step1b
    assert "status `Passed`" in step1b


def test_existing_agents_offer_only_missing_installations_before_selection():
    step1b = (_ONBOARDING / "step1b.md").read_text(encoding="utf-8")
    normalized = " ".join(step1b.split())

    assert "ESS_AGENT_DISCOVERY_JSON:" in step1b
    assert "DISCOVERY.availableInstallations" in step1b
    assert "Would you like to install another ESS agent?" in step1b
    assert "Continue with installed agents" in step1b
    assert "only the other supported agents that are still missing" in normalized

    another_agent_route = step1b[
        step1b.index("## 1.4a"):step1b.index("## 1.5")
    ]
    assert "go to step 1.8f" in another_agent_route
    assert "go to step 1.8e" not in another_agent_route
