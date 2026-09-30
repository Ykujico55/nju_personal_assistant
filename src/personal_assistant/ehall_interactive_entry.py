"""Direct interactive ehall command, with a local click before final submission."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from personal_assistant.core.approvals.canonicalize import canonical_sha256
from personal_assistant.core.browser import (
    compute_page_fingerprint,
    page_structure_document,
)
from personal_assistant.infrastructure.browser.driver import InteractiveHeadedBrowser
from personal_assistant.infrastructure.browser.interactive import (
    EhallInteractiveFlow,
    ObservedField,
    parse_instruction,
    sample_values,
    split_scoped_action_step,
)

ENTRY_URL = "https://ehall.nju.edu.cn/ywtb-portal/official/index.html#/home/official_home"


def _safe_capture_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, "", parts.fragment.split("?", 1)[0])
    )


def _redacted_capture(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Record page shape and fingerprint without values or URL query tokens."""

    structure = raw.get("structure")
    if not isinstance(structure, Mapping):
        raise ValueError("页面结构缺失，未生成采集文件")
    controls = [
        {**item, "value": ""}
        for item in structure.get("controls", ())
        if isinstance(item, Mapping)
    ]
    forms = [
        {**item, "action": _safe_capture_url(str(item.get("action", "")))}
        for item in structure.get("forms", ())
        if isinstance(item, Mapping)
    ]
    links = [
        {**item, "path": _safe_capture_url(str(item.get("path", "")))}
        for item in structure.get("links", ())
        if isinstance(item, Mapping)
    ]
    headings = [item for item in structure.get("headings", ()) if isinstance(item, Mapping)]
    hidden_fields = [
        item for item in structure.get("hidden_fields", ()) if isinstance(item, Mapping)
    ]
    document = page_structure_document(
        controls=controls,
        headings=headings,
        links=links,
        forms=forms,
        hidden_fields=hidden_fields,
    )
    fingerprint = compute_page_fingerprint(document)
    return {
        "url": _safe_capture_url(str(raw.get("url", ""))),
        "fingerprint": fingerprint,
        "structure_hashes": {
            key: canonical_sha256(document.get(key, []))
            for key in ("controls", "headings", "links", "forms", "hidden_fields")
        },
        "controls": [
            {
                "locator": item.get("locator", ""),
                "tag": item.get("tag", ""),
                "type": item.get("type", ""),
                "name": item.get("name", ""),
                "required": item.get("required", False),
                "readonly": item.get("readonly", False),
                "disabled": item.get("disabled", False),
                "option_count": len(item.get("options", ())),
            }
            for item in controls
        ],
        "actions": [
            {
                "locator": item.get("locator", ""),
                "label": item.get("label", ""),
                "kind": item.get("kind", ""),
                "tag": item.get("tag", ""),
                "html_type": item.get("html_type", ""),
            }
            for item in raw.get("actions", ())
            if isinstance(item, Mapping)
        ],
        "forms": forms,
        "hidden_fields": [
            {
                "type": item.get("type", ""),
                "name": item.get("name", ""),
            }
            for item in hidden_fields
        ],
    }


