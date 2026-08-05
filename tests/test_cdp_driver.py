# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for CdpDriver's pure helpers (no browser).

The browser-touching code (attach, drive, capture) is validated live, not in CI.
These cover the pure article-classification and chrome-stripping ported from the
internal probe — the parts that decide role/card/body from a bubble's raw text.
"""
from __future__ import annotations

from cdp_driver import _classify_article, _strip_bubble_chrome


def test_strip_removes_reaction_and_timestamp_chrome():
    assert _strip_bubble_chrome("Your cases:\nLike\nDislike") == "Your cases:"
    assert _strip_bubble_chrome("Done Sent at 3:04 PM") == "Done"
    assert _strip_bubble_chrome("Plain text") == "Plain text"


def test_classify_bot_text_reply():
    role, had_card, body = _classify_article("Bot said: You have 3 open HR cases.")
    assert role == "bot"
    assert had_card is False
    assert body == "You have 3 open HR cases."


def test_classify_bot_card_reply():
    role, had_card, body = _classify_article("Bot attached: <card>")
    assert role == "bot"
    assert had_card is True
    assert body == "<card>"


def test_classify_user_echo():
    role, had_card, body = _classify_article("You said: show my cases")
    assert role == "user"
    assert had_card is False
    assert body == "show my cases"


def test_classify_unknown_prefix_kept_verbatim():
    role, had_card, body = _classify_article("System notice")
    assert role == "unknown"
    assert had_card is False
    assert body == "System notice"


def test_classify_strips_chrome_from_body():
    role, _had_card, body = _classify_article("Bot said: Here you go\nLike\nDislike")
    assert role == "bot"
    assert body == "Here you go"


def test_build_launch_args_has_mandatory_flags():
    from cdp_driver import build_launch_args
    args = build_launch_args(debug_port=9222, user_data_dir="C:/tmp/x",
                             start_url="https://example/pane")
    assert "--remote-debugging-port=9222" in args
    assert "--user-data-dir=C:/tmp/x" in args
    assert "--inprivate" in args           # test-account isolation is mandatory
    assert "--no-first-run" in args
    assert args[-1] == "https://example/pane"  # url is last


def test_build_launch_args_can_disable_inprivate():
    from cdp_driver import build_launch_args
    args = build_launch_args(debug_port=9222, user_data_dir="d", start_url="u",
                             inprivate=False)
    assert "--inprivate" not in args


class _FakePage:
    def __init__(self, url):
        self.url = url


class _FakeBrowser:
    def __init__(self, urls):
        self.contexts = [type("Ctx", (), {"pages": [_FakePage(u) for u in urls]})()]


def test_pick_page_without_match_returns_first_copilotstudio_page():
    from cdp_driver import CdpDriver
    browser = _FakeBrowser(["https://other.com/x",
                            "https://copilotstudio.microsoft.com/a"])
    assert CdpDriver._pick_page(browser).url == "https://copilotstudio.microsoft.com/a"


def test_pick_page_with_match_requires_the_token_in_url():
    from cdp_driver import CdpDriver
    bot = "2731c539"
    browser = _FakeBrowser([
        "https://copilotstudio.microsoft.com/environments/e/bots/OTHER/overview",
        f"https://copilotstudio.microsoft.com/environments/e/bots/{bot}/overview",
    ])
    page = CdpDriver._pick_page(browser, bot)
    assert bot in page.url  # attaches to THIS bot's pane, not the other tab


def test_pick_page_with_match_returns_none_when_no_page_matches():
    from cdp_driver import CdpDriver
    browser = _FakeBrowser(["https://copilotstudio.microsoft.com/bots/OTHER/overview"])
    assert CdpDriver._pick_page(browser, "2731c539") is None
