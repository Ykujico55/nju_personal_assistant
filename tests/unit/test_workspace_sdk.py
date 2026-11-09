from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from personal_assistant_sdk import RuntimeContext, workspace_config

from personal_assistant.core.extensions.manifest import ManifestParser
from personal_assistant.core.extensions.rpc import ExtensionWorker


def runtime(config=None, workspace=None):
    return RuntimeContext(
        "1",
        "example.test",
        "1.0.0",
        "ext_test",
        non_secret_config=config or {},
        workspace=workspace,
    )


def envelope():
    return {
        "workspace_id": "instance",
        "revision": "revision-1",
        "roots": [{"key": "notes", "label": "Notes", "path": "/synthetic", "mode": "read_only"}],
    }


def test_workspace_requires_opt_in_and_never_merges_legacy_roots():
    configured = {"roots": [{"path": "/legacy"}]}
    assert workspace_config(runtime(configured, envelope())) == configured
    with pytest.raises(ValueError, match="WORKSPACE_BINDING_INVALID"):
        workspace_config(runtime({**configured, "workspace_binding": "instance"}, envelope()))
    bound = runtime({"workspace_binding": "instance", "option": True}, envelope())
    result = workspace_config(bound)
    assert result["roots"] == envelope()["roots"]
    assert result["_workspace_revision"] == "revision-1"
    assert result["option"] is True
    assert "workspace_binding" not in result
    result["roots"][0]["mode"] = "read_write"
    assert bound.workspace["roots"][0]["mode"] == "read_only"


@pytest.mark.parametrize(
    "workspace",
    [
        None,
        {},
        {**envelope(), "roots": []},
        {**envelope(), "revision": ""},
        {**envelope(), "roots": [*envelope()["roots"], *envelope()["roots"]]},
    ],
)
def test_bound_workspace_invalid_or_unavailable_is_rejected(workspace):
    with pytest.raises(ValueError, match="WORKSPACE_"):
        workspace_config(runtime({"workspace_binding": "instance"}, workspace))


async def test_worker_handshake_omits_workspace_for_legacy_and_forwards_bound_snapshot():
    manifest = ManifestParser().parse(Path(__file__).parents[2] / "extensions" / "example_echo")
    from personal_assistant.core.extensions.manifest import compute_schema_hash

    client = AsyncMock()
    client.call.return_value = {
        "id": manifest.id,
        "version": manifest.version,
        "protocol_version": "1",
        "schema_hash": compute_schema_hash(manifest),
        "slots": list(manifest.slots),
    }
    worker = ExtensionWorker(manifest, client)
    await worker.start()
    assert "workspace" not in client.call.call_args.args[1]["runtime"]
    await worker.start(workspace=envelope())
    assert client.call.call_args.args[1]["runtime"]["workspace"] == envelope()
