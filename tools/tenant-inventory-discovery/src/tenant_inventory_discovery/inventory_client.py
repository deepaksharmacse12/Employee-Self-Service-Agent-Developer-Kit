"""WeveNova Inventory API client + retry/backoff (spec §5.2, §5.3, §6, §8).

.. warning::
   **Dep-1 / Dep-3 (spec §9).** The Inventory write path (``POST /inventory``) and the
   **reconcile trigger** route are not yet live / not yet enumerated in DesignSpec §8
   (which lists only Read/Upsert/Retire). The concrete :class:`HttpInventoryClient` below
   encodes the *expected* contract and is marked ``[verify]``; pin the exact routes,
   headers, and the reconcile payload shape with the server team before production use.

The :class:`InventoryClient` Protocol lets the orchestrator run against the real HTTP
client or an in-memory fake (tests) interchangeably.

.. note::
   This design carries **no per-run watermark (RunId)**. Drift is retired via a
   set-based reconcile: at crawl completion the skill reports, per fully-crawled scope,
   the set of natural keys it observed, and the server retires Discovered/Active rows in
   that scope whose key was not observed.
"""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable

from .config import RetryPolicy
from .errors import InventoryApiError, PreconditionFailedError, ThrottledError
from .mapping import idempotency_key, to_request_body
from .models import InventoryItem, ScopeSnapshot, UpsertResult


@runtime_checkable
class InventoryClient(Protocol):
    """The write surface the skill depends on (spec §3, §5)."""

    def upsert(
        self, item: InventoryItem, *, if_match: str | None = None
    ) -> UpsertResult:
        """Idempotent ``POST /inventory`` for one item (§5.2). No per-run watermark."""
        ...

    def reconcile(self, snapshots: list[ScopeSnapshot]) -> dict[str, int]:
        """Trigger server-side reconcile over the fully-crawled scopes (§5.3, §6.3).

        Each :class:`ScopeSnapshot` names a scope plus the natural keys observed in it.
        The server retires ``Source = Discovered``, ``State = Active`` rows within each
        reported scope whose ``naturalKey`` is not in the reported set. Returns retire
        counts keyed by scope for telemetry.
        """


def _sleep_backoff(attempt: int, policy: RetryPolicy, retry_after: float | None) -> None:
    """Bounded exponential backoff, honoring an explicit ``Retry-After`` (spec §6)."""
    if retry_after is not None:
        delay = min(retry_after, policy.max_delay_seconds)
    else:
        delay = min(
            policy.base_delay_seconds * (policy.backoff_multiplier ** attempt),
            policy.max_delay_seconds,
        )
    time.sleep(delay)


def with_retry(func, policy: RetryPolicy):  # type: ignore[no-untyped-def]
    """Run ``func`` with retry on transient failures (5xx/timeout/429) (spec §6, §7).

    Transient upsert failures retry with the **same** ``Idempotency-Key`` (safe replay);
    ``PreconditionFailedError`` (412) is *not* retried here -- the caller re-reads and
    re-applies (§5.2).
    """
    last_exc: Exception | None = None
    for attempt in range(policy.max_attempts):
        try:
            return func()
        except ThrottledError as exc:
            last_exc = exc
            _sleep_backoff(attempt, policy, exc.retry_after)
        except InventoryApiError as exc:
            # Non-precondition API errors are treated as transient (5xx/timeout).
            last_exc = exc
            _sleep_backoff(attempt, policy, None)
    assert last_exc is not None
    raise last_exc


class HttpInventoryClient:
    """httpx-backed Inventory API client (spec §5.2, §8). [verify Dep-1/Dep-3].

    ``httpx`` is imported lazily so the package (and its unit tests, which use the
    in-memory fake) do not hard-require the dependency.
    """

    def __init__(
        self,
        base_url: str,
        entity_set: str,
        reconcile_route: str,
        *,
        auth_token_provider,  # callable returning the admin's delegated bearer token (§8)
        retry: RetryPolicy | None = None,
        timeout: float = 30.0,
    ) -> None:
        import httpx  # lazy import -- see class docstring

        self._base_url = base_url.rstrip("/")
        self._entity_set = entity_set.strip("/")
        self._reconcile_route = reconcile_route.strip("/")
        self._auth_token_provider = auth_token_provider
        self._retry = retry or RetryPolicy()
        self._client = httpx.Client(timeout=timeout)
        self._httpx = httpx

    def _headers(self, *, idem_key: str | None = None, if_match: str | None = None) -> dict:
        # Auth runs as the admin end-to-end (spec §8): delegated bearer token, never a
        # lower-privilege identity.
        headers = {"Authorization": f"Bearer {self._auth_token_provider()}"}
        if idem_key is not None:
            headers["Idempotency-Key"] = idem_key
        if if_match is not None:
            headers["If-Match"] = if_match
        return headers

    def upsert(
        self, item: InventoryItem, *, if_match: str | None = None
    ) -> UpsertResult:
        url = f"{self._base_url}/{self._entity_set}"
        body = to_request_body(item)
        idem = idempotency_key(item)

        def _do() -> UpsertResult:
            try:
                resp = self._client.post(
                    url, json=body, headers=self._headers(idem_key=idem, if_match=if_match)
                )
            except self._httpx.HTTPError as exc:  # network/timeout -> transient
                raise InventoryApiError(f"POST {url} failed: {exc}") from exc

            if resp.status_code == 412:
                raise PreconditionFailedError(item.natural_key)
            if resp.status_code == 429:
                raise ThrottledError(_parse_retry_after(resp.headers.get("Retry-After")))
            if resp.status_code >= 500:
                raise InventoryApiError(f"POST {url} -> {resp.status_code}")
            if resp.status_code >= 400:
                # 4xx (other than 412/429) is a non-retryable client error (§6, §8).
                raise InventoryApiError(f"POST {url} -> {resp.status_code}: {resp.text}")

            return UpsertResult(
                natural_key=item.natural_key,
                kind=item.kind,
                etag=resp.headers.get("ETag"),
                created=resp.status_code == 201,
            )

        return with_retry(_do, self._retry)

    def reconcile(self, snapshots: list[ScopeSnapshot]) -> dict[str, int]:
        # [verify Dep-3] Route + payload must be pinned with the server. This encodes the
        # expected watermark-free shape: report each fully-crawled (environmentId, kind)
        # scope together with the natural keys observed in it, so the server retires only
        # Discovered/Active rows in those scopes whose key was not observed (§6.3).
        url = f"{self._base_url}/{self._reconcile_route}"
        payload = {
            "scopes": [
                {
                    "environmentId": s.scope.environment_id,
                    "kind": s.scope.kind.discriminator,
                    "presentKeys": sorted(s.present_keys),
                }
                for s in snapshots
            ],
        }

        def _do() -> dict[str, int]:
            try:
                resp = self._client.post(url, json=payload, headers=self._headers())
            except self._httpx.HTTPError as exc:
                raise InventoryApiError(f"POST {url} failed: {exc}") from exc
            if resp.status_code == 429:
                raise ThrottledError(_parse_retry_after(resp.headers.get("Retry-After")))
            if resp.status_code >= 500:
                raise InventoryApiError(f"POST {url} -> {resp.status_code}")
            if resp.status_code >= 400:
                raise InventoryApiError(f"POST {url} -> {resp.status_code}: {resp.text}")
            data = resp.json()
            return dict(data.get("retiredCounts", {}))

        return with_retry(_do, self._retry)

    def close(self) -> None:
        self._client.close()


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None
