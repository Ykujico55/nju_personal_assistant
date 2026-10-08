"""Secrets enter through a hidden terminal prompt, never argv or output."""
from __future__ import annotations

import importlib
import json
from unittest.mock import AsyncMock, Mock

import pytest

from personal_assistant.core.secrets import SecretHandle

HANDLE = SecretHandle("wcm1_" + "a" * 32, "model_api_key")
SYNTHETIC = "synthetic-cli-secret-do-not-print"


def setup_cli(monkeypatch):
    module = importlib.import_module("personal_assistant.cli.secret_commands")
    store = Mock()
    store.put = AsyncMock(return_value=HANDLE)
    store.delete = AsyncMock()
    monkeypatch.setattr(module, "build_secret_store", lambda: store)
    monkeypatch.setattr(module.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(module.sys.stderr, "isatty", lambda: True)
    monkeypatch.setattr(module.getpass, "getpass", lambda *args, **kwargs: SYNTHETIC)
    return store, module


def test_put_is_hidden_metadata_only_and_never_calls_admin(monkeypatch, capsys):
    store, _ = setup_cli(monkeypatch)
    from personal_assistant.cli import main

    monkeypatch.setattr(main, "_request", Mock(side_effect=AssertionError("network forbidden")))
    assert main.main(["secret", "put", "--name", "model test", "--kind", HANDLE.kind]) == 0
    store.put.assert_awaited_once_with(name="model test", kind=HANDLE.kind, value=SYNTHETIC)
    output = capsys.readouterr()
    assert SYNTHETIC not in output.out + output.err
    receipt = json.loads(output.out)
    assert receipt == {"ok": True, "id": HANDLE.id, "kind": HANDLE.kind, "handle": str(HANDLE)}


def test_non_terminal_input_is_refused_without_reading(monkeypatch, capsys):
    store, module = setup_cli(monkeypatch)
    from personal_assistant.cli.main import main

    monkeypatch.setattr(module.sys.stdin, "isatty", lambda: False)
    prompt = Mock(side_effect=AssertionError("must not read"))
    monkeypatch.setattr(module.getpass, "getpass", prompt)
    assert main(["secret", "put", "--name", "label", "--kind", HANDLE.kind]) != 0
    store.put.assert_not_awaited()
    prompt.assert_not_called()
    assert SYNTHETIC not in str(capsys.readouterr())


def test_hidden_prompt_warning_and_backend_error_are_sanitized(monkeypatch, capsys):
    store, module = setup_cli(monkeypatch)
    from personal_assistant.cli.main import main

    def unsafe_prompt(*args, **kwargs):
        import warnings
        warnings.warn(SYNTHETIC, module.getpass.GetPassWarning, stacklevel=2)
        return SYNTHETIC

    monkeypatch.setattr(module.getpass, "getpass", unsafe_prompt)
    assert main(["secret", "put", "--name", "label", "--kind", HANDLE.kind]) != 0
    store.put.assert_not_awaited()
    assert SYNTHETIC not in str(capsys.readouterr())
    monkeypatch.setattr(module.getpass, "getpass", lambda *args: SYNTHETIC)
    store.put.side_effect = OSError(SYNTHETIC)
    assert main(["secret", "put", "--name", "label", "--kind", HANDLE.kind]) != 0
    assert SYNTHETIC not in str(capsys.readouterr())


def test_cli_has_no_value_or_export_option_and_does_not_echo_unknown_secret(capsys):
    from personal_assistant.cli.main import main

    with pytest.raises(SystemExit) as failure:
        main(["secret", "put", "--name", "label", "--kind", HANDLE.kind,
              "--value", SYNTHETIC])
    assert failure.value.code == 2
    assert SYNTHETIC not in str(capsys.readouterr())


def test_delete_needs_only_opaque_reference(monkeypatch, capsys):
    store, _ = setup_cli(monkeypatch)
    from personal_assistant.cli.main import main

    assert main(["secret", "delete", "--id", HANDLE.id, "--kind", HANDLE.kind]) == 0
    store.delete.assert_awaited_once_with(HANDLE)
    assert json.loads(capsys.readouterr().out) == {"ok": True, "deleted": str(HANDLE)}
