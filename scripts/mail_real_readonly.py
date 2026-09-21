# ruff: noqa: E501
"""Controlled real-mail read-only acceptance harness (loopback form, no sends).

This is NOT production code.  F09 (Windows Credential Manager) is not
implemented, so the production credential backend is fail-closed.  This harness
is a one-shot local run:

* the client-specific password is typed into a loopback-only web form and is
  kept only in this process's memory; it is never written to disk, logs, the
  database, fixtures or the terminal;
* the real ``nju.smail`` extension is installed from the local repository (the
  page requires an explicit confirmation checkbox), enabled against the real
  PostgreSQL host and executed in its real per-version Worker;
* the real TLS IMAP broker performs a read-only sync (EXAMINE/BODY.PEEK only);
* no send tool is ever invoked and ``PA_MAIL_SEND_ENABLED`` stays false.

Run from the repository root with::

    .venv-win\\Scripts\\python.exe scripts/mail_real_readonly.py
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import secrets
import shutil
import sys
import tempfile
import threading
import webbrowser
import zipfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parents[1]
EXTENSION_ID = "nju.smail"
EXTENSION_VERSION = "0.1.0"
EXTENSION_DIR = ROOT / "extensions" / "nju_smail"
ACCOUNT_ID = "nju"
DEFAULT_DATABASE_URL = "postgresql+asyncpg://assistant:change-me@127.0.0.1:5432/assistant"
DEFAULT_IMAP_HOST = "imap.exmail.qq.com"
DEFAULT_IMAP_PORT = 993
DEFAULT_FOLDER = "INBOX"
SKIP_PARTS = {".venv", "__pycache__", ".pytest_cache", ".mypy_cache", "build", ".git"}
NAMESPACE = "ext_nju_2e_smail"

HINTS = {
    "MAIL_AUTH_FAILED": "认证失败：请确认使用的是邮箱网页端生成的“客户端专用密码”，且已开启 IMAP/SMTP 服务。",
    "MAIL_AUTH_UNSUPPORTED": "服务器不接受该认证方式；请检查邮箱服务商要求。",
    "MAIL_CREDENTIAL_UNAVAILABLE": "宿主凭据不可用。",
    "MAIL_TLS_FAILED": "TLS 证书或连接失败：请检查域名与端口。",
    "MAIL_TIMEOUT": "连接或读取超时：请检查本机网络/校园网。",
    "MAIL_RATE_LIMITED": "服务器限流：请稍后重试。",
    "MAIL_UNAVAILABLE": "IMAP 连接失败：请检查本机网络或稍后重试；本次未做任何写入。",
    "MAIL_FOLDER_UNAVAILABLE": "服务器拒绝以只读方式打开该文件夹（QQ 邮箱常见原因是客户端未识别；本次已发送 ID，若仍失败请联系管理员）。",
    "MAIL_PROTOCOL_ERROR": "IMAP 协议交互失败；本次未做任何写入。",
    "REQUIRED_CAPABILITY_UNAVAILABLE": "必需宿主能力不可用（需要 PostgreSQL 数据能力）。",
}


@dataclass(frozen=True, slots=True)
class RunResult:
    address_masked: str
    imap_host: str
    imap_port: int
    folder: str
    first_sync: dict[str, object]
    second_sync: dict[str, object] | None
    stored_messages: int
    stored_events: int
    send_enabled: bool
    duration_seconds: float
    error_code: str = ""
    error_hint: str = ""
    imap_diagnostics: tuple[dict[str, object], ...] = ()
    folders: tuple[str, ...] = ()


def _mask(address: str) -> str:
    local, _, domain = address.partition("@")
    if not local:
        return "***"
    return f"{local[:2]}***@{domain}"


def _zip_artifact(target: Path) -> Path:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(EXTENSION_DIR.rglob("*")):
            if any(part in SKIP_PARTS for part in path.parts):
                continue
            if path.is_dir():
                continue
            archive.write(path, path.relative_to(EXTENSION_DIR).as_posix())
    return target


async def _run_readonly(
    *,
    address: str,
    password: str,
    folder: str,
    imap_host: str,
    imap_port: int,
    scope: str = "inbox",
    custom_folders: str = "",
    include_junk_deleted: bool = False,
) -> RunResult:
    from personal_assistant.bootstrap import build_container
    from personal_assistant.core.extensions.lifecycle import InstallationConfirmation
    from personal_assistant.core.mail import MailAccountRecord
    from personal_assistant.infrastructure.memory.secrets import InMemorySecretStore
    from personal_assistant.settings import Settings

    started = datetime.now(UTC)
    masked = _mask(address)
    _install_frame_log()
    store = InMemorySecretStore()
    handle = await store.put(name=f"mail:{masked}", kind="mail_password", value=password)

    settings = Settings(
        environment="development",
        log_level="INFO",
        public_host="127.0.0.1",
        public_port=8000,
        admin_host="127.0.0.1",
        admin_port=8001,
        health_host="127.0.0.1",
        health_port=8010,
        storage_backend="postgres",
        database_url=os.getenv("PA_DATABASE_URL", DEFAULT_DATABASE_URL),
        extension_root=Path("./var/extensions").resolve(),
        artifact_root=Path("./var/artifacts").resolve(),
        trust_cloudflare_access=False,
        public_origin=None,
        cf_access_team_domain=None,
        cf_access_aud=None,
        mail_send_enabled=False,
    )
    container = build_container(settings, secret_store=store)
    supervisor = container.extension_supervisor
    error_code = ""
    first: dict[str, object] = {}
    second: dict[str, object] | None = None
    messages = 0
    events = 0
    diagnostics: tuple[dict[str, object], ...] = ()
    resolved_folders: tuple[str, ...] = ()
    try:
        await container.storage.startup()
        record = await supervisor.record(EXTENSION_ID)
        state = record.state.value if record is not None else "MISSING"
        code_missing = record is None or not record.install_path
        if code_missing or state in {"UNINSTALLED", "REJECTED", "DISCOVERED", "STAGED"}:
            with tempfile.TemporaryDirectory(prefix="pa_realmail_") as tmp:
                artifact = _zip_artifact(Path(tmp) / "nju_smail.zip")
                preview = await supervisor.inspect(str(artifact))
                confirmation = InstallationConfirmation(
                    plan_id=preview.plan_id,
                    confirmation_nonce=preview.confirmation_nonce,
                    preview_hash=preview.preview_hash,
                    actor="owner",
                    confirmed_at=datetime.now(UTC),
                    accepted_warning=True,
                )
                operation = await supervisor.begin_install(preview.plan_id, confirmation)
                result = await supervisor.wait_operation(operation.id, timeout_seconds=300)
                if result.status.value != "SUCCEEDED":
                    raise RuntimeError(result.diagnostic_code or "INSTALL_FAILED")
            state = "INSTALLED_DISABLED"
        await container.mail_accounts.upsert(
            MailAccountRecord(
                account_id=ACCOUNT_ID,
                address=address,
                imap_host=imap_host,
                smtp_host="smtp.exmail.qq.com",
                secret_handle_id=handle.id,
                imap_port=imap_port,
                read_enabled=True,
                send_enabled=False,
            )
        )
        resolved_folders = await _resolve_folders(
            container,
            scope=scope,
            custom_folders=custom_folders,
            include_junk_deleted=include_junk_deleted,
            default_folder=folder,
        )
        await container.extension_config_store.save(
            EXTENSION_ID,
            {
                "accounts": [{"account_id": ACCOUNT_ID, "display_name": masked}],
                "folders": list(resolved_folders) or [folder],
                "max_messages_per_sync": 100,
                "poll_interval_seconds": 300,
            },
        )
        if state == "ENABLED":
            operation = await supervisor.begin_disable(EXTENSION_ID)
            await supervisor.wait_operation(operation.id, timeout_seconds=120)
        operation = await supervisor.begin_enable(EXTENSION_ID)
        result = await supervisor.wait_operation(operation.id, timeout_seconds=180)
        if result.status.value != "SUCCEEDED":
            raise RuntimeError(result.diagnostic_code or "ENABLE_FAILED")

        first_errors: list[str] = []
        first_total_new = 0
        first_total_events = 0
        rounds = 0
        for _ in range(20):
            round_result = await _sync(supervisor, None)
            if round_result.get("outcome") != "SUCCEEDED":
                raise RuntimeError(
                    str(round_result.get("user_action_code") or "SYNC_NEEDS_USER_ACTION")
                )
            rounds += 1
            output = dict(round_result.get("output", {}))
            first = round_result
            first_errors = _folder_errors(output)
            if first_errors:
                break
            new_messages = int(output.get("new_messages", 0) or 0)
            first_total_new += new_messages
            first_total_events += int(output.get("events", 0) or 0)
            if new_messages == 0:
                break
        if first:
            first = dict(first)
            first["output"] = {
                **dict(first.get("output", {})),
                "new_messages": first_total_new,
                "events": first_total_events,
                "rounds": rounds,
            }
        second_errors: list[str] = []
        if not first_errors:
            second = await _sync(supervisor, None)
            second_errors = _folder_errors(dict(second.get("output", {})))
        if first_errors or second_errors:
            error_code = (first_errors or second_errors)[0]
        record = await supervisor.record(EXTENSION_ID)
        if record is not None and record.install_path:
            venv_python = Path(record.install_path) / "venv" / "Scripts" / "python.exe"
            diagnostics = await _run_imap_diagnostic(
                venv_python=venv_python,
                address=address,
                password=password,
                host=imap_host,
                port=imap_port,
            )
        if first_errors or second_errors:
            diagnostics = (
                *diagnostics,
                *await _run_broker_diagnostic(
                    container, resolved_folders[0] if resolved_folders else folder
                ),
            )
        messages, events = await _counts(container)
    except Exception as exc:  # noqa: BLE001 - typed codes are surfaced to the page
        error_code = _error_code(exc)
        _dump_worker_diagnostics(supervisor)
    finally:
        try:
            await supervisor.stop_all()
        finally:
            await container.storage.close()
        await store.delete(handle)
        password = ""  # drop the local reference; memory is not zeroed by Python

    duration = (datetime.now(UTC) - started).total_seconds()
    return RunResult(
        address_masked=masked,
        imap_host=imap_host,
        imap_port=imap_port,
        folder=folder,
        first_sync=dict(first.get("output", {})) if first else {},
        second_sync=dict(second.get("output", {})) if second else None,
        stored_messages=messages,
        stored_events=events,
        send_enabled=False,
        duration_seconds=round(duration, 1),
        error_code=error_code,
        error_hint=HINTS.get(error_code, ""),
        imap_diagnostics=diagnostics,
        folders=resolved_folders,
    )


async def _run_imap_diagnostic(
    *,
    venv_python: Path,
    address: str,
    password: str,
    host: str,
    port: int,
) -> tuple[dict[str, object], ...]:
    """Run the read-only step diagnostic inside the extension venv.

    The password is written to the child's stdin only; the child prints one
    JSON step line per command with bounded server text and no credentials.
    """

    from personal_assistant.core.extensions.rpc import _SAFE_ENVIRONMENT_KEYS

    if not venv_python.is_file():
        return ()
    payload = (
        json.dumps(
            {"address": address, "password": password, "host": host, "port": port},
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")
    environment = {
        key: os.environ[key] for key in _SAFE_ENVIRONMENT_KEYS if key in os.environ
    }
    script = ROOT / "scripts" / "mail_imap_diagnostic.py"
    try:
        process = await asyncio.create_subprocess_exec(
            str(venv_python),
            str(script),
            cwd=str(ROOT),
            env=environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(payload), timeout=60)
    except (OSError, TimeoutError):
        return ()
    steps: list[dict[str, object]] = []
    for line in stdout.decode("utf-8", "replace").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, list) and len(entry) == 2:
            steps.append({"step": str(entry[0]), "result": str(entry[1])})
    return tuple(steps)


async def _run_broker_diagnostic(
    container: object, folder: str
) -> tuple[dict[str, object], ...]:
    """Exercise the exact production broker path with the in-memory credential."""

    steps: list[dict[str, object]] = []

    def record(name: str, exc: Exception | None = None, detail: str = "ok") -> None:
        if exc is None:
            steps.append({"step": name, "result": detail})
            return
        code = getattr(exc, "code", "")
        steps.append(
            {"step": name, "result": f"{type(exc).__name__} {code} {str(exc)[:160]}"}
        )

    try:
        session = await container.mail_broker.read_session(ACCOUNT_ID)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 - diagnostic output only
        record("broker.read_session", exc)
        return tuple(steps)
    record("broker.read_session")
    try:
        try:
            capabilities = await session.probe()
            record(
                "broker.probe",
                detail=f"ok exists={capabilities.exists} uidvalidity={capabilities.uidvalidity}",
            )
        except Exception as exc:  # noqa: BLE001 - diagnostic output only
            record("broker.probe", exc)
            return tuple(steps)
        try:
            folders = await session.list_folders()
            record("broker.list_folders", detail=f"ok {len(folders)} folders")
        except Exception as exc:  # noqa: BLE001 - diagnostic output only
            record("broker.list_folders", exc)
            return tuple(steps)
        try:
            fetched = await session.fetch(
                folder, uidvalidity=capabilities.uidvalidity, start_uid=0, limit=100
            )
            record("broker.fetch", detail=f"ok {len(fetched.messages)} messages")
        except Exception as exc:  # noqa: BLE001 - diagnostic output only
            record("broker.fetch", exc)
    finally:
        await session.close()
    return tuple(steps)


async def _resolve_folders(
    container: object,
    *,
    scope: str,
    custom_folders: str,
    include_junk_deleted: bool,
    default_folder: str,
) -> tuple[str, ...]:
    """Resolve the folders to configure: INBOX, an explicit list, or all readable."""

    if scope == "custom":
        names = [item.strip() for item in custom_folders.split(",") if item.strip()]
        return tuple(names[:16]) or (default_folder,)
    if scope != "all":
        return (default_folder,)
    session = await container.mail_broker.read_session(ACCOUNT_ID)  # type: ignore[attr-defined]
    try:
        discovered = await session.list_folders()
    finally:
        await session.close()
    chosen = ["INBOX"]
    for item in discovered:
        if not item.selectable or item.name.upper() == "INBOX":
            continue
        lowered = item.name.lower()
        if not include_junk_deleted and ("junk" in lowered or "deleted" in lowered):
            continue
        if item.name not in chosen:
            chosen.append(item.name)
    return tuple(chosen[:16])


async def _sync(supervisor: object, folder: str | None) -> dict[str, object]:
    arguments: dict[str, object] = {"account_id": ACCOUNT_ID, "force": True}
    if folder is not None:
        arguments["folder"] = folder
    result = await supervisor.invoke_tool(  # type: ignore[attr-defined]
        EXTENSION_ID,
        "smail.sync",
        arguments,
        task_id="task-real-mail",
        run_id="run-real-mail",
        idempotency_key=f"real-mail-sync-{secrets.token_hex(8)}",
        deadline_seconds=180.0,
    )
    outcome = str(result.get("outcome", ""))
    output = result.get("output", {})
    if not isinstance(output, dict):
        output = {}
    accounts = output.get("accounts", [])
    if isinstance(accounts, list):
        for account in accounts:
            if isinstance(account, dict) and account.get("status") == "NEEDS_USER_ACTION":
                code = str(account.get("error_code") or "NEEDS_USER_ACTION")
                return {"outcome": "NEEDS_USER_ACTION", "user_action_code": code, "output": output}
    return {"outcome": outcome, "output": output}


async def _counts(container: object) -> tuple[int, int]:
    database = container.storage.database  # type: ignore[attr-defined]
    async with database.connection() as connection:
        messages = await connection.fetchval(
            f"SELECT count(*) FROM {NAMESPACE}.mail_messages"
        )
        events = await connection.fetchval(
            f"SELECT count(*) FROM {NAMESPACE}.mail_events"
        )
    return int(messages or 0), int(events or 0)



_FRAME_LOG: Path | None = None


def _install_frame_log() -> None:
    """Log host->worker frame sizes/ids only (never payloads) for diagnosis."""

    global _FRAME_LOG  # noqa: PLW0603 - one-shot harness instrumentation
    import personal_assistant.core.extensions.rpc as rpc_module

    evidence = Path("./var/realtest").resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    _FRAME_LOG = evidence / f"host-frames-{stamp}.log"
    original = rpc_module.encode_frame

    def logging_encode(message: object) -> bytes:
        frame = original(message)  # type: ignore[arg-type]
        entry: dict[str, object] = {"bytes": len(frame)}
        try:
            body = json.loads(frame)
            entry["id"] = body.get("id")
            entry["method"] = body.get("method")
            entry["has_result"] = "result" in body
            entry["has_error"] = "error" in body
        except Exception:  # noqa: BLE001 - a parsed frame is always expected
            entry["unparsed_prefix"] = frame[:120].decode("utf-8", "replace")
        with open(_FRAME_LOG, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return frame

    rpc_module.encode_frame = logging_encode  # type: ignore[assignment]


def _dump_worker_diagnostics(supervisor: object) -> None:
    """Persist the worker's captured stderr for local diagnosis (gitignored)."""

    try:
        runtime = supervisor._runtime
        lines = tuple(runtime.diagnostics(EXTENSION_ID))
    except Exception:  # noqa: BLE001 - diagnostics must never mask the outcome
        return
    if not lines:
        return
    evidence = Path("./var/realtest").resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    (evidence / f"worker-stderr-{stamp}.txt").write_text(
        "\n".join(lines[-200:]) + "\n", encoding="utf-8"
    )


