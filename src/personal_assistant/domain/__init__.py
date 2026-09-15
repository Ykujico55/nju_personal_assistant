"""Pure domain types shared by the assistant core.

This package deliberately has no framework or infrastructure dependencies.  API,
database and extension adapters may import it; the domain must never import them.
"""

from .enums import (
    ApprovalState,
    ContextLayer,
    RetryPolicy,
    RiskLevel,
    Sensitivity,
    TaskState,
    ToolOutcomeKind,
    TrustLevel,
)
from .errors import (
    AlreadyExistsError,
    ConcurrentModificationError,
    DomainError,
    InvalidStateTransitionError,
    NotFoundError,
    ValidationError,
)
from .models import (
    AttachmentDigest,
    Task,
    TaskRun,
    ToolCall,
    ToolDescriptor,
    ToolOutcome,
    utc_now,
)

__all__ = [
    "AlreadyExistsError",
    "ApprovalState",
    "AttachmentDigest",
    "ConcurrentModificationError",
    "ContextLayer",
    "DomainError",
    "InvalidStateTransitionError",
    "NotFoundError",
    "RetryPolicy",
    "RiskLevel",
    "Sensitivity",
    "Task",
    "TaskRun",
    "TaskState",
    "ToolCall",
    "ToolDescriptor",
    "ToolOutcome",
    "ToolOutcomeKind",
    "TrustLevel",
    "ValidationError",
    "utc_now",
]
