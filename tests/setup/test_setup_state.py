# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Pure-logic tests for the integration-neutral setup state domain."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from setup_state import (
    EnvironmentType,
    JsonSetupStateRepository,
    LegacyWorkdayStateMigrator,
    SetupState,
    SetupStateError,
    SetupStateService,
    SetupWorkflow,
    StarterScope,
    StepStatus,
    ValidationMode,
    ValidationStatus,
)


def test_new_state_has_one_deterministic_resume_step() -> None:
    state = SetupState()

    assert state.active_step == "SETUP-01"
    assert SetupWorkflow.next_step(state) == "SETUP-01"
    assert all(record.state == "pending" for record in state.steps.values())


def test_workflow_rejects_multiple_in_progress_steps() -> None:
    state = SetupState()
    state.steps["SETUP-01"].state = StepStatus.IN_PROGRESS
    state.steps["SETUP-02"].state = StepStatus.IN_PROGRESS

    with pytest.raises(SetupStateError, match="Only one"):
        SetupWorkflow.validate(state)


def test_step_updates_advance_to_first_incomplete_step() -> None:
    state = SetupState()
    for check_id in (
        "SETUP-SCOPE-001",
        "SETUP-SCOPE-002",
        "SETUP-SCOPE-003",
        "SETUP-PREREQ-ACCESS-001",
        "SETUP-PREREQ-DV-001",
        "SETUP-PREREQ-CAP-001",
        "SETUP-PREREQ-GOV-001",
        "SETUP-PREREQ-BLOCK-001",
    ):
        SetupWorkflow.record_validation(
            state,
            check_id,
            ValidationStatus.PASS,
            ValidationMode.AUTOMATED,
            {},
            [],
        )

    SetupWorkflow.update_step(state, "SETUP-01", StepStatus.DONE)
    SetupWorkflow.update_step(state, "SETUP-02", StepStatus.DONE)

    assert state.active_step == "SETUP-03"


def test_done_step_cannot_regress() -> None:
    state = SetupState()
    for check_id in (
        "SETUP-SCOPE-001",
        "SETUP-SCOPE-002",
        "SETUP-SCOPE-003",
    ):
        SetupWorkflow.record_validation(
            state,
            check_id,
            ValidationStatus.PASS,
            ValidationMode.AUTOMATED,
            {},
            [],
        )
    SetupWorkflow.update_step(state, "SETUP-01", StepStatus.DONE)

    with pytest.raises(SetupStateError, match="Invalid transition"):
        SetupWorkflow.update_step(
            state,
            "SETUP-01",
            StepStatus.IN_PROGRESS,
        )


def test_validation_contract_is_persisted() -> None:
    state = SetupState()

    SetupWorkflow.record_validation(
        state,
        "SETUP-PREREQ-ACCESS-001",
        ValidationStatus.PASS,
        ValidationMode.MANUAL_ATTESTED,
        {"attestor": "maker"},
        [],
    )

    record = state.validations["SETUP-PREREQ-ACCESS-001"]
    assert record.status == "pass"
    assert record.mode == "manual-attested"
    assert record.evidence == {"attestor": "maker"}
    assert record.cause_codes == []


def test_json_repository_round_trips_atomically(tmp_path: Path) -> None:
    state_path = tmp_path / ".local" / "setup" / "config.json"
    repository = JsonSetupStateRepository(state_path)
    state = SetupState()
    state.starter_scope = StarterScope.BOTH

    repository.save(state)
    loaded = repository.load()

    assert loaded.starter_scope == "both"
    assert loaded.active_step == "SETUP-01"
    assert not list(state_path.parent.glob("*.tmp"))


def test_repository_rejects_corrupt_state(tmp_path: Path) -> None:
    state_path = tmp_path / "config.json"
    state_path.write_text("{broken", encoding="utf-8")

    with pytest.raises(SetupStateError, match="unreadable"):
        JsonSetupStateRepository(state_path).load()


