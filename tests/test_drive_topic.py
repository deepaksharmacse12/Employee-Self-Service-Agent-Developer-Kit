# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for drive_topic's pure/orchestration logic (no browser).

Covers the test-pane URL, the drive-resolution decisions (attach vs launch vs
warn), and the warn-and-fall-back behavior when no browser can be reached — the
paths the user must be able to trust without a live browser.
"""
from __future__ import annotations

import drive_topic
from reply_signal import ReplySignal


def test_test_pane_url_uses_dashed_guids():
    url = drive_topic.test_pane_url("11111111-1111-1111-1111-111111111111",
                                    "22222222-2222-2222-2222-222222222222")
    assert url == (
        "https://copilotstudio.preview.microsoft.com/environments/"
        "11111111-1111-1111-1111-111111111111/bots/"
        "22222222-2222-2222-2222-222222222222/overview"
    )


def test_connect_no_launch_and_no_browser_raises_actionable(monkeypatch):
    monkeypatch.setattr(drive_topic, "is_cdp_up", lambda ep: False)
    try:
        drive_topic._connect(env_id="e", bot_id="b", allow_launch=False,
                             cdp_endpoint="http://localhost:9222")
        raised = None
    except RuntimeError as exc:
        raised = str(exc)
    assert raised is not None
    assert "--no-launch" in raised  # tells the operator exactly what to do


def test_connect_launch_without_env_bot_raises(monkeypatch):
    monkeypatch.setattr(drive_topic, "is_cdp_up", lambda ep: False)
    try:
        drive_topic._connect(env_id=None, bot_id=None, allow_launch=True,
                             cdp_endpoint="http://localhost:9222")
        raised = None
    except RuntimeError as exc:
        raised = str(exc)
    assert raised is not None
    assert "--env" in raised and "--bot" in raised


def test_connect_attaches_when_cdp_already_up(monkeypatch):
    # CDP already up -> no launch, straight to attach (a stub surface).
    monkeypatch.setattr(drive_topic, "is_cdp_up", lambda ep: True)
    started = {"n": 0}

    class StubSurface:
        def __init__(self, driver):
            pass

        def start(self):
            started["n"] += 1

    monkeypatch.setattr(drive_topic, "DriveSurface", StubSurface)
    monkeypatch.setattr(drive_topic, "CdpDriver", lambda ep: object())
    surface = drive_topic._connect(env_id="e", bot_id="b", allow_launch=True,
                                   cdp_endpoint="http://localhost:9222")
    assert started["n"] == 1
    assert isinstance(surface, StubSurface)


def test_main_warns_and_returns_2_when_cannot_connect(monkeypatch, capsys):
    def _raise(**kw):
        raise RuntimeError("no signed-in Copilot Studio page")

    monkeypatch.setattr(drive_topic, "_connect", _raise)
    monkeypatch.setattr(drive_topic, "_load_env_bot", lambda e, b: (e, b))
    rc = drive_topic.main(["--prompt", "hi", "--env", "e", "--bot", "b"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "Cannot drive automatically" in out
    assert "Drive manually" in out  # offers the fallback, does not silently die


def test_main_drives_and_classifies(monkeypatch, capsys):
    from drive_surface import DriveResult

    class StubSurface:
        def drive(self, prompt, timeout_s):
            return DriveResult(reply_text="You have 2 open HR cases.",
                               timed_out=False, bubble_count=1, had_card=False)

        def close(self):
            pass

    monkeypatch.setattr(drive_topic, "_connect", lambda **kw: StubSurface())
    monkeypatch.setattr(drive_topic, "_load_env_bot", lambda e, b: ("e", "b"))
    rc = drive_topic.main(["--prompt", "show cases", "--env", "e", "--bot", "b"])
    out = capsys.readouterr().out
    assert rc == 0
    assert f"signal: {ReplySignal.OK.value}" in out
    assert "You have 2 open HR cases." in out
