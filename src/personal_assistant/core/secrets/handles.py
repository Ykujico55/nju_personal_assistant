from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SecretHandle:
    id: str
    kind: str

    def __str__(self) -> str:
        return f"secret://{self.kind}/{self.id}"

