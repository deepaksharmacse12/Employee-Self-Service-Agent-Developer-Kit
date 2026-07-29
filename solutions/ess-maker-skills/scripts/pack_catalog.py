# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""ServiceNow extension-pack catalog and per-product artifact resolver.

Single source of truth for the two orthogonal axes the ServiceNow setup works
across:

  * **persona** — the ESS agent flavor (``hr`` / ``it``), derived from the
    agent's Dataverse schema. Selects the *parent* solution and the family of
    child extension packs.
  * **product** — the ServiceNow connector product (``hrsd`` / ``itsm``),
    captured as ``scope`` in the connect config. Selects *which* child pack(s)
    within a persona are installed.

The pack unique name is therefore a ``persona × product`` lookup
(:data:`SERVICENOW_PACK_CATALOG`). Crucially, the **product is NOT recoverable
from a connection-reference logical name** — the logical-name prefix encodes the
*persona* (e.g. ``msdyn_copilotforemployeeselfservicehr.…``) while the product
is only determined by the *owning child solution* (e.g.
``msdyn_EssHRServiceNowITSM``). So to attribute a bound reference / activated
flow to a product, we resolve it through its owning solution, not by parsing its
name.

:func:`resolve_product_artifacts` performs that resolution: for each in-scope
product it finds the installed child solution and returns the connection
references and flows that solution *owns* (via ``solutioncomponents``), so the
connect action scripts can record their per-product ``S6.x`` state from the
artifacts that actually belong to each product.

Read-only: every query here is a Dataverse GET. No writes.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import auth  # noqa: E402
import connect_state  # noqa: E402

# Parent ESS solution each persona's extension packs depend on.
PARENT_BY_PERSONA = {
    "hr": "msdyn_CopilotForEmployeeSelfServiceHR",
    "it": "msdyn_CopilotForEmployeeSelfServiceIT",
}

# ServiceNow extension pack unique names, keyed by persona then product.
# (Future work: promote this to a data-driven catalog per parent solution.)
SERVICENOW_PACK_CATALOG = {
    "hr": {
        "hrsd": "msdyn_EssHRServiceNowHRSD",
        "itsm": "msdyn_EssHRServiceNowITSM",
    },
    "it": {
        "hrsd": "msdyn_EssITServiceNowHRSD",
        "itsm": "msdyn_EssITServiceNowITSM",
    },
}

# Columns fetched for a connection reference — enough to classify its connector
# and tell whether it is bound (mirrors ``bind_connections._REF_SELECT``).
_REF_SELECT = (
    "connectionreferenceid,connectionreferencelogicalname,"
    "connectionreferencedisplayname,connectorid,connectionid,statuscode"
)
_WORKFLOW_SELECT = "workflowid,name,category,statecode"


# ─────────────────────────────────────────────────────────────────────
# Pure helpers (no I/O — unit-testable).
# ─────────────────────────────────────────────────────────────────────
def resolve_persona(schema: str | None) -> str | None:
    """Derive the ESS persona ("hr"/"it") from the agent's Dataverse schema.

    ``msdyn_copilotforemployeeselfservicehr`` -> ``hr``
    ``msdyn_copilotforemployeeselfserviceit`` -> ``it``
    """
    low = (schema or "").lower()
    if low.endswith("hr"):
        return "hr"
    if low.endswith("it"):
        return "it"
    return None


def servicenow_packages(persona: str | None, scope: dict | None) -> list[str]:
    """Resolve the ServiceNow extension pack unique names for a persona + scope.

    ``scope`` is the config ``scope`` object (``{"hrsd": bool, "itsm": bool}``).
    Only products explicitly selected (truthy) in ``scope`` are targeted. This
    fails **closed**: if no product is selected (empty/all-false scope), NOTHING
    is targeted — installing every pack when the maker picked none would silently
    install products they never requested. Callers turn an empty result into a
    ``no_targets`` stop.
    """
    catalog = SERVICENOW_PACK_CATALOG.get(persona or "", {})
    scope = scope or {}
    return [
        unique_name
        for product, unique_name in catalog.items()
        if scope.get(product)
    ]


def parent_package(persona: str | None) -> str | None:
    """Return the parent ESS solution unique name for a persona."""
    return PARENT_BY_PERSONA.get(persona or "")


def bot_schema(config: dict | None) -> str | None:
    """Resolve the active agent's Dataverse schema name from ``config.json``."""
    if not isinstance(config, dict):
        return None
    agents = config.get("agents") or []
    active = config.get("activeAgent")
    if active:
        for agent in agents:
            if agent.get("slug") == active and agent.get("schemaName"):
                return agent["schemaName"]
    if config.get("schemaName"):
        return config["schemaName"]
    agent = config.get("agent") or {}
    if agent.get("schemaName"):
        return agent["schemaName"]
    for agent in agents:
        if agent.get("schemaName"):
            return agent["schemaName"]
    return None


def solution_by_product(
    persona: str | None, scope: dict | None, solutions: list[dict],
) -> dict[str, dict | None]:
    """Map each **in-scope** product to its installed child solution row.

    ``solutions`` is the ``solutions`` table (rows carrying ``uniquename`` /
    ``solutionid``). Returns ``{product: solution_row_or_None}`` for every
    product selected in ``scope`` — the value is ``None`` when the product is in
    scope but its pack is not actually installed (the real-world drift case where
    ``packs`` claims installed but the solution is absent).
    """
    catalog = SERVICENOW_PACK_CATALOG.get(persona or "", {})
    scope = scope or {}
    by_uniquename = {s.get("uniquename"): s for s in solutions}
    return {
        product: by_uniquename.get(unique_name)
        for product, unique_name in catalog.items()
        if scope.get(product)
    }


