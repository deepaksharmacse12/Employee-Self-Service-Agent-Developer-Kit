"""§10: throttling backoff + per-tenant run lock (interim D6 mitigation)."""

from __future__ import annotations

import pytest

from tenant_inventory_discovery.config import RetryPolicy
from tenant_inventory_discovery.errors import (
    InventoryApiError,
    RunLockError,
    ThrottledError,
)
from tenant_inventory_discovery.inventory_client import with_retry
from tenant_inventory_discovery.lock import FileRunLock


def test_backoff_honors_retry_after(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(
        "tenant_inventory_discovery.inventory_client.time.sleep",
        lambda s: sleeps.append(s),
    )
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ThrottledError(retry_after=2.0)
        return "ok"

    policy = RetryPolicy(max_attempts=5, base_delay_seconds=0.1, max_delay_seconds=30)
    assert with_retry(flaky, policy) == "ok"
    assert sleeps == [2.0, 2.0]  # honored Retry-After, not exponential base


def test_retry_gives_up_after_max_attempts(monkeypatch):
    monkeypatch.setattr(
        "tenant_inventory_discovery.inventory_client.time.sleep", lambda s: None
    )

    def always_500():
        raise InventoryApiError("500")

    policy = RetryPolicy(max_attempts=3)
    with pytest.raises(InventoryApiError):
        with_retry(always_500, policy)


def test_run_lock_blocks_concurrent_run(tmp_path):
    lock = FileRunLock(tmp_path, ttl_seconds=3600)
    lock.acquire("tenant-1", "R1")
    with pytest.raises(RunLockError):
        lock.acquire("tenant-1", "R2")  # another run in flight
    lock.release("tenant-1", "R1")
    lock.acquire("tenant-1", "R2")  # now free


def test_stale_lock_reclaimed(tmp_path):
    lock = FileRunLock(tmp_path, ttl_seconds=-1)  # already expired
    lock.acquire("tenant-1", "R1")
    lock.acquire("tenant-1", "R2")  # stale lock reclaimed, no error
