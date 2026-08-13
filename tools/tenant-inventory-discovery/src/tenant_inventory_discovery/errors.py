"""Exception taxonomy (spec §8 error taxonomy: platform vs Inventory API vs validation)."""

from __future__ import annotations


class DiscoveryError(Exception):
    """Base class for all discovery-skill errors."""


class PlatformError(DiscoveryError):
    """A failure enumerating a resource from its platform surface (§6.2)."""


class InventoryApiError(DiscoveryError):
    """A failure talking to the WeveNova Inventory API (§8)."""


class PreconditionFailedError(InventoryApiError):
    """HTTP 412 -- an ``If-Match`` ETag was stale; a concurrent writer won (§5.2)."""

    def __init__(self, natural_key: str, message: str = "precondition failed") -> None:
        super().__init__(f"{message} for {natural_key}")
        self.natural_key = natural_key


class ThrottledError(InventoryApiError):
    """HTTP 429 -- throttled; ``retry_after`` seconds requested by the server (§6)."""

    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__("throttled (429)")
        self.retry_after = retry_after


class RunLockError(DiscoveryError):
    """Another discovery run holds the per-tenant lock (interim D6 mitigation, §7)."""