def owned_object_ids(components: list[dict]) -> set[str]:
    """Return the lower-cased ``objectid`` set of a solution's components.

    A component's ``objectid`` is the id of the underlying record (a
    connection reference's ``connectionreferenceid``, a flow's ``workflowid``,
    …), so intersecting this set with those ids attributes each record to the
    owning solution without needing to know each ``componenttype`` number.
    """
    return {
        c["objectid"].lower()
        for c in components
        if c.get("objectid")
    }


def owned_rows(rows: list[dict], id_field: str, owned: set[str]) -> list[dict]:
    """Filter ``rows`` to those whose ``id_field`` is in the ``owned`` id set."""
    return [r for r in rows if (r.get(id_field) or "").lower() in owned]


# ─────────────────────────────────────────────────────────────────────
# Resolver (I/O — reads Dataverse; ``query`` injectable for tests).
# ─────────────────────────────────────────────────────────────────────
def resolve_product_artifacts(
    env_url: str,
    token: str,
    persona: str | None,
    scope: dict | None,
    *,
    query=auth.query_all,
) -> dict[str, dict | None]:
    """Resolve, per in-scope product, the connection references and flows its
    installed extension pack owns.

    Returns ``{product: artifacts_or_None}`` where ``artifacts`` is::

        {
          "solutionUniqueName": str,
          "solutionId": str,
          "connectionRefs": [ <connectionreferences rows the solution owns> ],
          "workflows":      [ <workflows rows the solution owns> ],
        }

    and the value is ``None`` when the product is in scope but its pack is not
    installed. ``connectionRefs`` rows carry ``connectorid`` / ``connectionid`` /
    ``statuscode`` so the caller can classify ServiceNow vs Dataverse and tell
    whether each is bound; ``workflows`` rows carry ``category`` / ``statecode``
    so the caller can tell whether each flow is turned on. ``query`` defaults to
    :func:`auth.query_all` and is injected in tests.
    """
    solutions = query(env_url, token, "solutions", "solutionid,uniquename")
    refs = query(env_url, token, "connectionreferences", _REF_SELECT)
    workflows = query(env_url, token, "workflows", _WORKFLOW_SELECT)

    result: dict[str, dict | None] = {}
    for product, sol in solution_by_product(persona, scope, solutions).items():
        if not sol:
            result[product] = None
            continue
        solution_id = sol.get("solutionid")
        components = query(
            env_url, token, "solutioncomponents", "componenttype,objectid",
            filter_expr=f"_solutionid_value eq {solution_id}",
        )
        owned = owned_object_ids(components)
        result[product] = {
            "solutionUniqueName": sol.get("uniquename"),
            "solutionId": solution_id,
            "connectionRefs": owned_rows(refs, "connectionreferenceid", owned),
            "workflows": owned_rows(workflows, "workflowid", owned),
        }
    return result


def products_satisfying(artifacts: dict, predicate) -> list[str]:
    """Return the products whose (installed, non-``None``) artifacts satisfy
    ``predicate(artifacts_for_product)``."""
    return [p for p, art in artifacts.items() if art and predicate(art)]


def _read_json(path: str) -> dict:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def persona_and_scope(connector: str = "servicenow") -> tuple[str | None, dict | None]:
    """Derive ``(persona, scope)`` from the on-disk config.

    Persona (hr/it) comes from the agent schema in the root ``.local/config.json``;
    product ``scope`` (hrsd/itsm) comes from the connector's connect config. This
    mirrors how ``install_extension_pack`` resolves its install targets, so the
    connect action scripts attribute artifacts to the same products the install
    targeted.
    """
    root = _read_json(os.path.join(".local", "config.json"))
    scope = connect_state.load(connector).get("scope")
    return resolve_persona(bot_schema(root)), scope


def record_product_steps(
    env_url: str,
    persona: str | None,
    scope: dict | None,
    step_id: str,
    checkpoint: str,
    predicate,
    note_for,
    *,
    connector: str = "servicenow",
    query=auth.query_all,
    authenticate=auth.authenticate,
) -> list[str]:
    """Record ``productStatus.<product>.<step_id>`` for each in-scope product
    whose owned artifacts satisfy ``predicate``.

    Resolves each in-scope product's owned references/flows (see
    :func:`resolve_product_artifacts`), then — for every product whose artifacts
    satisfy ``predicate(artifacts)`` — writes a per-product setup step via
    :func:`connect_state.record_product_setup_step` with ``note_for(product)``.
    Returns the list of products recorded. ``query`` / ``authenticate`` are
    injected in tests so no network is required.
    """
    token = authenticate(env_url)
    artifacts = resolve_product_artifacts(
        env_url, token, persona, scope, query=query)
    recorded: list[str] = []
    for product in products_satisfying(artifacts, predicate):
        connect_state.record_product_setup_step(
            connector, product, step_id, checkpoint, note=note_for(product))
        recorded.append(product)
    return recorded
