# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Persistent state management for the integration-neutral ESS setup workflow."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 2
DEFAULT_STATE_PATH = Path(".local/setup/config.json")
DEFAULT_LEGACY_WORKDAY_PATH = Path(".local/connect/workday/config.json")
SETUP_INTENT = "prereqs + base ESS install only"
STEP_ORDER = (
    "SETUP-01",
    "SETUP-02",
    "SETUP-03",
    "SETUP-04",
    "SETUP-05",
    "SETUP-06",
    "SETUP-07",
)
REQUIRED_CHECKS_BY_STEP = {
    "SETUP-01": (
        "SETUP-SCOPE-001",
        "SETUP-SCOPE-002",
        "SETUP-SCOPE-003",
    ),
    "SETUP-02": (
        "SETUP-PREREQ-ACCESS-001",
        "SETUP-PREREQ-DV-001",
        "SETUP-PREREQ-CAP-001",
        "SETUP-PREREQ-GOV-001",
        "SETUP-PREREQ-MCP-001",
        "SETUP-PREREQ-BLOCK-001",
    ),
    "SETUP-03": (
        "SETUP-ENV-001",
        "SETUP-ENV-002",
        "SETUP-ENV-003",
    ),
    "SETUP-04": (
        "SETUP-ALM-001",
        "SETUP-ALM-002",
        "SETUP-ALM-003",
    ),
    "SETUP-05": (
        "SETUP-INSTALL-SELECTION-001",
        "SETUP-INSTALL-001",
        "SETUP-INSTALL-002",
        "SETUP-INSTALL-003",
        "SETUP-INSTALL-004",
    ),
    "SETUP-06": (
        "SETUP-READINESS-001",
        "SETUP-READINESS-002",
        "SETUP-READINESS-003",
    ),
}
FINAL_BUNDLE_CHECKS = (
    "SETUP-PREREQ-ACCESS-001",
    "SETUP-PREREQ-DV-001",
    "SETUP-PREREQ-CAP-001",
    "SETUP-PREREQ-GOV-001",
    "SETUP-PREREQ-MCP-001",
    "SETUP-ENV-001",
    "SETUP-ALM-001",
    "SETUP-ALM-002",
    "SETUP-INSTALL-SELECTION-001",
    "SETUP-INSTALL-001",
    "SETUP-INSTALL-002",
    "SETUP-INSTALL-004",
    "SETUP-READINESS-001",
    "SETUP-READINESS-002",
    "SETUP-HANDOFF-002",
)


class SetupStateError(ValueError):
    """Raised when setup state is invalid or a transition is not allowed."""


class StepStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in-progress"
    BLOCKED = "blocked"
    DONE = "done"


class ProductId(StrEnum):
    DA_ESSHR = "da.esshr"
    DA_ESSIT = "da.essit"
    DA_ESSHUB = "da.esshub"
    CEA_ESSHR = "cea.esshr"
    CEA_ESSIT = "cea.essit"
    CEA_ESSHUB = "cea.esshub"


class InstallationStatus(StrEnum):
    NOT_SELECTED = "not-selected"
    PENDING = "pending"
    CONNECTION_REQUIRED = "connection-required"
    READY = "ready"
    INSTALLING = "installing"
    MANUAL_REQUIRED = "manual-required"
    INSTALLED = "installed"
    CONNECTION_ATTESTATION_REQUIRED = "connection-attestation-required"
    BOUND = "bound"
    FAILED = "failed"


class EnvironmentType(StrEnum):
    DEV = "Dev"
    TEST = "Test"
    PROD = "Prod"


class ValidationStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"


class ValidationMode(StrEnum):
    AUTOMATED = "automated"
    MANUAL_ATTESTED = "manual-attested"


@dataclass
class StepRecord:
    state: str = StepStatus.PENDING
    updated_at: str | None = None
    failure_causes: list[str] = field(default_factory=list)


@dataclass
class ValidationRecord:
    check_id: str
    status: str
    mode: str
    evidence: dict[str, Any] = field(default_factory=dict)
    cause_codes: list[str] = field(default_factory=list)
    recorded_at: str = field(default_factory=lambda: utc_now())


@dataclass
class ProductInstallationRecord:
    selected: bool = False
    installation_status: str = InstallationStatus.NOT_SELECTED
    connection_name: str | None = None
    schema_name: str | None = None
    requires_connection_attestation: bool = False
    agent_id: str | None = None
    agent_name: str | None = None
    connection_settings_url: str | None = None
    connection_attested_at: str | None = None
    ready: bool = False
    failure_cause: str | None = None
    updated_at: str | None = None


def _default_products() -> dict[str, ProductInstallationRecord]:
    return {
        product_id.value: ProductInstallationRecord()
        for product_id in ProductId
    }


