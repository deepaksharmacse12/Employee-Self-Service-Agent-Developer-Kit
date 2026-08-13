"""Public entry point for the Tenant Inventory discovery skill (spec §3, §5.1, §7, §8).

Orchestrates the full run lifecycle:

1. Mint a local ``correlation_id`` for logs/telemetry (never sent to inventory, never
   stamped on rows -- this design carries no per-run watermark).
2. Acquire the per-tenant single-flight lock (interim D6 mitigation, §7).
3. Run the crawl (enumerate -> map -> upsert).
4. **Reconcile** the fully-crawled scopes only, reporting the natural keys observed in
   each -> server-side set-based retirement of drift (§6.3). A crashed run never reaches
   this step, so nothing is retired (§7).
5. Emit the structured run-summary telemetry and release the lock.
"""

from __future__ import annotations

import logging
import uuid

from .config import DiscoveryConfig
from .inventory_client import InventoryClient
from .lock import RunLock
from .models import RunSummary, ScopeSnapshot
from .platform_clients import PlatformSurface
from .runner import DiscoveryRunner
from .telemetry import LoggingTelemetrySink, TelemetrySink

logger = logging.getLogger("tenant_inventory_discovery")


class DiscoverySkill:
    """The admin-run crawler facade (spec §1, §3)."""

    def __init__(
        self,
        platform: PlatformSurface,
        inventory: InventoryClient,
        *,
        config: DiscoveryConfig | None = None,
        run_lock: RunLock | None = None,
        telemetry: TelemetrySink | None = None,
    ) -> None:
        self._platform = platform
        self._inventory = inventory
        self._config = config or DiscoveryConfig()
        self._lock = run_lock
        self._telemetry = telemetry or LoggingTelemetrySink()

    def discover(
        self,
        tenant_id: str,
        *,
        environment_ids: list[str] | None = None,
    ) -> RunSummary:
        """Run one discovery pass for ``tenant_id`` (spec §5.1).

        Returns the :class:`RunSummary`. On a crash before reconcile, ``aborted`` is
        True and no reconcile is triggered (the completeness invariant, §7); the remedy
        is a recrawl.
        """
        correlation_id = f"run-{uuid.uuid4()}"
        runner = DiscoveryRunner(self._platform, self._inventory, self._config)

        if self._lock is not None:
            self._lock.acquire(tenant_id, correlation_id)

        summary = RunSummary(correlation_id=correlation_id)
        try:
            summary = runner.run(environment_ids=environment_ids)
            summary.correlation_id = correlation_id
            self._reconcile(summary)
        except Exception:
            # Crash path: reconcile was never called, so the server retires nothing
            # (§7). Surface the failure; recrawl is the recovery path.
            summary.aborted = True
            logger.exception(
                "discovery run %s aborted; nothing reconciled", correlation_id
            )
            raise
        finally:
            self._telemetry.emit_run_summary(summary)
            if self._lock is not None:
                self._lock.release(tenant_id, correlation_id)

        return summary

    def _reconcile(self, summary: RunSummary) -> None:
        """Trigger reconcile for the fully-crawled scopes only (spec §6.3, §7).

        Each completed scope reports the natural keys observed in it; the server retires
        Discovered/Active rows in that scope whose key was not observed. Incomplete scopes
        are excluded. With no completed scopes there is nothing to reconcile -- skip.
        """
        completed = set(summary.completed_scopes)
        snapshots = [
            ScopeSnapshot(scope=r.scope, present_keys=frozenset(r.observed_keys))
            for r in summary.scopes
            if r.scope in completed
        ]
        if not snapshots:
            logger.info(
                "run %s: no fully-crawled scopes; skipping reconcile",
                summary.correlation_id,
            )
            return
        summary.retired_counts = self._inventory.reconcile(snapshots)
