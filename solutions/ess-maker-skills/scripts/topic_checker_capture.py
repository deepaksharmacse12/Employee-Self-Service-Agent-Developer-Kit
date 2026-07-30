# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Capture Copilot Studio Topic checker errors over CDP (read-only).

VS Code diagnostics can report a topic clean while Copilot Studio's authoring
canvas Topic checker shows real failures — unrecognized identifiers, incompatible
types, wrong assignment types (surfaced as ``PowerFxError`` and detailed
type errors). Those live only in the authoring UI's Topic checker panel; the
local YAML diagnostics never see them. This tool opens that panel over a
CDP-attached browser and captures the visible errors so they can gate a push
(complements the local static diagnostics and the runtime debug loop — a defect
can pass local diagnostics yet fail the Topic checker, and separately pass the
Topic checker yet fail at runtime).

Read-only: it attaches to an already-signed-in browser (the operator launched
Edge InPrivate with a debug port — see cdp_driver's launch note), opens the
Topic checker panel if closed, and reads the error nodes. It never edits,
publishes, or navigates destructively.

Exit codes (so a pre-push gate can branch):
  0  the Topic checker ran and reported no errors
  1  the Topic checker ran and reported one or more errors
  2  the Topic checker could not be run (no signed-in authoring page, or the
     panel could not be opened and no errors were visible) — deliberately NOT
     conflated with "clean", so a run that never happened is never mistaken for
     a passing check.

Usage:
    python scripts/topic_checker_capture.py [--topic-id <GUID>] [--json]

Known limitation (ADK gap #17): when Copilot Studio omits ``data-node-id`` on an
error node, the linked node/field ancestry is not fully recovered — the message
is captured but ``node`` may be empty.
"""
from __future__ import annotations

import argparse
import json

from cdp_driver import CDP_ENDPOINT

_ERROR_SELECTOR = '[data-testid="node-error"]'


# --------------------------------------------------------------------------- #
# Pure logic (offline-testable): de-dupe, exit-code decision, report rendering.
# --------------------------------------------------------------------------- #

def dedupe_errors(errors: list[dict]) -> list[dict]:
    """Collapse identical (message, node) errors into one entry with a ``count``.

    Copilot Studio can render the same generic ``PowerFxError`` many times; a
    de-duplicated, counted list is what a reader (agentic or human) reasons about.
    First-seen order is preserved so the report reads top-to-bottom as authored.
    """
    order: list[tuple[str, str]] = []
    counts: dict[tuple[str, str], int] = {}
    for e in errors:
        key = (e.get("message", ""), e.get("node", ""))
        if key not in counts:
            counts[key] = 0
            order.append(key)
        counts[key] += 1
    return [{"message": msg, "node": node, "count": counts[(msg, node)]}
            for (msg, node) in order]


def decide_exit(errors: list[dict], *, checker_found: bool) -> int:
    """Map (errors, whether the checker ran) to an exit code.

    Errors present -> 1 (they demonstrably ran, even if the button lookup failed).
    No errors AND the checker never opened -> 2 (unknown, NOT clean). No errors
    with the checker open -> 0 (genuinely clean).
    """
    if errors:
        return 1
    return 0 if checker_found else 2


def render_report(url: str, errors: list[dict], *, checker_found: bool) -> str:
    """Human-readable report. Makes 'did not run' unmistakably distinct from
    'clean' so a false-clean can never read as a pass."""
    lines = [f"Topic checker @ {url}"]
    if not checker_found and not errors:
        lines.append(
            "WARNING: the Topic checker panel could not be opened and no error "
            "nodes were visible — the check DID NOT RUN. This is not a clean "
            "result. Open the topic's authoring canvas, sign in, and retry.")
        return "\n".join(lines)
    if not errors:
        lines.append("0 errors — the Topic checker reported no problems.")
        return "\n".join(lines)
    lines.append(f"{len(errors)} error(s):")
    for i, e in enumerate(errors, 1):
        node = f" [{e['node']}]" if e.get("node") else ""
        count = e.get("count", 1)
        rep = f" x{count}" if count > 1 else ""
        lines.append(f"  {i}. {e['message']}{node}{rep}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Browser I/O (live-only): page selection, panel open, error capture.
# --------------------------------------------------------------------------- #

def _article_count(page) -> int:
    frames = [page.main_frame, *[f for f in page.frames if f != page.main_frame]]
    return max((f.locator("article").count() for f in frames), default=0)


def _select_page(browser, topic_id: str | None):
    """Pick the authoring page. Prefer an adaptive/authoring canvas URL and the
    page with the most rendered articles; narrow to ``topic_id`` when given."""
    pages = [p for ctx in browser.contexts for p in ctx.pages
             if "copilotstudio" in (p.url or "")]
    if topic_id:
        matching = [p for p in pages if topic_id.lower() in (p.url or "").lower()]
        if matching:
            pages = matching
    if not pages:
        raise RuntimeError("no open Copilot Studio page on the CDP endpoint")
    return max(pages, key=lambda p: (
        "/adaptive/" in (p.url or ""),
        _article_count(p),
    ))


def _open_checker(page) -> bool:
    """Open the Topic checker panel if a button is present. Returns True when the
    checker is demonstrably available — the button was found, OR error nodes are
    already visible (panel already open). Returns False when neither holds, so the
    caller can refuse to report a false-clean."""
    button = page.get_by_role("button", name="Topic checker", exact=True)
    if button.count() and button.is_visible(timeout=1000):
        button.click()
        page.wait_for_timeout(1500)
        return True
    # Button not found — but if error nodes are already on the page, the checker
    # has clearly run in this session; treat that as available.
    try:
        return page.locator(_ERROR_SELECTOR).count() > 0
    except Exception:
        return False


def capture_errors(page) -> list[dict[str, str]]:
    """Capture the visible Topic checker error nodes as ``{message, node}``."""
    errors = page.locator(_ERROR_SELECTOR)
    captured = []
    for i in range(errors.count()):
        node = errors.nth(i)
        if not node.is_visible():
            continue
        captured.append({
            "message": node.inner_text().strip(),
            "node": node.get_attribute("data-node-id") or "",
        })
    return captured


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Capture visible Copilot Studio Topic checker errors (read-only).")
    parser.add_argument("--topic-id", help="topic component GUID to disambiguate the page")
    parser.add_argument("--cdp", default=CDP_ENDPOINT, help="CDP endpoint to attach")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(args.cdp)
        page = _select_page(browser, args.topic_id)
        checker_found = _open_checker(page)
        raw = capture_errors(page)
        # Read page.url INSIDE the context — the connection is gone after the
        # `with` block, so any attribute access would be stale/raise.
        url = page.url

    errors = dedupe_errors(raw)

    if args.json:
        print(json.dumps(
            {"url": url, "checkerRan": checker_found, "errors": errors}, indent=2))
    else:
        print(render_report(url, errors, checker_found=checker_found))

    return decide_exit(errors, checker_found=checker_found)


if __name__ == "__main__":
    raise SystemExit(main())