def test_legacy_migration_imports_only_common_foundation(
    tmp_path: Path,
) -> None:
    legacy_path = tmp_path / "workday.json"
    legacy_path.write_text(json.dumps({
        "setupStatus": {
            "S1.1": {"state": "done"},
            "S1.2": {"state": "done"},
            "S2.1": {"state": "done"},
            "S3.1": {"state": "done"},
        }
    }), encoding="utf-8")
    state_path = tmp_path / "setup.json"
    service = SetupStateService(
        JsonSetupStateRepository(state_path),
        LegacyWorkdayStateMigrator(legacy_path),
    )

    state = service.initialize()

    assert all(record.state == "pending" for record in state.steps.values())
    assert state.legacy_migration["observed"] == [
        "legacy-environment-ready",
        "legacy-ess-solution-installed",
    ]
    assert "S3.1" not in state.legacy_migration["observed"]


def test_manual_attestation_requires_evidence() -> None:
    state = SetupState()

    with pytest.raises(SetupStateError, match="requires evidence"):
        SetupWorkflow.record_validation(
            state,
            "SETUP-PREREQ-CAP-001",
            ValidationStatus.PASS,
            ValidationMode.MANUAL_ATTESTED,
            {},
            [],
        )


def test_steps_cannot_complete_out_of_order() -> None:
    state = SetupState()
    for check_id in (
        "SETUP-PREREQ-ACCESS-001",
        "SETUP-PREREQ-DV-001",
        "SETUP-PREREQ-CAP-001",
        "SETUP-PREREQ-GOV-001",
        "SETUP-PREREQ-BLOCK-001",
    ):
        SetupWorkflow.record_validation(
            state,
            check_id,
            ValidationStatus.PASS,
            ValidationMode.AUTOMATED,
            {},
            [],
        )

    with pytest.raises(SetupStateError, match="prior steps"):
        SetupWorkflow.update_step(state, "SETUP-02", StepStatus.DONE)


def test_locked_scope_cannot_drift() -> None:
    state = SetupState()
    SetupWorkflow.set_scope(
        state,
        environment_id="env-1",
        environment_name="Development",
        environment_type=EnvironmentType.DEV,
        tenant_endpoint="https://dev.crm.dynamics.com",
        starter_scope=StarterScope.HR,
    )

    with pytest.raises(SetupStateError, match="scope is locked"):
        SetupWorkflow.set_scope(
            state,
            environment_id="env-2",
            environment_name="Production",
            environment_type=EnvironmentType.PROD,
            tenant_endpoint="https://prod.crm.dynamics.com",
            starter_scope=StarterScope.HR,
        )


def test_finalize_requires_all_prior_steps() -> None:
    state = SetupState()

    with pytest.raises(SetupStateError, match="incomplete steps"):
        SetupWorkflow.finalize(state)


def test_final_step_cannot_bypass_bundle() -> None:
    state = SetupState()

    with pytest.raises(SetupStateError, match="final setup bundle"):
        SetupWorkflow.update_step(state, "SETUP-07", StepStatus.DONE)


def test_finalize_marks_connect_ready() -> None:
    state = SetupState()
    for check_ids in (
        (
            "SETUP-SCOPE-001",
            "SETUP-SCOPE-002",
            "SETUP-SCOPE-003",
        ),
        (
            "SETUP-PREREQ-ACCESS-001",
            "SETUP-PREREQ-DV-001",
            "SETUP-PREREQ-CAP-001",
            "SETUP-PREREQ-GOV-001",
            "SETUP-PREREQ-BLOCK-001",
        ),
        (
            "SETUP-ENV-001",
            "SETUP-ENV-002",
            "SETUP-ENV-003",
        ),
        (
            "SETUP-ALM-001",
            "SETUP-ALM-002",
            "SETUP-ALM-003",
        ),
        (
            "SETUP-INSTALL-001",
            "SETUP-INSTALL-002",
            "SETUP-INSTALL-003",
        ),
        (
            "SETUP-READINESS-001",
            "SETUP-READINESS-002",
            "SETUP-READINESS-003",
        ),
        ("SETUP-HANDOFF-002",),
    ):
        for check_id in check_ids:
            SetupWorkflow.record_validation(
                state,
                check_id,
                ValidationStatus.PASS,
                ValidationMode.AUTOMATED,
                {},
                [],
            )
    for step_id in tuple(state.steps)[:-1]:
        SetupWorkflow.update_step(state, step_id, StepStatus.DONE)

    SetupWorkflow.finalize(state)

    assert state.steps["SETUP-07"].state == "done"
    assert state.connect_ready is True
    assert state.completed_at is not None
    assert state.validations["SETUP-FINAL-001"].status == "pass"
    assert state.validations["SETUP-FINAL-002"].status == "pass"
