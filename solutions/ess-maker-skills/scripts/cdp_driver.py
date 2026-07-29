# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""CdpDriver — a CDP-attach browser Driver for the Copilot Studio test pane.

Implements the ``drive_surface.Driver`` seam by attaching (over the Chrome
DevTools Protocol) to an Edge/Chromium the operator already launched InPrivate
and signed into, then driving the agent test pane and capturing the turn's reply
as bubbles. The surface above it (``DriveSurface`` / ``aggregate_turn``) turns
those into the browser-agnostic ``DriveResult`` the diagnostic tools consume.

Attribution: the DOM logic here is ported (copy-and-adapt) from the internal ESS
``bot-test/probe/probe.py`` — selectors, article-based bubble capture, the
pvaruntime turn-completion signal, consent/reset handling. It is duplicated here
(not imported) because the ADK is a standalone public kit; see the port ledger
`adk-drive-capture-port-ledger.md` for the per-piece mapping and the switch-back
path. The pure re-typing/aggregation and the completion *decision* live in
``drive_surface`` and are reused, not re-copied.

Launch (operator, once): Edge InPrivate with a dedicated user-data-dir and the
debug port — InPrivate disables Windows SSO so a test account signs in cleanly:

    msedge --inprivate --remote-debugging-port=9222 \\
        --user-data-dir=<fresh dir> --no-first-run --no-default-browser-check <url>

