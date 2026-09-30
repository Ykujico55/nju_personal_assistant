"""One-login real ehall route and synthetic volunteer form trial; never submit."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from personal_assistant.ehall_interactive_entry import ENTRY_URL
from personal_assistant.infrastructure.browser.driver import InteractiveHeadedBrowser
from personal_assistant.infrastructure.browser.interactive import (
    FIELD_SELECTOR,
    EhallInteractiveFlow,
)

ROUTE = (
    "第二课堂",
    "第二课堂成绩单（在线办理）",
    "志愿服务经历（申请）",
)
SAMPLE_FIELDS = (
    ("名称", "自动化测试请勿提交"),
    ("开始时间", "2026-09-27 09:00"),
    ("结束时间", "2026-09-28"),
    ("团队职务", "无"),
    ("服务时长", "1.0"),
    ("审核学院", "文学院"),
)
FORM_PATH = "/tw/xssq/xssq/create"


def _key(label: str) -> str:
    return re.sub(r"[\s:：*＊]+", "", label).casefold()


def _reviewer_option(options: tuple[str, ...]) -> str:
    for option in options:
        label = option.strip()
        if label and not label.startswith("请选择"):
            return label
    raise RuntimeError("审核人下拉未出现实际选项；已停在提交前")


async def drive_trial(
    flow: Any, *, progress: Callable[[str], None] | None = None
) -> dict[str, str]:
    """Follow the observed route and fill each field top to bottom."""

    kwargs = {"progress": progress} if progress is not None else {}
    if progress is not None:
        progress(f"正在进入：{ROUTE[0]}")
    await flow.open_service(ROUTE[0], **kwargs)
    if progress is not None:
        progress(f"正在进入：{ROUTE[1]}")
    for attempt in range(40):
        if await flow.has_visible_text("志愿服务经历"):
            break
        try:
            await flow.open_service(
                ROUTE[1], current_page_only=True, catalog=False,
                timeout_seconds=15, **kwargs
            )
            break
        except TimeoutError:
            if await flow.has_visible_text("志愿服务经历"):
                break
            if progress is not None and attempt == 0:
                progress("在线办理页暂未出现；若显示 500，请在 Chromium 中刷新。")
    else:
        raise TimeoutError("没有进入第二课堂成绩单在线办理页")
    if progress is not None:
        progress(f"正在进入：{ROUTE[2]}")
    await flow.open_scoped_action("志愿服务经历", "申请", **kwargs)
    planned: dict[str, str] = {}
    for label, value in SAMPLE_FIELDS:
        if progress is not None:
            progress(f"正在填写：{label}")
        await flow.fill({label: value})
        planned[label] = value
    if progress is not None:
        progress("正在读取审核人选项")
    reviewer = _reviewer_option(await flow.inspect_options("审核人"))
    if progress is not None:
        progress("正在填写：审核人")
    await flow.fill({"审核人": reviewer})
    planned["审核人"] = reviewer
    return planned


async def read_visible_values(
    flow: EhallInteractiveFlow, labels: tuple[str, ...]
) -> dict[str, str]:
    """Read only the seven visible field values for local equality checks."""

    fields = await flow.observe_fields()
    values: dict[str, str] = {}
    for label in labels:
        matches = [field for field in fields if _key(field.label) == _key(label)]
        if len(matches) != 1:
            raise RuntimeError(f"字段“{label}”未唯一出现；已停在提交前")
        field = matches[0]
        element = flow.page.frames[field.frame_index].locator(FIELD_SELECTOR).nth(
            field.dom_index
        )
        try:
            values[label] = str(await element.input_value(timeout=3000)).strip()
        except Exception:
            values[label] = str(await element.inner_text(timeout=3000)).strip()
    return values


def matching_visible_values(
    planned: Mapping[str, str], visible: Mapping[str, str]
) -> bool:
    return bool(planned) and all(visible.get(label) == value for label, value in planned.items())


async def submit_button_visible(flow: EhallInteractiveFlow) -> bool:
    count = 0
    for frame in flow.page.frames:
        buttons = frame.get_by_role("button", name="提交", exact=True)
        for index in range(min(await buttons.count(), 10)):
            count += bool(await buttons.nth(index).is_visible())
    return count == 1


def _write_report(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


async def run(out: Path) -> int:
    browser = InteractiveHeadedBrowser()
    report: dict[str, Any] = {
        "mode": "direct-auto-route-synthetic",
        "captured_at": datetime.now(UTC).isoformat(),
        "route": list(ROUTE),
        "one_browser_context": True,
        "navigation_completed": False,
        "all_fields_visible_and_match": False,
        "submit_button_visible": False,
        "user_verified": False,
        "submitted": False,
        "supervised_gateway_chain": False,
    }
    try:
        report["stage"] = "browser_start"
        await browser.start()
        report["stage"] = "portal_navigation"
        await browser.page.goto(ENTRY_URL, wait_until="domcontentloaded")
        print(
            "Chromium 已打开。请只在其中完成一次统一认证；"
            "助手会自动进入志愿服务申请表。",
            flush=True,
        )
        flow = EhallInteractiveFlow(browser.context, browser.page)
        report["stage"] = "route_and_fill"
        planned = await drive_trial(flow, progress=print)
        report["navigation_completed"] = True
        report["form_frame_count"] = sum(
            urlsplit(str(frame.url)).path == FORM_PATH for frame in flow.page.frames
        )
        visible = await read_visible_values(flow, tuple(planned))
        report["field_matches"] = {
            label: visible.get(label) == value for label, value in planned.items()
        }
        report["all_fields_visible_and_match"] = matching_visible_values(
            planned, visible
        )
        report["submit_button_visible"] = await submit_button_visible(flow)
        report["filled_field_labels"] = list(planned)
        report["sample_values"] = {
            label: value if label != "审核人" else "[页面选项已选中]"
            for label, value in planned.items()
        }
        _write_report(out, report)
        print("已填到最终“提交”按钮前；脚本不会点击该按钮。", flush=True)
        print(f"脱敏报告：{out}", flush=True)
        reply = await asyncio.to_thread(
            input, "请在 Chromium 核对七项；输入 ok 表示均正常，其他输入将记为未通过："
        )
        report["user_verified"] = reply.strip().casefold() == "ok"
        _write_report(out, report)
        await asyncio.to_thread(input, "按回车关闭浏览器（不要点“提交”）：")
        return int(
            not (
                report["all_fields_visible_and_match"]
                and report["submit_button_visible"]
                and report["form_frame_count"] == 1
                and report["user_verified"]
            )
        )
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        _write_report(out, report)
        print(
            f"试填未完成：{report['stage']} / {type(exc).__name__}。"
            f"未点击“提交”；报告：{out}",
            flush=True,
        )
        return 1
    finally:
        await browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return asyncio.run(run(args.out))


if __name__ == "__main__":
    raise SystemExit(main())
