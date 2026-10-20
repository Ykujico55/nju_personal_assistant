"""Interactive model setup persists public settings and an opaque credential reference only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from personal_assistant.core.agent.decisions import parse_proposal
from personal_assistant.core.models import (
    ContextField,
    DataClassification,
    ModelRequest,
    ModelRouter,
)
from personal_assistant.core.secrets import SecretHandle, SecretStorePort
from personal_assistant.infrastructure.models.openai_compatible import (
    OpenAICompatibleChatProvider,
    OpenAICompatibleConfig,
)
from personal_assistant.settings import (
    normalize_model_endpoint,
    normalize_model_id,
    normalize_provider_id,
    normalize_secret_handle_id,
)

KEYS = frozenset(
    {
        "PA_MODEL_REMOTE_PROVIDER_ID",
        "PA_MODEL_REMOTE_BASE_URL",
        "PA_MODEL_REMOTE_MODEL",
        "PA_MODEL_REMOTE_SECRET_HANDLE",
        "PA_MODEL_REMOTE_JSON_MODE",
        "PA_MODEL_REMOTE_THINKING",
    }
)


def load_config(path: Path) -> dict[str, str]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or set(value) != KEYS
        or any(not isinstance(v, str) for v in value.values())
    ):
        raise ValueError("model configuration must contain public settings and a handle only")
    normalize_provider_id(value["PA_MODEL_REMOTE_PROVIDER_ID"], field_name="provider")
    normalize_model_endpoint(value["PA_MODEL_REMOTE_BASE_URL"], field_name="endpoint")
    normalize_model_id(value["PA_MODEL_REMOTE_MODEL"], field_name="model")
    normalize_secret_handle_id(
        value["PA_MODEL_REMOTE_SECRET_HANDLE"], field_name="credential reference"
    )
    if value["PA_MODEL_REMOTE_THINKING"] not in {"enabled", "disabled"}:
        raise ValueError("invalid thinking mode")
    if value["PA_MODEL_REMOTE_JSON_MODE"] not in {"true", "false"}:
        raise ValueError("invalid JSON mode")
    return value


async def configure(path: Path, store: SecretStorePort, value: str) -> dict[str, str]:
    if path.exists():
        raise FileExistsError(
            "model settings already exist; explicit credential rotation is required"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    # Reserve before writing the credential, so another setup cannot overwrite/delete it.
    with path.open("x", encoding="utf-8") as output:
        handle = None
        try:
            handle = await store.put(name="assistant.deepseek", kind="model_api_key", value=value)
            settings = {
                "PA_MODEL_REMOTE_PROVIDER_ID": "deepseek",
                "PA_MODEL_REMOTE_BASE_URL": "https://api.deepseek.com",
                "PA_MODEL_REMOTE_MODEL": "deepseek-flash",
                "PA_MODEL_REMOTE_SECRET_HANDLE": handle.id,
                "PA_MODEL_REMOTE_JSON_MODE": "true",
                "PA_MODEL_REMOTE_THINKING": "disabled",
            }
            json.dump(settings, output, indent=2)
        except BaseException:
            if handle is not None:
                await store.delete(handle)
            output.close()
            path.unlink(missing_ok=True)
            raise
    return settings


async def trial(settings: dict[str, str], store: SecretStorePort) -> dict[str, Any]:
    """One bounded PUBLIC-only planning call; no mailbox, task database, or tool invocation."""
    provider = OpenAICompatibleChatProvider(
        OpenAICompatibleConfig(
            provider_id=settings["PA_MODEL_REMOTE_PROVIDER_ID"],
            model_id=settings["PA_MODEL_REMOTE_MODEL"],
            base_url=settings["PA_MODEL_REMOTE_BASE_URL"],
            timeout_seconds=45,
            max_output_tokens=512,
            secret_handle=SecretHandle(settings["PA_MODEL_REMOTE_SECRET_HANDLE"], "model_api_key"),
            json_mode=True,
            thinking="disabled",
        ),
        secret_store=store,
    )
    router = ModelRouter((provider,))
    try:
        response = await router.complete(
            ModelRequest(
                purpose="synthetic.model.smoke",
                instruction=(
                    'Return JSON exactly with kind="COMPLETE" and a short Chinese message. '
                    "No tools are available."
                ),
                fields=(
                    ContextField(
                        "test",
                        "只用一句话说明助手已连接模型，不执行外部动作。",
                        DataClassification.PUBLIC,
                        "synthetic.fixture",
                    ),
                ),
            ),
            provider_id=settings["PA_MODEL_REMOTE_PROVIDER_ID"],
        )
        proposal = parse_proposal(response.text)
        if (
            proposal.get("kind") != "COMPLETE"
            or set(proposal) - {"kind", "message", "result"}
            or not isinstance(proposal.get("message"), str)
        ):
            raise ValueError("unexpected model proposal")
        return {
            "ok": True,
            "model": settings["PA_MODEL_REMOTE_MODEL"],
            "message": proposal["message"],
            "usage": dict(response.usage or {}),
        }
    finally:
        await router.aclose()
