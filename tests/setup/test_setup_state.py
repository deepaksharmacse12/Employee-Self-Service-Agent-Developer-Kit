# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Pure-logic tests for the integration-neutral setup state domain."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import setup_state
from setup_state import (
    EnvironmentType,
    InstallationStatus,
    JsonSetupStateRepository,
    LegacyWorkdayStateMigrator,
    ProductId,
    SetupState,
    SetupStateError,
    SetupStateService,
    SetupWorkflow,
    StepStatus,
    ValidationRecord,
    ValidationMode,
    ValidationStatus,
)


def test_new_state_has_one_deterministic_resume_step() -> None:
    state = SetupState()

    assert state.active_step == "SETUP-01"
    assert SetupWorkflow.next_step(state) == "SETUP-01"
    assert all(record.state == "pending" for record in state.steps.values())
    assert set(state.products) == {
        "da.esshr",
        "da.essit",
        "da.esshub",
        "cea.esshr",
        "cea.essit",
        "cea.esshub",
    }


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
        "SETUP-PREREQ-MCP-001",
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
    state.selected_products = [
        ProductId.DA_ESSHR,
        ProductId.CEA_ESSIT,
    ]
    state.products[ProductId.DA_ESSHR].selected = True
    state.products[ProductId.DA_ESSHR].installation_status = (
        InstallationStatus.PENDING
    )
    state.products[ProductId.CEA_ESSIT].selected = True
    state.products[ProductId.CEA_ESSIT].installation_status = (
        InstallationStatus.PENDING
    )

    repository.save(state)
    loaded = repository.load()

    assert loaded.selected_products == ["da.esshr", "cea.essit"]
    assert loaded.active_step == "SETUP-01"
    assert not list(state_path.parent.glob("*.tmp"))


def test_repository_rejects_corrupt_state(tmp_path: Path) -> None:
    state_path = tmp_path / "config.json"
    state_path.write_text("{broken", encoding="utf-8")

    with pytest.raises(SetupStateError, match="unreadable"):
        JsonSetupStateRepository(state_path).load()


@pytest.mark.parametrize("corruption", [
    "step-string",
    "step-unknown-key",
    "validations-container-list",
    "validations-list",
    "products-container-list",
    "product-string",
])
def test_repository_normalizes_malformed_records(
    tmp_path: Path,
    corruption: str,
) -> None:
    state_path = tmp_path / "config.json"
    raw = SetupState().to_dict()
    if corruption == "step-string":
        raw["steps"]["SETUP-01"] = "not-a-step-record"
    elif corruption == "step-unknown-key":
        raw["steps"]["SETUP-01"] = {"unknown": True}
    elif corruption == "validations-container-list":
        raw["validations"] = []
    elif corruption == "validations-list":
        raw["validations"] = {"CHECK-001": []}
    elif corruption == "products-container-list":
        raw["products"] = []
    else:
        raw["products"]["da.esshr"] = "not-a-product-record"
    state_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(SetupStateError, match="malformed"):
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
        "SETUP-PREREQ-MCP-001",
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
    )
    SetupWorkflow.select_initial_product(state, ProductId.DA_ESSHR)

    with pytest.raises(SetupStateError, match="scope is locked"):
        SetupWorkflow.set_scope(
            state,
            environment_id="env-2",
            environment_name="Production",
            environment_type=EnvironmentType.PROD,
            tenant_endpoint="https://prod.crm.dynamics.com",
        )


def test_scope_accepts_discovered_power_platform_environment_type() -> None:
    state = SetupState()

    SetupWorkflow.set_scope(
        state,
        environment_id="environment-id",
        environment_name="Developer Environment",
        environment_type="Developer",
        tenant_endpoint="https://dev.crm.dynamics.com",
    )

    assert state.environment["type"] == "Developer"
    assert state.selected_products == []


def test_product_selection_has_a_separate_cli_transition() -> None:
    args = setup_state.build_parser().parse_args([
        "select-product",
        "--product",
        "da.esshr",
    ])

    assert args.command == "select-product"
    assert args.product == "da.esshr"


def test_initial_product_selection_requires_locked_environment() -> None:
    state = SetupState()

    with pytest.raises(SetupStateError, match="environment is locked"):
        SetupWorkflow.select_initial_product(state, ProductId.DA_ESSHR)


def test_initial_product_can_only_be_selected_once() -> None:
    state = SetupState()
    SetupWorkflow.set_scope(
        state,
        environment_id="env-1",
        environment_name="Development",
        environment_type=EnvironmentType.DEV,
        tenant_endpoint="https://dev.crm.dynamics.com",
    )
    SetupWorkflow.select_initial_product(state, ProductId.DA_ESSHR)

    with pytest.raises(SetupStateError, match="already selected"):
        SetupWorkflow.select_initial_product(state, ProductId.CEA_ESSIT)


