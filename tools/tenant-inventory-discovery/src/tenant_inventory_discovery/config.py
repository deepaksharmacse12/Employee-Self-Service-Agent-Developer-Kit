"""Skill configuration (spec §6, §8 -- tunable caps).

All values are tunable per the spec: page sizes, concurrency, retry/backoff, endpoint
base, and per-tenant run-lock TTL. Defaults are deliberately conservative (§6).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .schemas import AttributeCaps


@dataclass
class RetryPolicy:
    """Bounded exponential backoff, honoring ``Retry-After`` on 429 (spec §6, §7)."""

    max_attempts: int = 5
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 30.0
    backoff_multiplier: float = 2.0


@dataclass
class DiscoveryConfig:
    """Top-level, tunable skill configuration (spec §8-config)."""

    # WeveNova Inventory API. [verify Dep-1] entity-set route + [verify Dep-3] the
    # run-complete/reconcile trigger route are not yet enumerated in DesignSpec §8.
    inventory_base_url: str = "https://weavenova.example/api/beta"
    inventory_entity_set: str = "inventory"  # POST {base}/inventory
    # [verify Dep-3] run-complete / reconcile trigger route -- pin with the server team.
    reconcile_route: str = "inventory/runComplete"

    # Per-platform page sizes (§6 paging). [verify Q-A] real API max page sizes.
    page_size: int = 200

    # Bounded parallelism for enumerate/upsert (§6). Conservative default.
    max_concurrency: int = 4

    retry: RetryPolicy = field(default_factory=RetryPolicy)
    caps: AttributeCaps = field(default_factory=AttributeCaps)

    # Per-tenant single-flight run lock TTL (interim D6 mitigation, spec §7/Q-B).
    run_lock_ttl_seconds: int = 3600