def _error_code(exc: Exception) -> str:
    for attribute in ("code", "diagnostic_code"):
        value = getattr(exc, attribute, None)
        if isinstance(value, str) and value:
            return value
    data = getattr(exc, "data", None)
    if isinstance(data, dict):
        code = data.get("code")
        if isinstance(code, str) and code:
            return code
    text = str(exc)
    return text[:80] if text else type(exc).__name__


def _summary(report: dict[str, object]) -> str:
    entries = report.get("accounts", [])
    if not isinstance(entries, list) or not entries:
        return "无文件夹结果"
    parts = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        error = item.get("error_code")
        suffix = f"、错误 {error}" if error else ""
        parts.append(
            f"{item.get('folder', '?')}: {item.get('status', '?')} "
            f"新邮件 {item.get('new_messages', 0)}、扫描 {item.get('scanned', 0)}{suffix}"
        )
    detail = "；".join(parts) or "无文件夹结果"
    if "rounds" in report:
        detail = (
            f"共 {report.get('rounds')} 轮、合计新邮件 {report.get('new_messages', 0)}、"
            f"事件 {report.get('events', 0)}；最后一批：{detail}"
        )
    return detail


def _folder_errors(report: dict[str, object]) -> list[str]:
    entries = report.get("accounts", [])
    if not isinstance(entries, list):
        return []
    return [
        str(item.get("error_code"))
        for item in entries
        if isinstance(item, dict) and item.get("error_code")
    ]


