# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for unmanaged preferred-solution discovery and selection."""

from __future__ import annotations

import pytest

from preferred_solution import PreferredSolutionGateway, PreferredSolutionService


SOLUTION_ONE = "11111111-1111-1111-1111-111111111111"
SOLUTION_TWO = "22222222-2222-2222-2222-222222222222"


class FakeGateway(PreferredSolutionGateway):
    def __init__(self, preferred_id: str | None = None) -> None:
        self.preferred_id = preferred_id
        self.set_calls = []

    def list_unmanaged_solutions(self) -> list[dict]:
        return [
            {
                "solutionid": SOLUTION_ONE,
                "uniquename": "ContosoESS",
                "friendlyname": "Contoso ESS",
                "version": "1.0.0.0",
                "_publisherid_value": "publisher-one",
            },
            {
                "solutionid": SOLUTION_TWO,
                "uniquename": "FabrikamESS",
                "friendlyname": "Fabrikam ESS",
                "version": "2.0.0.0",
                "_publisherid_value": "publisher-two",
            },
        ]

    def get_preferred_solution_id(self) -> str | None:
        return self.preferred_id

    def get_publisher(self, publisher_id: str) -> dict:
        if publisher_id == "publisher-one":
            return {
                "uniquename": "ContosoPublisher",
                "friendlyname": "Contoso",
                "customizationprefix": "contoso",
            }
        return {
            "uniquename": "DefaultPublisherorg",
            "friendlyname": "Default Publisher",
            "customizationprefix": "cr123",
        }

    def set_preferred_solution(self, solution_id: str) -> None:
        self.set_calls.append(solution_id)
        self.preferred_id = solution_id


def test_candidates_include_metadata_and_mark_current_preferred() -> None:
    service = PreferredSolutionService(FakeGateway(SOLUTION_TWO))

    candidates = service.list_candidates()

    assert [candidate.solution_id for candidate in candidates] == [
        SOLUTION_TWO,
        SOLUTION_ONE,
    ]
    assert candidates[0].is_preferred is True
    assert candidates[0].publisher_is_default is True
    assert candidates[1].publisher_prefix == "contoso"


def test_configure_skips_write_when_solution_is_already_preferred() -> None:
    gateway = FakeGateway(SOLUTION_ONE)
    service = PreferredSolutionService(gateway)

    selected, already_preferred = service.configure(SOLUTION_ONE)

    assert selected.unique_name == "ContosoESS"
    assert already_preferred is True
    assert gateway.set_calls == []


def test_configure_sets_and_verifies_new_preferred_solution() -> None:
    gateway = FakeGateway(SOLUTION_ONE)
    service = PreferredSolutionService(gateway)

    selected, already_preferred = service.configure(SOLUTION_TWO)

    assert selected.unique_name == "FabrikamESS"
    assert already_preferred is False
    assert gateway.set_calls == [SOLUTION_TWO]
    assert gateway.preferred_id == SOLUTION_TWO


def test_configure_rejects_solution_outside_discovered_list() -> None:
    service = PreferredSolutionService(FakeGateway())

    with pytest.raises(ValueError, match="not an eligible unmanaged solution"):
        service.configure("33333333-3333-3333-3333-333333333333")


def test_configure_fails_when_dataverse_does_not_retain_selection() -> None:
    class NonPersistingGateway(FakeGateway):
        def set_preferred_solution(self, solution_id: str) -> None:
            self.set_calls.append(solution_id)

    service = PreferredSolutionService(NonPersistingGateway(SOLUTION_ONE))

    with pytest.raises(RuntimeError, match="did not retain"):
        service.configure(SOLUTION_TWO)