def test_product_installation_states_are_independent() -> None:
    state = SetupState()
    SetupWorkflow.set_scope(
        state,
        environment_id="env-1",
        environment_name="Development",
        environment_type=EnvironmentType.DEV,
        tenant_endpoint="https://dev.crm.dynamics.com",
    )
    SetupWorkflow.select_initial_product(state, ProductId.DA_ESSHR)
    state.selected_products.append(ProductId.CEA_ESSIT)
    state.products[ProductId.CEA_ESSIT].selected = True
    state.products[ProductId.CEA_ESSIT].installation_status = (
        InstallationStatus.PENDING
    )

    SetupWorkflow.update_product_installation(
        state,
        ProductId.DA_ESSHR,
        InstallationStatus.INSTALLING,
    )
    SetupWorkflow.update_product_installation(
        state,
        ProductId.DA_ESSHR,
        InstallationStatus.INSTALLED,
    )
    SetupWorkflow.update_product_installation(
        state,
        ProductId.DA_ESSHR,
        InstallationStatus.BOUND,
    )
    SetupWorkflow.update_product_installation(
        state,
        ProductId.CEA_ESSIT,
        InstallationStatus.CONNECTION_REQUIRED,
    )

    assert state.products["da.esshr"].installation_status == "bound"
    assert (
        state.products["cea.essit"].installation_status
        == "connection-required"
    )
    assert state.products["da.essit"].installation_status == "not-selected"


def test_unselected_product_cannot_be_updated() -> None:
    state = SetupState()

    with pytest.raises(SetupStateError, match="unselected product"):
        SetupWorkflow.update_product_installation(
            state,
            ProductId.DA_ESSHR,
            InstallationStatus.INSTALLING,
        )


def test_install_step_requires_every_selected_product_to_be_bound() -> None:
    state = SetupState()
    state.selected_products = [ProductId.DA_ESSHR]
    state.products[ProductId.DA_ESSHR].selected = True
    state.products[ProductId.DA_ESSHR].installation_status = (
        InstallationStatus.INSTALLED
    )
    for step_id in ("SETUP-01", "SETUP-02", "SETUP-03", "SETUP-04"):
        state.steps[step_id].state = StepStatus.DONE
    state.active_step = "SETUP-05"
    for check_id in (
        "SETUP-INSTALL-SELECTION-001",
        "SETUP-INSTALL-001",
        "SETUP-INSTALL-002",
        "SETUP-INSTALL-003",
        "SETUP-INSTALL-004",
    ):
        SetupWorkflow.record_validation(
            state,
            check_id,
            ValidationStatus.PASS,
            ValidationMode.AUTOMATED,
            {},
            [],
        )

    with pytest.raises(SetupStateError, match="not installed and bound"):
        SetupWorkflow.update_step(state, "SETUP-05", StepStatus.DONE)


def test_installation_progress_updates_are_resumable() -> None:
    state = SetupState()
    state.selected_products = [ProductId.DA_ESSHR]
    state.products[ProductId.DA_ESSHR].selected = True
    state.products[ProductId.DA_ESSHR].installation_status = (
        InstallationStatus.INSTALLING
    )

    SetupWorkflow.update_product_installation(
        state,
        ProductId.DA_ESSHR,
        InstallationStatus.INSTALLING,
    )
    SetupWorkflow.update_product_installation(
        state,
        ProductId.DA_ESSHR,
        InstallationStatus.INSTALLED,
    )
    SetupWorkflow.update_product_installation(
        state,
        ProductId.DA_ESSHR,
        InstallationStatus.INSTALLED,
    )

    assert state.products["da.esshr"].installation_status == "installed"


