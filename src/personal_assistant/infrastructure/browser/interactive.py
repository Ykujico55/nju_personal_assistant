"""Interactive ehall service navigation, form fill, and local click confirmation."""

from __future__ import annotations

import asyncio
import contextlib
import html
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from personal_assistant.infrastructure.browser.driver import is_transient_frame_error

CONTROL_SELECTOR = (
    "input:not([type='hidden']):not([type='password']), textarea, select, "
    "[contenteditable='true']"
)
FIELD_SELECTOR = (
    "input:not([type='hidden']):not([type='password']), textarea, select, "
    "[contenteditable='true'], [role='textbox'], [role='combobox'], "
    "[role='spinbutton'], [role='checkbox'], [role='radio'], [role='listbox'], "
    "label[for], .bh-form-label, .el-form-item__label, "
    ".ant-form-item-label, .form-label"
)
OPTION_SELECTOR = (
    "[role='option'], [role='listbox'] li, .bh-select-list li, "
    ".el-select-dropdown__item, .ant-select-item-option"
)
SCOPED_OPTION_SELECTOR = f"{OPTION_SELECTOR}, li, [role='menuitem']"
POPUP_FIELD_ANCESTOR = (
    "[role='listbox'], [role='option'], .jqx-combobox-popup, "
    ".jqx-listbox, .bh-select-list, .el-select-dropdown, .ant-select-dropdown"
)
SEARCH_SELECTOR = (
    "input[placeholder*='搜索'], input[placeholder*='服务'], "
    "input[placeholder*='事项'], input[placeholder*='关键字'], input[type='search']"
)


@dataclass(frozen=True, slots=True)
class Instruction:
    service: str
    fields: dict[str, str]
    submit_label: str = "提交"
    route: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Control:
    frame_index: int
    index: int
    kind: str
    label: str
    name: str
    placeholder: str
    value: str
    element_id: str = ""
    options: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ObservedField:
    """One visible field candidate; never carry a page value into the inventory."""

    frame_index: int
    label: str
    kind: str
    fillable: bool
    dom_index: int = -1


@dataclass(frozen=True, slots=True)
class _VisibleOption:
    frame_index: int
    index: int
    label: str


def parse_instruction(text: str) -> Instruction:
    """Accept a service request followed by optional ``field=value`` clauses."""

    clauses = [part.strip() for part in re.split(r"[；;]", text) if part.strip()]
    if not clauses:
        raise ValueError("请输入要打开的事务名称")
    service = re.sub(r"^(?:请帮我|帮我|我想|我要|请)?\s*(?:打开|办理)\s*", "", clauses[0]).strip()
    if not service:
        raise ValueError("指令中没有事务名称")
    route = tuple(re.split(r"\s*(?:->|>|→)\s*", service))
    if not all(route):
        raise ValueError("事务导航步骤不能为空")
    fields: dict[str, str] = {}
    submit_label = "提交"
    for clause in clauses[1:]:
        clause = re.sub(r"^填写\s*", "", clause)
        if "=" not in clause and "＝" not in clause:
            raise ValueError(f"字段指令应使用 字段=值：{clause}")
        key, value = re.split(r"[=＝]", clause, maxsplit=1)
        key, value = key.strip(), value.strip()
        if not key:
            raise ValueError("字段名不能为空")
        if key == "提交按钮":
            submit_label = value
        else:
            fields[key] = value
    if not submit_label:
        raise ValueError("提交按钮名称不能为空")
    return Instruction(service, fields, submit_label, route)


def split_scoped_action_step(step: str) -> tuple[str, str] | None:
    """Recognize an item followed by the button to click inside its own row."""

    match = re.fullmatch(r"(.+?)\s*[（(](申请|办理|查看|进入)[）)]", step.strip())
    if match is None:
        return None
    return match.group(1).strip(), match.group(2)


def _normalized(value: str) -> str:
    return re.sub(r"[\s:：*＊]+", "", value).casefold()


def match_control(field: str, controls: tuple[Control, ...]) -> Control:
    wanted = _normalized(field)
    if not wanted:
        raise ValueError("字段名不能为空")
    scored: list[tuple[float, Control]] = []
    for control in controls:
        keys = (
            control.label,
            control.name,
            control.placeholder,
            control.element_id,
        )
        scores: list[float] = []
        for key in keys:
            candidate = _normalized(key)
            if not candidate:
                continue
            if candidate == wanted:
                scores.append(100)
            elif wanted in candidate or candidate in wanted:
                scores.append(75)
            else:
                scores.append(SequenceMatcher(None, wanted, candidate).ratio() * 60)
        if scores and max(scores) >= 35:
            scored.append((max(scores), control))
    if not scored:
        available = "、".join(item.label or item.name for item in controls)
        raise ValueError(f"找不到字段“{field}”；当前可填写字段：{available}")
    scored.sort(key=lambda item: item[0], reverse=True)
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        raise ValueError(f"字段“{field}”对应多个控件，请使用更具体的字段名")
    return scored[0][1]