@dataclass
class SetupState:
    schema_version: int = SCHEMA_VERSION
    intent: str = SETUP_INTENT
    environment: dict[str, Any] = field(default_factory=dict)
    selected_products: list[str] = field(default_factory=list)
    prerequisites: dict[str, Any] = field(default_factory=dict)
    alm: dict[str, Any] = field(default_factory=dict)
    products: dict[str, ProductInstallationRecord] = field(
        default_factory=_default_products
    )
    steps: dict[str, StepRecord] = field(default_factory=lambda: {
        step_id: StepRecord() for step_id in STEP_ORDER
    })
    validations: dict[str, ValidationRecord] = field(default_factory=dict)
    active_step: str = STEP_ORDER[0]
    connect_ready: bool = False
    open_issues: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: utc_now())
    updated_at: str = field(default_factory=lambda: utc_now())
    completed_at: str | None = None
    legacy_migration: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SetupState":
        if raw.get("schema_version") != SCHEMA_VERSION:
            raise SetupStateError(
                f"Unsupported setup schema version: {raw.get('schema_version')!r}"
            )

        steps_raw = raw.get("steps")
        if not isinstance(steps_raw, dict):
            raise SetupStateError("Setup state must contain a steps object")
        if set(steps_raw) != set(STEP_ORDER):
            raise SetupStateError("Setup state contains an unexpected step set")

        validations_raw = raw.get("validations", {})
        if not isinstance(validations_raw, dict):
            raise SetupStateError(
                "Setup state contains a malformed validations object"
            )
        products_raw = raw.get("products", {})
        if not isinstance(products_raw, dict):
            raise SetupStateError(
                "Setup state contains a malformed products object"
            )

        try:
            steps = {
                step_id: StepRecord(**record)
                for step_id, record in steps_raw.items()
            }
            validations = {
                check_id: ValidationRecord(**record)
                for check_id, record in validations_raw.items()
            }
            products = _default_products()
            products.update({
                product_id: ProductInstallationRecord(**record)
                for product_id, record in products_raw.items()
            })
        except (TypeError, ValueError) as exc:
            raise SetupStateError(
                "Setup state contains malformed step, validation, or product "
                "records"
            ) from exc

        state = cls(
            schema_version=raw["schema_version"],
            intent=raw.get("intent", SETUP_INTENT),
            environment=raw.get("environment", {}),
            selected_products=list(raw.get("selected_products", [])),
            prerequisites=raw.get("prerequisites", {}),
            alm=raw.get("alm", {}),
            products=products,
            steps=steps,
            validations=validations,
            active_step=raw.get("active_step", STEP_ORDER[0]),
            connect_ready=bool(raw.get("connect_ready", False)),
            open_issues=list(raw.get("open_issues", [])),
            created_at=raw.get("created_at", utc_now()),
            updated_at=raw.get("updated_at", utc_now()),
            completed_at=raw.get("completed_at"),
            legacy_migration=raw.get("legacy_migration", {}),
        )
        SetupWorkflow.validate(state)
        return state

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SetupStateRepository(ABC):
    """Persistence boundary for setup state."""

    @abstractmethod
    def exists(self) -> bool:
        """Return whether setup state exists."""

    @abstractmethod
    def load(self) -> SetupState:
        """Load and validate setup state."""

    @abstractmethod
    def save(self, state: SetupState) -> None:
        """Persist setup state atomically."""


