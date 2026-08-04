# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Structural guards for the foundation setup checklist."""

from __future__ import annotations

import re
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE = (
    _REPO_ROOT
    / "solutions"
    / "ess-maker-skills"
    / "src"
    / "skills"
    / "foundation-setup"
    / "tasks.md"
)
_EXPECTED_STEPS = {f"SETUP-{number:02d}" for number in range(1, 8)}
_ITEM_RE = re.compile(
    r"^-\s*\[(?P<box>[ xX])\]\s+.*\n\s*<!--(?P<meta>.*?)-->\s*$",
    re.MULTILINE,
)


def _parse_items() -> list[dict[str, str]]:
    items = []
    for match in _ITEM_RE.finditer(_TEMPLATE.read_text(encoding="utf-8")):
        meta = {"checkbox": match.group("box").lower()}
        for field in match.group("meta").split("|"):
            key, separator, value = field.partition(":")
            if separator:
                meta[key.strip()] = value.strip()
        items.append(meta)
    return items


def test_template_has_seven_unique_foundation_steps() -> None:
    items = _parse_items()
    step_ids = [item["id"] for item in items]

    assert len(items) == 7
    assert set(step_ids) == _EXPECTED_STEPS
    assert len(step_ids) == len(set(step_ids))


def test_template_starts_pending_and_unchecked() -> None:
    for item in _parse_items():
        assert item["status"] == "pending"
        assert item["checkbox"] == " "
        assert item["checks"].startswith("SETUP-")
