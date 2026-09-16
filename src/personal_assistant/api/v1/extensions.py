from __future__ import annotations

from fastapi import APIRouter, Depends

from personal_assistant.api.dependencies import get_container
from personal_assistant.bootstrap import Container
from personal_assistant.core.extensions import ExtensionManifest, ExtensionRecord

router = APIRouter(prefix="/extensions", tags=["extensions"])


def _manifest_item(
    manifest: ExtensionManifest, record: ExtensionRecord | None
) -> dict[str, object]:
    return {
        "id": manifest.id,
        "name": manifest.name,
        "version": manifest.version,
        "state": record.state.value if record is not None else "DISCOVERED",
        "slots": {key: list(value) for key, value in manifest.slots.items()},
        "capabilities": {
            "required": list(manifest.capabilities.required),
            "optional": list(manifest.capabilities.optional),
        },
    }


@router.get("")
async def list_extensions(container: Container = Depends(get_container)) -> dict[str, object]:
    """Read-only extension status.

    Lifecycle state comes from the durable store rather than the process-local
    registry snapshot, so the loopback Admin process and the public process show
    the same installed/enabled state in production.
    """

    manifests = container.extension_registry.discover(str(container.bundled_extensions_root))
    records = {record.manifest.id: record for record in await container.lifecycle_store.all()}
    items: list[dict[str, object]] = []
    for manifest in manifests:
        record = records.pop(manifest.id, None)
        items.append(_manifest_item(manifest, record))
    for record in sorted(records.values(), key=lambda item: item.manifest.id):
        items.append(_manifest_item(record.manifest, record))
    return {"items": items}
