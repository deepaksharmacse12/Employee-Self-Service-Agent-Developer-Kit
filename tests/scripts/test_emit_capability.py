# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for non-blocking capability telemetry."""

from __future__ import annotations

import os
import sys

import emit_capability


def test_capability_emit_starts_detached_worker(monkeypatch) -> None:
    calls = []

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return object()

    monkeypatch.setattr(emit_capability.subprocess, "Popen", fake_popen)

    result = emit_capability.main(["emit_capability.py", "setup"])

    assert result == 0
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command == [
        sys.executable,
        os.path.abspath(emit_capability.__file__),
        "--worker",
        "setup",
    ]
    assert kwargs["stdin"] is emit_capability.subprocess.DEVNULL
    assert kwargs["stdout"] is emit_capability.subprocess.DEVNULL
    assert kwargs["stderr"] is emit_capability.subprocess.DEVNULL


def test_worker_emits_synchronously(monkeypatch) -> None:
    emitted = []

    monkeypatch.setattr(
        "adk_telemetry.emit_capability_use",
        lambda capability, block: emitted.append((capability, block)),
    )

    result = emit_capability.main([
        "emit_capability.py",
        "--worker",
        "setup",
    ])

    assert result == 0
    assert emitted == [("setup", True)]
