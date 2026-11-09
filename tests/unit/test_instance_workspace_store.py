"""Instance authorization snapshots, never plugin-owned copies of roots."""
import json

import pytest

from personal_assistant.core.extensions.config import ExtensionConfigError, config_digest
from personal_assistant.infrastructure.extensions.config_store import FileExtensionConfigStore
from personal_assistant.infrastructure.extensions.workspace import WorkspaceExtensionConfigStore


def stores(tmp_path):
    base = FileExtensionConfigStore(tmp_path / "extensions" / "config")
    return base, WorkspaceExtensionConfigStore(base, tmp_path / "workspace")


def config(path, mode="read_write"):
    path.mkdir(exist_ok=True)
    return {"workspace_id": "instance", "roots": [
        {"key": "personal", "label": "资料", "path": str(path), "mode": mode}
    ]}


async def activate(store, document):
    saved = await store.save_workspace(document)
    return await store.apply_workspace(saved["application"]["saved_digest"])


async def bind(store, extension_id):
    await store.save(extension_id, {"workspace_binding": "instance"})
    await store.apply(extension_id, (await store.status(extension_id))["saved_digest"])


async def test_workspace_is_single_source_saved_apply_and_return_to_old_root_new_revision(tmp_path):
    base, store = stores(tmp_path)
    await bind(store, "example.files")
    await bind(store, "example.index")
    a, b = config(tmp_path / "a"), config(tmp_path / "b")
    await activate(store, a)
    old = await store.get_applied("example.files")
    other = await store.get_applied("example.index")
    assert old["workspace"] == other["workspace"]
    assert old["config"] == {"workspace_binding": "instance"}
    saved = await store.save_workspace(b)
    assert (await store.get_applied("example.files")) == old
    assert saved["application"]["state"] == "PENDING"
    await store.apply_workspace(saved["application"]["saved_digest"])
    assert (await store.get_applied("example.files"))["workspace"]["roots"] == b["roots"]
    await activate(store, a)
    renewed = await store.get_applied("example.files")
    assert renewed["workspace"]["roots"] == old["workspace"]["roots"]
    assert renewed["generation"] != old["generation"]
    assert renewed["workspace"]["revision"] != old["workspace"]["revision"]
    assert (await base.get_applied("example.files"))["config"] == old["config"]
    _, restarted = stores(tmp_path)
    assert await restarted.get_applied("example.files") == renewed


async def test_stale_apply_and_empty_workspace_never_reuse_old_roots(tmp_path):
    _, store = stores(tmp_path)
    await bind(store, "example.files")
    saved = await store.save_workspace(config(tmp_path / "a"))
    await store.save_workspace(config(tmp_path / "b"))
    with pytest.raises(ExtensionConfigError, match="CONFIG_REVISION_CONFLICT"):
        await store.apply_workspace(saved["application"]["saved_digest"])
    await activate(store, {"workspace_id": "instance", "roots": []})
    assert (await store.get_applied("example.files"))["workspace"]["roots"] == []
    with pytest.raises(ExtensionConfigError):
        await store.save("example.files", {"workspace_binding": "instance", "roots": []})


async def test_legacy_is_not_implicitly_migrated_or_granted_workspace(tmp_path):
    base, store = stores(tmp_path)
    (tmp_path / "extensions" / "config").mkdir(parents=True)
    (tmp_path / "extensions" / "config" / "example.old.json").write_text(
        json.dumps({"roots": [{"path": "legacy"}]}), encoding="utf-8")
    await activate(store, config(tmp_path / "a"))
    assert await store.get_applied("example.old") == await base.get_applied("example.old")
    assert "workspace" not in await store.get_applied("example.old")


async def test_status_requires_every_live_consumer_and_does_not_leak_root_paths(tmp_path):
    _, store = stores(tmp_path)
    for name in ("example.files", "example.index"):
        await bind(store, name)
    await activate(store, config(tmp_path / "a"))
    try:
        for name in ("example.files", "example.index"):
            target = await store.get_applied(name)
            await store.report_runtime(name, "a" * 32, loaded_digest=target["digest"],
                generation=target["generation"], state="RUNNING")
        status = (await store.get_workspace())["application"]
        assert status["state"] == "APPLIED"
        assert str(tmp_path) not in json.dumps(status)
        await activate(store, config(tmp_path / "b"))
        assert (await store.get_workspace())["application"]["state"] == "APPLYING"
        target = await store.get_applied("example.files")
        await store.report_runtime("example.files", "a" * 32, loaded_digest=target["digest"],
            generation=target["generation"], state="RUNNING")
        assert (await store.get_workspace())["application"]["state"] != "APPLIED"
    finally:
        await store.release_runtime("a" * 32)