def _render_result(result: RunResult) -> str:
    if result.error_code:
        head = f"<h1>只读同步失败</h1><p class='err'>{html.escape(result.error_code)}</p>"
        if result.error_hint:
            head += f"<p>{html.escape(result.error_hint)}</p>"
    else:
        head = "<h1>真实邮箱只读同步完成</h1><p class='ok'>未发送任何邮件，未做任何写入式 IMAP 操作。</p>"
    rows = [
        ("邮箱", result.address_masked),
        ("IMAP", f"{result.imap_host}:{result.imap_port}"),
        ("文件夹", "、".join(result.folders) if result.folders else result.folder),
        ("第一次同步", f"{result.first_sync.get('rounds', 1)} 轮；{_summary(result.first_sync)}"),
        ("第二次同步（幂等验证）", _summary(result.second_sync) if result.second_sync else "未执行"),
        ("库内已存邮件", str(result.stored_messages)),
        ("库内事件", str(result.stored_events)),
        ("发送能力", "关闭（PA_MAIL_SEND_ENABLED=false）"),
        ("密码落盘", "否：仅存在于本次进程内存，已清除"),
        ("耗时", f"{result.duration_seconds}s"),
    ]
    if result.imap_diagnostics:
        diag_rows = "".join(
            f"<tr><th>{html.escape(str(item.get('step', '')))}</th>"
            f"<td>{html.escape(str(item.get('result', '')))}</td></tr>"
            for item in result.imap_diagnostics
        )
        diag_html = f"<h2>IMAP 分步诊断</h2><table>{diag_rows}</table>"
    else:
        diag_html = ""
    body = "".join(
        f"<tr><th>{html.escape(key)}</th><td>{html.escape(value)}</td></tr>" for key, value in rows
    )
    return f"""{_PAGE_HEAD}{head}<table>{body}</table>{diag_html}
<p class="note">说明：F09（Windows Credential Manager）尚未实现，本次为受控真实测试；
生产环境的凭据后端仍 fail-closed，后续应用内同步需要 F09 完成后再绑定同一 account_id。</p>
</body></html>"""


