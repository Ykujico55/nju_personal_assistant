"""TUI opt-in CLI does not start the host, execute tasks or load model secrets."""

from pathlib import Path
from unittest.mock import patch

from personal_assistant.cli.main import build_parser, main


def test_tui_arguments_are_local_and_optional():
    args = build_parser().parse_args(["tui", "--profile", "host.json", "--refresh", "4"])
    assert args.profile == Path("host.json")
    assert args.refresh == 4


def test_tui_dispatch_is_lazy_and_passes_explicit_profile():
    with patch("personal_assistant.cli.tui.launch.run", return_value=0) as run:
        assert main(["--admin-url", "http://127.0.0.1:8123", "tui", "--profile", "host.json"]) == 0
    assert run.call_args.kwargs["admin_url"] == "http://127.0.0.1:8123"
    assert run.call_args.kwargs["profile"] == Path("host.json")


def test_tui_rejects_public_admin_url_before_opening_ui(capsys):
    from personal_assistant.cli.tui.launch import run

    assert run(admin_url="https://example.com") == 2
    assert "LOCAL_URL_REQUIRED" in capsys.readouterr().err