class JsonSetupStateRepository(SetupStateRepository):
    """JSON-backed setup repository with atomic replacement."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def exists(self) -> bool:
        return self._path.is_file()

    def load(self) -> SetupState:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise SetupStateError(f"Setup state not found: {self._path}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise SetupStateError(f"Setup state is unreadable: {exc}") from exc
        if not isinstance(raw, dict):
            raise SetupStateError("Setup state root must be an object")
        return SetupState.from_dict(raw)

    def save(self, state: SetupState) -> None:
        SetupWorkflow.validate(state)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(state.to_dict(), indent=2, ensure_ascii=False) + "\n"
        fd, tmp_name = tempfile.mkstemp(
            prefix=f"{self._path.name}.",
            suffix=".tmp",
            dir=self._path.parent,
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
            os.replace(tmp_name, self._path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)


class LegacyWorkdayStateMigrator:
    """Imports only common foundation progress from legacy Workday setup state."""

    def __init__(self, legacy_path: Path) -> None:
        self._legacy_path = legacy_path

    def migrate(self, state: SetupState) -> SetupState:
        if not self._legacy_path.is_file() or state.legacy_migration:
            return state

        try:
            raw = json.loads(self._legacy_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SetupStateError(f"Legacy Workday state is unreadable: {exc}") from exc

        setup_status = raw.get("setupStatus", {})
        observed_steps: list[str] = []
        if self._is_done(setup_status, "S1.1") and self._is_done(
            setup_status, "S1.2"
        ):
            observed_steps.append("legacy-environment-ready")
        if self._is_done(setup_status, "S2.1"):
            observed_steps.append("legacy-ess-solution-installed")

        state.legacy_migration = {
            "source": str(self._legacy_path).replace("\\", "/"),
            "migrated_at": utc_now(),
            "observed": observed_steps,
            "note": (
                "Legacy evidence is retained for fast re-verification but does "
                "not complete new setup steps because environment identity and "
                "selected ESS products are not proven."
            ),
        }
        SetupWorkflow.refresh_active_step(state)
        return state

    @staticmethod
    def _is_done(setup_status: Any, step_id: str) -> bool:
        if not isinstance(setup_status, dict):
            return False
        record = setup_status.get(step_id)
        return isinstance(record, dict) and record.get("state") == StepStatus.DONE


class SetupWorkflow:
    """Domain service enforcing setup invariants and transitions."""

    @staticmethod
    def validate(state: SetupState) -> None:
        if state.intent != SETUP_INTENT:
            raise SetupStateError("Setup intent cannot include integration work")
        if set(state.steps) != set(STEP_ORDER):
            raise SetupStateError("Setup state must contain every canonical step")
        expected_products = {product_id.value for product_id in ProductId}
        if set(state.products) != expected_products:
            raise SetupStateError(
                "Setup state must contain every supported ESS product record"
            )

        in_progress = [
            step_id
            for step_id, record in state.steps.items()
            if record.state == StepStatus.IN_PROGRESS
        ]
        if len(in_progress) > 1:
            raise SetupStateError("Only one setup step may be in-progress")

        for step_id, record in state.steps.items():
            try:
                StepStatus(record.state)
            except ValueError as exc:
                raise SetupStateError(
                    f"Invalid state for {step_id}: {record.state!r}"
                ) from exc

        if len(state.selected_products) != len(set(state.selected_products)):
            raise SetupStateError("Selected ESS products must be unique")
        for product_id in state.selected_products:
            try:
                ProductId(product_id)
            except ValueError as exc:
                raise SetupStateError(
                    f"Invalid ESS product: {product_id!r}"
                ) from exc
        selected = set(state.selected_products)
        for product_id, record in state.products.items():
            try:
                InstallationStatus(record.installation_status)
            except ValueError as exc:
                raise SetupStateError(
                    f"Invalid installation status for {product_id}: "
                    f"{record.installation_status!r}"
                ) from exc
            if record.selected != (product_id in selected):
                raise SetupStateError(
                    f"Product selection flag is inconsistent for {product_id}"
                )
            if not record.selected and (
                record.installation_status != InstallationStatus.NOT_SELECTED
            ):
                raise SetupStateError(
                    f"Unselected product {product_id} cannot have installation "
                    "progress"
                )
            if (
                record.installation_status
                == InstallationStatus.CONNECTION_ATTESTATION_REQUIRED
            ):
                required_metadata = (
                    record.connection_name,
                    record.agent_id,
                    record.agent_name,
                    record.connection_settings_url,
                )
                if (
                    not record.requires_connection_attestation
                    or any(not value for value in required_metadata)
                ):
                    raise SetupStateError(
                        f"Product {product_id} has incomplete connection "
                        "attestation state"
                    )
            if (
                record.installation_status == InstallationStatus.BOUND
                and record.requires_connection_attestation
                and not record.connection_attested_at
            ):
                raise SetupStateError(
                    f"Product {product_id} is bound without required maker "
                    "connection attestation"
                )

        if state.active_step not in STEP_ORDER:
            raise SetupStateError(f"Invalid active step: {state.active_step!r}")

        expected_active = SetupWorkflow.next_step(state)
        if state.active_step != expected_active:
            raise SetupStateError(
                f"Active step {state.active_step!r} does not match "
                f"deterministic next step {expected_active!r}"
            )

        if state.connect_ready and not SetupWorkflow.is_complete(state):
            raise SetupStateError("Connect cannot be ready before setup is complete")

    @staticmethod
    def next_step(state: SetupState) -> str:
        for step_id in STEP_ORDER:
            if state.steps[step_id].state != StepStatus.DONE:
                return step_id
        return STEP_ORDER[-1]

    @staticmethod
    def refresh_active_step(state: SetupState) -> None:
        state.active_step = SetupWorkflow.next_step(state)
        state.updated_at = utc_now()

    @staticmethod
    def set_scope(
        state: SetupState,
        *,
        environment_id: str,
        environment_name: str,
        environment_type: str,
        tenant_endpoint: str,
    ) -> None:
        proposed_environment = {
            "id": environment_id,
            "name": environment_name,
            "type": environment_type,
            "tenant_endpoint": tenant_endpoint.rstrip("/"),
            "locked": True,
            "selected_at": utc_now(),
        }
        current_environment = state.environment
        if current_environment.get("locked"):
            locked_identity = {
                key: current_environment.get(key)
                for key in ("id", "name", "type", "tenant_endpoint")
            }
            proposed_identity = {
                key: proposed_environment.get(key)
                for key in ("id", "name", "type", "tenant_endpoint")
            }
            if locked_identity != proposed_identity:
                raise SetupStateError(
                    "Setup scope is locked; start a new setup run to change "
                    "the environment"
                )

        state.environment = proposed_environment
        state.selected_products = []
        for product_id, record in state.products.items():
            record.selected = False
            record.installation_status = InstallationStatus.NOT_SELECTED
            record.connection_name = None
            record.schema_name = None
            record.requires_connection_attestation = False
            record.agent_id = None
            record.agent_name = None
            record.connection_settings_url = None
            record.connection_attested_at = None
            record.ready = False
            record.failure_cause = None
            record.updated_at = utc_now()
        for check_id, evidence in (
            (
                "SETUP-SCOPE-001",
                {
                    "environment_id": environment_id,
                    "environment_name": environment_name,
                },
            ),
            (
                "SETUP-SCOPE-002",
                {"environment_type": environment_type},
            ),
            (
                "SETUP-SCOPE-003",
                {"intent": SETUP_INTENT},
            ),
        ):
            SetupWorkflow.record_validation(
                state,
                check_id,
                ValidationStatus.PASS,
                ValidationMode.AUTOMATED,
                evidence,
                [],
            )
        SetupWorkflow.update_step(state, "SETUP-01", StepStatus.DONE)

    @staticmethod
    def select_initial_product(
        state: SetupState,
        product_id: ProductId,
    ) -> None:
        if not state.environment.get("locked"):
            raise SetupStateError(
                "Cannot select a product before the environment is locked"
            )
        if state.selected_products:
            raise SetupStateError(
                "An initial ESS product is already selected"
            )

        product_id = ProductId(product_id)
        record = state.products[product_id.value]
        state.selected_products = [product_id.value]
        record.selected = True
        record.installation_status = InstallationStatus.PENDING
        record.updated_at = utc_now()
        SetupWorkflow.record_validation(
            state,
            "SETUP-INSTALL-SELECTION-001",
            ValidationStatus.PASS,
            ValidationMode.AUTOMATED,
            {"selected_product": product_id.value},
            [],
        )
        SetupWorkflow.refresh_active_step(state)

    @staticmethod
    def update_step(
        state: SetupState,
        step_id: str,
        status: StepStatus,
        failure_causes: list[str] | None = None,
        *,
        finalizing: bool = False,
    ) -> None:
        if step_id not in state.steps:
            raise SetupStateError(f"Unknown setup step: {step_id}")
        if (
            step_id == "SETUP-07"
            and status == StepStatus.DONE
            and not finalizing
        ):
            raise SetupStateError(
                "SETUP-07 can only be completed by the final setup bundle"
            )
        current = StepStatus(state.steps[step_id].state)
        allowed = {
            StepStatus.PENDING: {
                StepStatus.IN_PROGRESS,
                StepStatus.BLOCKED,
                StepStatus.DONE,
            },
            StepStatus.IN_PROGRESS: {
                StepStatus.BLOCKED,
                StepStatus.DONE,
            },
            StepStatus.BLOCKED: {
                StepStatus.BLOCKED,
                StepStatus.IN_PROGRESS,
                StepStatus.DONE,
            },
            StepStatus.DONE: {StepStatus.DONE},
        }
        if status not in allowed[current]:
            raise SetupStateError(
                f"Invalid transition for {step_id}: {current} -> {status}"
            )
        step_index = STEP_ORDER.index(step_id)
        incomplete_prior = [
            prior_id
            for prior_id in STEP_ORDER[:step_index]
            if state.steps[prior_id].state != StepStatus.DONE
        ]
        if current != StepStatus.DONE and incomplete_prior:
            raise SetupStateError(
                f"Cannot update {step_id}; prior steps are incomplete: "
                f"{', '.join(incomplete_prior)}"
            )
        if status == StepStatus.DONE:
            SetupWorkflow.ensure_required_checks_pass(state, step_id)
            if step_id == "SETUP-05":
                incomplete_products = [
                    product_id
                    for product_id in state.selected_products
                    if state.products[product_id].installation_status
                    != InstallationStatus.BOUND
                ]
                if incomplete_products:
                    raise SetupStateError(
                        "Cannot complete SETUP-05; products are not installed "
                        f"and bound: {', '.join(incomplete_products)}"
                    )
            if step_id == "SETUP-06":
                unready_products = [
                    product_id
                    for product_id in state.selected_products
                    if not state.products[product_id].ready
                ]
                if unready_products:
                    raise SetupStateError(
                        "Cannot complete SETUP-06; products are not ready: "
                        f"{', '.join(unready_products)}"
                    )
        if status == StepStatus.IN_PROGRESS:
            for other_id, record in state.steps.items():
                if (
                    other_id != step_id
                    and record.state == StepStatus.IN_PROGRESS
                ):
                    raise SetupStateError(
                        f"{other_id} is already the active in-progress step"
                    )

        state.steps[step_id] = StepRecord(
            state=status,
            updated_at=utc_now(),
            failure_causes=list(failure_causes or []),
        )
        SetupWorkflow.refresh_active_step(state)

    @staticmethod
    def record_validation(
        state: SetupState,
        check_id: str,
        status: ValidationStatus,
        mode: ValidationMode,
        evidence: dict[str, Any],
        cause_codes: list[str],
    ) -> None:
        if mode == ValidationMode.MANUAL_ATTESTED and not evidence:
            raise SetupStateError(
                f"Manual attestation for {check_id} requires evidence"
            )
        state.validations[check_id] = ValidationRecord(
            check_id=check_id,
            status=status,
            mode=mode,
            evidence=evidence,
            cause_codes=cause_codes,
        )
        state.updated_at = utc_now()

    @staticmethod
    def ensure_required_checks_pass(
        state: SetupState,
        step_id: str,
    ) -> None:
        required = REQUIRED_CHECKS_BY_STEP.get(step_id, ())
        missing = [
            check_id
            for check_id in required
            if check_id not in state.validations
        ]
        failed = [
            check_id
            for check_id in required
            if check_id in state.validations
            and state.validations[check_id].status != ValidationStatus.PASS
        ]
        if missing or failed:
            details = []
            if missing:
                details.append(f"missing checks: {', '.join(missing)}")
            if failed:
                details.append(f"failed checks: {', '.join(failed)}")
            raise SetupStateError(
                f"Cannot complete {step_id}; {'; '.join(details)}"
            )

    @staticmethod
    def selected_starters(state: SetupState) -> tuple[str, ...]:
        return tuple(state.selected_products)

    @staticmethod
    def update_product_installation(
        state: SetupState,
        product_id: ProductId,
        status: InstallationStatus,
        *,
        connection_name: str | None = None,
        schema_name: str | None = None,
        requires_connection_attestation: bool | None = None,
        agent_id: str | None = None,
        agent_name: str | None = None,
        connection_settings_url: str | None = None,
        failure_cause: str | None = None,
    ) -> None:
        product_id = ProductId(product_id)
        status = InstallationStatus(status)
        record = state.products[product_id.value]
        if not record.selected:
            raise SetupStateError(
                f"Cannot update unselected product {product_id.value}"
            )
        current = InstallationStatus(record.installation_status)
        allowed = {
            InstallationStatus.PENDING: {
                InstallationStatus.PENDING,
                InstallationStatus.CONNECTION_REQUIRED,
                InstallationStatus.READY,
                InstallationStatus.INSTALLING,
                InstallationStatus.INSTALLED,
                InstallationStatus.FAILED,
            },
            InstallationStatus.CONNECTION_REQUIRED: {
                InstallationStatus.CONNECTION_REQUIRED,
                InstallationStatus.READY,
                InstallationStatus.FAILED,
            },
            InstallationStatus.READY: {
                InstallationStatus.READY,
                InstallationStatus.INSTALLING,
                InstallationStatus.INSTALLED,
                InstallationStatus.FAILED,
            },
            InstallationStatus.INSTALLING: {
                InstallationStatus.INSTALLING,
                InstallationStatus.INSTALLED,
                InstallationStatus.MANUAL_REQUIRED,
                InstallationStatus.FAILED,
            },
            InstallationStatus.MANUAL_REQUIRED: {
                InstallationStatus.MANUAL_REQUIRED,
                InstallationStatus.INSTALLING,
                InstallationStatus.INSTALLED,
                InstallationStatus.FAILED,
            },
            InstallationStatus.INSTALLED: {
                InstallationStatus.INSTALLED,
                InstallationStatus.CONNECTION_ATTESTATION_REQUIRED,
                InstallationStatus.BOUND,
                InstallationStatus.FAILED,
            },
            InstallationStatus.CONNECTION_ATTESTATION_REQUIRED: {
                InstallationStatus.CONNECTION_ATTESTATION_REQUIRED,
                InstallationStatus.BOUND,
                InstallationStatus.FAILED,
            },
            InstallationStatus.FAILED: {
                InstallationStatus.PENDING,
                InstallationStatus.CONNECTION_REQUIRED,
                InstallationStatus.READY,
                InstallationStatus.INSTALLING,
                InstallationStatus.INSTALLED,
                InstallationStatus.FAILED,
            },
            InstallationStatus.BOUND: {InstallationStatus.BOUND},
        }
        if status not in allowed.get(current, set()):
            raise SetupStateError(
                f"Invalid installation transition for {product_id.value}: "
                f"{current} -> {status}"
            )
        if status == InstallationStatus.CONNECTION_ATTESTATION_REQUIRED:
            required_metadata = {
                "connection name": connection_name or record.connection_name,
                "agent id": agent_id or record.agent_id,
                "agent name": agent_name or record.agent_name,
                "connection settings URL": (
                    connection_settings_url or record.connection_settings_url
                ),
            }
            missing = [
                label for label, value in required_metadata.items()
                if not isinstance(value, str) or not value.strip()
            ]
            if missing:
                raise SetupStateError(
                    "Connection attestation requires "
                    f"{', '.join(missing)}"
                )
            if requires_connection_attestation is not True:
                raise SetupStateError(
                    "Connection attestation status requires an invoker connection"
                )
        if (
            status == InstallationStatus.BOUND
            and record.requires_connection_attestation
            and not record.connection_attested_at
        ):
            raise SetupStateError(
                f"Product {product_id.value} requires maker connection "
                "attestation before it can be bound"
            )
        record.installation_status = status
        if connection_name is not None:
            record.connection_name = connection_name
        if schema_name is not None:
            record.schema_name = schema_name
        if requires_connection_attestation is not None:
            record.requires_connection_attestation = (
                requires_connection_attestation
            )
        if agent_id is not None:
            record.agent_id = agent_id
        if agent_name is not None:
            record.agent_name = agent_name
        if connection_settings_url is not None:
            record.connection_settings_url = connection_settings_url
        if status == InstallationStatus.INSTALLING:
            record.requires_connection_attestation = False
            record.agent_id = None
            record.agent_name = None
            record.connection_settings_url = None
            record.connection_attested_at = None
        record.failure_cause = failure_cause
        record.updated_at = utc_now()
        state.updated_at = record.updated_at

    @staticmethod
    def attest_product_connection(
        state: SetupState,
        product_id: ProductId,
    ) -> None:
        """Record the maker's mandatory post-binding invoker attestation."""
        product_id = ProductId(product_id)
        record = state.products[product_id.value]
        if (
            record.installation_status
            != InstallationStatus.CONNECTION_ATTESTATION_REQUIRED
        ):
            raise SetupStateError(
                f"Product {product_id.value} is not awaiting connection "
                "attestation"
            )
        if not record.requires_connection_attestation:
            raise SetupStateError(
                f"Product {product_id.value} does not require connection "
                "attestation"
            )

        attested_at = utc_now()
        record.connection_attested_at = attested_at
        SetupWorkflow.update_product_installation(
            state,
            product_id,
            InstallationStatus.BOUND,
        )
        check_id = (
            "SETUP-INSTALL-CONNECTION-"
            f"{product_id.value.upper().replace('.', '-')}"
        )
        SetupWorkflow.record_validation(
            state,
            check_id,
            ValidationStatus.PASS,
            ValidationMode.MANUAL_ATTESTED,
            {
                "product_id": product_id.value,
                "agent_id": record.agent_id,
                "agent_name": record.agent_name,
                "connection_name": record.connection_name,
                "connection_settings_url": record.connection_settings_url,
                "attested_at": attested_at,
            },
            [],
        )
        attested_products = [
            selected_product_id
            for selected_product_id in state.selected_products
            if (
                state.products[
                    selected_product_id
                ].requires_connection_attestation
                and state.products[selected_product_id].connection_attested_at
            )
        ]
        SetupWorkflow.record_validation(
            state,
            "SETUP-INSTALL-004",
            ValidationStatus.PASS,
            ValidationMode.MANUAL_ATTESTED,
            {"attested_products": attested_products},
            [],
        )

    @staticmethod
    def set_product_readiness(
        state: SetupState,
        product_id: ProductId,
        ready: bool,
    ) -> None:
        product_id = ProductId(product_id)
        record = state.products[product_id.value]
        if not record.selected:
            raise SetupStateError(
                f"Cannot update unselected product {product_id.value}"
            )
        if ready and record.installation_status != InstallationStatus.BOUND:
            raise SetupStateError(
                f"Product {product_id.value} must be installed and bound "
                "before readiness can pass"
            )
        record.ready = ready
        record.updated_at = utc_now()
        state.updated_at = record.updated_at

    @staticmethod
    def set_alm(
        state: SetupState,
        *,
        solution_id: str,
        solution_name: str,
        publisher_prefix: str,
        version: str,
    ) -> None:
        required = {
            "solution_id": solution_id,
            "solution_name": solution_name,
            "publisher_prefix": publisher_prefix,
            "version": version,
        }
        missing = [
            name for name, value in required.items()
            if not isinstance(value, str) or not value.strip()
        ]
        if missing:
            raise SetupStateError(
                f"ALM metadata is missing: {', '.join(missing)}"
            )
        state.alm = {
            **required,
            "preferred": True,
            "updated_at": utc_now(),
        }
        state.updated_at = state.alm["updated_at"]

    @staticmethod
    def add_products(
        state: SetupState,
        product_ids: tuple[ProductId, ...],
    ) -> None:
        """Extend a completed foundation scope and reopen dependent steps."""
        if not state.environment.get("locked"):
            raise SetupStateError(
                "Cannot add products before the environment is locked"
            )
        if not state.connect_ready or not SetupWorkflow.is_complete(state):
            raise SetupStateError(
                "Products can be added only after foundation setup is complete"
            )
        requested = {ProductId(product_id).value for product_id in product_ids}
        additions = requested - set(state.selected_products)
        if not additions:
            return

        state.selected_products = [
            product_id.value
            for product_id in ProductId
            if (
                product_id.value in state.selected_products
                or product_id.value in additions
            )
        ]
        for product_id in additions:
            record = state.products[product_id]
            record.selected = True
            record.installation_status = InstallationStatus.PENDING
            record.connection_name = None
            record.schema_name = None
            record.requires_connection_attestation = False
            record.agent_id = None
            record.agent_name = None
            record.connection_settings_url = None
            record.connection_attested_at = None
            record.ready = False
            record.failure_cause = None
            record.updated_at = utc_now()

        for step_id in ("SETUP-05", "SETUP-06", "SETUP-07"):
            state.steps[step_id] = StepRecord()
        for check_id in tuple(state.validations):
            if check_id.startswith((
                "SETUP-INSTALL-",
                "SETUP-READINESS-",
                "SETUP-HANDOFF-",
                "SETUP-FINAL-",
            )):
                del state.validations[check_id]
        SetupWorkflow.record_validation(
            state,
            "SETUP-INSTALL-SELECTION-001",
            ValidationStatus.PASS,
            ValidationMode.AUTOMATED,
            {
                "selected_products": state.selected_products,
                "scope_extended": True,
            },
            [],
        )
        state.connect_ready = False
        state.completed_at = None
        SetupWorkflow.refresh_active_step(state)

    @staticmethod
    def is_complete(state: SetupState) -> bool:
        return all(
            record.state == StepStatus.DONE
            for record in state.steps.values()
        )

    @staticmethod
    def finalize(state: SetupState) -> None:
        missing = [
            step_id
            for step_id, record in state.steps.items()
            if step_id != "SETUP-07" and record.state != StepStatus.DONE
        ]
        if missing:
            raise SetupStateError(
                f"Cannot finalize setup; incomplete steps: {', '.join(missing)}"
            )
        missing_bundle = [
            check_id
            for check_id in FINAL_BUNDLE_CHECKS
            if check_id not in state.validations
        ]
        failed_bundle = [
            check_id
            for check_id in FINAL_BUNDLE_CHECKS
            if check_id in state.validations
            and state.validations[check_id].status != ValidationStatus.PASS
        ]
        if missing_bundle or failed_bundle:
            details = []
            if missing_bundle:
                details.append(
                    f"missing checks: {', '.join(missing_bundle)}"
                )
            if failed_bundle:
                details.append(
                    f"failed checks: {', '.join(failed_bundle)}"
                )
            raise SetupStateError(
                f"Final setup bundle failed; {'; '.join(details)}"
            )
        SetupWorkflow.record_validation(
            state,
            "SETUP-FINAL-001",
            ValidationStatus.PASS,
            ValidationMode.AUTOMATED,
            {"modules": list(FINAL_BUNDLE_CHECKS)},
            [],
        )
        SetupWorkflow.record_validation(
            state,
            "SETUP-FINAL-002",
            ValidationStatus.PASS,
            ValidationMode.AUTOMATED,
            {"next_action": "/connect"},
            [],
        )
        SetupWorkflow.record_validation(
            state,
            "SETUP-FINAL-003",
            ValidationStatus.PASS,
            ValidationMode.AUTOMATED,
            {"failure_recovery_contract": "blocked with causes and resume step"},
            [],
        )
        SetupWorkflow.update_step(
            state,
            "SETUP-07",
            StepStatus.DONE,
            finalizing=True,
        )
        state.connect_ready = True
        state.completed_at = utc_now()
        state.updated_at = state.completed_at


