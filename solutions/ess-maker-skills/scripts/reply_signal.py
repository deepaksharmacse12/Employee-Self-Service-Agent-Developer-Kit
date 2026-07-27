"""Drive-outcome signalling: classify a captured bot reply into a ReplySignal.

When you drive a turn against an agent's test pane, the surface returns text —
but before you assert against that text you need to know whether it is a REAL
reply or a gate/non-reply that would make the assertion vacuous. The canonical
failure this prevents: a connector "Connect to continue" consent card (or a
connection-manager prompt) passing an absence/notContains check because the
backend call never actually ran.

This module is the pure, browser-free core. ``classify_reply_signal`` maps the
reply text (plus a timeout flag the caller sets when the turn did not complete)
to a ``ReplySignal`` the caller can branch on. A live driver can layer a
DOM-level consent check on top for the case where a consent card renders without
recognizable text; this module pins the text-level contract.
"""
from __future__ import annotations

from enum import Enum

# Text markers of an unauthorized-connection gate. Two shapes seen in practice:
#  - the adaptive "Connect to continue" consent card (first connector call per
#    conversation)
#  - the "Open connection manager to verify your credentials" prompt (a stale or
#    repeat connection)
_CONSENT_MARKERS: tuple[str, ...] = (
    "connect to continue",
    "connect and to get the information",
    "open connection manager",
    "get you connected first",
    "verify your credentials",
)


class ReplySignal(Enum):
    """Outcome of a drive turn, from the caller's point of view."""

    OK = "ok"                       # a real bot reply — assert against it
    CONSENT_GATE = "consent_gate"   # connector consent / connection-manager gate
    TIMEOUT = "timeout"             # the turn did not complete in time
    EMPTY = "empty"                 # no reply text captured

    @property
    def is_reply(self) -> bool:
        """True only when the text is a real reply safe to assert against."""
        return self is ReplySignal.OK

    @property
    def needs_consent(self) -> bool:
        """True when the block is recoverable by authorizing the connection
        (a manual/inline consent click), as opposed to a timeout/empty."""
        return self is ReplySignal.CONSENT_GATE


def _is_consent_text(reply: str) -> bool:
    low = reply.lower()
    return any(m in low for m in _CONSENT_MARKERS)


def classify_reply_signal(reply: str | None, *, timed_out: bool = False) -> ReplySignal:
    """Classify a captured reply.

    Precedence: TIMEOUT (the turn did not complete, so any scraped text is
    partial/stale) > CONSENT_GATE (a recognizable gate) > EMPTY (nothing) > OK.
    A genuine backend error reply (e.g. "Error code: 400 ...") is OK — the error
    path is a real turn and any assertion must run against it.
    """
    if timed_out:
        return ReplySignal.TIMEOUT
    text = (reply or "").strip()
    if not text:
        return ReplySignal.EMPTY
    if _is_consent_text(text):
        return ReplySignal.CONSENT_GATE
    return ReplySignal.OK


_REMEDIATION = {
    ReplySignal.OK: "Real reply — safe to assert against; continue diagnosis.",
    ReplySignal.CONSENT_GATE: (
        "Authorize the connection (inline consent card or the maker portal's "
        "connection manager), then re-drive the turn."
    ),
    ReplySignal.TIMEOUT: (
        "The turn did not complete — re-drive (a hibernating backend may need a "
        "warm-up call first)."
    ),
    ReplySignal.EMPTY: (
        "No reply captured — confirm the topic actually triggered, then re-drive."
    ),
}


def main(argv=None) -> int:
    """CLI: classify a captured reply so a driver knows whether to trust it.

    Prints the signal (ok / consent_gate / timeout / empty) on the first line
    and a one-line remediation on the second.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Classify a captured bot reply into a drive-outcome signal.")
    parser.add_argument("reply", nargs="?", default="",
                        help="the captured reply text (quote it)")
    parser.add_argument("--timed-out", action="store_true",
                        help="the drive reported a timeout (the turn did not complete)")
    args = parser.parse_args(argv)

    signal = classify_reply_signal(args.reply, timed_out=args.timed_out)
    print(signal.value)
    print(_REMEDIATION[signal])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
