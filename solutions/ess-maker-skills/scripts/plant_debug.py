# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Plant a DBG SendActivity node into a deployed topic, then publish.

Concrete driver for the pure ``debug_plant`` core against the maker kit's own
Dataverse access (``auth.py``). Plants one DBG node after a named action so the
topic projects internal state into the transcript, PATCHes the topic, records
provenance for a guaranteed strip, and publishes so the change goes live.

Strip it afterwards with ``strip_debug.py`` (reads the provenance this writes).

Usage:
    python scripts/plant_debug.py --topic <schemaname> --after <action_id> \\
        --activity "DBG branch={Topic.SomeVar}" [--node-id <id>] [--yes]

The ``--after`` action id must exist in the topic; a mis-targeted plant refuses
to PATCH rather than instrument the wrong place.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from debug_plant import PlantSpec, plant_debug_nodes_live
from http_errors import APIError

# Provenance is written here by plant and read by strip. Lives under the kit's
# internal state dir so it is not mistaken for a user-edited file.
PROVENANCE_PATH = Path(".local") / ".dbg_provenance.json"

# Publish throttle tolerance: the PvaPublish bound action throttles under rapid
# get+patch+publish bursts, surfacing inconsistently as a bare 401 or a
# 400-wrapping-inner-429 — with a token that is otherwise valid. Treat those as
# transient and retry with backoff rather than as an auth failure.
_PUBLISH_ATTEMPTS = 5
_PUBLISH_BASE_DELAY = 3.0


def _is_transient_publish_error(err: APIError) -> bool:
    """True when a publish failure is throttling (retryable), not a real error.

    A bare 401 or 429 during publish is throttle noise on a valid token; a 400
    that wraps an inner 429 is the same throttle in a different envelope. A plain
    400/403/404 is a real error and must not be retried.
    """
    code = getattr(err, "status_code", None)
    if code in (401, 429):
        return True
    if code == 400 and "429" in str(err):
        return True
    return False


def publish_with_retry(publish_fn, *, attempts=_PUBLISH_ATTEMPTS,
                       base_delay=_PUBLISH_BASE_DELAY, sleep=time.sleep) -> None:
    """Call ``publish_fn()`` retrying transient throttle failures with backoff.

    ``publish_fn`` is a zero-arg callable (bind bot id / token at the call site).
    Non-transient APIErrors propagate immediately; a transient one backs off
    (base_delay * 2**attempt) and retries up to ``attempts`` times, re-raising
    the last error if it never clears.
    """
    for attempt in range(attempts):
        try:
            publish_fn()
            return
        except APIError as err:
            if not _is_transient_publish_error(err) or attempt == attempts - 1:
                raise
            delay = base_delay * (2 ** attempt)
            print(f"  Publish throttled ({getattr(err, 'status_code', '?')}); "
                  f"retrying in {delay:.0f}s...")
            sleep(delay)


def save_provenance(provenance, path: Path = PROVENANCE_PATH) -> None:
    """Persist plant provenance so a later strip can restore byte-identically."""
    payload = {
        "topic": provenance.topic,
        "record_id": provenance.record_id,
        "planted_node_ids": list(provenance.planted_node_ids),
        "specs": [
            {"after_action_id": s.after_action_id, "node_id": s.node_id,
             "activity": s.activity}
            for s in provenance.specs
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_provenance(path: Path = PROVENANCE_PATH):
    """Reconstruct a PlantProvenance written by ``save_provenance``."""
    from debug_plant import PlantProvenance
    payload = json.loads(path.read_text(encoding="utf-8"))
    return PlantProvenance(
        topic=payload["topic"],
        record_id=payload["record_id"],
        planted_node_ids=list(payload["planted_node_ids"]),
        specs=[PlantSpec(**s) for s in payload["specs"]],
    )


class AuthDataverseClient:
    """DataverseClient backed by the maker kit's auth.py access layer.

    Satisfies the structural ``debug_plant.DataverseClient`` Protocol
    (get_topic / patch_topic / publish_bot) without importing this module into
    the pure core. Publish is throttle-tolerant.
    """

    _TOPIC_ENTITY_SET = "botcomponents"

    def __init__(self, env_url: str, token: str):
        self._env_url = env_url
        self._token = token

    def get_topic(self, schemaname: str) -> tuple[str, str]:
        from auth import query_all
        escaped = schemaname.replace("'", "''")
        rows = query_all(
            self._env_url, self._token, self._TOPIC_ENTITY_SET,
            select="botcomponentid,content",
            filter_expr=f"schemaname eq '{escaped}'",
        )
        if not rows:
            raise LookupError(f"no botcomponent found with schemaname {schemaname!r}")
        row = rows[0]
        return row["botcomponentid"], row.get("content") or ""

    def patch_topic(self, record_id: str, content: str) -> None:
        from auth import update_record
        update_record(self._env_url, self._token, self._TOPIC_ENTITY_SET,
                      record_id, {"content": content})

    def publish_bot(self, bot_id: str) -> None:
        from auth import publish_bot as _publish
        publish_with_retry(lambda: _publish(self._env_url, self._token, bot_id))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Plant a DBG SendActivity node into a deployed topic and publish.")
    parser.add_argument("--topic", required=True,
                        help="schemaname of the topic (botcomponent) to instrument")
    parser.add_argument("--after", required=True,
                        help="action id to plant the DBG node after")
    parser.add_argument("--activity", required=True,
                        help="DBG activity text, e.g. \"DBG branch={Topic.SomeVar}\"")
    parser.add_argument("--node-id", default=None,
                        help="id for the planted node (default: derived from --after)")
    parser.add_argument("--yes", action="store_true",
                        help="skip the confirmation prompt")
    args = parser.parse_args(argv)

    from auth import authenticate, load_config

    config = load_config()
    env_url = config["dataverseEndpoint"]
    bot_id = config["agent"]["botId"]
    node_id = args.node_id or f"sendActivity_DBG_{args.after}"

    if PROVENANCE_PATH.exists():
        print(f"Provenance already exists at {PROVENANCE_PATH}. Run strip_debug.py "
              "first (an un-stripped plant is still live in your topic).")
        return 1

    if not args.yes:
        resp = input(
            f"Plant DBG node {node_id!r} after {args.after!r} in topic "
            f"{args.topic!r} and publish? (yes/no): ").strip().lower()
        if resp not in ("yes", "y"):
            print("Plant cancelled.")
            return 0

    token = authenticate(env_url)
    client = AuthDataverseClient(env_url, token)
    spec = PlantSpec(after_action_id=args.after, node_id=node_id, activity=args.activity)

    provenance = plant_debug_nodes_live(client, args.topic, [spec])
    save_provenance(provenance)
    print(f"  Planted {node_id!r}; provenance saved to {PROVENANCE_PATH}.")

    print("Publishing...")
    client.publish_bot(bot_id)
    print("  Published. Drive the topic, read the DBG line, then run strip_debug.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
