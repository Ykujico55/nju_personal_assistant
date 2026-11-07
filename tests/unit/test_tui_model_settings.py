"""Local TUI configuration writes only public model options with version checks."""

import json
from pathlib import Path

import pytest

from personal_assistant.cli.tui.model_settings import ModelSettings, ModelSettingsError

CONFIG = {
    "PA_MODEL_REMOTE_PROVIDER_ID": "deepseek",
    "PA_MODEL_REMOTE_BASE_URL": "https://api.example.invalid",
    "PA_MODEL_REMOTE_MODEL": "fixture-model",
    "PA_MODEL_REMOTE_SECRET_HANDLE": "wcm1_fixture_not_a_secret",
    "PA_MODEL_REMOTE_JSON_MODE": "true",
    "PA_MODEL_REMOTE_THINKING": "disabled",
}


def config(tmp_path: Path) -> Path:
    path = tmp_path / "model.json"
    path.write_text(json.dumps(CONFIG), encoding="utf-8")
    return path


def test_public_view_hides_reference_and_saves_only_options(tmp_path):
    path = config(tmp_path)
    store = ModelSettings(path)
    old = store.load()
    assert old["credential_configured"] is True
    assert CONFIG["PA_MODEL_REMOTE_SECRET_HANDLE"] not in json.dumps(old)
    value = store.save(
        {
            "model": "new-model",
            "json_mode": False,
            "thinking": "enabled",
            "revision": old["revision"],
        },
        "save-one",
    )
    actual = json.loads(path.read_text())
    assert actual["PA_MODEL_REMOTE_SECRET_HANDLE"] == CONFIG["PA_MODEL_REMOTE_SECRET_HANDLE"]
    assert actual["PA_MODEL_REMOTE_BASE_URL"] == CONFIG["PA_MODEL_REMOTE_BASE_URL"]
    assert actual["PA_MODEL_REMOTE_MODEL"] == "new-model"
    assert value["restart_required"] is True
    assert (
        store.save(
            {
                "model": "new-model",
                "json_mode": False,
                "thinking": "enabled",
                "revision": old["revision"],
            },
            "save-one",
        )
        == value
    )


def test_stale_revision_and_foreign_fields_are_rejected(tmp_path):
    path = config(tmp_path)
    store = ModelSettings(path)
    old = store.load()
    changed = {**CONFIG, "PA_MODEL_REMOTE_MODEL": "changed-elsewhere"}
    path.write_text(json.dumps(changed), encoding="utf-8")
    for fields in (
        {"model": "lost", "json_mode": True, "thinking": "disabled", "revision": old["revision"]},
        {"base_url": "https://unrelated.invalid", "revision": store.load()["revision"]},
    ):
        with pytest.raises(ModelSettingsError):
            store.save(fields, "same-key")
    assert json.loads(path.read_text()) == changed


def test_explicit_profile_preserves_other_host_configuration(tmp_path):
    shared = config(tmp_path)
    profile = tmp_path / "host.json"
    original = {
        "PA_STORAGE_BACKEND": "postgres",
        "PA_DATABASE_SECRET_HANDLE": "wcm1_database",
        "PA_EXTENSION_ROOT": "var/extensions",
        "PA_MAIL_SEND_ENABLED": "false",
    }
    profile.write_text(json.dumps(original), encoding="utf-8")
    store = ModelSettings(profile, profile=True, defaults_path=shared)
    old = store.load()
    store.save(
        {
            "model": "another-model",
            "json_mode": True,
            "thinking": "disabled",
            "revision": old["revision"],
        },
        "profile-write",
    )
    actual = json.loads(profile.read_text())
    assert all(actual[k] == v for k, v in original.items())
    assert actual["PA_MODEL_REMOTE_MODEL"] == "another-model"
    assert json.loads(shared.read_text()) == CONFIG


def test_concurrent_default_change_cannot_be_silently_pinned(tmp_path):
    shared = config(tmp_path)
    profile = tmp_path / "host.json"
    profile.write_text("{}", encoding="utf-8")
    store = ModelSettings(profile, profile=True, defaults_path=shared)
    old = store.load()
    shared.write_text(json.dumps({**CONFIG, "PA_MODEL_REMOTE_MODEL": "changed"}))
    with pytest.raises(ModelSettingsError):
        store.save(
            {
                "model": "new",
                "json_mode": True,
                "thinking": "disabled",
                "revision": old["revision"],
            },
            "write",
        )
    assert profile.read_text() == "{}"


def test_missing_configuration_never_creates_credential_or_file(tmp_path):
    path = tmp_path / "missing.json"
    with pytest.raises(ModelSettingsError):
        ModelSettings(path).load()
    assert not path.exists()


def test_bool_validation_and_same_key_different_payload(tmp_path):
    store = ModelSettings(config(tmp_path))
    old = store.load()
    fields = {
        "model": "new",
        "json_mode": "false",
        "thinking": "disabled",
        "revision": old["revision"],
    }
    with pytest.raises(ModelSettingsError):
        store.save(fields, "request")
    fields["json_mode"] = False
    store.save(fields, "request")
    with pytest.raises(ModelSettingsError):
        store.save({**fields, "model": "different"}, "request")
