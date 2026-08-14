# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for scripts/decode_agent_icon.py."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

import decode_agent_icon


def _png_payload() -> tuple[bytes, str]:
    content = decode_agent_icon.PNG_SIGNATURE + b"test-image-content"
    return content, base64.b64encode(content).decode("ascii")


def test_decode_png_payload_accepts_strict_png() -> None:
    content, payload = _png_payload()

    assert decode_agent_icon.decode_png_payload(f"\n{payload}\n") == content


def test_decode_png_payload_rejects_truncated_base64() -> None:
    with pytest.raises(ValueError, match="not divisible by 4"):
        decode_agent_icon.decode_png_payload("abc")


def test_decode_png_payload_never_repairs_invalid_base64() -> None:
    with pytest.raises(ValueError, match="not valid base64"):
        decode_agent_icon.decode_png_payload("!!!!")


def test_decode_png_payload_rejects_non_png() -> None:
    payload = base64.b64encode(b"not a png").decode("ascii")

    with pytest.raises(ValueError, match="not a PNG"):
        decode_agent_icon.decode_png_payload(payload)


def test_decode_icon_file_writes_valid_png_atomically(tmp_path: Path) -> None:
    content, payload = _png_payload()
    source = tmp_path / "icon.b64"
    target = tmp_path / "nested" / "icon.png"
    source.write_text(payload, encoding="ascii")

    decode_agent_icon.decode_icon_file(source, target)

    assert target.read_bytes() == content
    assert not (target.parent / "icon.png.tmp").exists()


def test_decode_icon_file_removes_stale_output_on_failure(
    tmp_path: Path,
) -> None:
    source = tmp_path / "icon.b64"
    target = tmp_path / "icon.png"
    source.write_text("abc", encoding="ascii")
    target.write_bytes(b"stale")

    with pytest.raises(ValueError, match="not divisible by 4"):
        decode_agent_icon.decode_icon_file(source, target)

    assert not target.exists()
    assert not (tmp_path / "icon.png.tmp").exists()
