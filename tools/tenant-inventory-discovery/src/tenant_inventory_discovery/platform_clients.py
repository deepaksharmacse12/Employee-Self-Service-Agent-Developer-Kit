"""Tenant-platform enumeration surfaces (spec §4, §6.2).

Each ``Kind`` is enumerated from a platform surface. In the real ADK these bind to the
existing tenant-platform client layer (BAP, Dataverse, Microsoft Graph, Copilot Studio) --
**do not add new SDKs if a client already exists** (spec §2). Here we define narrow
:class:`Protocol` surfaces the crawlers depend on, plus an in-memory
:class:`FakePlatform` for tests/dry-runs.

Every enumerator yields raw resources already projected into the §5.3 camelCase key
space and **must page to completion** -- an un-paged first page is a *partial crawl*
(spec §6). The ``paged`` flag on each yielded page lets crawlers assert full enumeration.

.. warning::
   **Q-A (spec §9).** The specific surfaces for ``EntraApp`` (Graph app registrations),
   ``SharePointSite`` (Graph sites), and ``KnowledgeSource`` (Copilot Studio) are the
   *expected* sources and must be ``[verify]``-confirmed against the live platform APIs.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Protocol

# A discovered resource is a dict of §5.3 camelCase attributes.
Resource = dict[str, object]


@dataclass
class Page:
    """One page of enumeration results plus paging state (spec §6)."""

    items: list[Resource]
    is_last: bool


class PlatformSurface(Protocol):
    """The enumeration methods the crawlers call. [verify Q-A] real bindings."""

    # Tenant-root surfaces --------------------------------------------------------
    def list_environments(self, page_size: int) -> Iterator[Page]: ...
    def list_entra_apps(self, page_size: int) -> Iterator[Page]: ...
    def list_connectors(self, page_size: int) -> Iterator[Page]: ...
    def list_sharepoint_sites(self, page_size: int) -> Iterator[Page]: ...

    # Env-scoped surfaces ---------------------------------------------------------
    def list_connections(self, environment_id: str, page_size: int) -> Iterator[Page]: ...
    def list_knowledge_sources(
        self, environment_id: str, page_size: int
    ) -> Iterator[Page]: ...
    def list_extension_packs(
        self, environment_id: str, page_size: int
    ) -> Iterator[Page]: ...
    def list_scenario_templates(
        self, environment_id: str, page_size: int
    ) -> Iterator[Page]: ...


def _paginate(resources: list[Resource], page_size: int) -> Iterator[Page]:
    """Yield ``resources`` in pages, marking the final page ``is_last=True``."""
    if not resources:
        yield Page(items=[], is_last=True)
        return
    for start in range(0, len(resources), page_size):
        chunk = resources[start : start + page_size]
        is_last = start + page_size >= len(resources)
        yield Page(items=list(chunk), is_last=is_last)


@dataclass
class FakePlatform:
    """In-memory :class:`PlatformSurface` for tests and dry-runs.

    Populate the per-kind collections; env-scoped collections are keyed by
    ``environment_id``. Set an entry in :attr:`fail_on` to simulate a fatal enumeration
    error mid-crawl (used by the partial-crawl tests, §10).
    """

    environments: list[Resource] = field(default_factory=list)
    entra_apps: list[Resource] = field(default_factory=list)
    connectors: list[Resource] = field(default_factory=list)
    sharepoint_sites: list[Resource] = field(default_factory=list)
    connections: dict[str, list[Resource]] = field(default_factory=dict)
    knowledge_sources: dict[str, list[Resource]] = field(default_factory=dict)
    extension_packs: dict[str, list[Resource]] = field(default_factory=dict)
    scenario_templates: dict[str, list[Resource]] = field(default_factory=dict)

    # Method names that should raise to simulate a fatal enumeration error (§7).
    fail_on: set[str] = field(default_factory=set)

    def _maybe_fail(self, name: str) -> None:
        if name in self.fail_on:
            from .errors import PlatformError

            raise PlatformError(f"simulated enumeration failure in {name}")

    def list_environments(self, page_size: int) -> Iterator[Page]:
        self._maybe_fail("list_environments")
        return _paginate(self.environments, page_size)

    def list_entra_apps(self, page_size: int) -> Iterator[Page]:
        self._maybe_fail("list_entra_apps")
        return _paginate(self.entra_apps, page_size)

    def list_connectors(self, page_size: int) -> Iterator[Page]:
        self._maybe_fail("list_connectors")
        return _paginate(self.connectors, page_size)

    def list_sharepoint_sites(self, page_size: int) -> Iterator[Page]:
        self._maybe_fail("list_sharepoint_sites")
        return _paginate(self.sharepoint_sites, page_size)

    def list_connections(self, environment_id: str, page_size: int) -> Iterator[Page]:
        self._maybe_fail("list_connections")
        return _paginate(self.connections.get(environment_id, []), page_size)

    def list_knowledge_sources(self, environment_id: str, page_size: int) -> Iterator[Page]:
        self._maybe_fail("list_knowledge_sources")
        return _paginate(self.knowledge_sources.get(environment_id, []), page_size)

    def list_extension_packs(self, environment_id: str, page_size: int) -> Iterator[Page]:
        self._maybe_fail("list_extension_packs")
        return _paginate(self.extension_packs.get(environment_id, []), page_size)

    def list_scenario_templates(self, environment_id: str, page_size: int) -> Iterator[Page]:
        self._maybe_fail("list_scenario_templates")
        return _paginate(self.scenario_templates.get(environment_id, []), page_size)


def drain(pages: Iterable[Page]) -> tuple[list[Resource], bool]:
    """Consume every page; return ``(all_items, fully_enumerated)`` (spec §6).

    ``fully_enumerated`` is True only when the iterator yielded a page with
    ``is_last=True`` -- i.e. paging ran to completion. Any exception propagates to the
    caller, which marks the scope incomplete (§7).
    """
    items: list[Resource] = []
    saw_last = False
    for page in pages:
        items.extend(page.items)
        if page.is_last:
            saw_last = True
            break
    return items, saw_last