async def _capture_live_page(
    browser: InteractiveHeadedBrowser,
    flow: EhallInteractiveFlow,
    fields: tuple[ObservedField, ...],
    output_path: Path,
) -> None:
    frames: list[dict[str, Any]] = []
    for index, frame in enumerate(flow.page.frames):
        source = flow.page if index == 0 else frame
        captured = _redacted_capture(await browser.snapshot_page(source))
        captured["frame_index"] = index
        frames.append(captured)
    report = {
        "captured_at": datetime.now(UTC).isoformat(),
        "service": flow.service,
        "supervised": False,
        "frames": frames,
        "visible_fields": [
            {
                "frame_index": item.frame_index,
                "label": item.label,
                "kind": item.kind,
                "fillable": item.fillable,
            }
            for item in fields
        ],
        "filled": False,
        "submitted": False,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


async def _show_field_inventory(
    flow: EhallInteractiveFlow,
) -> tuple[ObservedField, ...]:
    fields = await flow.wait_for_fields()
    print(
        f"当前可见字段候选：{len(fields)} 个；"
        f"可直接填写：{sum(field.fillable for field in fields)} 个。"
    )
    for field in fields:
        status = "可填写" if field.fillable else "需手动操作或适配"
        print(f"- {field.label} [{field.kind}，{status}]")
    return fields


async def _show_combobox_options(
    flow: EhallInteractiveFlow, fields: tuple[ObservedField, ...]
) -> None:
    for field in fields:
        if field.kind != "combobox":
            continue
        try:
            options = await flow.inspect_options(field.label)
        except Exception as exc:
            print(f"下拉选项“{field.label}”探测失败（{type(exc).__name__}）")
            continue
        shown = "、".join(options[:12])
        if len(options) > 12:
            shown += "、…"
        print(f"下拉选项“{field.label}”：{shown or '当前无可见选项'}")


async def run_ehall(
    raw_instruction: str | None,
    *,
    sample: bool = False,
    capture_out: Path | None = None,
) -> int:
    if raw_instruction is None:
        print("示例：办理 在读证明申请；填写 申请理由=毕业用途；提交按钮=提交")
        try:
            raw_instruction = input("请输入办理指令：").strip()
        except EOFError:
            return 1
    try:
        instruction = parse_instruction(raw_instruction)
    except ValueError as exc:
        print(f"指令错误：{exc}")
        return 1
    if capture_out is not None and (sample or instruction.fields):
        print("结构采集不能与试填或字段填写同时运行")
        return 1
    browser = InteractiveHeadedBrowser()
    try:
        await browser.start()
        await browser.page.goto(ENTRY_URL, wait_until="domcontentloaded")
        print("浏览器已打开 ehall。若出现统一认证，请在 Chromium 中完成登录。")
        print("登录后将依次进入：" + " → ".join(instruction.route))
        flow = EhallInteractiveFlow(browser.context, browser.page)
        for index, step in enumerate(instruction.route):
            scoped_action = split_scoped_action_step(step) if index > 0 else None
            if scoped_action is None:
                await flow.open_service(
                    step,
                    progress=print,
                    current_page_only=index > 0,
                    catalog=index == 0,
                )
            else:
                await flow.open_scoped_action(
                    *scoped_action,
                    progress=print,
                )
            print(f"已进入“{step}”。")

        observed = await _show_field_inventory(flow)

        if capture_out is not None:
            await _capture_live_page(browser, flow, observed, capture_out)
            print(f"已保存脱敏页面结构：{capture_out}")
            with contextlib.suppress(EOFError):
                input("浏览器保持打开；检查完页面后按回车关闭：")
            return 0

        if sample:
            controls = await flow.controls()
            values, skipped = sample_values(controls)
            values.update(instruction.fields)
            skipped = tuple(label for label in skipped if label not in instruction.fields)
            filled: list[str] = []
            failed: list[str] = []
            if values:
                for field, value in values.items():
                    try:
                        await flow.fill({field: value})
                    except Exception as exc:
                        failed.append(field)
                        print(f"试填失败：{field}（{type(exc).__name__}）")
                    else:
                        filled.append(field)
                if filled:
                    print("已试填：" + "、".join(filled))
                print("请在 ehall 页面核对测试值；本模式不点击提交。")
            else:
                print("没有可确定的文字/人数字段，未填写任何内容。")
            if skipped:
                print("未自动填写：" + "、".join(skipped[:30]))
            await _show_combobox_options(flow, observed)
            with contextlib.suppress(EOFError):
                prompt = "在页面调整字段后输入 r 重扫字段和选项；按回车关闭："
                while input(prompt).strip().casefold() == "r":
                    observed = await _show_field_inventory(flow)
                    await _show_combobox_options(flow, observed)
            return 1 if failed else 0

        values = dict(instruction.fields)
        if not values:
            controls = await flow.controls()
            labels = "、".join(item.label or item.name for item in controls)
            print(f"当前可填写字段：{labels or '无'}")
            extra = input("输入 字段=值；字段=值（直接回车只打开页面）：").strip()
            if extra:
                values = parse_instruction(f"办理 {instruction.service}；{extra}").fields
        if values:
            await flow.fill(values)
            print("表单已填写。请在浏览器内检查，随后点击新标签页的确认按钮。")
            submitted = await flow.confirm_and_submit(instruction.submit_label)
            if submitted:
                print("已点击 ehall 页面上的提交按钮；请在页面查看结果或回执。")
            else:
                print("已取消，没有点击提交按钮。")
        else:
            print("未填写或提交表单。")
        with contextlib.suppress(EOFError):
            input("浏览器保持打开；检查完页面后按回车关闭：")
        return 0
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        print(f"ehall 操作未完成：{exc}")
        return 1
    finally:
        await browser.close()
