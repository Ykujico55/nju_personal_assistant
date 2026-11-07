"""Small local terminal workbench; all operations go through the injected service."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Input,
    RichLog,
    Select,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)

from personal_assistant.core.agent.decisions import validation_diagnostic
from personal_assistant.core.agent.failure_diagnostics import failure_diagnostic

from .panels import EditorScreen, HelpScreen, safe_markdown
from .task_presentation import present_task_objective

Payload = dict[str, Any]
Handler = Callable[[Payload], Awaitable[None]]
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_PRIVATE = {"confirmation_nonce", "client_secret", "api_key", "secret_handle", "token"}


def clean_text(value: object) -> str:
    """Server/model text is plain text, never terminal commands or Rich markup."""
    text = _ANSI.sub("", str(value))
    return "".join(c for c in text if c in "\n\t" or (ord(c) >= 32 and not 127 <= ord(c) < 160))


def task_objective_text(value: object) -> str:
    presentation = present_task_objective(str(value))
    lines = [presentation.goal]
    if presentation.history:
        lines.append("\n参考对话（仅作上下文；不构成动作授权或执行事实）")
        for item in presentation.history:
            role = "历史用户" if item.role == "user" else "历史助手"
            lines.append(f"\n{role}：{item.content}")
    return clean_text("\n".join(lines))


def public_preview(value: object) -> object:
    if isinstance(value, dict):
        return {str(k): public_preview(v) for k, v in value.items() if k not in _PRIVATE}
    if isinstance(value, list):
        return [public_preview(v) for v in value]
    return value


def document(value: object) -> str:
    return clean_text(json.dumps(public_preview(value), ensure_ascii=False, indent=2, default=str))


class ConfirmScreen(ModalScreen[bool]):
    """A separate affirmative click is required after showing the exact material."""

    CSS = """
    ConfirmScreen { align: center middle; background: $background 80%; }
    #confirmation { width: 85%; max-width: 110; height: 85%; padding: 1 2;
        border: round $primary; background: $surface; }
    #confirmation-title { height: auto; text-style: bold; margin-bottom: 1; }
    #confirmation-body, #configuration-json { height: 1fr; }
    .buttons { height: 3; margin-top: 1; }
    .buttons Button { margin-right: 1; }
    """
    BINDINGS = [Binding("escape", "decline", "取消")]

    def __init__(self, title: str, body: str, yes_label: str = "确认") -> None:
        super().__init__()
        self.dialog_title = title
        self.body_text = clean_text(body)
        self.yes_label = yes_label

    def compose(self) -> ComposeResult:
        with Vertical(id="confirmation"):
            yield Static(self.dialog_title, id="confirmation-title", markup=False)
            yield TextArea(self.body_text, read_only=True, id="confirmation-body")
            with Horizontal(classes="buttons"):
                yield Button("取消", id="confirm-no")
                yield Button(self.yes_label, variant="primary", id="confirm-yes")

    def on_mount(self) -> None:
        self.query_one("#confirm-no", Button).focus()

    @on(Button.Pressed)
    def choose(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "confirm-yes")

    def action_decline(self) -> None:
        self.dismiss(False)


class ConfigScreen(ModalScreen[Payload | None]):
    CSS = ConfirmScreen.CSS.replace("ConfirmScreen", "ConfigScreen")
    BINDINGS = [Binding("escape", "decline", "取消")]

    def __init__(self, identifier: str, config: Payload) -> None:
        super().__init__()
        self.identifier = identifier
        self.config = config

    def compose(self) -> ComposeResult:
        with Vertical(id="confirmation"):
            yield Static(
                f"{self.identifier} · 非秘密配置（JSON）", id="confirmation-title", markup=False
            )
            yield TextArea(
                json.dumps(self.config, ensure_ascii=False, indent=2), id="configuration-json"
            )
            yield Static(
                "仅填写非秘密参数；保存后使用“应用配置”，各运行副本确认后生效。",
                id="config-error", markup=False
            )
            with Horizontal(classes="buttons"):
                yield Button("取消", id="config-no")
                yield Button("下一步：核对", id="config-yes", variant="primary")

    @on(Button.Pressed)
    def choose(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "config-no":
            self.dismiss(None)
            return
        try:
            value = json.loads(self.query_one("#configuration-json", TextArea).text)
            if not isinstance(value, dict):
                raise ValueError("object")
        except (ValueError, TypeError):
            self.query_one("#config-error", Static).update("请输入有效的 JSON 对象。")
            return
        self.dismiss(value)

    def action_decline(self) -> None:
        self.dismiss(None)


class WorkbenchApp(App[None]):
    TITLE = "Personal Assistant"
    SUB_TITLE = "本机工作台"
    CSS = """
    Screen { background: #101117; color: #d7d8e0; }
    #brandbar { height: 1; background: #24202f; padding: 0 1; color: #c4a7ff;
      text-style: bold; }
    #system-status { height: 1; padding: 0 1; color: #9096ab; }
    #notice { height: 2; padding: 0 1; color: #d6c49a; }
    TabbedContent { height: 1fr; }
    TabPane { padding: 0 1; }
    Tabs { height: 2; }
    Tab { padding: 0 2; }
    Underline { color: #a78bfa; }
    #chat-body { height: 1fr; }
    #conversation { width: 1fr; height: 1fr; }
    #welcome { height: 1fr; content-align: center middle; border: round #343244;
      color: #c4a7ff; padding: 1 2; }
    #chat-log { display: none; }
    RichLog { border: round #343244; height: 1fr; padding: 0 1; background: #14161e; }
    #context-sidebar { width: 30%; min-width: 32; max-width: 46; margin-left: 1;
      border: round #343244; padding: 1; }
    #context-sidebar Static { height: auto; margin-bottom: 1; }
    .panel-title { color: #c4a7ff; text-style: bold; }
    #sidebar-body { color: #a4a9bb; }
    #composer-bar { height: 1; margin-top: 1; color: #bfa2f5; }
    #usage-stats, #instance-usage { height: 1; color: #9398ab; }
    .hint { height: auto; color: #9398ab; }
    .buttons { height: 3; }
    .buttons Button { margin-right: 1; min-width: 8; height: 3; border: none;
      background: #252733; color: #d7d8e0; }
    .buttons Button.-primary { background: #65469c; color: #ffffff; }
    .buttons Button:hover { background: #443659; }
    Input { height: 3; border: round #464053; background: #191b25; }
    Input:focus { border: round #a78bfa; }
    DataTable { height: 50%; min-height: 9; border: round #343244;
      scrollbar-gutter: stable; }
    #task-progress { height: 2; }
    #task-actions Button { min-width: 6; padding: 0; }
    Screen.short-terminal #task-actions, Screen.short-terminal #task-actions Button { height: 1; }
    #extension-detail { height: 1fr; }
    #settings-fields { width: 100%; max-width: 100; padding: 1; }
    #settings-fields Static { height: auto; margin-top: 1; }
    #settings-fields Select { height: 3; }
    #reply-recovery { dock: bottom; height: 3; }
    #reply-recovery Button { width: 1fr; }
    Footer { background: #24202f; }
    """
    BINDINGS = [
        Binding("ctrl+q", "quit", "退出"),
        Binding("f1", "help", "帮助"),
        Binding("ctrl+o", "editor", "多行"),
        Binding("f2", "tab('extensions-tab')", "扩展", show=False),
        Binding("f3", "tab('tasks-tab')", "任务", show=False),
        Binding("f4", "tab('settings-tab')", "设置", show=False),
        Binding("f5", "reload", "刷新"),
        Binding("ctrl+1", "tab('chat-tab')", "对话", show=False),
        Binding("ctrl+2", "tab('tasks-tab')", "任务", show=False),
        Binding("ctrl+3", "tab('extensions-tab')", "扩展", show=False),
        Binding("ctrl+4", "tab('settings-tab')", "设置", show=False),
    ]

    def __init__(self, service: Any, *, refresh_seconds: float = 5) -> None:
        super().__init__()
        self.service = service
        self.refresh_seconds = max(2.0, refresh_seconds)
        self.status_text = "正在连接本机管理服务…"
        self.last_notice = "就绪"
        self.selected_task_id: str | None = None
        self.selected_extension_id: str | None = None
        self.history: list[Payload] = []
        self._chat_receipts: dict[int, Payload] = {}
        self._task_detail: Payload = {}
        self._task_consents: dict[str, str] = {}
        self._home: Screen[Any]
        self._usage: list[Payload] = []
        self._settings: Payload = {}
        self._settings_loaded = False
        self._compatible = False
        self._backend_capabilities: Payload = {}
        self._busy = False
        self._refreshing = False
        self._retry_handler: Handler | None = None
        self._operation_id: str | None = None
        self.mode = "ask"
        self._multiline_text: str | None = None
        self._multiline_preview = ""
        self._extension_count = 0
        self._task_count = 0
        self._rendered_task: Payload | None = None
        self._disclosure_mode = "manual"
        self._stream_question: str | None = None
        self._stream_text = ""
        self._stream_state = ""
        self._extensions: dict[str, Payload] = {}
        self._tasks: dict[str, Payload] = {}
        self._table_rows: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
        self._table_widths: dict[str, tuple[int, ...]] = {}

    def compose(self) -> ComposeResult:
        yield Static("PA / Personal Assistant   ·   本机 Agent 工作台", id="brandbar", markup=False)
        yield Static(self.status_text, id="system-status", markup=False)
        yield Static(self.last_notice, id="notice", markup=False)
        with TabbedContent(id="workbench-tabs"):
            with TabPane("对话", id="chat-tab"):
                with Horizontal(id="chat-body"):
                    with Vertical(id="conversation"):
                        yield Static(
                            "P E R S O N A L\nA S S I S T A N T\n\n"
                            "从一个问题或目标开始。\n"
                            "对话按需查阅实例资料，识别执行请求后交给 Agent。\n\n"
                            "/help 命令   ·   Ctrl+O 多行编辑",
                            id="welcome",
                            markup=False,
                        )
                        yield RichLog(id="chat-log", wrap=True, markup=False, highlight=False)
                    with Vertical(id="context-sidebar"):
                        yield Static("工作上下文", classes="panel-title")
                        yield Static("正在读取本机状态…", id="sidebar-body", markup=False)
                        yield Static("执行边界", classes="panel-title")
                        yield Static(
                            "使用已启用插件\n外部动作逐项审批\n未知结果不自动重试",
                            classes="hint",
                            markup=False,
                        )
                yield Static("对话 · 模型待读取", id="composer-bar", markup=False)
                yield Static("本会话 Token：尚无已确认用量", id="usage-stats", markup=False)
                yield Static("本实例模型用量：尚未读取", id="instance-usage", markup=False)
                yield Input(
                    placeholder="› 输入消息，或 /help 查看命令 · Ctrl+O 多行", id="chat-input"
                )
                with Horizontal(classes="buttons"):
                    yield Button("对话", id="ask", variant="primary")
                    yield Button("Agent 任务", id="publish-task")
                    yield Button("新对话", id="clear-chat")
            with TabPane("任务", id="tasks-tab"):
                yield DataTable(id="tasks-table", cursor_type="row", zebra_stripes=True)
                yield Static(
                    "↑↓ 浏览列表，Enter 选定任务后操作；下方显示任务详情。",
                    id="task-progress",
                    markup=False,
                )
                yield RichLog(id="task-detail", wrap=True, markup=False, highlight=False)
                yield Input(
                    placeholder="给选中任务补充消息（不会自动继续未决动作）", id="task-message"
                )
                with Horizontal(classes="buttons", id="task-actions"):
                    yield Button("追加消息", id="task-message-send")
                    yield Button("继续处理", id="task-resume")
                    yield Button("携原目标新建", id="task-recreate")
                    yield Button("模型披露", id="task-disclosure")
                    yield Button("上移", id="task-move-up")
                    yield Button("下移", id="task-move-down")
                    yield Button("置顶", id="task-move-top")
                    yield Button("移除", id="task-remove")
            with TabPane("扩展", id="extensions-tab"):
                yield DataTable(id="extensions-table", cursor_type="row", zebra_stripes=True)
                yield Static(
                    "选择扩展查看状态；安装前展示材料，再由你确认。",
                    id="extension-detail",
                    markup=False,
                )
                yield Input(placeholder="本机扩展材料目录", id="extension-source")
                with Horizontal(classes="buttons"):
                    yield Button("预览安装", id="install-preview", variant="primary")
                    yield Button("启用", id="extension-enable")
                    yield Button("禁用", id="extension-disable")
                    yield Button("卸载", id="extension-uninstall")
                    yield Button("配置", id="extension-config")
                    yield Button("应用配置", id="extension-apply-config")
            with TabPane("设置", id="settings-tab"), VerticalScroll(id="settings-fields"):
                yield Static(
                    "配置本机模型参数；保存后需重启对应服务。密钥不在终端表单显示。",
                    classes="hint",
                    markup=False,
                )
                yield Static("服务提供方 / 接口", markup=False)
                yield Static("尚未加载", id="model-endpoint", markup=False)
                yield Static("模型名称", markup=False)
                yield Input(placeholder="例如 deepseek-flash", id="model-name")
                yield Checkbox("使用 JSON 模式", id="model-json", value=True)
                yield Static("思考模式", markup=False)
                yield Select(
                    [("关闭", "disabled"), ("启用", "enabled")],
                    value="disabled",
                    allow_blank=False,
                    id="model-thinking",
                )
                yield Static("凭据状态尚未核对", id="model-credential", markup=False)
                with Horizontal(classes="buttons"):
                    yield Button("保存参数", id="save-settings", variant="primary")
                    yield Button("重新加载", id="reload-settings")
        with Horizontal(id="reply-recovery"):
            yield Button("重试原请求", id="retry-command", disabled=True)
            yield Button("放弃未完成回复", id="discard-reply", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        self._home = self.screen
        self._home.set_class(self.size.height < 30, "short-terminal")
        self._resize_tables()
        self._resize_sidebar()
        self.action_reload()
        self.set_interval(self.refresh_seconds, self.action_reload)

    def _resize_sidebar(self) -> None:
        if hasattr(self, "_home"):
            self._home.query_one("#context-sidebar").display = self.size.width >= 105

    def on_resize(self, event: events.Resize) -> None:
        if hasattr(self, "_home"):
            self._home.set_class(event.size.height < 30, "short-terminal")
            self._home.query_one("#context-sidebar").display = event.size.width >= 105
            self._resize_tables(event.size.width)

    def _update_chrome(self) -> None:
        mode = (
            "Agent 任务" if self.mode == "agent" else
            "对话 · 按需查资料 / 自动建任务" if self._disclosure_mode == "automatic" else
            "普通问答 · 手动披露模式"
        )
        model = self._settings.get("model", "未配置")
        working = " · 处理中" if self._busy else ""
        self._home.query_one("#composer-bar", Static).update(
            clean_text(
                f"{mode}  ·  本地模型配置 {model}（重启生效）"
                f" · {'直接发送' if self._disclosure_mode == 'automatic' else '逐次确认'}{working}"
            )
        )
        self._home.query_one("#ask", Button).variant = (
            "primary" if self.mode == "ask" else "default"
        )
        self._home.query_one("#publish-task", Button).variant = (
            "primary" if self.mode == "agent" else "default"
        )
        task = self._task_detail.get("task", {})
        projected = present_task_objective(str(task.get("objective", "尚未选择")))
        objective = (clean_text(projected.goal).splitlines() or ["未命名任务"])[0][:100]
        context = (
            f"任务列表  {self._task_count}\n扩展列表  {self._extension_count}\n\n"
            f"当前任务\n{objective}\n"
            f"{task.get('state', '')}\n\n"
            f"本会话 Token\n{self._usage_label(self._usage)}"
        )
        self._home.query_one("#sidebar-body", Static).update(clean_text(context))

    def composer_value(self) -> str:
        value = self._home.query_one("#chat-input", Input).value
        if self._multiline_text is not None and value == self._multiline_preview:
            return self._multiline_text
        return value

    def _set_composer(self, value: str) -> None:
        self._multiline_text = value if "\n" in value else None
        self._multiline_preview = value.replace("\n", " ↵ ")
        self._home.query_one("#chat-input", Input).value = self._multiline_preview

    def action_help(self) -> None:
        if self.screen is self._home:
            self.push_screen(HelpScreen())

    def action_editor(self) -> None:
        if self.screen is not self._home or self._busy or self.service.pending:
            return

        def edited(value: str | None) -> None:
            if value is not None:
                self._set_composer(value)
            self._home.query_one("#chat-input", Input).focus()

        self.push_screen(EditorScreen(self.composer_value()), edited)

    def _local_command(self) -> bool:
        value = self.composer_value().strip()
        if not value.startswith("/"):
            return False
        if self._busy or self.service.pending:
            self.notice("请先处理当前请求；本地输入已保留。")
            return True
        command = value.lower()
        if command in {"/ask", "/agent"}:
            self.mode = command[1:]
            self._set_composer("")
            self._update_chrome()
            self.notice("已切换模式；输入内容后按 Enter 发送。")
        elif command in {"/tasks", "/extensions", "/settings"}:
            self._set_composer("")
            self.action_tab(command[1:] + "-tab")
        elif command == "/help":
            self._set_composer("")
            self.action_help()
        elif command == "/usage":
            self._set_composer("")
            self.notice("本会话 Token：" + self._usage_label(self._usage))
        elif command == "/new":
            self._set_composer("")
            self._start("clear-chat")
        else:
            self.notice("未知命令，请输入 /help；未发送给模型。")
        return True

    async def on_unmount(self) -> None:
        await self.service.aclose()

    def action_tab(self, tab: str) -> None:
        self._home.query_one("#workbench-tabs", TabbedContent).active = tab

    def notice(self, message: str) -> None:
        self.last_notice = clean_text(message)
        self._home.query_one("#notice", Static).update(self.last_notice)

    def _buttons(self) -> None:
        pending = self.service.pending
        retryable = bool(pending and pending.get("retryable", True))
        self._home.query_one("#reply-recovery").display = bool(pending)
        for button in self._home.query(Button):
            if button.id == "retry-command":
                button.disabled = self._busy or not retryable or not self._compatible
                button.label = "重试原请求" if retryable else "结果未确认 · 请只读核对配置"
                button.display = bool(pending)
            elif button.id == "discard-reply":
                reply = bool(pending and pending.get("path") ==
                             "/admin/v1/workbench/conversation/stream")
                button.disabled = self._busy or not reply or not self._compatible
                button.display = reply
            else:
                disabled = self._busy or bool(pending)
                disabled |= not self._compatible and button.id not in {
                    "clear-chat", "save-settings", "reload-settings"
                }
                task = self._tasks.get(self.selected_task_id or "", {})
                if button.id and button.id.startswith("task-move-"):
                    disabled |= not self._queue_target_selected()
                    disabled |= not (
                        isinstance(task.get("queue_job_id"), str)
                        and type(task.get("queue_version")) is int
                        and not task.get("requires_reconciliation")
                    )
                elif button.id == "task-remove":
                    disabled |= not self._queue_target_selected()
                    disabled |= not self.selected_task_id or bool(
                        task.get("requires_reconciliation")
                    )
                button.disabled = disabled

    def action_reload(self) -> None:
        if not self._refreshing:
            self.run_worker(self._refresh(), name="read-status", exit_on_error=False)

    def _resize_tables(self, width: int | None = None) -> None:
        # Reserve tab/border/gutter space and cell padding. Keep names flexible,
        # with visible status/version columns even at an 80-column terminal.
        name_width = max(12, (self.size.width if width is None else width) - 44)
        definitions = (
            ("tasks-table", ("任务", "状态", "顺序"), (name_width, 24, 6)),
            ("extensions-table", ("扩展", "版本", "状态"), (name_width, 6, 24)),
        )
        for identifier, labels, widths in definitions:
            if self._table_widths.get(identifier) == widths:
                continue
            table = self._home.query_one("#" + identifier, DataTable)
            previous = self._table_position(table)
            rows = self._table_rows.get(identifier, [])
            with self.batch_update():
                table.clear(columns=True)
                for label, width in zip(labels, widths, strict=True):
                    table.add_column(label, width=width)
                for key, cells in rows:
                    table.add_row(
                        *(Text(cell, no_wrap=True, overflow="ellipsis") for cell in cells), key=key
                    )
                self._restore_table_position(table, previous)
            self._table_widths[identifier] = widths

    @staticmethod
    def _table_position(table: DataTable[Any]) -> tuple[str | None, int, str | None, float]:
        keys = list(table.rows)
        cursor, top = table.cursor_row, int(table.scroll_y)
        return (
            str(keys[cursor].value) if 0 <= cursor < len(keys) else None,
            cursor,
            str(keys[top].value) if 0 <= top < len(keys) else None,
            table.scroll_y,
        )

    @staticmethod
    def _restore_table_position(
        table: DataTable[Any], previous: tuple[str | None, int, str | None, float]
    ) -> None:
        keys = [str(key.value) for key in table.rows]
        if not keys:
            return
        cursor_key, cursor_row, top_key, offset = previous
        row = keys.index(cursor_key) if cursor_key in keys else min(cursor_row, len(keys) - 1)
        table.move_cursor(row=max(0, row), animate=False, scroll=False)
        y = keys.index(top_key) + offset % 1 if top_key in keys else offset
        # DataTable's cursor watcher can scroll after its dimensions settle.
        # Restore the visible row after that refresh, never force it to selection.
        table.call_after_refresh(
            table.scroll_to, x=0, y=y, animate=False, immediate=True, force=True
        )

    def _sync_table(self, identifier: str, rows: list[tuple[str, tuple[str, ...]]]) -> None:
        previous_rows = self._table_rows.get(identifier, [])
        if previous_rows == rows:
            return
        table = self._home.query_one("#" + identifier, DataTable)
        if [key for key, _ in previous_rows] == [key for key, _ in rows]:
            for (key, cells), (_, old_cells) in zip(rows, previous_rows, strict=True):
                for column, cell, old in zip(table.columns, cells, old_cells, strict=True):
                    if cell != old:
                        table.update_cell(
                            key, column, Text(cell, no_wrap=True, overflow="ellipsis")
                        )
        else:
            position = self._table_position(table)
            with self.batch_update():
                table.clear()
                for key, cells in rows:
                    table.add_row(
                        *(Text(cell, no_wrap=True, overflow="ellipsis") for cell in cells), key=key
                    )
                self._restore_table_position(table, position)
        self._table_rows[identifier] = rows

    async def _ensure_compatibility(self) -> None:
        try:
            self._backend_capabilities = await self.service.negotiate()
        except Exception:
            self._compatible = False
            raise
        self._compatible = True

    async def _load_local_settings(self) -> None:
        if self._settings_loaded:
            return
        try:
            self._settings = await self.service.settings()
            self._show_settings()
            self._settings_loaded = True
        except Exception as error:
            code = clean_text(getattr(error, "code", type(error).__name__))
            self._home.query_one("#model-endpoint", Static).update(
                f"本地配置未读取（{code}）；可修正文件后重新加载。"
            )

    async def _refresh(self) -> None:
        if self._refreshing:
            return
        self._refreshing = True
        try:
            await self._load_local_settings()
            await self._ensure_compatibility()
            status = await self.service.status()
            self._disclosure_mode = status.get("model_disclosure_mode", "manual")
            worker = (
                "已冻结 · 不领取任务"
                if status.get("frozen")
                else "Worker 在线"
                if status.get("worker_online")
                else "Worker 未连接 · 等待执行器"
            )
            self.status_text = f"已连接本机服务  |  {worker}  |  {status.get('storage', '')}"
            tasks = await self.service.tasks()
            self._task_count = len(tasks)
            self._tasks = {item["id"]: item for item in tasks}
            self._sync_table(
                "tasks-table",
                [
                    (
                        task["id"],
                        (
                            clean_text(
                                present_task_objective(str(task.get("objective", task["id"]))).goal
                            ).replace("\n", " "),
                            "待对账（外部结果未确认）"
                            if task.get("requires_reconciliation")
                            else clean_text(task.get("state", "")),
                            str(task.get("queue_position") or "—"),
                        ),
                    )
                    for task in tasks
                ],
            )
            extensions = await self.service.extensions()
            self._extensions = {item["id"]: item for item in extensions}
            self._extension_count = len(extensions)
            self._sync_table(
                "extensions-table",
                [
                    (
                        item["id"],
                        (
                            clean_text(item["id"]),
                            clean_text(item.get("version", item.get("active_version", ""))),
                            clean_text(
                                "未安装（已发现源码）"
                                if item.get("lifecycle_state", item.get("state")) == "DISCOVERED"
                                else item.get("lifecycle_state", item.get("state", ""))
                            ),
                        ),
                    )
                    for item in extensions
                ],
            )
            if self.selected_extension_id and hasattr(self.service, "extension_config"):
                await self._show_extension_config(self.selected_extension_id)
            if self.selected_task_id:
                await self._open_task(self.selected_task_id)
            if hasattr(self.service, "usage"):
                usage = await self.service.usage()
                label = self._usage_label([usage])
                if usage.get("missing_usage_calls"):
                    label += "（仅已知用量；部分调用未提供统计）"
                self._home.query_one("#instance-usage", Static).update(
                    "本实例审计已完成调用（最多5000次） · " + label
                )
            if self._operation_id:
                operation = await self.service.operation(self._operation_id)
                state = operation.get("status", "PENDING")
                if state in {"SUCCEEDED", "FAILED"}:
                    self.notice(self._operation_notice(self._operation_id, operation))
                    self._operation_id = None
        except Exception as error:
            code = clean_text(getattr(error, "code", type(error).__name__))
            if code == "WORKBENCH_INCOMPATIBLE":
                self.status_text = (
                    "工作台与后台接口不兼容，请重启更新后的后台；本地配置和输入已保留。"
                )
            elif not self._compatible:
                self.status_text = f"后台暂不可达或接口检查未完成（{code}）；本地配置和输入已保留。"
            else:
                self.status_text = (
                    f"服务不可用或部分读取失败（{code}）；已有内容可能过期，输入已保留。"
                )
            reference = getattr(error, "correlation_id", None)
            if reference:
                self.status_text += f" 关联编号 {reference}"
        finally:
            self._refreshing = False
            self._home.query_one("#system-status", Static).update(clean_text(self.status_text))
            self._buttons()
            self._update_chrome()

    @staticmethod
    def _usage_label(items: list[Payload]) -> str:
        if not items:
            return "尚无已确认用量"
        values = []
        for name, label in (
            ("prompt_tokens", "输入"),
            ("completion_tokens", "输出"),
            ("total_tokens", "合计"),
        ):
            numbers = [item.get(name) for item in items]
            known = [value for value in numbers if type(value) is int and value >= 0]
            if len(known) == len(items):
                values.append(f"{label} {sum(known)}")
            elif known:
                values.append(f"{label} 已知 {sum(known)}（其余未提供）")
            else:
                values.append(f"{label} 未提供")
        return " / ".join(values)

    def _show_usage(self) -> None:
        self._home.query_one("#usage-stats", Static).update(
            "本会话 Token：" + self._usage_label(self._usage)
        )

    def _show_settings(self) -> None:
        value = self._settings
        provider = value.get("provider", value.get("provider_id", ""))
        self._home.query_one("#model-endpoint", Static).update(
            clean_text(f"{provider}  {value.get('base_url', '')}")
        )
        self._home.query_one("#model-name", Input).value = str(value.get("model", ""))
        self._home.query_one("#model-json", Checkbox).value = bool(value.get("json_mode", True))
        self._home.query_one("#model-thinking", Select).value = value.get("thinking", "disabled")
        self._home.query_one("#model-credential", Static).update(
            "密钥已配置（只保存安全引用）"
            if value.get("credential_configured")
            else "未配置密钥，请先在本机运行 configure-model.py。"
        )

    def _queue_target_selected(self) -> bool:
        table = self._home.query_one("#tasks-table", DataTable)
        keys = list(table.rows)
        row = table.cursor_row
        return bool(
            self.selected_task_id
            and self._task_detail.get("task", {}).get("id") == self.selected_task_id
            and 0 <= row < len(keys)
            and keys[row].value == self.selected_task_id
        )

    @on(DataTable.RowHighlighted, "#tasks-table")
    def task_highlighted(self, event: DataTable.RowHighlighted) -> None:
        # Arrow navigation is not selection. Do not operate on an old detail
        # while another row is highlighted, and do not start I/O during refresh.
        self._buttons()

    @on(DataTable.RowSelected)
    def select_row(self, event: DataTable.RowSelected) -> None:
        identifier = str(event.row_key.value)
        if event.data_table.id == "tasks-table":
            self.selected_task_id = identifier
            self._task_detail = {}
            self.run_worker(self._load_selection(identifier), exit_on_error=False)
        else:
            self.selected_extension_id = identifier
            self._home.query_one("#extension-detail", Static).update(clean_text(identifier))

    async def _load_selection(self, identifier: str) -> None:
        try:
            await self._ensure_compatibility()
            await self._open_task(identifier)
        except Exception as error:
            if getattr(error, "code", None) == "WORKBENCH_INCOMPATIBLE":
                self.notice("工作台与后台接口不兼容，请重启更新后的后台。")
            else:
                self.notice("无法读取任务：" + clean_text(getattr(error, "code", "读取失败")))
            self._buttons()

    async def _open_task(self, identifier: str) -> None:
        detail = await self.service.task(identifier)
        if self.selected_task_id != identifier:
            return
        self._task_detail = detail
        task = detail["task"]
        progress = f"当前任务 {identifier} · {self._task_state_label(task)}"
        if hasattr(self.service, "execution"):
            execution = await self.service.execution(identifier)
            if identifier != self.selected_task_id:
                return
            trace = execution.get("trace") or {}
            phase = trace.get("phase", execution.get("phase", execution.get("state", "")))
            tool = trace.get("pending_tool")
            phase_label = (
                f"等待领取（上次阶段：{phase}）" if task.get("state") == "QUEUED" and phase
                else phase
            )
            progress = f"当前任务 {identifier} · {self._task_state_label(task)} · {phase_label}"
            toolset = trace.get("toolset")
            if isinstance(toolset, dict) and toolset.get("message"):
                progress += "\n" + str(toolset["message"])
            dispatch_failure = failure_diagnostic(trace.get("failure_diagnostic"))
            if dispatch_failure is not None:
                progress += (
                    f"\n{dispatch_failure['code']}：{dispatch_failure['message']}"
                    f" · 关联编号 {dispatch_failure['correlation_id']}"
                )
            failure = trace.get("validation_failure")
            if isinstance(failure, dict):
                diagnostic = validation_diagnostic(failure.get("code"))
                if diagnostic is not None:
                    progress += f" · {diagnostic['code']}：{diagnostic['message']}"
            if isinstance(tool, dict):
                tool_name = tool.get("tool_id", "")
                extension_name = tool.get("extension_id", "")
                risk = tool.get("risk", "")
                progress += f" · 工具 {extension_name}/{tool_name} [{risk}]"
            if execution.get("model_disclosure"):
                progress += " · 等待模型披露许可"
            if execution.get("pending_input"):
                progress += " · 需要补充信息"
        self._home.query_one("#task-progress", Static).update(clean_text(progress))
        self._update_chrome()
        self._buttons()
        if self._rendered_task == detail:
            return
        log = self._home.query_one("#task-detail", RichLog)
        reading_history = not log.is_vertical_scroll_end
        position = log.scroll_y
        same_task = self._rendered_task is not None and (
            self._rendered_task.get("task", {}).get("id") == identifier
        )
        self._rendered_task = detail
        keep_position = same_task and reading_history
        log.clear()
        log.write(
            Text(clean_text(f"{task['id']}  {task.get('state')}  v{task.get('version')}")),
            scroll_end=not keep_position,
        )
        log.write(
            Text(task_objective_text(task.get("objective", ""))), scroll_end=not keep_position
        )
        for message in detail.get("messages", []):
            role = message.get("role", message.get("actor", "消息"))
            log.write(
                Text(clean_text(f"\n{role}: {message.get('content', '')}")),
                scroll_end=not keep_position,
            )
        if keep_position:
            log.scroll_to(y=position, animate=False, immediate=True, force=True)

    @on(Input.Submitted, "#chat-input")
    def submit_question(self) -> None:
        if not self._local_command():
            self._start("publish-task" if self.mode == "agent" else "ask")

    @on(Button.Pressed)
    def button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id:
            self._start(event.button.id)

    def _start(self, action: str) -> None:
        allowed = {
            "ask",
            "publish-task",
            "clear-chat",
            "retry-command",
            "discard-reply",
            "task-message-send",
            "task-resume",
            "task-recreate",
            "task-disclosure",
            "task-move-up",
            "task-move-down",
            "task-move-top",
            "task-remove",
            "install-preview",
            "extension-enable",
            "extension-disable",
            "extension-uninstall",
            "extension-config",
            "extension-apply-config",
            "save-settings",
            "reload-settings",
        }
        if action not in allowed:
            return
        if action in {"ask", "publish-task"} and self._local_command():
            return
        if self._busy:
            return
        if self.service.pending:
            if not self.service.pending.get("retryable", True):
                self.notice("配置保存结果未确认，不能安全重发；请重开工作台只读核对配置。")
                return
            if action not in {"retry-command", "discard-reply"}:
                self.notice("上一请求结果未确认，请先重试原请求；没有创建新命令。")
                return
        if action in {"ask", "publish-task"}:
            self.mode = "agent" if action == "publish-task" else "ask"
        self._busy = True
        self._update_chrome()
        self.notice("正在处理… 输入已保留，请等待回执。")
        self._buttons()
        self.run_worker(self._perform(action), name=action, exit_on_error=False)

    async def _perform(self, action: str) -> None:
        try:
            if action not in {"clear-chat", "save-settings", "reload-settings"}:
                await self._ensure_compatibility()
            if action == "retry-command":
                handler = self._retry_handler
                result = await self.service.retry_pending()
                self._retry_handler = None
                if handler:
                    await handler(result)
                else:
                    self.notice("原请求已确认；请刷新查看当前状态。")
            elif action == "discard-reply":
                handler = self._retry_handler
                try:
                    result = await self.service.discard_pending_reply()
                except Exception:
                    if self.service.pending is None:
                        self._retry_handler = None
                    raise
                self._retry_handler = None
                if handler:
                    await handler(result)
                else:
                    self.notice("原回复已完成，已保留其成功回执。")
            elif action == "clear-chat":
                self.history.clear()
                self._chat_receipts.clear()
                self._stream_question, self._stream_text, self._stream_state = None, "", ""
                self._usage.clear()
                self._show_usage()
                self._home.query_one("#chat-log", RichLog).clear()
                self._home.query_one("#chat-log").display = False
                self._home.query_one("#welcome").display = True
                self.notice("已开始新对话。")
            elif action in {"ask", "publish-task"}:
                await self._send_chat(action)
            elif action.startswith("task-"):
                await self._task_action(action)
            elif action == "install-preview":
                source = self._home.query_one("#extension-source", Input).value.strip()
                if not source:
                    raise ValueError("source")
                await self._call(
                    self.service.inspect_extension(source, self._key()), self._confirm_install
                )
            elif action.startswith("extension-"):
                await self._extension_action(action.removeprefix("extension-"))
            elif action == "reload-settings":
                changed = self._settings_loaded and (
                    self._home.query_one("#model-name", Input).value
                    != self._settings.get("model", "")
                    or self._home.query_one("#model-json", Checkbox).value
                    != self._settings.get("json_mode", True)
                    or self._home.query_one("#model-thinking", Select).value
                    != self._settings.get("thinking", "disabled")
                )
                if changed and not await self.push_screen_wait(
                    ConfirmScreen(
                        "重新加载模型参数", "放弃当前尚未保存的参数，读取本机最新配置？", "重新加载"
                    )
                ):
                    return
                self._settings = await self.service.settings()
                self._settings_loaded = True
                self._show_settings()
                self.notice("已重新加载本机配置；不会重启后台。")
            elif action == "save-settings":
                await self._save_settings()
        except asyncio.CancelledError:
            if self._stream_question is not None:
                self._stream_state = "未完成 · 已停止接收，原请求结果待核对"
                self._render_chat()
            raise
        except Exception as error:
            if self._stream_question is not None:
                self._stream_state = (
                    "未完成 · 回复已停止，片段不计入对话上下文"
                    if getattr(error, "uncertain", None) is False else
                    "未完成 · 回复未确认，不计入对话上下文"
                )
                self._render_chat()
            code = clean_text(getattr(error, "code", "操作失败"))
            message = clean_text(getattr(error, "message", "请核对输入或刷新状态后重试。"))
            if code == "WORKBENCH_INCOMPATIBLE":
                self.notice("工作台与后台接口不兼容，请重启更新后的后台；原请求和输入仍保留。")
            elif self.service.pending:
                if self.service.pending.get("retryable", True):
                    self.notice(f"结果未确认（{code}）；输入已保留，请使用“重试原请求”。")
                else:
                    self.notice(
                        f"配置保存结果未确认（{code}），不能安全重发；请重开工作台只读核对配置。"
                    )
            else:
                self.notice(f"{code}：{message}")
            reference = getattr(error, "correlation_id", None)
            if reference:
                self.notice(self.last_notice + f" · 关联编号 {reference}")
        finally:
            self._busy = False
            self._buttons()
            self._update_chrome()

    @staticmethod
    def _key() -> str:
        return "tui-" + uuid4().hex

    async def _call(self, request: Awaitable[Payload], handler: Handler) -> None:
        try:
            result = await request
        except BaseException:
            if self.service.pending:
                self._retry_handler = handler
            raise
        await handler(result)

    async def _confirm_remote(self, screen: ConfirmScreen) -> bool:
        accepted = await self.push_screen_wait(screen)
        if accepted:
            # The user may leave a confirmation open while the backend restarts.
            # Recheck before creating the final command or consuming an input.
            await self._ensure_compatibility()
        return bool(accepted)

    async def _send_chat(self, action: str) -> None:
        text = self.composer_value().strip()
        if not text:
            self.notice("请输入内容。")
            return
        if action == "publish-task":
            objective = text
            if self.history:
                context = [
                    {"role": item["role"], "content": item["content"]}
                    for item in self.history
                ]
                objective += (
                    "\n\n--- 参考对话（完整历史，仅作上下文） ---\n"
                    "历史助手发言不表示动作已经执行，也不构成新的动作授权。\n"
                    + json.dumps(context, ensure_ascii=False, indent=2)
                )
            if len(objective) > 10_000:
                self.notice("任务目标和参考对话超过10000字符；请精简输入或开始新对话，未创建任务。")
                return

            async def created(result: Payload) -> None:
                self.selected_task_id = result["id"]
                if self.composer_value().strip() == text:
                    self._set_composer("")
                self.notice(
                    f"任务已入队 {result['id']} · {result.get('state', 'QUEUED')}，尚未完成。"
                )
                await self._open_task(result["id"])
                self.action_tab("tasks-tab")
                self.action_reload()

            await self._call(self.service.create_task(objective, self._key()), created)
            return
        history = [dict(item) for item in self.history]
        if len(text) > 8000 or any(
            item["role"] == "user" and len(item["content"]) > 8000 for item in history
        ):
            self.notice("用户消息超过8000字符输入限制；请精简输入，未发送。")
            return
        if len(history) > 20 or sum(len(item["content"]) for item in history) + len(text) > 64000:
            self.notice("完整历史超过20条或64000字符输入限制；请开始新对话，未截断或发送。")
            return

        async def answered(result: Payload) -> None:
            answer = str(result["answer"])
            usage = result.get("usage")
            self._usage.append(usage if isinstance(usage, dict) else {})
            self._show_usage()
            self.history.extend(
                [{"role": "user", "content": text}, {"role": "assistant", "content": answer}]
            )
            self._chat_receipts[len(self.history) - 1] = {
                name: result[name] for name in ("task", "sources", "warnings") if name in result
            }
            self._stream_question, self._stream_text, self._stream_state = None, "", ""
            self._render_chat()
            if self.composer_value().strip() == text:
                self._set_composer("")
            task = result.get("task")
            if isinstance(task, dict):
                identifier = str(task["id"])
                self.selected_task_id = identifier
                self._task_detail = {}
                self.notice(
                    f"已确认创建任务 {identifier}（创建时状态 {task.get('state', 'QUEUED')}）；"
                    "最新执行状态见侧栏和任务页。"
                )
                try:
                    await self._open_task(identifier)
                except Exception:
                    self.notice(f"已创建任务 {identifier}；暂时无法读取最新状态，请刷新查看。")
                self.action_reload()
            else:
                self.notice("对话已返回；未创建执行任务。")

        async def delta(value: str) -> None:
            self._stream_text += value
            self._render_chat()

        async def send(preview_hash: str | None = None) -> None:
            self._stream_question, self._stream_text = text, ""
            self._stream_state = "正在回复…"
            self._render_chat()
            request = (
                self.service.conversation_stream(text, history, self._key(), on_delta=delta)
                if self._disclosure_mode == "automatic" else
                self.service.chat_stream(
                    text, history, self._key(), preview_hash=preview_hash, on_delta=delta
                )
            )
            await self._call(request, answered)

        if self._disclosure_mode == "automatic":
            await send()
            return

        async def previewed(preview: Payload) -> None:
            accepted = await self._confirm_remote(
                ConfirmScreen("确认发送给模型的内容", document(preview), "允许并提问")
            )
            if accepted:
                await send(preview["preview_hash"])
            else:
                self.notice("已取消，未调用模型。")

        await self._call(self.service.chat_preview(text, history, self._key()), previewed)

    def _render_chat(self) -> None:
        """Redraw the in-flight answer without appending duplicate partial messages."""
        log = self._home.query_one("#chat-log", RichLog)
        following = log.is_vertical_scroll_end
        position = log.scroll_y
        self._home.query_one("#welcome").display = False
        log.display = True
        log.clear()
        for index, item in enumerate(self.history):
            if item["role"] == "user":
                log.write(
                    Text(clean_text("你  › " + item["content"]), style="bold #c4a7ff"),
                    scroll_end=False,
                )
            else:
                log.write(Text("助手", style="bold #a1c9ba"), scroll_end=False)
                log.write(safe_markdown(item["content"]), scroll_end=False)
                receipt = self._chat_receipts.get(index, {})
                task = receipt.get("task")
                if isinstance(task, dict):
                    log.write(Text(clean_text(
                        f"已创建任务 {task.get('id', '')} · {task.get('state', '')}（创建回执）"
                        "；最新执行状态见侧栏 / 任务页。"
                    )), scroll_end=False)
                for source in receipt.get("sources", []):
                    locator = json.dumps(source.get("locator", {}), ensure_ascii=False)
                    log.write(Text(clean_text(
                        f"资料来源：{source.get('source_uri', '')} · {locator}"
                    )), scroll_end=False)
                for warning in receipt.get("warnings", []):
                    log.write(Text(clean_text("检索提示：" + warning)), scroll_end=False)
            log.write(Text(""), scroll_end=False)
        if self._stream_question is not None:
            log.write(
                Text(clean_text("你  › " + self._stream_question), style="bold #c4a7ff"),
                scroll_end=False,
            )
            log.write(Text("助手 · " + self._stream_state, style="bold #a1c9ba"), scroll_end=False)
            log.write(safe_markdown(self._stream_text), scroll_end=False)
        if following:
            log.scroll_end(animate=False, immediate=True, force=True)
        else:
            log.scroll_to(y=position, animate=False, immediate=True, force=True)

    def _task_state_label(self, task: Payload) -> str:
        if self._tasks.get(str(task.get("id", "")), {}).get("requires_reconciliation"):
            return "待对账 · 外部结果未确认，不能移除或重新执行"
        state = str(task.get("state", ""))
        descriptions = {
            "WAITING_USER": "等待补充信息，不占用执行器",
            "WAITING_APPROVAL": "等待审批，不占用执行器",
            "WAITING_RECONCILIATION": "等待外部结果核对，不自动重试",
            "PAUSED_EXTENSION": "扩展不可用，已暂停",
            "PAUSED_SAFETY": "安全熔断，已暂停",
            "FAILED": "已失败，停止执行",
            "CANCELLED": "已取消，停止执行",
        }
        description = descriptions.get(state)
        return f"{state} · {description}" if description else state

    async def _task_action(self, action: str) -> None:
        identifier = self.selected_task_id
        if not identifier:
            self.notice("请先选中一项任务。")
            return
        if (
            action.startswith("task-move-") or action == "task-remove"
        ) and not self._queue_target_selected():
            self.notice("请按 Enter 选定当前高亮任务、读完详情后再操作。")
            return
        task = self._task_detail.get("task", {})
        if task.get("id") != identifier:
            self.notice("任务详情尚未读取，请稍候再操作。")
            return
        version = task["version"]

        async def received(result: Payload) -> None:
            self.notice("请求已接受；请以任务状态为准。")
            await self._open_task(identifier)

        if (action.startswith("task-move-") or action == "task-remove") and self._tasks.get(
            identifier, {}
        ).get("requires_reconciliation"):
            self.notice("外部结果待核对，任务继续保留；不能移除或调整次序。")
            return
        if action == "task-recreate":
            if not hasattr(self.service, "execution"):
                self.notice("后台不支持工具快照检查，不能通过此按钮新建。")
                return
            execution = await self.service.execution(identifier)
            toolset = (execution.get("trace") or {}).get("toolset") or {}
            if toolset.get("can_recreate") is not True:
                self.notice("原任务仍在活动或外部结果待核对，不能携原目标重新执行。")
                return
            objective = task.get("objective")
            if not isinstance(objective, str) or not objective.strip():
                self.notice("原目标不可用，请刷新后核对。")
                return
            if not await self._confirm_remote(ConfirmScreen(
                "携原目标创建新任务", task_objective_text(objective) + "\n\n"
                "新任务使用当前工具目录；原任务、审批和未决动作均保留。", "创建新任务",
            )):
                return
            fresh = (await self.service.task(identifier)).get("task", {})
            execution = await self.service.execution(identifier)
            toolset = (execution.get("trace") or {}).get("toolset") or {}
            if (fresh.get("version") != version or fresh.get("objective") != objective
                    or toolset.get("can_recreate") is not True):
                self.notice("原任务状态已变化，不能继续此新建操作；请刷新核对。")
                return
            async def created(result: Payload) -> None:
                self.selected_task_id = str(result["id"])
                self._task_detail = {}
                self.notice("新任务已排队；原任务保持不变，请以执行状态为准。")
                await self._open_task(self.selected_task_id)
                self.action_reload()
            await self._call(self.service.create_task(objective, self._key()), created)
            return
        if action.startswith("task-move-"):
            queued = self._tasks.get(identifier, {})
            job_id, job_version = queued.get("queue_job_id"), queued.get("queue_version")
            if not isinstance(job_id, str) or type(job_version) is not int:
                self.notice("仅等待领取的任务可以调整次序；当前状态请刷新核对。")
                return

            async def moved(result: Payload) -> None:
                self.notice("队列次序已更新；正在执行的任务不会被抢占。")
                await self._refresh()

            await self._call(
                self.service.move_task(
                    identifier, job_id, job_version, action.removeprefix("task-move-"), self._key()
                ),
                moved,
            )
        elif action == "task-remove":
            if not await self._confirm_remote(
                ConfirmScreen(
                    "移除选中任务",
                    clean_text(task.get("objective", identifier)) + "\n\n"
                    "取消此任务后续执行，并从工作台列表移除；历史、审批和审计记录保留。"
                    "外部结果尚未确认时会拒绝移除，不能用此操作跳过核对。",
                    "确认移除",
                )
            ):
                return

            async def removed(result: Payload) -> None:
                if self.selected_task_id == identifier:
                    self.selected_task_id = None
                    self._task_detail = {}
                    self._rendered_task = None
                    self._home.query_one("#task-detail", RichLog).clear()
                    self._home.query_one("#task-progress", Static).update(
                        "任务已移除；历史记录保留。"
                    )
                await self._refresh()
                if self._tasks.get(identifier, {}).get("requires_reconciliation"):
                    if self.selected_task_id is None:
                        self.selected_task_id = identifier
                        await self._open_task(identifier)
                    self.notice("任务的外部结果待核对，仍保留在列表中。")
                else:
                    self.notice("任务已从工作台移除，历史记录保留。")

            await self._call(self.service.remove_task(identifier, version, self._key()), removed)
        elif action == "task-message-send":
            field = self._home.query_one("#task-message", Input)
            content = field.value.strip()
            if not content:
                self.notice("请输入补充消息。")
                return

            async def added(result: Payload) -> None:
                if field.value.strip() == content:
                    field.value = ""
                await received(result)

            await self._call(
                self.service.add_message(identifier, content, version, self._key()), added
            )
        elif action == "task-resume":
            await self._call(
                self.service.resume_task(
                    identifier, version, self._key(), consent_id=self._task_consents.get(identifier)
                ),
                received,
            )
        elif action == "task-disclosure":

            async def approved(result: Payload) -> None:
                self.notice("披露许可已保存。需要时再次选择继续处理。")
                consent = result.get("id", result.get("consent_id"))
                if isinstance(consent, str):
                    self._task_consents[identifier] = consent

            async def previewed(preview: Payload) -> None:
                accepted = await self._confirm_remote(
                    ConfirmScreen("确认本次任务的模型披露", document(preview), "允许披露")
                )
                if accepted:
                    await self._call(
                        self.service.disclosure_confirm(
                            identifier,
                            preview.get("task_version", version),
                            preview["preview_hash"],
                            self._key(),
                        ),
                        approved,
                    )

            await self._call(
                self.service.disclosure_preview(identifier, version, self._key()), previewed
            )

    async def _confirm_install(self, preview: Payload) -> None:
        accepted = await self._confirm_remote(
            ConfirmScreen(
                "安装/升级精确材料 — 代码将以本机用户权限运行", document(preview), "确认安装"
            )
        )
        if accepted:
            await self._call(self.service.install_extension(preview, self._key()), self._operation)
        else:
            self.notice("安装已取消，未执行材料。")

    @staticmethod
    def _configuration_label(value: Payload) -> str:
        application = value.get("application") or {}
        state = application.get("state", "UNAVAILABLE")
        labels = {
            "PENDING": "已保存，待生效：点击应用配置；未启动副本会在下次启动时加载。",
            "APPLYING": "应用中：等待各运行副本确认新配置，在途操作结束后切换。",
            "APPLIED": "已生效：运行副本已确认。目录变更后的索引需单独重建。",
            "FAILED": "配置应用失败：查看副本错误码；修正配置后应用，临时启动故障请重启后台。",
            "UNAVAILABLE": "后台未提供配置应用状态，请检查后台版本。",
        }
        text = labels.get(state, "配置状态未确认。")
        for runtime in application.get("runtimes", []):
            if isinstance(runtime, dict) and runtime.get("error_code"):
                text += "\n" + str(runtime["error_code"])
        return clean_text(text)

    async def _show_extension_config(self, identifier: str) -> None:
        try:
            value = await self.service.extension_config(identifier)
            detail = self._configuration_label(value)
        except Exception as error:
            detail = "配置状态无法读取：" + clean_text(getattr(error, "code", "CONFIG_UNAVAILABLE"))
        if self.selected_extension_id == identifier:
            self._home.query_one("#extension-detail", Static).update(
                clean_text(identifier) + "\n" + detail
            )

    async def _extension_action(self, action: str) -> None:
        identifier = self.selected_extension_id
        if not identifier:
            self.notice("请先选择扩展。")
            return
        item = self._extensions.get(identifier, {})
        if action == "enable" and item.get("lifecycle_state", item.get("state")) == "DISCOVERED":
            self.notice(
                "此扩展尚未安装：填写源码路径，先预览并确认安装，再启用；没有发送启用请求。"
            )
            return
        if action == "apply-config":
            value = await self.service.extension_config(identifier)
            application = value.get("application") or {}
            digest = application.get("saved_digest")
            version = value.get("extension_version")
            if not isinstance(digest, str) or not isinstance(version, str):
                self.notice("后台不支持应用配置，请重启更新后的后台。")
                return
            async def applied(result: Payload) -> None:
                self.notice(self._configuration_label(result))
                self.action_reload()
            await self._call(
                self.service.apply_extension_config(identifier, version, digest, self._key()),
                applied,
            )
            return
        if action == "config":
            value = await self.service.extension_config(identifier)
            current = value.get("config", value.get("values", {}))
            if not isinstance(current, dict):
                self.notice("扩展配置格式不可编辑，请查看本机管理页面。")
                return
            edited = await self.push_screen_wait(ConfigScreen(identifier, current))
            if edited is None:
                return
            accepted = await self._confirm_remote(
                ConfirmScreen("确认保存扩展配置", document(edited), "保存配置")
            )
            if accepted:

                async def saved(result: Payload) -> None:
                    self.notice("已保存；本次没有重启扩展。" + self._configuration_label(result))

                await self._call(
                    self.service.save_extension_config(identifier, edited, self._key()), saved
                )
            return
        accepted = await self._confirm_remote(
            ConfirmScreen(
                "确认扩展操作", f"{identifier}\n操作：{action}\n卸载默认保留数据。", "确认"
            )
        )
        if accepted:
            await self._call(
                self.service.extension_action(identifier, action, self._key()), self._operation
            )

    @staticmethod
    def _operation_notice(identifier: object, receipt: Payload) -> str:
        state = receipt.get("status", "PENDING")
        code = receipt.get("diagnostic_code")
        detail = f" · {code}" if code else ""
        if code == "NOT_INSTALLED":
            detail += "：尚未安装，请先预览并确认安装。"
        return clean_text(f"扩展操作 {identifier}：{state}{detail}")

    async def _operation(self, receipt: Payload) -> None:
        identifier = receipt.get("id", receipt.get("operation_id"))
        state = receipt.get("status", "PENDING")
        self.notice(self._operation_notice(identifier, receipt))
        if identifier and state not in {"SUCCEEDED", "FAILED"}:
            self._operation_id = str(identifier)
        self.action_reload()

    async def _save_settings(self) -> None:
        fields = {
            "model": self._home.query_one("#model-name", Input).value.strip(),
            "json_mode": self._home.query_one("#model-json", Checkbox).value,
            "thinking": self._home.query_one("#model-thinking", Select).value,
            "revision": self._settings.get("revision"),
        }
        accepted = await self.push_screen_wait(
            ConfirmScreen("保存本机模型参数", document(fields) + "\n需重启对应服务后生效。", "保存")
        )
        if not accepted:
            return

        async def saved(result: Payload) -> None:
            self._settings = result
            self._settings_loaded = False
            self.notice("参数已保存；需手动重启对应服务，本次没有重启或解冻。")

        await self._call(self.service.save_settings(fields, self._key()), saved)