class SetupStateService:
    """Application service coordinating persistence, migration, and workflow."""

    def __init__(
        self,
        repository: SetupStateRepository,
        migrator: LegacyWorkdayStateMigrator | None = None,
    ) -> None:
        self._repository = repository
        self._migrator = migrator

    def initialize(self) -> SetupState:
        state = self._repository.load() if self._repository.exists() else SetupState()
        if self._migrator is not None:
            state = self._migrator.migrate(state)
        SetupWorkflow.refresh_active_step(state)
        self._repository.save(state)
        return state

    def load(self) -> SetupState:
        return self._repository.load()

    def save(self, state: SetupState) -> None:
        self._repository.save(state)


def persist_product_installation_status(
    product_id: str,
    status: str,
    *,
    connection_name: str | None = None,
    schema_name: str | None = None,
    requires_connection_attestation: bool | None = None,
    agent_id: str | None = None,
    agent_name: str | None = None,
    connection_settings_url: str | None = None,
    failure_cause: str | None = None,
    state_path: Path = DEFAULT_STATE_PATH,
) -> None:
    """Persist one product lifecycle transition through the domain service."""
    service = SetupStateService(JsonSetupStateRepository(state_path))
    state = service.load()
    SetupWorkflow.update_product_installation(
        state,
        ProductId(product_id),
        InstallationStatus(status),
        connection_name=connection_name,
        schema_name=schema_name,
        requires_connection_attestation=requires_connection_attestation,
        agent_id=agent_id,
        agent_name=agent_name,
        connection_settings_url=connection_settings_url,
        failure_cause=failure_cause,
    )
    service.save(state)


