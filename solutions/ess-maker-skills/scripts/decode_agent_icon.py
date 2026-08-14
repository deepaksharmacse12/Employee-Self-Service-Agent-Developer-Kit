# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Strictly decode an AgentConfiguration PNG icon from a base64 payload."""

from __future__ import annotations

import argparse
import base64
import binascii
import os
from pathlib import Path


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def decode_png_payload(payload: str) -> bytes:
    """Decode strict base64 and require a PNG signature."""
    normalized = payload.strip()
    if not normalized:
        raise ValueError("icon payload is empty")
    if len(normalized) % 4 != 0:
        raise ValueError(
            "icon payload length is not divisible by 4; re-fetch it without "
            "adding padding"
        )

    try:
        decoded = base64.b64decode(normalized, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("icon payload is not valid base64") from error

    if not decoded.startswith(PNG_SIGNATURE):
        raise ValueError("decoded icon is not a PNG")
    return decoded


def decode_icon_file(input_path: Path, output_path: Path) -> None:
    """Decode input_path atomically and leave no output when validation fails."""
    temporary_path = output_path.with_name(f"{output_path.name}.tmp")
    try:
        decoded = decode_png_payload(input_path.read_text(encoding="ascii"))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_bytes(decoded)
        os.replace(temporary_path, output_path)
    except (OSError, UnicodeError, ValueError):
        temporary_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)
        raise


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Decode a strict base64 PNG agent icon."
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        decode_icon_file(args.input, args.output)
    except (OSError, UnicodeError, ValueError) as error:
        print(f"ERROR: {error}")
        return 1

    print(f"Decoded agent icon: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