@pytest.mark.parametrize("bad", ["relative", "nested", "protected", "duplicate", "private"])
async def test_invalid_authorization_never_changes_active_snapshot(tmp_path, bad):
    _, store = stores(tmp_path)
    await activate(store, config(tmp_path / "good"))
    before = await store.get_workspace()
    document = config(tmp_path / "a")
    if bad == "relative":
        document["roots"][0]["path"] = "relative"
    elif bad == "nested":
        (tmp_path / "a" / "nested").mkdir()
        document["roots"].append({**document["roots"][0], "key": "other",
                                  "path": str(tmp_path / "a" / "nested")})
    elif bad == "protected":
        document["roots"][0]["path"] = str(tmp_path)
    elif bad == "duplicate":
        document["roots"].append(dict(document["roots"][0]))
    else:
        (tmp_path / ".ssh").mkdir()
        document["roots"][0]["path"] = str(tmp_path / ".ssh")
    with pytest.raises(ExtensionConfigError):
        await store.save_workspace(document)
    assert await store.get_workspace() == before


async def test_saved_path_removed_before_apply_is_rejected(tmp_path):
    _, store = stores(tmp_path)
    document = config(tmp_path / "a")
    saved = await store.save_workspace(document)
    (tmp_path / "a").rmdir()
    with pytest.raises(ExtensionConfigError):
        await store.apply_workspace(saved["application"]["saved_digest"])


async def test_consumer_ack_from_new_revision_cannot_certify_older_workspace(tmp_path, monkeypatch):
    _, store = stores(tmp_path)
    await bind(store, "example.files")
    await activate(store, config(tmp_path / "a"))
    original = store.status
    switched = False

    async def switch_on_status(extension_id):
        nonlocal switched
        if not switched:
            switched = True
            await store._workspace.save("instance.workspace", config(tmp_path / "b"))
            saved = await store._workspace.status("instance.workspace")
            await store._workspace.apply("instance.workspace", saved["saved_digest"])
            target = await store.get_applied(extension_id)
            await store.report_runtime(extension_id, "a" * 32,
                loaded_digest=target["digest"], generation=target["generation"], state="RUNNING")
        return await original(extension_id)

    monkeypatch.setattr(store, "status", switch_on_status)
    try:
        observed = await store.get_workspace()
        assert observed["application"]["state"] != "APPLIED"
        assert observed["application"]["refresh_required"] is True
    finally:
        await store.release_runtime("a" * 32)


@pytest.mark.parametrize("other_applies", [False, True])
async def test_confirmation_content_matches_digest_after_concurrent_save(
    tmp_path, monkeypatch, other_applies,
):
    _, left = stores(tmp_path)
    _, right = stores(tmp_path)
    a, b = config(tmp_path / "a"), config(tmp_path / "b")
    await activate(left, a)
    real_get = left._workspace.get
    switched = False

    async def change_after_read(extension_id):
        nonlocal switched
        document = await real_get(extension_id)
        if not switched:
            switched = True
            saved = await right.save_workspace(b)
            if other_applies:
                await right.apply_workspace(saved["application"]["saved_digest"])
        return document

    monkeypatch.setattr(left._workspace, "get", change_after_read)
    shown = await left.get_workspace()
    assert config_digest(shown["config"]) == shown["application"]["saved_digest"]
    accepted = await left.apply_workspace(shown["application"]["saved_digest"])
    assert accepted["config"] == shown["config"] == b
    assert (await right._workspace.get_applied("instance.workspace"))["config"] == b


async def test_unstable_confirmation_is_rejected_without_applying(tmp_path, monkeypatch):
    _, left = stores(tmp_path)
    _, right = stores(tmp_path)
    a, b = config(tmp_path / "a"), config(tmp_path / "b")
    await activate(left, a)
    real_get = left._workspace.get
    reads = 0

    async def change_after_every_read(extension_id):
        nonlocal reads
        document = await real_get(extension_id)
        reads += 1
        await right.save_workspace(b if document == a else a)
        return document

    monkeypatch.setattr(left._workspace, "get", change_after_every_read)
    with pytest.raises(ExtensionConfigError, match="CONFIG_REVISION_CONFLICT"):
        await left.get_workspace()
    assert reads <= 3
    assert (await right._workspace.get_applied("instance.workspace"))["config"] == a


async def test_unsaved_workspace_digest_describes_returned_content(tmp_path):
    _, store = stores(tmp_path)
    shown = await store.get_workspace()
    assert shown["config"] == {}
    assert config_digest(shown["config"]) == shown["application"]["saved_digest"]