def persist_alm_solution(
    *,
    solution_id: str,
    solution_name: str,
    publisher_prefix: str,
    version: str,
    state_path: Path = DEFAULT_STATE_PATH,
) -> None:
    """Persist verified preferred-solution metadata."""
    service = SetupStateService(JsonSetupStateRepository(state_path))
    state = service.load()
    SetupWorkflow.set_alm(
        state,
        solution_id=solution_id,
        solution_name=solution_name,
        publisher_prefix=publisher_prefix,
        version=version,
    )
    service.save(state)


def _parse_json_object(value: str) -> dict[str, Any]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("value must be a JSON object")
    return parsed


def _service(args: argparse.Namespace) -> SetupStateService:
    repository = JsonSetupStateRepository(Path(args.state))
    migrator = LegacyWorkdayStateMigrator(Path(args.legacy_workday))
    return SetupStateService(repository, migrator)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", default=str(DEFAULT_STATE_PATH))
    parser.add_argument(
        "--legacy-workday",
        default=str(DEFAULT_LEGACY_WORKDAY_PATH),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("init")
    commands.add_parser("show")

    scope = commands.add_parser("set-scope")
    scope.add_argument("--environment-id", required=True)
    scope.add_argument("--environment-name", required=True)
    scope.add_argument(
        "--environment-type",
        required=True,
    )
    scope.add_argument("--tenant-endpoint", required=True)

    select_product = commands.add_parser("select-product")
    select_product.add_argument(
        "--product",
        required=True,
        choices=[item.value for item in ProductId],
    )

    step = commands.add_parser("update-step")
    step.add_argument("--step", required=True, choices=STEP_ORDER)
    step.add_argument(
        "--status",
        required=True,
        choices=[item.value for item in StepStatus],
    )
    step.add_argument("--cause", action="append", default=[])

    validation = commands.add_parser("record-check")
    validation.add_argument("--check-id", required=True)
    validation.add_argument(
        "--status",
        required=True,
        choices=[item.value for item in ValidationStatus],
    )
    validation.add_argument(
        "--mode",
        required=True,
        choices=[item.value for item in ValidationMode],
    )
    validation.add_argument(
        "--evidence-json",
        default="{}",
        type=_parse_json_object,
    )
    validation.add_argument("--cause-code", action="append", default=[])

    prerequisite = commands.add_parser("set-prerequisite")
    prerequisite.add_argument("--name", required=True)
    prerequisite.add_argument(
        "--status",
        required=True,
        choices=("complete", "pending"),
    )
    prerequisite.add_argument("--value")

    alm = commands.add_parser("set-alm")
    alm.add_argument("--solution-id", required=True)
    alm.add_argument("--solution-name", required=True)
    alm.add_argument("--publisher-prefix", required=True)
    alm.add_argument("--version", required=True)

    product = commands.add_parser("set-product-status")
    product.add_argument(
        "--product",
        required=True,
        choices=[item.value for item in ProductId],
    )
    product.add_argument(
        "--status",
        required=True,
        choices=[
            item.value
            for item in InstallationStatus
            if item != InstallationStatus.NOT_SELECTED
        ],
    )
    product.add_argument("--connection-name")
    product.add_argument("--schema-name")
    product.add_argument("--failure-cause")

    readiness = commands.add_parser("set-product-readiness")
    readiness.add_argument(
        "--product",
        required=True,
        choices=[item.value for item in ProductId],
    )
    readiness.add_argument(
        "--ready",
        required=True,
        action=argparse.BooleanOptionalAction,
    )

    attestation = commands.add_parser("attest-product-connection")
    attestation.add_argument(
        "--product",
        required=True,
        choices=[item.value for item in ProductId],
    )

    add_product = commands.add_parser("add-product")
    add_product.add_argument(
        "--product",
        required=True,
        action="append",
        choices=[item.value for item in ProductId],
        dest="products",
    )

    commands.add_parser("finalize")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    service = _service(args)

    try:
        if args.command == "init":
            state = service.initialize()
        else:
            state = service.load()

        if args.command == "set-scope":
            SetupWorkflow.set_scope(
                state,
                environment_id=args.environment_id,
                environment_name=args.environment_name,
                environment_type=args.environment_type,
                tenant_endpoint=args.tenant_endpoint,
            )
            service.save(state)
        elif args.command == "select-product":
            SetupWorkflow.select_initial_product(
                state,
                ProductId(args.product),
            )
            service.save(state)
        elif args.command == "update-step":
            SetupWorkflow.update_step(
                state,
                args.step,
                StepStatus(args.status),
                args.cause,
            )
            service.save(state)
        elif args.command == "record-check":
            SetupWorkflow.record_validation(
                state,
                args.check_id,
                ValidationStatus(args.status),
                ValidationMode(args.mode),
                args.evidence_json,
                args.cause_code,
            )
            service.save(state)
        elif args.command == "set-prerequisite":
            state.prerequisites[args.name] = {
                "status": args.status,
                "value": args.value,
                "updated_at": utc_now(),
            }
            state.updated_at = utc_now()
            service.save(state)
        elif args.command == "set-alm":
            SetupWorkflow.set_alm(
                state,
                solution_id=args.solution_id,
                solution_name=args.solution_name,
                publisher_prefix=args.publisher_prefix,
                version=args.version,
            )
            service.save(state)
        elif args.command == "set-product-status":
            SetupWorkflow.update_product_installation(
                state,
                ProductId(args.product),
                InstallationStatus(args.status),
                connection_name=args.connection_name,
                schema_name=args.schema_name,
                failure_cause=args.failure_cause,
            )
            service.save(state)
        elif args.command == "set-product-readiness":
            SetupWorkflow.set_product_readiness(
                state,
                ProductId(args.product),
                args.ready,
            )
            service.save(state)
        elif args.command == "attest-product-connection":
            SetupWorkflow.attest_product_connection(
                state,
                ProductId(args.product),
            )
            service.save(state)
        elif args.command == "add-product":
            SetupWorkflow.add_products(
                state,
                tuple(ProductId(product_id) for product_id in args.products),
            )
            service.save(state)
        elif args.command == "finalize":
            try:
                SetupWorkflow.finalize(state)
            except SetupStateError as exc:
                SetupWorkflow.record_validation(
                    state,
                    "SETUP-FINAL-001",
                    ValidationStatus.FAIL,
                    ValidationMode.AUTOMATED,
                    {"error": str(exc)},
                    ["FINAL_BUNDLE_FAILED"],
                )
                SetupWorkflow.record_validation(
                    state,
                    "SETUP-FINAL-003",
                    ValidationStatus.PASS,
                    ValidationMode.AUTOMATED,
                    {
                        "state": "blocked",
                        "resume_step": "SETUP-07",
                        "cause": str(exc),
                    },
                    [],
                )
                recovery_step = SetupWorkflow.next_step(state)
                if state.steps[recovery_step].state == StepStatus.DONE:
                    state.steps[recovery_step] = StepRecord(
                        state=StepStatus.BLOCKED,
                        updated_at=utc_now(),
                        failure_causes=[str(exc)],
                    )
                    SetupWorkflow.refresh_active_step(state)
                else:
                    SetupWorkflow.update_step(
                        state,
                        recovery_step,
                        StepStatus.BLOCKED,
                        [str(exc)],
                    )
                service.save(state)
                raise
            service.save(state)

        print(json.dumps(state.to_dict(), indent=2))
        return 0
    except SetupStateError as exc:
        print(f"ERROR: {exc}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