Then this driver attaches to ``http://localhost:9222`` and does not launch or
authenticate.
"""
from __future__ import annotations

import os
import sys
import time

from drive_surface import Bubble, turn_complete
from reply_signal import _CONSENT_MARKERS

CDP_ENDPOINT = "http://localhost:9222"

_DEBUG = bool(os.environ.get("DRIVE_DEBUG"))

# Candidate selectors for the test-pane message input (first visible wins).
INPUT_CANDIDATES = [
    'textarea[placeholder*="Ask" i]',
    'textarea[aria-label*="message" i]',
    '[contenteditable="true"][aria-label*="message" i]',
    'input[placeholder*="Ask" i]',
    'textarea[data-testid*="webchat" i]',
    '[data-testid="chat-input"] textarea',
    'textarea',
]

# Article a11y role prefixes: card reply, text reply, the user's own echo.
_ARTICLE_ROLES = (
    ("Bot attached:", "bot", True),
    ("Bot said:", "bot", False),
    ("You said:", "user", False),
)

_RESET_BUTTON_NAMES = (
    "Refresh", "Start new test session", "New test session", "New chat", "Restart",
)


# --------------------------------------------------------------------------- #
# Pure helpers (no browser) — unit-tested.
# --------------------------------------------------------------------------- #

def _strip_bubble_chrome(txt: str) -> str:
    """Drop the trailing reaction/timestamp chrome the CS test pane appends to a
    bubble ('Like'/'Dislike' reactions, 'Sent at <time>'). Handles both the
    space-joined and newline-joined a11y renderings."""
    for marker in ("Sent at", "Like\nDislike", "Like Dislike", "\nLike", "\nDislike"):
        i = txt.find(marker)
        if i != -1:
            txt = txt[:i]
    return txt.strip()


def _classify_article(raw_text: str) -> tuple[str, bool, str]:
    """Split an article's raw inner_text into (role, had_card, body) by its a11y
    prefix, stripping the prefix and trailing chrome. Unknown prefix -> role
    'unknown', kept verbatim."""
    t = (raw_text or "").strip()
    for prefix, role, had_card in _ARTICLE_ROLES:
        if t.startswith(prefix):
            return role, had_card, _strip_bubble_chrome(t[len(prefix):].strip())
    return "unknown", False, _strip_bubble_chrome(t)


# --------------------------------------------------------------------------- #
# DOM helpers (browser) — live-validated, not unit-tested.
# --------------------------------------------------------------------------- #

def _all_frames(page):
    return [page.main_frame, *[f for f in page.frames if f != page.main_frame]]


def _first_visible(page, selectors):
    for f in _all_frames(page):
        for sel in selectors:
            try:
                loc = f.locator(sel).first
                if loc.is_visible(timeout=500):
                    return f, sel
            except Exception:
                continue
    return None, None


def _find_input(page):
    frame, sel = _first_visible(page, INPUT_CANDIDATES)
    if frame is None:
        return None, None
    return frame.locator(sel).first, sel


def _chat_frame(page):
    """The frame holding the transcript — the one with the most <article> bubbles."""
    best, best_n = None, 0
    for f in _all_frames(page):
        try:
            n = f.locator("article").count()
        except Exception:
            continue
        if n > best_n:
            best, best_n = f, n
    return best


def _card_text(scope) -> str:
    """Concatenate rendered Adaptive Card text (.ac-textBlock) within ``scope``."""
    parts = []
    try:
        blocks = scope.locator(".ac-textBlock")
        for i in range(min(blocks.count(), 40)):
            try:
                t = blocks.nth(i).inner_text(timeout=500).strip()
            except Exception:
                continue
            if t:
                parts.append(t)
    except Exception:
        return ""
    deduped = []
    for p in parts:
        if not deduped or deduped[-1] != p:
            deduped.append(p)
    return "\n".join(deduped)


class _Article:
    __slots__ = ("role", "text", "had_card")

    def __init__(self, role, text, had_card):
        self.role = role
        self.text = text
        self.had_card = had_card


def _articles(page) -> list[_Article]:
    """All transcript bubbles in DOM order (article-based), role-attributed."""
    frame = _chat_frame(page)
    if frame is None:
        return []
    articles = frame.locator("article")
    out: list[_Article] = []
    for i in range(articles.count()):
        art = articles.nth(i)
        try:
            raw = art.inner_text(timeout=1000)
        except Exception:
            continue
        role, had_card, body = _classify_article(raw)
        if role == "bot" and (had_card or not body):
            card = _card_text(art)
            if card:
                body, had_card = card, True
        out.append(_Article(role, body, had_card))
    return out


def _turn_bot_bubbles(page) -> list[Bubble]:
    """Bot bubbles produced since the last user turn — the current turn's reply.
    Mapped to the driver-agnostic ``drive_surface.Bubble`` (text + had_card)."""
    articles = _articles(page)
    last_user = max((i for i, a in enumerate(articles) if a.role == "user"), default=-1)
    return [Bubble(text=a.text, had_card=a.had_card)
            for a in articles[last_user + 1:] if a.role == "bot" and a.text]


def _dismiss_consent(page):
    for f in _all_frames(page):
        try:
            btn = f.get_by_role("button", name="Confirm")
            if btn.is_visible(timeout=800):
                btn.click()
                time.sleep(1)
        except Exception:
            pass


def consent_card_present(page) -> bool:
    """True when an unauthorized-connection consent card is rendered (backend
    replies are unreachable until the connection is authorized manually)."""
    for f in _all_frames(page):
        try:
            blocks = f.locator(".ac-textBlock")
            for i in range(min(blocks.count(), 30)):
                txt = blocks.nth(i).inner_text(timeout=500)
                if any(m.lower() in txt.lower() for m in _CONSENT_MARKERS):
                    return True
        except Exception:
            continue
    return False


def _reset_conversation(page, settle_ms=2500) -> bool:
    for f in _all_frames(page):
        for name in _RESET_BUTTON_NAMES:
            try:
                btn = f.get_by_role("button", name=name)
                if btn.is_visible(timeout=800):
                    btn.click()
                    page.wait_for_timeout(settle_ms)
                    return True
            except Exception:
                continue
    return False


def _is_turn_request(url, method) -> bool:
    """True for the pvaruntime POST that streams one bot turn — the deterministic
    completion signal on this surface."""
    if (method or "").upper() != "POST":
        return False
    u = (url or "").lower()
    return "pvaruntime" in u and "/test/conversations/" in u


def _drive_turn(page, text, timeout_s, *, arm_window_s=10, quiet_s=1.5, poll_ms=250):
    """Send ``text`` into the input, wait for the turn to finish via the
    pvaruntime stream-close signal (reusing ``turn_complete``), and return
    ``(bubbles, timed_out)``. ``timed_out`` is True only when the wait hit the
    deadline without a completion signal."""
    box, _sel = _find_input(page)
    if box is None:
        raise RuntimeError(
            "no message input found on the test pane; is the browser on the "
            "agent test pane and signed in?")

    client = page.context.new_cdp_session(page)
    client.send("Network.enable")
    state = {"seen": False, "in_flight": set(), "last_event": time.time()}

    def _on_req(params):
        req = params.get("request", {}) or {}
        if _is_turn_request(req.get("url", ""), req.get("method", "")):
            state["in_flight"].add(params.get("requestId"))
            state["seen"] = True
            state["last_event"] = time.time()

    def _on_done(params):
        rid = params.get("requestId")
        if rid in state["in_flight"]:
            state["in_flight"].discard(rid)
            state["last_event"] = time.time()

    client.on("Network.requestWillBeSent", _on_req)
    client.on("Network.loadingFinished", _on_done)
    client.on("Network.loadingFailed", _on_done)

    box.click()
    box.fill(text)
    box.press("Enter")
    if _DEBUG:
        print(f"  [drive] {text!r}", file=sys.stderr)

    deadline = time.time() + timeout_s
    arm_deadline = time.time() + arm_window_s
    completed = False
    while time.time() < deadline:
        page.wait_for_timeout(poll_ms)
        if not state["seen"] and time.time() > arm_deadline:
            # No network turn request observed — treat as a settle and read what
            # is on the DOM (a cached/no-op turn). Not a hard timeout.
            completed = True
            break
        if turn_complete(
            seen_any=state["seen"], in_flight=len(state["in_flight"]),
            quiet_elapsed=time.time() - state["last_event"], quiet_s=quiet_s,
        ):
            completed = True
            break

    try:
        client.detach()
    except Exception:
        pass

    if completed:
        # Stream closed but the final bubble may paint a beat later.
        paint_deadline = time.time() + 8
        while time.time() < paint_deadline:
            bubbles = _turn_bot_bubbles(page)
            if bubbles:
                return bubbles, False
            page.wait_for_timeout(poll_ms)
        return _turn_bot_bubbles(page), False

    return _turn_bot_bubbles(page), True  # hit the deadline


# --------------------------------------------------------------------------- #
# The Driver.
# --------------------------------------------------------------------------- #

class CdpDriver:
    """A ``drive_surface.Driver`` backed by a CDP-attached test-pane browser.

    Attaches (does not launch): the operator opens Edge InPrivate on the debug
    port and signs in; ``start`` connects, ``send`` drives one turn and returns
    ``(bubbles, timed_out)``, ``reset`` starts a fresh test session, ``close``
    detaches (leaving the browser running).
    """

    def __init__(self, cdp_endpoint: str = CDP_ENDPOINT):
        self._cdp = cdp_endpoint
        self._pw = None
        self._browser = None
        self._page = None

    def start(self) -> None:
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.connect_over_cdp(self._cdp)
            if not self._browser.contexts:
                raise RuntimeError("no browser context on the CDP endpoint")
            page = self._pick_page(self._browser)
            if page is None:
                raise RuntimeError("no open Copilot Studio page on the CDP endpoint")
            self._page = page
        except Exception:
            self._pw.stop()
            self._pw = self._browser = self._page = None
            raise
        _dismiss_consent(self._page)

    @staticmethod
    def _pick_page(browser):
        for ctx in browser.contexts:
            for page in ctx.pages:
                if "copilotstudio" in (page.url or ""):
                    return page
        return None

    def send(self, text: str, timeout_s: int) -> tuple[list[Bubble], bool]:
        if self._page is None:
            raise RuntimeError("driver not started; call start() first")
        return _drive_turn(self._page, text, timeout_s)

    def reset(self) -> bool:
        if self._page is None:
            raise RuntimeError("driver not started; call start() first")
        return _reset_conversation(self._page)

    def close(self) -> None:
        if self._pw is not None:
            try:
                self._pw.stop()
            finally:
                self._pw = self._browser = self._page = None
