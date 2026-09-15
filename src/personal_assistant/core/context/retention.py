from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    transient_debug: timedelta = timedelta(days=7)
    model_and_tool_process: timedelta = timedelta(days=30)
    structured_audit: timedelta = timedelta(days=365)

