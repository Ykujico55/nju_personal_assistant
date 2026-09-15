from __future__ import annotations

from fastapi import APIRouter, Depends

from personal_assistant.api.dependencies import get_container
from personal_assistant.bootstrap import Container

router = APIRouter(prefix="/extensions", tags=["extensions"])


@router.get("")
async def list_extensions(container: Container = Depends(get_container)) -> dict[str, object]:
    manifests = container.extension_registry.discover(str(container.bundled_extensions_root))
    enabled = container.extension_registry.snapshot.extensions
    return {
        "items": [
            {
                "id": manifest.id,
                "name": manifest.name,
                "version": manifest.version,
                "state": enabled[manifest.id].state.value
                if manifest.id in enabled
                else "DISCOVERED",
                "slots": {key: list(value) for key, value in manifest.slots.items()},
                "capabilities": {
                    "required": list(manifest.capabilities.required),
                    "optional": list(manifest.capabilities.optional),
                },
            }
            for manifest in manifests
        ]
    }

