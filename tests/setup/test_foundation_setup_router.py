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