def sample_values(controls: tuple[Control, ...]) -> tuple[dict[str, str], tuple[str, ...]]:
    """Build a small local trial fill without guessing identity or booking choices."""

    values: dict[str, str] = {}
    skipped: list[str] = []
    for control in controls:
        label = control.label or control.name or control.element_id
        kind = control.kind.lower()
        value = ""
        if not control.value.strip() and kind in {"text", "number", "textarea"}:
            if any(word in label for word in ("事由", "用途", "理由", "说明", "备注", "主题")):
                value = "测试填写（未提交）"
            elif "人数" in label:
                value = "10"
            elif "教室容量" in label:
                value = "30"
            elif "教室借用数量" in label:
                value = "1"
        if value and label and label not in values:
            try:
                if match_control(label, controls) == control:
                    values[label] = value
                    continue
            except ValueError:
                pass
        skipped.append(label or f"未命名{kind}控件")
    return values, tuple(skipped)


async def _visible_text(
    pages: list[Any], label: str, *, exact: bool, limit: int
) -> tuple[Any, Any] | None:
    for page in pages:
        for frame in page.frames:
            try:
                candidates = frame.get_by_text(label, exact=exact)
                for index in range(min(await candidates.count(), limit)):
                    item = candidates.nth(index)
                    if await item.is_visible():
                        return page, item
            except Exception as exc:
                if not is_transient_frame_error(exc):
                    raise
                # SSO and SPA navigation can detach a frame between listing it
                # and querying it. The next poll reads the replacement frame.
    return None


async def _visible_search(pages: list[Any]) -> Any | None:
    for page in pages:
        for frame in page.frames:
            try:
                candidates = frame.locator(SEARCH_SELECTOR)
                for index in range(min(await candidates.count(), 10)):
                    item = candidates.nth(index)
                    if await item.is_visible():
                        return item
            except Exception as exc:
                if not is_transient_frame_error(exc):
                    raise
    return None


