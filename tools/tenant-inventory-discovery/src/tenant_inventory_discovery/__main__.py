"""CLI entry point for the discovery skill.

Runs a single discovery pass against a configured platform surface and Inventory API.
By default it uses the in-memory fakes so the lifecycle can be exercised end-to-end
without the live WeveNova API (Dep-1/Dep-3). Wire :class:`HttpInventoryClient` and the
real platform client layer for production (see README ``[verify]`` items).
"""

from __future__ import annotations

import argparse
import logging
import sys

from .config import DiscoveryConfig
from .discovery_skill import DiscoverySkill
from .fake_inventory import FakeInventoryClient
from .platform_clients import FakePlatform


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tenant-inventory-discovery",
        description="Admin-run crawler for the WeveNova tenant inventory.",
    )
    parser.add_argument("--tenant-id", required=True, help="Tenant identifier to crawl.")
    parser.add_argument(
        "--environment-id",
        action="append",
        dest="environment_ids",
        help="Restrict to specific environment id(s). Omit for a full/tenant-root crawl.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable info logging.")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s %(message)s",
    )

    # NOTE: fakes for demo. Replace with the real platform client layer and
    # HttpInventoryClient in production (README §Wiring).
    skill = DiscoverySkill(
        platform=FakePlatform(),
        inventory=FakeInventoryClient(),
        config=DiscoveryConfig(),
    )
    summary = skill.discover(args.tenant_id, environment_ids=args.environment_ids)

    print(f"correlation_id={summary.correlation_id} aborted={summary.aborted}")
    print(f"completed_scopes={len(summary.completed_scopes)}")
    print(f"retired={summary.retired_counts}")
    return 1 if summary.aborted else 0


if __name__ == "__main__":
    sys.exit(main())
