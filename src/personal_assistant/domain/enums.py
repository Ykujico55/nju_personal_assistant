"""Stable domain enums.

Persist enum *values*, not member names.  Values are part of the storage/API
contract and therefore must only change through an explicit migration.
"""

from __future__ import annotations

from enum import StrEnum


class RiskLevel(StrEnum):
    """Risk is assigned by deterministic policy, never by the model."""

    READ = "READ"  # R0
    INTERNAL_WRITE = "INTERNAL_WRITE"  # R1
    EXTERNAL_WRITE = "EXTERNAL_WRITE"  # R2
    PROHIBITED = "PROHIBITED"  # R3

    @property
    def code(self) -> str:
        return {
            RiskLevel.READ: "R0",
            RiskLevel.INTERNAL_WRITE: "R1",
            RiskLevel.EXTERNAL_WRITE: "R2",
            RiskLevel.PROHIBITED: "R3",
        }[self]


class RetryPolicy(StrEnum):
    NONE = "NONE"
    IDEMPOTENT_ONLY = "IDEMPOTENT_ONLY"


class TaskState(StrEnum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_USER = "WAITING_USER"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    WAITING_RECONCILIATION = "WAITING_RECONCILIATION"
    PAUSED_SAFETY = "PAUSED_SAFETY"
    PAUSED_EXTENSION = "PAUSED_EXTENSION"
    # Friendly vocabulary alias used by some adapters.  The persisted value stays
    # aligned with the design document's PAUSED_EXTENSION state.
    WAITING_EXTENSION = "PAUSED_EXTENSION"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def terminal(self) -> bool:
        return self in {
            TaskState.SUCCEEDED,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }


class ToolOutcomeKind(StrEnum):
    SUCCESS = "SUCCESS"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    USER_ACTION_REQUIRED = "USER_ACTION_REQUIRED"
    EXTENSION_UNAVAILABLE = "EXTENSION_UNAVAILABLE"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    SAFETY_PAUSE = "SAFETY_PAUSE"
    FAILED = "FAILED"


class ApprovalState(StrEnum):
    DRAFT = "DRAFT"
    PREPARED = "PREPARED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"

    @property
    def terminal(self) -> bool:
        return self in {
            ApprovalState.SUCCEEDED,
            ApprovalState.FAILED,
            ApprovalState.UNKNOWN,
            ApprovalState.EXPIRED,
            ApprovalState.CANCELLED,
        }


class ContextLayer(StrEnum):
    POLICY = "POLICY"
    WORKFLOW = "WORKFLOW"
    APPROVED_RULE = "APPROVED_RULE"
    TASK_WORKING_SET = "TASK_WORKING_SET"
    RETRIEVED_EVIDENCE = "RETRIEVED_EVIDENCE"
    EPISODIC_SUMMARY = "EPISODIC_SUMMARY"
    TOOL_OBSERVATION = "TOOL_OBSERVATION"


class Sensitivity(StrEnum):
    PUBLIC = "PUBLIC"
    PERSONAL = "PERSONAL"
    SENSITIVE = "SENSITIVE"
    SECRET = "SECRET"


class TrustLevel(StrEnum):
    USER_SOURCE = "USER_SOURCE"
    OFFICIAL_SOURCE = "OFFICIAL_SOURCE"
    EXTERNAL_MESSAGE = "EXTERNAL_MESSAGE"
    MODEL_GENERATED = "MODEL_GENERATED"
    INFERRED = "INFERRED"
    TOOL_OUTPUT = "TOOL_OUTPUT"
