"""Per-tenant single-flight run lock (interim D6 mitigation, spec §7, Q-B).

Until the overlapping-run rule (DesignSpec D6) is finalized server-side, the skill must
**serialize runs per tenant** so two interleaving discovery runs can't restamp each
other's rows. This module provides a lock :class:`Protocol` and a simple file-based
implementation with a TTL so a crashed run's stale lock eventually clears.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Protocol

from .errors import RunLockError


class RunLock(Protocol):
    """A per-tenant advisory lock (spec §7).

    ``token`` is an opaque ownership token (the skill passes its local ``correlation_id``);
    it is lock bookkeeping only and is never sent to inventory or stamped on rows.
    """

    def acquire(self, tenant_id: str, token: str) -> None: ...
    def release(self, tenant_id: str, token: str) -> None: ...


class FileRunLock:
    """File-based per-tenant lock with a TTL (interim D6 mitigation, spec §7).

    A lock file records the owning ``token`` and an expiry timestamp. Acquisition fails
    with :class:`RunLockError` if a live (non-expired) lock is held by another run. A
    stale lock (past its TTL -- e.g. left by a crashed run) is reclaimed.
    """

    def __init__(self, lock_dir: str | os.PathLike[str], ttl_seconds: int) -> None:
        self._dir = Path(lock_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._ttl = ttl_seconds

    def _path(self, tenant_id: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in tenant_id)
        return self._dir / f"discovery-{safe}.lock"

    def acquire(self, tenant_id: str, token: str) -> None:
        path = self._path(tenant_id)
        now = time.time()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                data = {}
            expires_at = float(data.get("expires_at", 0))
            owner = data.get("token")
            if owner != token and expires_at > now:
                raise RunLockError(
                    f"tenant {tenant_id!r} run in progress (token={owner}); "
                    f"serialize runs per tenant (spec §7)"
                )
        path.write_text(
            json.dumps({"token": token, "expires_at": now + self._ttl}),
            encoding="utf-8",
        )

    def release(self, tenant_id: str, token: str) -> None:
        path = self._path(tenant_id)
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            data = {}
        # Only the owner clears the lock (don't stomp a run that reclaimed a stale lock).
        if data.get("token") == token:
            path.unlink(missing_ok=True)
