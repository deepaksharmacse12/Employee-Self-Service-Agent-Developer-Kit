# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Read-only inspection of a cloud flow's run history (Flow Management API).

The decisive "why" surface for flow-backed / connector-path bugs: did the
connector actually get called, which action failed, did the failure branch fire,
and why is the reply a generic error. Neither the bot reply nor the flow source
alone reveals this — the per-action run view does.

This module is read-only. It performs plain HTTPS GETs against the Flow
Management API to list a flow's runs and read one run's per-action cascade. It
never creates, patches, invokes, or deletes anything, which keeps its trust
surface tiny: a developer reads their own flow's run history in their own
environment.

Token: callers pass a Flow Management API bearer token
(resource ``https://service.flow.microsoft.com/``). Acquisition is intentionally
left to the caller — the maker kit's Dataverse MSAL flow targets a different
audience, so a Flow-scoped token is a separate concern (see the module's
companion skill-doc / follow-up).

Two layers:
  * The GET helpers (``get_latest_run`` / ``get_run_by_id`` / ``get_run_actions``)
    are thin REST reads.
  * ``summarize_actions`` is a pure interpreter over an already-fetched action
    list, producing the ``{name, status, statusCode}`` cascade a caller (agentic
    or human) reasons about. It is the offline-testable consumer contract.
"""
from __future__ import annotations

import logging
import re

import requests

from http_errors import raise_api_error

logger = logging.getLogger(__name__)

_FLOW_API_HOST = "https://api.flow.microsoft.com"
API_TIMEOUT_SECONDS = 30
_GUID_NODASH_RE = re.compile(r"^[0-9a-fA-F]{32}$")


def _validate_https_url(url: str) -> None:
    """Reject non-https URLs — sending a bearer token over cleartext is unacceptable."""
    if not url.lower().startswith("https://"):
        raise ValueError(
            f"url must use https:// (got: {url!r}). Refusing to send a bearer "
            "token over an unencrypted channel."
        )


def _validate_guid_nodashes(value: str, *, name: str = "value") -> str:
    """Raise ValueError unless ``value`` (with dashes stripped) is 32 hex chars.

    Guards the environment and flow ids that are interpolated into the request
    URL, so a malformed or injected value can't reshape the path.
    """
    no_dashes = value.replace("-", "") if isinstance(value, str) else ""
    if not _GUID_NODASH_RE.match(no_dashes):
        raise ValueError(
            f"{name} must be a GUID (32 hex chars after stripping dashes); got: {value!r}"
        )
    return no_dashes


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def get_latest_run(environment: str, flow_id: str, token: str) -> dict | None:
    """Return the most recent run for a flow, or None if it has never run."""
    _validate_guid_nodashes(environment, name="environment")
    _validate_guid_nodashes(flow_id, name="flow_id")
    url = (
        f"{_FLOW_API_HOST}/providers/Microsoft.ProcessSimple/environments/"
        f"{environment}/flows/{flow_id}/runs?api-version=2016-11-01&$top=1"
    )
    _validate_https_url(url)
    resp = requests.get(url, headers=_auth_headers(token), timeout=API_TIMEOUT_SECONDS)
    raise_api_error(resp, resource_name="cloud flows", operation="read")
    runs = resp.json().get("value", [])
    return runs[0] if runs else None


def get_run_by_id(environment: str, flow_id: str, run_id: str, token: str) -> dict | None:
    """Return a specific run by id. Returns None if the run is gone (404)."""
    _validate_guid_nodashes(environment, name="environment")
    _validate_guid_nodashes(flow_id, name="flow_id")
    url = (
        f"{_FLOW_API_HOST}/providers/Microsoft.ProcessSimple/environments/"
        f"{environment}/flows/{flow_id}/runs/{run_id}?api-version=2016-11-01"
    )
    _validate_https_url(url)
    resp = requests.get(url, headers=_auth_headers(token), timeout=API_TIMEOUT_SECONDS)
    if resp.status_code == 404:
        return None
    raise_api_error(resp, resource_name="cloud flows", operation="read")
    return resp.json()


def get_run_actions(environment: str, flow_id: str, run_id: str, token: str) -> list[dict]:
    """Return a run's actions as ``[{name, status, outputs}]``.

    ``status`` is the per-action run status (Succeeded / Skipped / Failed /
    TimedOut / Cancelled). ``outputs`` is fetched via the action's anonymous SAS
    ``outputsLink`` when present (that is where a connector action's
    ``statusCode`` lives); a SAS fetch failure is logged and leaves ``outputs``
    as None rather than aborting the whole dump.

    Inputs are intentionally not fetched — the consumer contract
    (``summarize_actions``) needs only status + statusCode, and skipping the
    extra SAS reads keeps the surface minimal.
    """
    _validate_guid_nodashes(environment, name="environment")
    _validate_guid_nodashes(flow_id, name="flow_id")
    url = (
        f"{_FLOW_API_HOST}/providers/Microsoft.ProcessSimple/environments/"
        f"{environment}/flows/{flow_id}/runs/{run_id}/actions?api-version=2016-11-01"
    )
    _validate_https_url(url)
    resp = requests.get(url, headers=_auth_headers(token), timeout=API_TIMEOUT_SECONDS)
    raise_api_error(resp, resource_name="cloud flows", operation="read")

    actions: list[dict] = []
    for action in resp.json().get("value", []):
        entry = {
            "name": action["name"],
            "status": action["properties"]["status"],
            "outputs": None,
        }
        outputs_uri = action["properties"].get("outputsLink", {}).get("uri")
        if outputs_uri:
            try:
                out_resp = requests.get(outputs_uri, timeout=API_TIMEOUT_SECONDS)
                if out_resp.status_code == 200:
                    entry["outputs"] = out_resp.json()
                else:
                    logger.warning(
                        "SAS outputs fetch for action %s returned HTTP %s",
                        action["name"], out_resp.status_code,
                    )
            except Exception as exc:
                logger.warning(
                    "SAS outputs fetch for action %s failed: %s", action["name"], exc
                )
        actions.append(entry)
    return actions


def _extract_status_code(outputs: object) -> int | None:
    """Pull the connector/HTTP ``statusCode`` out of an action's outputs, if any.

    Returns None when outputs are absent (e.g. a Skipped action) or carry no
    status code (e.g. a Compose / SetVariable), so the caller can distinguish
    "no code" from a real code without a KeyError.
    """
    if isinstance(outputs, dict):
        code = outputs.get("statusCode")
        if isinstance(code, int):
            return code
    return None


def summarize_actions(actions: list[dict]) -> list[dict]:
    """Reduce ``get_run_actions`` output to the ``{name, status, statusCode}``
    cascade a caller reasons about.

    Pure and side-effect-free: given a recorded action list it returns the same
    summary every time, which makes the interpretation contract offline-testable.
    Interpreting the cascade (e.g. a Failed scope despite a Succeeded failure
    handler) is the caller's job, taught by the companion skill-doc; this
    function only shapes the data that interpretation runs on.
    """
    return [
        {
            "name": a.get("name"),
            "status": a.get("status"),
            "statusCode": _extract_status_code(a.get("outputs")),
        }
        for a in actions
    ]
