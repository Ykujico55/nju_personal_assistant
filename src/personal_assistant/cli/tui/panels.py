"""Terminal-only helpers; reference-inspired interactions, no external commands."""

from __future__ import annotations

import re

from rich.console import Console, ConsoleOptions, RenderResult
from rich.markdown import Markdown
from rich.theme import Theme
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static, TextArea

_CONTROL = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")


_MARKDOWN_THEME = Theme(
    {
        **{f"markdown.h{level}": "bold #c4a7ff" for level in range(1, 7)},
        "markdown.paragraph": "#d7d8e0",
        "markdown.text": "#d7d8e0",
        "markdown.link": "underline #bca7ef",
        "markdown.link_url": "#bca7ef",
        "markdown.block_quote": "#a1c9ba",
    }
)


class TerminalMarkdown(Markdown):
    """Keep readable colors independent of the terminal's inherited theme."""

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        with console.use_theme(_MARKDOWN_THEME):
            yield from super().__rich_console__(console, options)


def safe_markdown(value: str) -> Markdown:
    text = _CONTROL.sub("", value)
    text = "".join(c for c in text if c in "\n\t" or (ord(c) >= 32 and not 127 <= ord(c) < 160))
    return TerminalMarkdown(text, hyperlinks=False, code_theme="monokai", style="#d7d8e0")


class HelpScreen(ModalScreen[None]):
    CSS = """
    HelpScreen { align: center middle; background: #0c0d13 80%; }
    #help-panel { width: 76; max-width: 95%; height: 22; max-height: 95%;
      border: round #a78bfa; padding: 1 2; background: #171923; }
    #command-help { height: 1fr; }
    """
    BINDINGS = [Binding("escape", "close", "返回"), Binding("f1", "close", "返回")]

    def compose(self) -> ComposeResult:
        with Vertical(id="help-panel"):
            yield Static("工作台命令", classes="panel-title")
            yield Static(
                "/ask          切到对话（自动配置下按需检索/建任务）\n"
                "/agent        切到 Agent 任务模式\n"
                "/tasks        任务与执行状态\n"
                "/extensions   扩展与配置\n"
                "/settings     模型参数\n"
                "/usage        查看已确认 Token\n"
                "/new          新对话\n"
                "/help         本帮助\n\n"
                "Enter 发送当前模式 · Ctrl+O 多行编辑\n"
                "F2 扩展 · F3 任务 · F4 设置 · F5 刷新\n"
                "模式切换不执行任务；外部动作仍需精确审批。",
                id="command-help",
                markup=False,
            )
            yield Button("返回输入", id="help-close", variant="primary")

    @on(Button.Pressed, "#help-close")
    def action_close(self) -> None:
        self.dismiss(None)


class EditorScreen(ModalScreen[str | None]):
    CSS = """
    EditorScreen { align: center middle; background: #0c0d13 80%; }
    #editor-panel { width: 90%; height: 80%; border: round #a78bfa;
      padding: 1 2; background: #171923; }
    #message-editor { height: 1fr; }
    #editor-actions { height: 3; }
    """
    BINDINGS = [Binding("escape", "cancel", "取消"), Binding("ctrl+enter", "apply", "保留文字")]

    def __init__(self, value: str) -> None:
        super().__init__()
        self.value = value

    def compose(self) -> ComposeResult:
        with Vertical(id="editor-panel"):
            yield Static("编辑消息 · 保留文字后再发送", classes="panel-title", markup=False)
            yield TextArea(self.value, id="message-editor", soft_wrap=True)
            with Horizontal(id="editor-actions"):
                yield Button("取消", id="editor-cancel")
                yield Button("保留文字", id="editor-apply", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#message-editor", TextArea).focus()

    @on(Button.Pressed, "#editor-cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#editor-apply")
    def action_apply(self) -> None:
        self.dismiss(self.query_one("#message-editor", TextArea).text)
