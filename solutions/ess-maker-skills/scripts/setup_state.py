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


SCHEMA_VERSION = 1
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
        "SETUP-INSTALL-001",
        "SETUP-INSTALL-002",
        "SETUP-INSTALL-003",
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
    "SETUP-ENV-001",
    "SETUP-ALM-001",
    "SETUP-ALM-002",
    "SETUP-INSTALL-001",
    "SETUP-INSTALL-002",
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


class StarterScope(StrEnum):
    HR = "HR"
    IT = "IT"
    BOTH = "both"


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
class SetupState:
    schema_version: int = SCHEMA_VERSION
    intent: str = SETUP_INTENT
    environment: dict[str, Any] = field(default_factory=dict)
    starter_scope: str | None = None
    prerequisites: dict[str, Any] = field(default_factory=dict)
    alm: dict[str, Any] = field(default_factory=dict)
    starters: dict[str, Any] = field(default_factory=lambda: {
        "HR": {"installed": False, "ready": False},
        "IT": {"installed": False, "ready": False},
    })
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

        steps = {
            step_id: StepRecord(**record)
            for step_id, record in steps_raw.items()
        }
        validations = {
            check_id: ValidationRecord(**record)
            for check_id, record in raw.get("validations", {}).items()
        }

        state = cls(
            schema_version=raw["schema_version"],
            intent=raw.get("intent", SETUP_INTENT),
            environment=raw.get("environment", {}),
            starter_scope=raw.get("starter_scope"),
            prerequisites=raw.get("prerequisites", {}),
            alm=raw.get("alm", {}),
            starters=raw.get("starters", {
                "HR": {"installed": False, "ready": False},
                "IT": {"installed": False, "ready": False},
            }),
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
                "HR/IT starter scope are not proven."
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
        if set(state.starters) != {"HR", "IT"}:
            raise SetupStateError("Setup state must contain HR and IT starter records")

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

        if state.starter_scope is not None:
            try:
                StarterScope(state.starter_scope)
            except ValueError as exc:
                raise SetupStateError(
                    f"Invalid starter scope: {state.starter_scope!r}"
                ) from exc

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
        environment_type: EnvironmentType,
        tenant_endpoint: str,
        starter_scope: StarterScope,
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
            if state.starter_scope != starter_scope:
                raise SetupStateError(
                    "Setup scope is locked; start a new setup run to change "
                    "the starter scope"
                )

        state.environment = proposed_environment
        state.starter_scope = starter_scope
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
                {"starter_scope": starter_scope},
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
        scope = StarterScope(state.starter_scope)
        if scope == StarterScope.BOTH:
            return (StarterScope.HR, StarterScope.IT)
        return (scope,)

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
        choices=[item.value for item in EnvironmentType],
    )
    scope.add_argument("--tenant-endpoint", required=True)
    scope.add_argument(
        "--starter-scope",
        required=True,
        choices=[item.value for item in StarterScope],
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

    starter = commands.add_parser("set-starter")
    starter.add_argument("--starter", required=True, choices=("HR", "IT"))
    starter.add_argument("--installed", action=argparse.BooleanOptionalAction)
    starter.add_argument("--ready", action=argparse.BooleanOptionalAction)

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
                environment_type=EnvironmentType(args.environment_type),
                tenant_endpoint=args.tenant_endpoint,
                starter_scope=StarterScope(args.starter_scope),
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
            state.alm = {
                "solution_id": args.solution_id,
                "solution_name": args.solution_name,
                "publisher_prefix": args.publisher_prefix,
                "version": args.version,
                "preferred": True,
                "updated_at": utc_now(),
            }
            service.save(state)
        elif args.command == "set-starter":
            record = state.starters[args.starter]
            if args.installed is not None:
                record["installed"] = args.installed
            if args.ready is not None:
                record["ready"] = args.ready
            record["updated_at"] = utc_now()
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