_PAGE_HEAD = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>真实邮箱只读同步（本机回环）</title><style>
body{font-family:system-ui,"Segoe UI",sans-serif;max-width:720px;margin:40px auto;padding:0 16px;color:#1c2430}
h1{font-size:22px} form{display:grid;gap:12px;margin-top:16px}
label{display:grid;gap:4px;font-size:14px} input{padding:8px;font-size:15px;border:1px solid #c6ced8;border-radius:6px}
button{padding:10px 16px;font-size:15px;border:0;border-radius:6px;background:#14508c;color:#fff;cursor:pointer}
table{border-collapse:collapse;width:100%;margin-top:16px} th,td{border:1px solid #d7dee7;padding:8px;text-align:left;font-size:14px}
th{background:#f2f5f9;width:180px}.ok{color:#0c6b37;font-weight:600}.err{color:#a11919;font-weight:600}
.note{color:#5b6675;font-size:13px;margin-top:16px}.warn{background:#fff7e6;border:1px solid #f0d28a;padding:10px;border-radius:6px;font-size:13px}
</style></head><body>"""

_FORM_PAGE = f"""{_PAGE_HEAD}
<h1>真实邮箱只读同步（受控测试）</h1>
<div class="warn">该页面只监听 127.0.0.1。密码仅用于本次进程内存中的凭据句柄，不落盘、不入库、不进日志；
系统只执行 IMAP 只读命令（EXAMINE/BODY.PEEK），不会读取后标记已读，也不会发送任何邮件。<br>
请先在你常用的邮箱网页端（设置 → 客户端/IMAP 相关）生成“客户端专用密码”，再回到本页填写；
本页不会打开、嵌入或接管任何邮箱网页登录，也不接受网页登录密码。</div>
<form method="post" action="/run">
<label>邮箱地址（例如 学号@smail.nju.edu.cn）
<input name="address" required autocomplete="off" placeholder="xxxxxxxxxx@smail.nju.edu.cn"></label>
<label>客户端专用密码（网页端生成，不是登录密码）
<input type="password" name="password" required autocomplete="new-password"></label>
<label>同步范围
<select name="scope">
<option value="inbox">仅 INBOX（默认）</option>
<option value="all">全部可读文件夹（自动发现，建议选这个看全部邮件）</option>
<option value="custom">自定义（在下方填写）</option>
</select></label>
<label>自定义文件夹（逗号分隔，仅“自定义”时使用）
<input name="custom_folders" autocomplete="off" placeholder="INBOX, Sent Messages, 其他文件夹"></label>
<label><span><input type="checkbox" name="include_junk_deleted" value="yes">
全部可读时包含 Junk 与已删除文件夹</span></label>
<label>文件夹（仅“仅 INBOX”时使用）
<input name="folder" value="{DEFAULT_FOLDER}" autocomplete="off"></label>
<label>IMAP 服务器
<input name="imap_host" value="{DEFAULT_IMAP_HOST}" autocomplete="off"></label>
<label>IMAP 端口
<input name="imap_port" value="{DEFAULT_IMAP_PORT}" inputmode="numeric" autocomplete="off"></label>
<label><span><input type="checkbox" name="confirm" value="yes" required>
我确认：这是本机受控只读测试；我授权安装并启用仓库内的 nju.smail 扩展，且不会发送任何邮件。</span></label>
<input type="hidden" name="token" value="{{token}}">
<button type="submit">开始只读同步</button>
</form>
</body></html>"""


@dataclass(slots=True)
class _State:
    token: str
    finished: threading.Event


def _parse_address(raw: str) -> str:
    address = raw.strip().lower()
    if "@" not in address or len(address) > 320:
        raise ValueError("邮箱地址格式不正确")
    return address


def _parse_port(raw: str) -> int:
    try:
        port = int(raw)
    except (TypeError, ValueError):
        raise ValueError("端口必须是数字") from None
    if not 1 <= port <= 65535:
        raise ValueError("端口超出范围")
    return port


def main() -> None:
    state = _State(token=secrets.token_urlsafe(24), finished=threading.Event())

    class Handler(BaseHTTPRequestHandler):
        server_version = "RealMailReadonly/1.0"

        def log_message(self, *_args: object) -> None:
            return None

        def _send(self, status: int, body: str) -> None:
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Pragma", "no-cache")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802 - stdlib naming
            if self.path not in ("/", "/index.html"):
                self._send(404, f"{_PAGE_HEAD}<p>Not found</p></body></html>")
                return
            self._send(
                200,
                _FORM_PAGE.replace("{token}", html.escape(state.token, quote=True)),
            )

        def do_POST(self) -> None:  # noqa: N802 - stdlib naming
            if self.path != "/run":
                self._send(404, f"{_PAGE_HEAD}<p>Not found</p></body></html>")
                return
            length = int(self.headers.get("Content-Length", "0") or 0)
            form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
            try:
                if (form.get("token") or [""])[0] != state.token:
                    raise ValueError("页面令牌已失效，请重新打开本页面")
                if (form.get("confirm") or [""])[0] != "yes":
                    raise ValueError("请勾选确认项")
                address = _parse_address((form.get("address") or [""])[0])
                password = (form.get("password") or [""])[0]
                if not password:
                    raise ValueError("密码不能为空")
                folder = (form.get("folder") or [DEFAULT_FOLDER])[0].strip() or DEFAULT_FOLDER
                imap_host = (form.get("imap_host") or [DEFAULT_IMAP_HOST])[0].strip()
                scope_raw = (form.get("scope") or ["inbox"])[0].strip().lower()
                scope = scope_raw if scope_raw in {"inbox", "all", "custom"} else "inbox"
                custom_folders = (form.get("custom_folders") or [""])[0]
                include_junk_deleted = (
                    (form.get("include_junk_deleted") or [""])[0] == "yes"
                )
                imap_port = _parse_port((form.get("imap_port") or [str(DEFAULT_IMAP_PORT)])[0])
            except ValueError as exc:
                self._send(
                    400,
                    f"{_PAGE_HEAD}<p class='err'>{html.escape(str(exc))}</p>"
                    "<p><a href='/'>返回</a></p></body></html>",
                )
                return
            result = asyncio.run(
                _run_readonly(
                    address=address,
                    password=password,
                    folder=folder,
                    imap_host=imap_host,
                    imap_port=imap_port,
                    scope=scope,
                    custom_folders=custom_folders,
                    include_junk_deleted=include_junk_deleted,
                )
            )
            del password
            evidence = Path("./var/realtest").resolve()
            evidence.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            (evidence / f"mail-readonly-{stamp}.json").write_text(
                json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self._send(200, _render_result(result))
            state.finished.set()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 1.0
    port = int(server.server_address[1])
    url = f"http://127.0.0.1:{port}/"
    print(f"打开浏览器页面完成凭据输入与确认：{url}", flush=True)
    if os.getenv("PA_REALMAIL_NO_BROWSER") != "1":
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
    try:
        while not state.finished.is_set():
            server.handle_request()
    except KeyboardInterrupt:
        print("已取消。", flush=True)
    finally:
        server.server_close()
        shutil.rmtree(Path(tempfile.gettempdir()) / "pa_realmail", ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
