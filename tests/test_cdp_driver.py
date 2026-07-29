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
