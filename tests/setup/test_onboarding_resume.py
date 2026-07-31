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


def test_complete_config_reoffers_skipped_readiness_check():
    prompt = _SETUP_PROMPT.read_text(encoding="utf-8")
    normalized = " ".join(prompt.split())

    assert "steps 1–4 checked but step 5 unchecked" in prompt
    assert "resume onboarding immediately at step 5" in normalized
