"""Public-SDK bridge for independent career planning and approved delivery."""

from __future__ import annotations

import asyncio
import base64
import copy
from collections.abc import Mapping
from typing import Any, cast

from personal_assistant_sdk import (
    PROTOCOL_VERSION,
    DrainReport,
    ExtensionInfo,
    FormSchemaDescriptor,
    HealthReport,
    InvocationContext,
    Outcome,
    RiskLevel,
    RuntimeContext,
    ToolDescriptor,
    ToolResult,
)
from personal_assistant_sdk.host import HostCapabilityError
from personal_assistant_sdk.models import JsonValue
from personal_assistant_sdk.worker import run_stdio_worker

from .domain import DomainError, FilterSpec, Job, ResumeRef, build_plan, filter_jobs
from .local_files import LocalResumeError, ResumeDirectory
from .mail import CareerMail, MailPlanError
from .sources import SourceError


def obj(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def text(maximum: int = 512, *, minimum: int = 0) -> dict[str, Any]:
    return {"type": "string", "minLength": minimum, "maxLength": maximum}


HASH = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
JOB = obj(
    {
        name: text(10_000 if name == "description" else 2048)
        for name in (
            "url",
            "job_id",
            "title",
            "company",
            "recruiter",
            "location",
            "salary_text",
            "description",
        )
    },
    ("url", "title", "company"),
)
RESUME = obj(
    {
        "filename": text(180, minimum=1),
        "sha256": HASH,
        "size_bytes": {"type": "integer", "minimum": 1, "maximum": 8 * 1024 * 1024},
        "media_type": text(128, minimum=1),
        "artifact_id": text(512),
    },
    ("filename", "sha256", "size_bytes"),
)
RECIPIENT = obj(
    {
        name: text(254 if name == "email" else 500)
        for name in ("email", "name", "company", "position")
    },
    ("email",),
)
ACTION = {"type": "string", "enum": ["greet", "message", "send_resume"]}
TARGET = obj(
    {
        "url": text(2048, minimum=1),
        "job_id": text(128, minimum=1),
        "company": text(300, minimum=1),
        "recruiter": text(300, minimum=1),
    },
    ("url", "job_id", "company", "recruiter"),
)
EXECUTE_INPUT = obj(
    {
        "local_action_id": text(100, minimum=1),
        "target": TARGET,
        "operation": obj(
            {
                "kind": ACTION,
                "text": text(4000),
                "snapshot_fingerprint": HASH,
                "resume_label": text(180),
            },
            ("kind", "text", "snapshot_fingerprint"),
        ),
        "consequences": text(4000, minimum=1),
        "attachments": {
            "type": "array",
            "maxItems": 1,
            "items": obj(
                {
                    "artifact_id": text(512, minimum=1),
                    "name": text(180, minimum=1),
                    "sha256": HASH,
                    "size_bytes": {"type": "integer", "minimum": 1, "maximum": 512 * 1024},
                },
                ("artifact_id", "name", "sha256", "size_bytes"),
            ),
        },
    },
    ("local_action_id", "target", "operation", "consequences", "attachments"),
)
FILTER = obj(
    {
        **{
            name: {"type": "array", "maxItems": 100, "items": text(256, minimum=1)}
            for name in ("include_keywords", "exclude_keywords", "locations", "company_denylist")
        },
        "daily_limit": {"type": "integer", "minimum": 1, "maximum": 100},
    }
)
OUTPUT = {"type": "object", "additionalProperties": True}
SEND_INPUT = obj(
    {
        **{
            name: text(512, minimum=1)
            for name in (
                "local_action_id",
                "account_id",
                "account_fingerprint",
                "from_address",
                "draft_id",
                "message_id",
                "mime_artifact_id",
            )
        },
        "draft_version": {"type": "integer", "const": 1},
        "mime_sha256": HASH,
        "to": {"type": "array", "minItems": 1, "maxItems": 1, "items": text(254, minimum=1)},
        "cc": {"type": "array", "maxItems": 0, "items": text(254)},
        "bcc": {"type": "array", "maxItems": 0, "items": text(254)},
        "subject": text(200, minimum=1),
        "attachment_hashes": {"type": "array", "minItems": 1, "maxItems": 1, "items": HASH},
    },
    (
        "local_action_id",
        "account_id",
        "account_fingerprint",
        "from_address",
        "draft_id",
        "draft_version",
        "message_id",
        "mime_artifact_id",
        "mime_sha256",
        "to",
        "cc",
        "bcc",
        "subject",
        "attachment_hashes",
    ),
)
TOOL_SCHEMAS: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
    "career.jobs.filter": (
        obj(
            {"jobs": {"type": "array", "maxItems": 1000, "items": JOB}, "filter": FILTER}, ("jobs",)
        ),
        OUTPUT,
    ),
    "career.boss.plan": (
        obj(
            {"job": JOB, "action_kind": ACTION, "message": text(4000), "resume": RESUME},
            ("job", "action_kind"),
        ),
        OUTPUT,
    ),
    "career.boss.prepare": (
        obj(
            {
                "job": JOB,
                "action_kind": ACTION,
                "message": text(4000),
                "resume_label": text(180),
                "resume": RESUME,
            },
            ("job", "action_kind"),
        ),
        OUTPUT,
    ),
    "career.boss.execute.preview": (EXECUTE_INPUT, OUTPUT),
    "career.boss.execute": (EXECUTE_INPUT, OUTPUT),
    "career.mail.plan": (
        obj(
            {
                "recipients": {"type": "array", "maxItems": 1000, "items": RECIPIENT},
                "subject": text(200, minimum=1),
                "body": text(20_000, minimum=1),
                "resume": RESUME,
            },
            ("recipients", "subject", "body"),
        ),
        OUTPUT,
    ),
    "career.resume.list": (
        obj(
            {
                "root_key": text(64, minimum=1),
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            }
        ),
        OUTPUT,
    ),
    "career.resume.import_file": (
        obj(
            {
                "root_key": text(64, minimum=1),
                "relative_path": text(1024, minimum=1),
                "expected_sha256": HASH,
            },
            ("root_key", "relative_path", "expected_sha256"),
        ),
        OUTPUT,
    ),
    "career.resume.import": (
        obj(
            {
                "filename": text(180, minimum=1),
                "media_type": {
                    "type": "string",
                    "enum": [
                        "application/pdf",
                        "text/plain",
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    ],
                },
                "data_base64": text(699052, minimum=1),
            },
            ("filename", "media_type", "data_base64"),
        ),
        OUTPUT,
    ),
    "career.mail.prepare": (
        obj(
            {
                "to": text(254, minimum=1),
                "subject": text(200, minimum=1),
                "body": text(20_000, minimum=1),
                "resume": RESUME,
                "prepared_at": text(64, minimum=1),
            },
            ("to", "subject", "body", "resume", "prepared_at"),
        ),
        OUTPUT,
    ),
    "career.mail.send.preview": (SEND_INPUT, OUTPUT),
    "career.mail.send": (SEND_INPUT, OUTPUT),
    "career.mail.status": (
        obj({"local_action_id": text(100, minimum=1)}, ("local_action_id",)),
        OUTPUT,
    ),
}
RISKS = {
    "career.resume.import": RiskLevel.INTERNAL_WRITE,
    "career.resume.import_file": RiskLevel.INTERNAL_WRITE,
    "career.mail.prepare": RiskLevel.INTERNAL_WRITE,
    "career.mail.send": RiskLevel.EXTERNAL_WRITE,
    "career.boss.execute": RiskLevel.EXTERNAL_WRITE,
}


