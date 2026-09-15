"""Framework-neutral domain exceptions."""

from __future__ import annotations


class DomainError(Exception):
    """Base class for expected, user-presentable domain failures."""

    code = "domain_error"


class ValidationError(DomainError):
    code = "validation_error"


class NotFoundError(DomainError):
    code = "not_found"


class AlreadyExistsError(DomainError):
    code = "already_exists"


class ConcurrentModificationError(DomainError):
    code = "concurrent_modification"


class InvalidStateTransitionError(DomainError):
    code = "invalid_state_transition"

    def __init__(self, current: object, target: object) -> None:
        super().__init__(f"state transition is not allowed: {current!s} -> {target!s}")
        self.current = current
        self.target = target