def test_invoker_connection_requires_maker_attestation_before_bound() -> None:
    state = SetupState()
    SetupWorkflow.set_scope(
        state,
        environment_id="env-1",
        environment_name="Development",
        environment_type=EnvironmentType.DEV,
        tenant_endpoint="https://dev.crm.dynamics.com",
    )
    SetupWorkflow.select_initial_product(state, ProductId.DA_ESSIT)
    SetupWorkflow.update_product_installation(
        state,
        ProductId.DA_ESSIT,
        InstallationStatus.INSTALLING,
    )
    SetupWorkflow.update_product_installation(
        state,
        ProductId.DA_ESSIT,
        InstallationStatus.INSTALLED,
    )
    SetupWorkflow.update_product_installation(
        state,
        ProductId.DA_ESSIT,
        InstallationStatus.CONNECTION_ATTESTATION_REQUIRED,
        connection_name="alchemy",
        schema_name="msdyn_CopilotForEmployeeSelfServiceDAIT",
        requires_connection_attestation=True,
        agent_id="agent-id",
        agent_name="Employee Self-Service IT",
        connection_settings_url=(
            "https://copilotstudio.microsoft.com/environments/"
            "env-1/copilots/agent-id/settings/connectionSettings"
        ),
    )

    with pytest.raises(SetupStateError, match="requires maker connection"):
        SetupWorkflow.update_product_installation(
            state,
            ProductId.DA_ESSIT,
            InstallationStatus.BOUND,
        )

    SetupWorkflow.attest_product_connection(state, ProductId.DA_ESSIT)

    product = state.products["da.essit"]
    assert product.installation_status == "bound"
    assert product.connection_attested_at is not None
    assert (
        state.validations["SETUP-INSTALL-004"].mode
        == ValidationMode.MANUAL_ATTESTED
    )
    assert state.validations[
        "SETUP-INSTALL-CONNECTION-DA-ESSIT"
    ].evidence["agent_id"] == "agent-id"


def test_verified_alm_solution_metadata_is_persisted() -> None:
    state = SetupState()

    SetupWorkflow.set_alm(
        state,
        solution_id="11111111-1111-1111-1111-111111111111",
        solution_name="ContosoESS",
        publisher_prefix="contoso",
        version="1.0.0.0",
    )

    assert state.alm == {
        "solution_id": "11111111-1111-1111-1111-111111111111",
        "solution_name": "ContosoESS",
        "publisher_prefix": "contoso",
        "version": "1.0.0.0",
        "preferred": True,
        "updated_at": state.alm["updated_at"],
    }


def test_alm_solution_metadata_cannot_be_incomplete() -> None:
    state = SetupState()

    with pytest.raises(SetupStateError, match="publisher_prefix"):
        SetupWorkflow.set_alm(
            state,
            solution_id="11111111-1111-1111-1111-111111111111",
            solution_name="ContosoESS",
            publisher_prefix="",
            version="1.0.0.0",
        )


def test_adding_product_reopens_only_dependent_foundation_steps() -> None:
    state = SetupState()
    state.environment = {"locked": True}
    state.selected_products = [ProductId.DA_ESSHR]
    state.products[ProductId.DA_ESSHR].selected = True
    state.products[ProductId.DA_ESSHR].installation_status = (
        InstallationStatus.BOUND
    )
    state.products[ProductId.DA_ESSHR].ready = True
    for record in state.steps.values():
        record.state = StepStatus.DONE
    state.active_step = "SETUP-07"
    state.connect_ready = True
    state.completed_at = "2026-08-04T00:00:00+00:00"
    state.validations["SETUP-INSTALL-001"] = ValidationRecord(
        check_id="SETUP-INSTALL-001",
        status=ValidationStatus.PASS,
        mode=ValidationMode.AUTOMATED,
    )
    state.validations["SETUP-FINAL-001"] = ValidationRecord(
        check_id="SETUP-FINAL-001",
        status=ValidationStatus.PASS,
        mode=ValidationMode.AUTOMATED,
    )

    SetupWorkflow.add_products(state, (ProductId.CEA_ESSIT,))

    assert state.selected_products == ["da.esshr", "cea.essit"]
    assert state.products["da.esshr"].installation_status == "bound"
    assert state.products["da.esshr"].ready is True
    assert state.products["cea.essit"].installation_status == "pending"
    assert state.steps["SETUP-04"].state == "done"
    assert state.steps["SETUP-05"].state == "pending"
    assert state.steps["SETUP-06"].state == "pending"
    assert state.steps["SETUP-07"].state == "pending"
    assert state.active_step == "SETUP-05"
    assert state.connect_ready is False
    assert state.completed_at is None
    assert "SETUP-INSTALL-001" not in state.validations
    assert "SETUP-FINAL-001" not in state.validations
    assert state.validations["SETUP-INSTALL-SELECTION-001"].evidence[
        "scope_extended"
    ] is True


def test_product_cannot_be_added_before_foundation_completes() -> None:
    state = SetupState()
    state.environment = {"locked": True}

    with pytest.raises(SetupStateError, match="only after foundation"):
        SetupWorkflow.add_products(state, (ProductId.DA_ESSHR,))


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
            "SETUP-PREREQ-MCP-001",
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
            "SETUP-INSTALL-SELECTION-001",
            "SETUP-INSTALL-001",
            "SETUP-INSTALL-002",
            "SETUP-INSTALL-003",
            "SETUP-INSTALL-004",
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