class EhallInteractiveFlow:
    def __init__(self, context: Any, portal_page: Any) -> None:
        self.context = context
        self.page = portal_page
        self.service = ""
        self.applied: dict[str, str] = {}

    async def has_visible_text(self, label: str) -> bool:
        """Check the current tab, including its frames, without changing the page."""

        if self.page.is_closed():
            return False
        return await _visible_text([self.page], label, exact=True, limit=30) is not None

    async def open_service(
        self,
        name: str,
        *,
        timeout_seconds: float = 900,
        progress: Any = None,
        current_page_only: bool = False,
        catalog: bool = True,
    ) -> Any:
        """Wait through manual SSO, click the named portal card, and follow its tab."""

        deadline = asyncio.get_running_loop().time() + timeout_seconds
        started = asyncio.get_running_loop().time()
        target: Any = None
        target_page: Any = None
        catalog_opened = False
        searched = False
        last_notice = started
        while asyncio.get_running_loop().time() < deadline:
            if current_page_only:
                pages = [self.page] if not self.page.is_closed() else []
            else:
                pages = [page for page in reversed(self.context.pages) if not page.is_closed()]
            found = await _visible_text(pages, name, exact=True, limit=30)
            if found is None:
                found = await _visible_text(pages, name, exact=False, limit=30)
            if found is not None:
                target_page, target = found
            if target is not None:
                break
            elapsed = asyncio.get_running_loop().time() - started
            if catalog and not catalog_opened and elapsed >= 2:
                catalog_target = await _visible_text(
                    pages, "全部服务", exact=True, limit=10
                )
                if catalog_target is not None:
                    page, link = catalog_target
                    old_pages = set(self.context.pages)
                    try:
                        await link.click()
                    except Exception as exc:
                        if not is_transient_frame_error(exc):
                            raise
                    else:
                        if progress is not None:
                            progress("已登录大厅，正在进入全部服务列表…")
                        opened = [p for p in self.context.pages if p not in old_pages]
                        self.page = opened[-1] if opened else page
                        catalog_opened = True
            if not searched and elapsed >= 3:
                search = await _visible_search(pages)
                if search is not None:
                    try:
                        await search.fill(name)
                        await search.press("Enter")
                    except Exception as exc:
                        if not is_transient_frame_error(exc):
                            raise
                    else:
                        if progress is not None:
                            progress(f"正在搜索事务“{name}”…")
                        searched = True
            now = asyncio.get_running_loop().time()
            if progress is not None and now - last_notice >= 10:
                progress(f"仍在查找“{name}”（已打开 {len(pages)} 个标签页）…")
                last_notice = now
            await asyncio.sleep(1)
        if target is None:
            raise TimeoutError(f"没有在大厅找到事务“{name}”")
        old_pages = set(self.context.pages)
        await target.click()
        popup: Any = None
        for _ in range(50):
            opened = [page for page in self.context.pages if page not in old_pages]
            if opened:
                popup = opened[-1]
                break
            await asyncio.sleep(0.1)
        self.page = popup or target_page
        with contextlib.suppress(Exception):
            await self.page.wait_for_load_state("domcontentloaded", timeout=15000)
        await asyncio.sleep(0.5)
        self.service = name
        return self.page

    async def open_scoped_action(
        self,
        item_label: str,
        action_label: str,
        *,
        timeout_seconds: float = 900,
        progress: Any = None,
    ) -> Any:
        """Click one named action in the closest row containing the named item."""

        deadline = asyncio.get_running_loop().time() + timeout_seconds
        last_notice = asyncio.get_running_loop().time()
        target: Any = None
        target_page: Any = None
        while asyncio.get_running_loop().time() < deadline:
            matches: list[tuple[Any, Any]] = []
            pages = [page for page in reversed(self.context.pages) if not page.is_closed()]
            for page in pages:
                for frame in page.frames:
                    try:
                        items = frame.get_by_text(item_label, exact=True)
                        for index in range(min(await items.count(), 30)):
                            item = items.nth(index)
                            if not await item.is_visible():
                                continue
                            row = item.locator("xpath=ancestor::tr[1]")
                            if await row.count() == 1:
                                actions = row.get_by_text(action_label, exact=True)
                                visible = [
                                    actions.nth(action_index)
                                    for action_index in range(min(await actions.count(), 30))
                                    if await actions.nth(action_index).is_visible()
                                ]
                                if len(visible) == 1:
                                    matches.append((page, visible[0]))
                                    continue
                            ancestor = item
                            for _ in range(4):
                                ancestor = ancestor.locator("xpath=..")
                                buttons: list[Any] = []
                                for role in ("button", "link"):
                                    actions = ancestor.get_by_role(
                                        role, name=action_label, exact=True
                                    )
                                    for action_index in range(
                                        min(await actions.count(), 30)
                                    ):
                                        action = actions.nth(action_index)
                                        if await action.is_visible():
                                            buttons.append(action)
                                if len(buttons) == 1:
                                    matches.append((page, buttons[0]))
                                    break
                    except Exception as exc:
                        if not is_transient_frame_error(exc):
                            raise
            if len(matches) > 1:
                raise ValueError(f"“{item_label}”对应多个“{action_label}”按钮")
            if matches:
                target_page, target = matches[0]
                break
            now = asyncio.get_running_loop().time()
            if progress is not None and now - last_notice >= 10:
                progress(f"仍在查找“{item_label}”对应的“{action_label}”按钮…")
                last_notice = now
            await asyncio.sleep(1)
        if target is None or target_page is None:
            raise TimeoutError(f"找不到“{item_label}”对应的“{action_label}”按钮")
        old_pages = set(self.context.pages)
        await target.click()
        self.page = target_page
        for _ in range(50):
            opened = [page for page in self.context.pages if page not in old_pages]
            if opened:
                self.page = opened[-1]
                break
            await asyncio.sleep(0.1)
        with contextlib.suppress(Exception):
            await self.page.wait_for_load_state("domcontentloaded", timeout=15000)
        self.service = item_label
        return self.page

    async def controls(self) -> tuple[Control, ...]:
        items: list[Control] = []
        for frame_index, frame in enumerate(self.page.frames):
            raw = await frame.locator(CONTROL_SELECTOR).evaluate_all(
                """(elements, popupSelector) => {
                  const rowLabel = el => {
                    const cell = el.closest('td, th');
                    const row = cell?.closest('tr');
                    if (!row) return '';
                    const cells = [...row.children].filter(x => x.matches('td, th'));
                    for (let i = cells.indexOf(cell) - 1; i >= 0; i--) {
                      if (cells[i].querySelector('input, select, textarea, [role="combobox"]'))
                        continue;
                      const candidate = (cells[i].innerText || cells[i].textContent || '').trim();
                      if (candidate && candidate.length <= 100)
                        return candidate.replace(/[\\s:：*＊]+$/g, '').trim();
                    }
                    return '';
                  };
                  const siblingLabel = el => {
                    const fieldRect = el.getBoundingClientRect();
                    let node = el;
                    for (let depth = 0; node?.parentElement && depth < 5; depth++) {
                      const siblings = [...node.parentElement.children];
                      for (let i = siblings.indexOf(node) - 1; i >= 0; i--) {
                        const sibling = siblings[i];
                        if (sibling.querySelector('input, select, textarea, '
                            + '[role="combobox"]')) continue;
                        const rect = sibling.getBoundingClientRect();
                        if (!rect.width || !rect.height || rect.right > fieldRect.left + 16
                            || Math.abs((rect.top + rect.bottom) / 2
                              - (fieldRect.top + fieldRect.bottom) / 2)
                              > Math.max(36, fieldRect.height * 0.6)) continue;
                        const candidate = (sibling.innerText || sibling.textContent || '')
                          .trim().replace(/[\\s:：*＊]+$/g, '').trim();
                        if (candidate && candidate.length <= 100) return candidate;
                      }
                      node = node.parentElement;
                    }
                    return '';
                  };
                  return elements.map((el, index) => {
                  const type = (el.getAttribute('type') ||
                    (el.tagName === 'INPUT' ? 'text' : el.tagName)).toLowerCase();
                  const transient = Boolean(el.closest(popupSelector));
                  const group = el.closest('.bh-form-group, .form-group, '
                    + '.el-form-item, .ant-form-item');
                  const label = Array.from(el.labels || []).map(x => x.innerText.trim()).join(' ')
                    || el.getAttribute('aria-label')
                    || group?.querySelector('label, .bh-form-label, '
                      + '.el-form-item__label, .ant-form-item-label, .form-label')
                      ?.textContent?.trim()
                    || rowLabel(el)
                    || siblingLabel(el)
                    || el.getAttribute('placeholder') || '';
                  return {index, type, label, name: el.getAttribute('name') || '',
                    id: el.id || '', placeholder: el.getAttribute('placeholder') || '',
                    value: transient || type === 'file' ? '' : (el.value || el.textContent || ''),
                    transient,
                    disabled: el.disabled || el.readOnly || !el.getClientRects().length,
                    options: el.tagName === 'SELECT' ? Array.from(el.options).map(o =>
                      [o.label, o.value]) : []};
                });
                }""",
                POPUP_FIELD_ANCESTOR,
            )
            for item in raw:
                if (
                    item["disabled"]
                    or item["transient"]
                    or item["type"] in {"submit", "button", "reset"}
                ):
                    continue
                items.append(
                    Control(
                        frame_index,
                        int(item["index"]),
                        str(item["type"]),
                        str(item["label"]).strip(),
                        str(item["name"]),
                        str(item["placeholder"]),
                        str(item["value"]),
                        str(item["id"]),
                        tuple((str(label), str(value)) for label, value in item["options"]),
                    )
                )
        return tuple(items)

    async def observe_fields(self) -> tuple[ObservedField, ...]:
        """Read visible native, ARIA, and labeled custom field candidates."""

        items: list[ObservedField] = []
        for frame_index, frame in enumerate(self.page.frames):
            try:
                raw = await frame.locator(FIELD_SELECTOR).evaluate_all(
                    """(elements, popupSelector) => {
                      const controls = 'input:not([type="hidden"]):not([type="password"]),'
                        + 'textarea, select, [contenteditable="true"], [role="textbox"],'
                        + '[role="combobox"], [role="spinbutton"], [role="checkbox"],'
                        + '[role="radio"], [role="listbox"]';
                      const visible = el => Boolean(el.getClientRects().length)
                        && el.getAttribute('aria-hidden') !== 'true';
                      const text = el => (el?.textContent || '').trim();
                      const rowLabel = el => {
                        const cell = el.closest('td, th');
                        const row = cell?.closest('tr');
                        if (!row) return '';
                        const cells = [...row.children].filter(x => x.matches('td, th'));
                        for (let i = cells.indexOf(cell) - 1; i >= 0; i--) {
                          if (cells[i].querySelector('input, select, textarea, '
                            + '[role="combobox"]')) continue;
                          const candidate = (cells[i].innerText || cells[i].textContent
                            || '').trim();
                          if (candidate && candidate.length <= 100)
                            return candidate.replace(/[\\s:：*＊]+$/g, '').trim();
                        }
                        return '';
                      };
                      const siblingLabel = el => {
                        const fieldRect = el.getBoundingClientRect();
                        let node = el;
                        for (let depth = 0; node?.parentElement && depth < 5; depth++) {
                          const siblings = [...node.parentElement.children];
                          for (let i = siblings.indexOf(node) - 1; i >= 0; i--) {
                            const sibling = siblings[i];
                            if (sibling.querySelector('input, select, textarea, '
                                + '[role="combobox"]')) continue;
                            const rect = sibling.getBoundingClientRect();
                            if (!rect.width || !rect.height
                                || rect.right > fieldRect.left + 16
                                || Math.abs((rect.top + rect.bottom) / 2
                                  - (fieldRect.top + fieldRect.bottom) / 2)
                                  > Math.max(36, fieldRect.height * 0.6)) continue;
                            const candidate = (sibling.innerText || sibling.textContent || '')
                              .trim().replace(/[\\s:：*＊]+$/g, '').trim();
                            if (candidate && candidate.length <= 100) return candidate;
                          }
                          node = node.parentElement;
                        }
                        return '';
                      };
                      return elements.flatMap((el, index) => {
                        if (!visible(el) || el.closest(popupSelector)) return [];
                        const tag = el.tagName.toLowerCase();
                        const role = el.getAttribute('role') || '';
                        const isLabel = el.matches('label[for], .bh-form-label, '
                          + '.el-form-item__label, .ant-form-item-label, .form-label');
                        if (isLabel && !el.matches(controls)) {
                          const target = el.getAttribute('for')
                            ? el.ownerDocument.getElementById(el.getAttribute('for')) : null;
                          const group = el.closest('.bh-form-group, .form-group, '
                            + '.el-form-item, .ant-form-item');
                          if ((target && target.matches(controls) && visible(target))
                              || (group && [...group.querySelectorAll(controls)].some(visible))) {
                            return [];
                          }
                          const label = text(el);
                          return label ? [{index, label, kind: 'custom', fillable: false}] : [];
                        }
                        const native = ['input', 'textarea', 'select'].includes(tag);
                        const editable = el.getAttribute('contenteditable') === 'true';
                        const kind = native
                          ? (tag === 'input'
                            ? (el.getAttribute('type') || 'text').toLowerCase() : tag)
                          : (role || 'contenteditable');
                        if (['submit', 'button', 'reset', 'image'].includes(kind)) return [];
                        const labelledBy = (el.getAttribute('aria-labelledby') || '')
                          .split(/\\s+/).map(id => text(el.ownerDocument.getElementById(id)))
                          .filter(Boolean).join(' ');
                        const group = el.closest('.bh-form-group, .form-group, '
                          + '.el-form-item, .ant-form-item');
                        const label = [...(el.labels || [])].map(text).filter(Boolean).join(' ')
                          || el.getAttribute('aria-label') || labelledBy
                          || text(group?.querySelector('label, .bh-form-label, '
                            + '.el-form-item__label, .ant-form-item-label, .form-label'))
                          || rowLabel(el)
                          || siblingLabel(el)
                          || el.getAttribute('placeholder') || el.getAttribute('title')
                          || el.getAttribute('name') || el.id || '';
                        return [{index, label, kind,
                          fillable: Boolean((native || editable) && !el.disabled && !el.readOnly)}];
                      });
                    }""",
                    POPUP_FIELD_ANCESTOR,
                )
            except Exception as exc:
                if not is_transient_frame_error(exc):
                    raise
                continue
            items.extend(
                ObservedField(
                    frame_index=frame_index,
                    label=str(item["label"]).strip() or "未命名",
                    kind=str(item["kind"]),
                    fillable=bool(item["fillable"]),
                    dom_index=int(item["index"]),
                )
                for item in raw
            )
        return tuple(items)

    async def wait_for_fields(
        self, *, timeout_seconds: float = 8
    ) -> tuple[ObservedField, ...]:
        """Wait for asynchronous form rendering and return a stable visible snapshot."""

        loop = asyncio.get_running_loop()
        started = loop.time()
        deadline = started + timeout_seconds
        last: tuple[ObservedField, ...] = ()
        changed_at = started
        while True:
            current = await self.observe_fields()
            now = loop.time()
            if current != last:
                last = current
                changed_at = now
            if now >= deadline or (
                current and now - started >= 2.5 and now - changed_at >= 0.8
            ):
                return current
            await asyncio.sleep(min(0.4, max(0, deadline - now)))

    async def _combobox_trigger(self, label: str) -> tuple[Any, int] | None:
        matches: list[ObservedField] = []
        for field in await self.observe_fields():
            if _normalized(field.label) != _normalized(label):
                continue
            if field.kind == "combobox":
                matches.append(field)
                continue
            if field.kind == "text" and not field.fillable:
                trigger = self.page.frames[field.frame_index].locator(FIELD_SELECTOR).nth(
                    field.dom_index
                )
                if (
                    await trigger.get_attribute("readonly") is not None
                    and await self._nearby_hidden_select(trigger, label) is not None
                ):
                    matches.append(field)
        if not matches:
            return None
        if len(matches) != 1:
            raise ValueError(f"字段“{label}”对应多个下拉控件")
        field = matches[0]
        trigger = self.page.frames[field.frame_index].locator(FIELD_SELECTOR).nth(
            field.dom_index
        )
        return trigger, field.frame_index

    @staticmethod
    async def _nearby_hidden_select(trigger: Any, label: str) -> tuple[Any, Any] | None:
        parent = trigger.locator("xpath=..")
        for _ in range(4):
            # A form or table row groups multiple fields. A select found there
            # may belong to another field and must never be changed as a guess.
            if await parent.evaluate(
                "el => ['FORM', 'FIELDSET', 'TABLE', 'TBODY', 'THEAD', "
                "'TFOOT', 'TR', 'BODY', 'HTML'].includes(el.tagName)"
            ):
                break
            selects = parent.locator("select")
            if await selects.count() == 1 and not await selects.first.is_visible():
                declared_label = await selects.first.evaluate(
                    r"""el => el.getAttribute('aria-label')
                      || (el.getAttribute('aria-labelledby') || '').split(/\s+/)
                        .map(id => el.ownerDocument.getElementById(id)?.textContent || '')
                        .filter(Boolean).join(' ')
                      || [...(el.labels || [])].map(item => item.textContent || '')
                        .filter(Boolean).join(' ')"""
                )
                # Proximity and a single visible input are not evidence that
                # an unlabeled hidden select belongs to this field. Another
                # field's menu may only appear after the trigger is clicked.
                if not declared_label or _normalized(str(declared_label)) != _normalized(label):
                    return None
                controls = parent.locator(
                    "input:not([type='hidden']):not([type='password']), "
                    "textarea, select, [role='combobox'], [contenteditable='true']"
                )
                visible = 0
                for index in range(await controls.count()):
                    visible += bool(await controls.nth(index).is_visible())
                    if visible > 1:
                        break
                if visible == 1:
                    return selects.first, parent
            parent = parent.locator("xpath=..")
        return None

    async def _visible_options(
        self, *, popup_id: str = "", popup_frame: int | None = None
    ) -> tuple[_VisibleOption, ...]:
        options: list[_VisibleOption] = []
        for frame_index, frame in enumerate(self.page.frames):
            if popup_id and popup_frame is not None and frame_index != popup_frame:
                continue
            try:
                selector = SCOPED_OPTION_SELECTOR if popup_id else OPTION_SELECTOR
                raw = await frame.locator(selector).evaluate_all(
                    """(elements, popupId) => elements.map((el, index) => ({
                      index,
                      label: (el.innerText || el.textContent || '').trim(),
                      visible: Boolean(el.getClientRects().length)
                        && el.getAttribute('aria-disabled') !== 'true'
                        && !el.classList.contains('disabled')
                        && (!popupId || Boolean(
                          el.ownerDocument.getElementById(popupId)?.contains(el)))
                    })).filter(item => item.visible && item.label)""",
                    popup_id,
                )
            except Exception as exc:
                if not is_transient_frame_error(exc):
                    raise
                continue
            options.extend(
                _VisibleOption(frame_index, int(item["index"]), str(item["label"]))
                for item in raw[:200]
            )
        return tuple(options)

    async def _wait_visible_options(
        self,
        baseline: tuple[_VisibleOption, ...],
        *,
        popup_id: str = "",
        popup_frame: int | None = None,
    ) -> tuple[_VisibleOption, ...]:
        deadline = asyncio.get_running_loop().time() + 2
        while True:
            if popup_id:
                scoped = await self._visible_options(
                    popup_id=popup_id, popup_frame=popup_frame
                )
                if scoped or asyncio.get_running_loop().time() >= deadline:
                    return scoped
                await asyncio.sleep(0.2)
                continue
            current = await self._visible_options()
            remaining = Counter(
                (option.frame_index, option.label) for option in baseline
            )
            fresh: list[_VisibleOption] = []
            for option in current:
                key = (option.frame_index, option.label)
                if remaining[key]:
                    remaining[key] -= 1
                else:
                    fresh.append(option)
            if fresh or asyncio.get_running_loop().time() >= deadline:
                return tuple(fresh)
            await asyncio.sleep(0.2)

    async def _popup_id(self, trigger: Any) -> str:
        for name in ("aria-controls", "aria-owns"):
            identifier = await trigger.get_attribute(name)
            if identifier and len(identifier.split()) == 1:
                return str(identifier)
        return ""

    async def _verified_popup_id(self, trigger: Any) -> str:
        """Require an explicit, unique trigger-to-popup relation before a write."""

        identifier = await self._popup_id(trigger)
        if not identifier:
            return ""
        owns_popup = await trigger.evaluate(
            """(el, id) => {
              const doc = el.ownerDocument;
              const matches = [...doc.querySelectorAll('[id]')]
                .filter(node => node.id === id);
              if (matches.length !== 1 || matches[0] === el
                  || matches[0].contains(el)) return false;
              const labelIds = (matches[0].getAttribute('aria-labelledby') || '')
                .split(/\\s+/).filter(Boolean);
              if (!labelIds.length) return true;
              const ownerIds = [el.id, ...[...(el.labels || [])]
                .map(label => label.id)].filter(Boolean);
              return labelIds.some(labelId => ownerIds.includes(labelId));
            }""",
            identifier,
        )
        return identifier if owns_popup else ""

    async def _dismiss_popup(self, frame_index: int) -> None:
        await self.page.keyboard.press("Escape")
        # Some widgets close on blur rather than Escape. Release the focused
        # input without changing any selected form value.
        with contextlib.suppress(Exception):
            await self.page.frames[frame_index].evaluate(
                "() => document.activeElement?.blur()"
            )

    @staticmethod
    async def _open_combobox(trigger: Any) -> None:
        try:
            await trigger.click(timeout=3000)
        except Exception as exc:
            if type(exc).__name__ != "TimeoutError":
                raise
            # A visible widget may be covered by a stale menu. Keyboard access
            # can still open it without forcing a pointer click through the menu.
            await trigger.focus()
            await trigger.press("Alt+ArrowDown")

    async def inspect_options(self, label: str) -> tuple[str, ...]:
        """Open one visible custom combobox and read, then dismiss, its choices."""

        located = await self._combobox_trigger(label)
        if located is None:
            raise ValueError(f"找不到下拉字段“{label}”")
        trigger, frame_index = located
        native_scope = await self._nearby_hidden_select(trigger, label)
        if native_scope is not None:
            native, _ = native_scope
            return tuple(
                str(item).strip()
                for item in (await native.locator("option").all_inner_texts())[:30]
            )
        await self._dismiss_popup(frame_index)
        baseline = await self._visible_options()
        popup_id = await self._popup_id(trigger)
        await self._open_combobox(trigger)
        try:
            if not popup_id:
                popup_id = await self._popup_id(trigger)
            options = await self._wait_visible_options(
                baseline, popup_id=popup_id, popup_frame=frame_index
            )
            return tuple(option.label for option in options[:30])
        finally:
            with contextlib.suppress(Exception):
                await self._dismiss_popup(frame_index)

    async def _select_combobox(
        self,
        trigger: Any,
        frame_index: int,
        label: str,
        value: str,
    ) -> None:
        popup_id = await self._verified_popup_id(trigger)
        if not popup_id:
            raise ValueError(f"下拉字段“{label}”缺少明确的菜单归属")
        await self._dismiss_popup(frame_index)
        await self._open_combobox(trigger)
        try:
            if await self._verified_popup_id(trigger) != popup_id:
                raise ValueError(f"下拉字段“{label}”的菜单归属已变化")
            options = await self._wait_visible_options(
                (), popup_id=popup_id, popup_frame=frame_index
            )
            matches = [
                option for option in options if _normalized(option.label) == _normalized(value)
            ]
            if len(matches) != 1:
                available = "、".join(option.label for option in options[:20])
                raise ValueError(
                    f"下拉字段“{label}”没有唯一关联选项“{value}”；"
                    f"当前可见选项：{available or '无'}"
                )
            choice = matches[0]
            await self.page.frames[choice.frame_index].locator(SCOPED_OPTION_SELECTOR).nth(
                choice.index
            ).click()
        finally:
            with contextlib.suppress(Exception):
                await self._dismiss_popup(frame_index)

    async def fill(self, fields: Mapping[str, str]) -> dict[str, str]:
        for field, value in fields.items():
            located = await self._combobox_trigger(field)
            if located is not None:
                trigger, frame_index = located
                native_scope = await self._nearby_hidden_select(trigger, field)
                if native_scope is not None:
                    native, _ = native_scope
                    options = native.locator("option")
                    labels = await options.all_inner_texts()
                    if labels.count(value) != 1:
                        raise ValueError(f"下拉字段“{field}”没有唯一选项“{value}”")
                    # Use the visible widget. Selecting a hidden native select
                    # first can fire change on an unrelated control if the DOM
                    # structure is ambiguous, including an autosave handler.
                    await self._select_combobox(trigger, frame_index, field, value)
                    displayed = str(await trigger.input_value()).strip()
                    selected = str(await native.locator("option:checked").inner_text()).strip()
                    if displayed != value or selected != value:
                        raise ValueError(f"下拉字段“{field}”未保持所选选项")
                else:
                    await self._select_combobox(trigger, frame_index, field, value)
                self.applied[field] = value
                continue
            controls = await self.controls()
            control = match_control(field, controls)
            element = self.page.frames[control.frame_index].locator(CONTROL_SELECTOR).nth(
                control.index
            )
            if (
                await element.get_attribute("readonly") is not None
                or not await element.is_enabled()
            ):
                raise ValueError(f"字段“{field}”不可直接填写，且未找到关联的下拉控件")
            if control.kind == "select":
                options = dict(control.options)
                if value in options:
                    await element.select_option(label=value)
                else:
                    await element.select_option(value=value)
            elif control.kind == "checkbox":
                if value.casefold() in {"是", "true", "1", "yes", "on"}:
                    await element.check()
                elif value.casefold() in {"否", "false", "0", "no", "off"}:
                    await element.uncheck()
                else:
                    raise ValueError(f"复选框“{field}”请填写 是 或 否")
            elif control.kind == "radio":
                await element.check()
            elif control.kind == "file":
                await element.set_input_files(value)
            else:
                await element.fill(value)
            self.applied[field] = value
        return dict(self.applied)

    async def confirm_and_submit(self, submit_label: str = "提交") -> bool:
        """Show a local confirmation tab before clicking the designated button."""

        visible: list[Any] = []
        for frame in self.page.frames:
            button = frame.get_by_text(submit_label, exact=True)
            for index in range(await button.count()):
                item = button.nth(index)
                if await item.is_visible():
                    visible.append(item)
        if len(visible) != 1:
            raise ValueError(f"找不到唯一的“{submit_label}”按钮，请指定页面上的准确按钮文字")
        selected = visible[0]
        preview = await self.context.new_page()
        decision: asyncio.Future[bool] = asyncio.get_running_loop().create_future()

        def choose(value: bool) -> None:
            if not decision.done():
                decision.set_result(value)

        await preview.expose_function("chooseAction", choose)
        preview.on("close", lambda _page: choose(False))
        rows = "".join(
            f"<tr><th>{html.escape(field)}</th><td>{html.escape(value)}</td></tr>"
            for field, value in self.applied.items()
        )
        content = f"""<!doctype html><html lang="zh"><meta charset="utf-8">
          <title>本机点击前确认</title><style>
          body{{font:16px system-ui;max-width:720px;margin:48px auto;line-height:1.6}}
          table{{border-collapse:collapse;width:100%}}
          td,th{{border:1px solid #ccc;padding:8px;text-align:left}}
          button{{font:inherit;padding:10px 18px;margin:20px 12px 0 0;cursor:pointer}}
          </style><h1>本机点击前确认</h1><p>事务：{html.escape(self.service)}</p>
          <p>将在 ehall 页面点击“{html.escape(submit_label)}”。</p>
          <p>仅列出本次脚本填写的字段和指定按钮；
          页面可能还有其他字段、材料或操作后果。
          请在 ehall 页面核对后决定是否点击。</p>
          <table>{rows}</table>
          <button onclick="chooseAction(true)">确认提交到 ehall</button>
          <button onclick="chooseAction(false)">取消</button></html>"""
        await preview.set_content(content)
        await preview.bring_to_front()
        approved = await decision
        with contextlib.suppress(Exception):
            await preview.close()
        if not approved:
            return False
        await self.page.bring_to_front()
        await selected.click()
        with contextlib.suppress(Exception):
            await self.page.wait_for_load_state("domcontentloaded", timeout=10000)
        return True
