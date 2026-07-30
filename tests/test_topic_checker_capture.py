# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for topic_checker_capture's pure logic (no browser).

The DOM capture (`capture_errors` / `_select_page` / `_open_checker`) is
Playwright/CDP-bound and live-only; these cover the offline consumer contract:
de-duplication of repeated errors, the human-readable report, and — critically —
the false-clean guard (a run where the Topic checker never opened must NOT read
as "0 errors / clean").
"""
from __future__ import annotations

from topic_checker_capture import decide_exit, dedupe_errors, render_report


# --------------------------------------------------------------------------- #
# dedupe_errors — repeated generic PowerFxError entries collapse with a count
# --------------------------------------------------------------------------- #

def test_dedupe_collapses_identical_entries_with_count():
    errors = [
        {"message": "PowerFxError", "node": "n1"},
        {"message": "PowerFxError", "node": "n1"},
        {"message": "Name isn't valid", "node": "n2"},
    ]
    deduped = dedupe_errors(errors)
    assert len(deduped) == 2
    by_msg = {(e["message"], e["node"]): e for e in deduped}
    assert by_msg[("PowerFxError", "n1")]["count"] == 2
    assert by_msg[("Name isn't valid", "n2")]["count"] == 1


def test_dedupe_distinguishes_same_message_different_node():
    errors = [
        {"message": "PowerFxError", "node": "n1"},
        {"message": "PowerFxError", "node": "n2"},
    ]
    deduped = dedupe_errors(errors)
    assert len(deduped) == 2
    assert all(e["count"] == 1 for e in deduped)


def test_dedupe_preserves_first_seen_order():
    errors = [
        {"message": "B", "node": ""},
        {"message": "A", "node": ""},
        {"message": "B", "node": ""},
    ]
    assert [e["message"] for e in dedupe_errors(errors)] == ["B", "A"]


def test_dedupe_empty():
    assert dedupe_errors([]) == []


# --------------------------------------------------------------------------- #
# decide_exit — 0 clean, 1 errors, 2 could-not-run (the false-clean guard)
# --------------------------------------------------------------------------- #

def test_exit_0_when_checker_ran_clean():
    assert decide_exit([], checker_found=True) == 0


def test_exit_1_when_errors_found():
    assert decide_exit([{"message": "x", "node": ""}], checker_found=True) == 1


def test_exit_2_when_checker_never_ran_and_no_errors():
    # The decisive guard: no checker + no errors is NOT "clean" — it's "unknown".
    assert decide_exit([], checker_found=False) == 2


def test_errors_present_beats_missing_button():
    # If node-error elements were captured, the checker demonstrably ran even if
    # the button lookup failed — report the errors (exit 1), never swallow them.
    assert decide_exit([{"message": "x", "node": ""}], checker_found=False) == 1


# --------------------------------------------------------------------------- #
# render_report — human-readable; must make "did not run" unmistakable
# --------------------------------------------------------------------------- #

def test_report_lists_errors_with_node():
    report = render_report(
        "https://copilotstudio/env/x/adaptive/y",
        [{"message": "Name isn't valid", "node": "setVar_1", "count": 1}],
        checker_found=True,
    )
    assert "1 error" in report
    assert "Name isn't valid" in report
    assert "setVar_1" in report


def test_report_shows_repeat_count():
    report = render_report(
        "url",
        [{"message": "PowerFxError", "node": "n1", "count": 3}],
        checker_found=True,
    )
    assert "x3" in report or "(3" in report


def test_report_clean_is_explicit():
    report = render_report("url", [], checker_found=True)
    assert "0 error" in report.lower() or "no error" in report.lower()


def test_report_not_run_is_a_warning_not_clean():
    report = render_report("url", [], checker_found=False)
    low = report.lower()
    assert "could not" in low or "did not run" in low or "not run" in low
    # Must NOT claim a clean bill of health when the checker never opened.
    assert "0 errors" not in low or "could not" in low
