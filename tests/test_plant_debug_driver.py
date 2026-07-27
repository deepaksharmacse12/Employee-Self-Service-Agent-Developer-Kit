# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for the plant/strip driver's testable contracts: throttle-tolerant
publish and provenance round-tripping.

The Dataverse-touching adapter methods (get_topic/patch_topic/publish_bot) are
live-only; these cover the offline logic — the publish retry policy (a bare 401
or 429, or a 400 wrapping an inner 429, is transient throttling on a valid token)
and provenance serialize/deserialize (so a later strip restores byte-identically).
"""
from __future__ import annotations

import pytest

from debug_plant import PlantProvenance, PlantSpec
from http_errors import APIError
from plant_debug import (
    _is_transient_publish_error,
    load_provenance,
    publish_with_retry,
    save_provenance,
)


def _api_error(status_code, message="err"):
    return APIError(status_code=status_code, message=message, tip="")


# --------------------------------------------------------------------------- #
# throttle classification
# --------------------------------------------------------------------------- #

def test_401_and_429_are_transient():
    assert _is_transient_publish_error(_api_error(401)) is True
    assert _is_transient_publish_error(_api_error(429)) is True


def test_400_wrapping_429_is_transient():
    assert _is_transient_publish_error(
        _api_error(400, "Bad request while trying to publish (inner 429)")) is True


def test_plain_400_403_404_are_not_transient():
    assert _is_transient_publish_error(_api_error(400, "malformed body")) is False
    assert _is_transient_publish_error(_api_error(403)) is False
    assert _is_transient_publish_error(_api_error(404)) is False


# --------------------------------------------------------------------------- #
# publish_with_retry
# --------------------------------------------------------------------------- #

def test_publish_succeeds_first_try_without_sleeping():
    calls = {"publish": 0, "sleep": 0}

    def publish():
        calls["publish"] += 1

    def sleep(_):
        calls["sleep"] += 1

    publish_with_retry(publish, sleep=sleep)
    assert calls["publish"] == 1
    assert calls["sleep"] == 0


def test_publish_retries_transient_then_succeeds():
    state = {"n": 0}
    slept = []

    def publish():
        state["n"] += 1
        if state["n"] < 3:
            raise _api_error(429)

    publish_with_retry(publish, attempts=5, base_delay=1.0, sleep=slept.append)
    assert state["n"] == 3
    # backed off before the 2nd and 3rd attempts: 1.0 * 2**0, 1.0 * 2**1
    assert slept == [1.0, 2.0]


def test_publish_non_transient_raises_immediately():
    slept = []

    def publish():
        raise _api_error(400, "malformed body")

    with pytest.raises(APIError):
        publish_with_retry(publish, sleep=slept.append)
    assert slept == []  # no retry on a real error


def test_publish_reraises_after_exhausting_attempts():
    calls = {"n": 0}

    def publish():
        calls["n"] += 1
        raise _api_error(429)

    with pytest.raises(APIError):
        publish_with_retry(publish, attempts=3, base_delay=1.0, sleep=lambda _: None)
    assert calls["n"] == 3  # tried the full budget, then re-raised


# --------------------------------------------------------------------------- #
# provenance round-trip
# --------------------------------------------------------------------------- #

def test_provenance_round_trips(tmp_path):
    path = tmp_path / ".dbg_provenance.json"
    prov = PlantProvenance(
        topic="snow_sys",
        record_id="rec-1",
        planted_node_ids=["sendActivity_DBG_a", "sendActivity_DBG_b"],
        specs=[
            PlantSpec(after_action_id="setVariable_a", node_id="sendActivity_DBG_a",
                      activity="DBG branch=a"),
            PlantSpec(after_action_id="beginDialog_b", node_id="sendActivity_DBG_b",
                      activity="DBG branch=b"),
        ],
    )
    save_provenance(prov, path)
    loaded = load_provenance(path)

    assert loaded.topic == prov.topic
    assert loaded.record_id == prov.record_id
    assert loaded.planted_node_ids == prov.planted_node_ids
    assert loaded.specs == prov.specs  # frozen dataclasses compare by value


def test_saved_provenance_ends_with_newline(tmp_path):
    path = tmp_path / ".dbg_provenance.json"
    prov = PlantProvenance(topic="t", record_id="r", planted_node_ids=["n"],
                           specs=[PlantSpec("a", "n", "DBG x")])
    save_provenance(prov, path)
    assert path.read_text(encoding="utf-8").endswith("}\n")