def result(outcome: Outcome, output: Mapping[str, Any]) -> ToolResult:
    return ToolResult(outcome=outcome, output=cast(Mapping[str, JsonValue], dict(output)))


class NeedsUser(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class CareerApplyExtension:
    def __init__(self) -> None:
        self._runtime: RuntimeContext | None = None
        self._draining = False
        self._active = 0
        self._boss: Any = None
        self._resume_files: ResumeDirectory | None = None

    async def initialize(self, runtime: RuntimeContext) -> ExtensionInfo:
        self._runtime = runtime
        self._draining = False
        self._resume_files = None
        return ExtensionInfo(
            runtime.extension_id,
            runtime.extension_version,
            PROTOCOL_VERSION,
            ("ToolProvider", "FormSchemaProvider"),
            runtime.manifest_schema_hash,
        )

    async def health(self) -> HealthReport:
        return HealthReport(
            healthy=self._runtime is not None and not self._draining,
            status="ready" if not self._draining else "draining",
        )

    async def drain(self, deadline: float) -> DrainReport:
        del deadline
        self._draining = True
        return DrainReport(drained=self._active == 0, active_calls=self._active)

    async def shutdown(self) -> None:
        self._draining = True
        self._runtime = None
        self._boss = None
        self._resume_files = None

    def tools(self) -> tuple[ToolDescriptor, ...]:
        return tuple(
            ToolDescriptor(
                id=name,
                risk=RISKS.get(name, RiskLevel.READ),
                input_schema=schemas[0],
                output_schema=schemas[1],
            )
            for name, schemas in TOOL_SCHEMAS.items()
        )

    def forms(self) -> tuple[FormSchemaDescriptor, ...]:
        return (
            FormSchemaDescriptor(
                id="career.application_plan",
                json_schema=JOB,
                ui_schema={},
                field_sensitivity={name: "PERSONAL" for name in JOB["properties"]},
            ),
        )

    def _browser(self) -> Any:
        if self._boss is None:
            runtime = self._runtime
            endpoint = runtime.non_secret_config.get("boss_cdp_endpoint") if runtime else None
            if not isinstance(endpoint, str) or not endpoint:
                raise NeedsUser("BROWSER_CONFIGURATION_REQUIRED")
            from .boss import BossAdapter

            self._boss = BossAdapter(endpoint, artifacts=runtime.host_artifact if runtime else None)
        return self._boss

    def _mail(self) -> CareerMail:
        runtime = self._runtime
        if runtime is None:
            raise NeedsUser("EXTENSION_NOT_INITIALIZED")
        account = runtime.non_secret_config.get("account_id", "")
        return CareerMail(
            runtime.host_mail, runtime.host_artifact, account if isinstance(account, str) else ""
        )

    def _resume_directory(self) -> ResumeDirectory:
        if self._resume_files is None:
            if self._runtime is None:
                raise NeedsUser("EXTENSION_NOT_INITIALIZED")
            self._resume_files = ResumeDirectory(
                self._runtime.non_secret_config.get("resume_roots")
            )
        return self._resume_files

    async def invoke(
        self, tool_id: str, arguments: dict[str, Any], context: InvocationContext
    ) -> ToolResult:
        if self._draining or self._runtime is None:
            return result(Outcome.RETRYABLE, {"code": "EXTENSION_NOT_READY"})
        if tool_id not in TOOL_SCHEMAS:
            return result(Outcome.PERMANENT, {"code": "UNKNOWN_TOOL"})
        self._active += 1
        try:
            return await self._invoke(tool_id, arguments, context)
        except (DomainError, SourceError, MailPlanError, NeedsUser, LocalResumeError) as exc:
            needs_configuration = isinstance(exc, NeedsUser) or (
                isinstance(exc, MailPlanError)
                and exc.code
                in {"MAIL_ACCOUNT_REQUIRED", "MAIL_SEND_DISABLED", "ARTIFACT_CAPABILITY_REQUIRED"}
            )
            needs_configuration = needs_configuration or (
                isinstance(exc, LocalResumeError)
                and exc.code in {
                    "RESUME_ROOTS_REQUIRED", "RESUME_ROOT_INVALID", "RESUME_ROOT_CHANGED",
                    "RESUME_ROOT_NOT_AUTHORIZED", "RESUME_FILE_UNAVAILABLE",
                    "RESUME_FILE_CHANGED",
                }
            )
            outcome = Outcome.NEEDS_USER_ACTION if needs_configuration else Outcome.PERMANENT
            return result(outcome, {"code": exc.code})
        except HostCapabilityError:
            return result(Outcome.NEEDS_USER_ACTION, {"code": "HOST_CAPABILITY_UNAVAILABLE"})
        except (ValueError, TypeError, KeyError):
            return result(Outcome.PERMANENT, {"code": "INVALID_INPUT"})
        finally:
            self._active -= 1

    async def _invoke(
        self, name: str, args: dict[str, Any], context: InvocationContext
    ) -> ToolResult:
        if name == "career.resume.list":
            listing = await asyncio.to_thread(
                self._resume_directory().list_files,
                root_key=args.get("root_key"),
                limit=args.get("limit", 100),
            )
            return result(Outcome.SUCCEEDED, listing)
        if name == "career.resume.import_file":
            directory = self._resume_directory()
            material = self._mail()
            # A missing host store must not start a local file read.
            material._artifacts()
            filename, media_type, data = await asyncio.to_thread(
                directory.read_file,
                root_key=args["root_key"],
                relative_path=args["relative_path"],
                expected_sha256=args["expected_sha256"],
            )
            imported = await material.import_resume(
                {
                    "filename": filename,
                    "media_type": media_type,
                    "data_base64": base64.b64encode(data).decode("ascii"),
                }
            )
            return result(Outcome.SUCCEEDED, imported)
        if name == "career.jobs.filter":
            spec = FilterSpec(**args.get("filter", {}))
            selected = filter_jobs((Job.from_mapping(job) for job in args["jobs"]), spec)
            return result(
                Outcome.SUCCEEDED,
                {"jobs": [job.as_dict() for job in selected], "execution_available": False},
            )
        if name == "career.boss.plan":
            resume = ResumeRef(**args["resume"]) if "resume" in args else None
            plan = build_plan(
                Job.from_mapping(args["job"]),
                args["action_kind"],
                message=args.get("message", ""),
                resume=resume,
            )
            return result(
                Outcome.SUCCEEDED,
                {**plan.as_dict(), "platform": "boss", "execution_available": False},
            )
        if name == "career.mail.plan":
            from .planning import mail_plans

            resume = ResumeRef(**args["resume"]) if "resume" in args else None
            plans = mail_plans(args["recipients"], args["subject"], args["body"], resume)
            return result(Outcome.SUCCEEDED, {"plans": plans, "execution_available": False})
        if name.startswith("career.boss."):
            browser = self._browser()
            try:
                if name == "career.boss.prepare":
                    prepared = await browser.prepare(
                        args["job"],
                        args["action_kind"],
                        message=args.get("message", ""),
                        resume_label=args.get("resume_label", ""),
                        resume=args.get("resume"),
                    )
                    return result(
                        Outcome.SUCCEEDED, {"send_arguments": prepared, "approval_required": True}
                    )
                if name.endswith(".preview"):
                    await browser.review(args)
                    return result(
                        Outcome.SUCCEEDED,
                        {
                            "kind": "EXTENSION_ACTION",
                            "target": copy.deepcopy(args["target"]),
                            "payload": copy.deepcopy(args),
                            "consequences": args["consequences"],
                            "attachments": args["attachments"],
                        },
                    )
                sent = await browser.execute(args)
            except Exception as exc:
                if hasattr(exc, "code") and exc.__class__.__name__ == "BossNeedsUser":
                    return result(Outcome.NEEDS_USER_ACTION, {"code": str(exc.code)})
                if name == "career.boss.execute":
                    return result(Outcome.UNKNOWN, {"code": "BROWSER_RESULT_UNCONFIRMED"})
                raise
            if sent.get("status") == "NEEDS_USER":
                return result(Outcome.NEEDS_USER_ACTION, {"code": "BROWSER_NEEDS_USER"})
            receipt = sent.get("receipt")
            if (
                sent.get("status") != "SUCCEEDED"
                or not isinstance(receipt, Mapping)
                or receipt.get("verified") is not True
                or not receipt.get("reference_id")
            ):
                return result(Outcome.UNKNOWN, {"code": "BROWSER_RESULT_UNCONFIRMED"})
            return ToolResult(
                outcome=Outcome.SUCCEEDED,
                output=cast(Mapping[str, JsonValue], sent),
                external_receipt=cast(Mapping[str, JsonValue], dict(receipt)),
            )
        mail = self._mail()
        if name == "career.resume.import":
            return result(Outcome.SUCCEEDED, await mail.import_resume(args))
        if name == "career.mail.prepare":
            prepared = await mail.prepare(
                args, request_id=context.idempotency_key, task_id=context.task_id
            )
            return result(
                Outcome.SUCCEEDED, {"send_arguments": prepared, "approval_required": True}
            )
        if name in {"career.mail.send", "career.mail.send.preview"}:
            return result(Outcome.SUCCEEDED, await mail.preview(args))
        if name == "career.mail.status":
            if mail.mail is None or not mail.account_id:
                raise NeedsUser("MAIL_ACCOUNT_REQUIRED")
            return result(
                Outcome.SUCCEEDED,
                await mail.mail.delivery_status(
                    mail.account_id, local_action_id=args["local_action_id"]
                ),
            )
        return result(Outcome.PERMANENT, {"code": "UNKNOWN_TOOL"})


def create_extension() -> CareerApplyExtension:
    return CareerApplyExtension()


if __name__ == "__main__":
    run_stdio_worker(create_extension)
