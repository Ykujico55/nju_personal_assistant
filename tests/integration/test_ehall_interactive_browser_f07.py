"""An interactive ehall run permits page data POSTs but waits for a human submit click."""

from __future__ import annotations

import asyncio
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

from personal_assistant.ehall_interactive_entry import _redacted_capture
from personal_assistant.infrastructure.browser.driver import (
    InteractiveHeadedBrowser,
    PlaywrightHeadedDriver,
)
from personal_assistant.infrastructure.browser.interactive import (
    EhallInteractiveFlow,
    sample_values,
)


class _Site(BaseHTTPRequestHandler):
    reads = 0
    submissions = 0

    def log_message(self, *_args: object) -> None:
        pass

    def do_GET(self) -> None:
        if self.path == "/portal":
            body = (
                '<html><body><div onclick="window.open(\'/form\', \'_blank\')">'
                "在读证明申请</div></body></html>"
            )
        elif self.path == "/portal-borrow":
            body = (
                '<html><body><div onclick="window.open(\'/borrow-menu\', \'_blank\')">'
                "教室借用</div></body></html>"
            )
        elif self.path == "/borrow-menu":
            body = (
                '<html><body><button onclick="location.href=\'/borrow-form\'">'
                "临时借用（仅借1天）</button></body></html>"
            )
        elif self.path == "/online-application":
            body = """<html><body><h1>第二课堂成绩单（在线办理）</h1>
              <table><tr><td>竞赛经历</td><td>
                <span onclick="location.href='/wrong-form'">申请</span></td></tr>
              <tr><td>志愿服务经历</td><td>
                <span onclick="location.href='/volunteer-form'">申请</span></td></tr>
              </table>
            </body></html>"""
        elif self.path == "/volunteer-form":
            body = """<html><body><label for="hours">志愿服务时长</label>
              <input id="hours" name="hours"><button type="submit">提交</button>
            </body></html>"""
        elif self.path == "/form-frame-shell":
            body = '<html><body><iframe src="/volunteer-form"></iframe></body></html>'
        elif self.path == "/hidden-select":
            body = """<html><body><form>
              <input id="display" readonly>
              <select id="college" name="college" style="display:none">
                <option value="">请选择</option><option value="college-1">测试学院</option>
              </select>
              <script>
                college.addEventListener('change', () => display.value=college.value);
              </script>
            </form></body></html>"""
        elif self.path == "/custom-hidden-select":
            body = """<html><body><form>
              <div class="picker">
                <label for="college-display">审核学院</label>
                <input id="college-display" readonly placeholder="请选择"
                  aria-controls="college-menu"
                  onclick="openMenu('college-menu')">
                <select id="college" name="college" aria-label="审核学院" style="display:none">
                  <option value="">请选择</option>
                  <option value="college-1">测试学院</option>
                </select>
                <ul id="college-menu" hidden>
                  <li onclick="chooseCollege()">测试学院</li>
                </ul>
              </div>
              <div class="picker">
                <label for="reviewer-display">审核人</label>
                <input id="reviewer-display" readonly placeholder="请选择"
                  aria-controls="reviewer-menu"
                  onclick="openMenu('reviewer-menu')">
                <select id="reviewer" name="reviewer" aria-label="审核人" style="display:none">
                  <option value="">请选择</option>
                </select>
                <ul id="reviewer-menu" hidden>
                  <li onclick="chooseReviewer()">测试审核人</li>
                </ul>
              </div>
              <script>
                function openMenu(id) { document.getElementById(id).hidden = false; }
                function chooseCollege() {
                  document.getElementById('college').value = 'college-1';
                  document.getElementById('college-display').value = '测试学院';
                  document.getElementById('college-menu').hidden = true;
                  const choice = document.createElement('option');
                  choice.value = 'reviewer-1'; choice.textContent = '测试审核人';
                  document.getElementById('reviewer').appendChild(choice);
                }
                function chooseReviewer() {
                  document.getElementById('reviewer').value = 'reviewer-1';
                  document.getElementById('reviewer-display').value = '测试审核人';
                  document.getElementById('reviewer-menu').hidden = true;
                }
              </script>
            </form></body></html>"""
        elif self.path == "/unrelated-hidden-select":
            body = """<html><body><form>
              <div class="fields">
                <div class="field"><label for="target-display">审核学院</label>
                  <input id="target-display" readonly placeholder="请选择"></div>
                <div class="field"><label for="other-display">其他字段</label>
                  <input id="other-display" readonly value="请选择">
                  <select id="other" style="display:none">
                    <option value="">请选择</option>
                    <option value="college-1">测试学院</option>
                  </select></div>
              </div>
              <script>
                window.unrelatedChangeEvents = 0;
                other.addEventListener('change', () => {
                  window.unrelatedChangeEvents += 1;
                  document.getElementById('other-display').value = other.value;
                });
              </script>
            </form></body></html>"""
        elif self.path == "/mislabeled-hidden-select":
            body = """<html><body><form><div class="picker">
              <label for="target-display">审核学院</label>
              <input id="target-display" readonly placeholder="请选择">
              <select id="other" aria-label="其他字段" style="display:none">
                <option value="">请选择</option>
                <option value="college-1">测试学院</option>
              </select>
              <input id="other-display" type="hidden" value="请选择">
              <script>
                window.unrelatedChangeEvents = 0;
                other.addEventListener('change', () => {
                  window.unrelatedChangeEvents += 1;
                  document.getElementById('other-display').value = other.value;
                });
              </script>
            </div></form></body></html>"""
        elif self.path == "/unlabeled-hidden-select":
            body = """<html><body><form><div class="picker">
              <label for="target-display">审核学院</label>
              <input id="target-display" readonly placeholder="请选择">
              <select id="other" style="display:none">
                <option value="">请选择</option>
                <option value="college-1">测试学院</option>
              </select>
              <input id="other-display" type="hidden" value="请选择">
              <ul id="other-menu"><li onclick="other.value='college-1';
                other.dispatchEvent(new Event('change', {bubbles:true}))">
                测试学院</li></ul>
              <script>
                window.unrelatedChangeEvents = 0;
                other.addEventListener('change', () => {
                  window.unrelatedChangeEvents += 1;
                  document.getElementById('other-display').value = other.value;
                });
              </script>
            </div></form></body></html>"""
        elif self.path == "/late-unrelated-menu":
            body = """<html><body><form><div class="picker">
              <label for="target-display">审核学院</label>
              <input id="target-display" readonly placeholder="请选择"
                onclick="window.targetClicks++; document.getElementById('other-menu').hidden=false">
              <label for="other-display">其他字段</label>
              <input id="other-display" type="hidden" value="请选择">
              <select id="other" style="display:none">
                <option value="">请选择</option>
                <option value="college-1">测试学院</option>
              </select>
              <ul id="other-menu" hidden><li onclick="other.value='college-1';
                other.dispatchEvent(new Event('change', {bubbles:true}))">
                测试学院</li></ul>
              <script>
                window.targetClicks = 0;
                window.unrelatedChangeEvents = 0;
                other.addEventListener('change', () => {
                  window.unrelatedChangeEvents += 1;
                  document.getElementById('other-display').value = other.value;
                });
              </script>
            </div></form></body></html>"""
        elif self.path == "/unowned-late-menu":
            body = """<html><body><form>
              <div class="picker">
                <label for="target-display">审核学院</label>
                <input id="target-display" readonly placeholder="请选择"
                  onclick="window.targetClicks++;
                    document.getElementById('other-menu').hidden=false">
                <select id="target" aria-label="审核学院" style="display:none">
                  <option value="">请选择</option>
                  <option value="college-1">测试学院</option>
                </select>
                <ul id="other-menu" hidden><li onclick="other.value='college-1';
                  other.dispatchEvent(new Event('change', {bubbles:true}))">
                  测试学院</li></ul>
              </div>
              <div class="picker">
                <label for="other-display">其他字段</label>
                <input id="other-display" type="hidden" value="请选择">
                <select id="other" aria-label="其他字段" style="display:none">
                  <option value="">请选择</option>
                  <option value="college-1">测试学院</option>
                </select>
              </div>
              <script>
                window.targetClicks = 0;
                window.unrelatedChangeEvents = 0;
                other.addEventListener('change', () => {
                  window.unrelatedChangeEvents += 1;
                  document.getElementById('other-display').value = other.value;
                });
              </script>
            </form></body></html>"""
        elif self.path == "/form-table-labels":
            body = """<html><body><table>
              <tr><td>名称:</td><td><input name="data.mc" value="private-answer"></td>
                <td>*</td></tr>
              <tr><td>开始时间</td><td><input name="data.kssj"></td><td>*</td></tr>
              <tr><td>团队职务</td><td><input name="preview-bdx"></td><td>*</td></tr>
              <tr><td>服务时长</td><td><input name="preview-bdx"></td><td>*</td></tr>
              <tr><td>审核学院</td><td><select name="data.shxy.id">
                <option>请选择</option><option>计算机学院</option></select></td>
                <td>*</td></tr>
            </table></body></html>"""
        elif self.path == "/form-div-labels":
            body = """<html><body><style>
              .line { display:grid; grid-template-columns: 160px 600px; align-items:center;
                margin: 16px 0; }
              input, select { height: 48px; }
              </style><form>
              <div class="line"><div>名称:</div><div><input name="data.mc"></div></div>
              <div class="line"><div>开始时间</div><div><input name="data.kssj"></div></div>
              <div class="line"><div>结束时间</div><div><input name="data.jssj"></div></div>
              <div class="line"><div>团队职务</div><div><input name="preview-bdx"></div></div>
              <div class="line"><div>服务时长</div><div><input name="preview-bdx"></div></div>
              <div class="line"><div>审核学院</div><div><select name="data.shxy.id">
                <option>请选择</option><option>计算机学院</option></select></div></div>
              </form></body></html>"""
        elif self.path == "/wrong-form":
            body = "<html><body>错误的申请</body></html>"
        elif self.path == "/borrow-form":
            body = (
                '<html><body><label for="reason">借用事由</label>'
                '<input id="reason" name="reason"><button>提交</button></body></html>'
            )
        elif self.path == "/form-group-labels":
            body = """<html><body>
              <div class="bh-form-group"><span class="bh-form-label">借用用途描述</span>
                <input id="purpose" placeholder="①请务必填写教室借用具体原因"></div>
              <div class="bh-form-group"><span class="bh-form-label">总人数</span>
                <input id="people" type="text"></div></body></html>"""
        elif self.path == "/form-combobox":
            body = """<html><body>
              <div role="combobox" aria-label="校区" aria-controls="choices" tabindex="0"
                onclick="document.getElementById('choices').hidden=false">请选择</div>
              <div id="choices" role="listbox" hidden>
                <div role="option" onclick="choose(this)">鼓楼校区</div>
                <div role="option" onclick="choose(this)">仙林校区</div>
              </div><script>
                function choose(el) {
                  document.querySelector('[role=combobox]').textContent = el.textContent;
                  document.getElementById('choices').hidden = true;
                }
                document.addEventListener('keydown', e => {
                  if (e.key === 'Escape') document.getElementById('choices').hidden = true;
                });
              </script></body></html>"""
        elif self.path == "/form-combobox-overlap":
            body = """<html><body>
              <div role="combobox" aria-label="指导教师所在单位" aria-controls="unit-options"
                tabindex="0" onclick="show('unit-options')">请选择</div>
              <div role="combobox" aria-label="校区"
                tabindex="0" onclick="show('campus-options')">请选择</div>
              <div role="combobox" aria-label="是否用于考试" aria-controls="exam-options"
                tabindex="0" onclick="show('exam-options')">请选择</div>
              <div role="combobox" aria-label="是否需活动桌椅" aria-controls="chairs-options"
                tabindex="0" onclick="show('chairs-options')">请选择</div>
              <div id="unit-options" role="listbox" hidden>
                <div role="option">辅导员</div><div role="option">学生工作处</div></div>
              <div id="campus-options" role="listbox" hidden>
                <div role="option">鼓楼校区</div><div role="option">仙林校区</div></div>
              <div id="exam-options" role="listbox" hidden>
                <div role="option">是</div><div role="option">否</div></div>
              <div id="chairs-options" role="listbox" hidden>
                <div role="option" onclick="choose(this)">是</div>
                <div role="option" onclick="choose(this)">否</div></div>
              <script>
                function show(id) { document.getElementById(id).hidden = false; }
                function choose(el) {
                  document.querySelector('[aria-label="是否需活动桌椅"]').textContent =
                    el.textContent;
                }
              </script></body></html>"""
        elif self.path == "/form-combobox-overlay":
            body = """<html><body>
              <div role="combobox" aria-label="单位" aria-controls="unit-list"
                tabindex="0" onkeydown="event.stopPropagation()"
                onclick="show('unit-list')">请选择</div>
              <div role="combobox" aria-label="校区" aria-controls="campus-list"
                tabindex="0" onclick="show('campus-list')">请选择</div>
              <div id="overlay" hidden style="position:fixed;inset:0;z-index:5"></div>
              <div id="unit-list" role="listbox" hidden style="position:relative;z-index:6">
                <div role="option">辅导员</div></div>
              <div id="campus-list" role="listbox" hidden style="position:relative;z-index:6">
                <div role="option">仙林校区</div></div>
              <script>
                function show(id) {
                  document.getElementById(id).hidden = false;
                  document.getElementById('overlay').hidden = false;
                  document.activeElement.blur();
                }
                document.addEventListener('keydown', e => {
                  if (e.key !== 'Escape') return;
                  document.getElementById('overlay').hidden = true;
                  document.getElementById('unit-list').hidden = true;
                  document.getElementById('campus-list').hidden = true;
                });
              </script></body></html>"""
        elif self.path == "/form-combobox-blur-overlay":
            body = """<html><body>
              <div role="combobox" aria-label="单位" aria-controls="unit-list"
                tabindex="0" onclick="show('unit-list', this)">请选择</div>
              <div role="combobox" aria-label="校区" aria-controls="campus-list"
                tabindex="0" onclick="show('campus-list', this)">请选择</div>
              <div id="overlay" hidden style="position:fixed;inset:0;z-index:5"></div>
              <div id="unit-list" role="listbox" hidden style="position:relative;z-index:6">
                <div role="option">辅导员</div></div>
              <div id="campus-list" role="listbox" hidden style="position:relative;z-index:6">
                <div role="option">仙林校区</div></div>
              <script>
                function closeMenu() {
                  document.getElementById('overlay').hidden = true;
                  document.getElementById('unit-list').hidden = true;
                  document.getElementById('campus-list').hidden = true;
                }
                function show(id, trigger) {
                  document.getElementById(id).hidden = false;
                  document.getElementById('overlay').hidden = false;
                  trigger.focus();
                  trigger.addEventListener('blur', closeMenu, {once: true});
                }
              </script></body></html>"""
        elif self.path == "/form-combobox-dependent":
            body = """<html><body>
              <div role="combobox" aria-label="校区" aria-controls="campus-list"
                tabindex="0" onclick="show('campus-list')">请选择</div>
              <div role="combobox" aria-label="开始节次" aria-controls="period-list"
                tabindex="0" onclick="show('period-list')">请选择</div>
              <div id="campus-list" role="listbox" hidden>
                <div role="option" onclick="chooseCampus(this)">仙林校区</div></div>
              <div id="period-list" role="listbox" hidden></div>
              <script>
                function show(id) { document.getElementById(id).hidden = false; }
                function chooseCampus(el) {
                  document.querySelector('[aria-label="校区"]').textContent = el.textContent;
                  document.getElementById('campus-list').hidden = true;
                  document.getElementById('period-list').innerHTML =
                    '<div role="option">第一节</div><div role="option">第二节</div>';
                }
              </script></body></html>"""
        elif self.path == "/form-combobox-keyboard":
            body = """<html><body>
              <div role="combobox" aria-label="校区" aria-controls="campus-list"
                tabindex="0">请选择</div>
              <div id="blocker" style="position:fixed;inset:0;z-index:5"></div>
              <div id="campus-list" role="listbox" hidden
                style="position:relative;z-index:6">
                <div role="option">仙林校区</div></div>
              <script>
                document.querySelector('[role="combobox"]').addEventListener('keydown', e => {
                  if (e.altKey && e.key === 'ArrowDown') {
                    document.getElementById('campus-list').hidden = false;
                  }
                });
              </script></body></html>"""
        elif self.path == "/form-popup-inventory":
            body = """<html><body>
              <label for="reason">借用用途描述</label><input id="reason">
              <div role="combobox" aria-label="校区" tabindex="0">请选择</div>
              <div class="jqx-combobox-popup" style="display:block">
                <input placeholder="请查找">
                <div id="innerListBoxjqxWidget123" role="listbox">
                  <div role="option">仙林校区</div>
                </div>
              </div></body></html>"""
        elif self.path == "/portal-fields":
            body = (
                '<html><body><button onclick="window.open(\'/form-fields\', \'_blank\')">'
                "字段测试</button></body></html>"
            )
        elif self.path == "/form-fields":
            body = """<html><body><div id="form">加载中</div><script>
              setTimeout(() => { document.getElementById('form').innerHTML = `
                <label for="reason">借用事由</label><input id="reason" name="reason" value="secret">
                <label for="date">借用日期</label><input id="date" readonly>
                <div role="combobox" aria-label="借用教室" tabindex="0">请选择</div>
                <div class="bh-form-group"><span class="bh-form-label">借用时段</span>
                  <button type="button">选择时间</button></div>`; }, 1200);
            </script></body></html>"""
        elif self.path == "/portal-home":
            body = '<html><body><a href="/catalog">全部服务</a></body></html>'
        elif self.path == "/portal-search":
            body = '<html><body><a href="/catalog-search">全部服务</a></body></html>'
        elif self.path == "/catalog":
            body = (
                '<html><body><div onclick="window.open(\'/form\', \'_blank\')">'
                "在读证明申请</div></body></html>"
            )
        elif self.path == "/catalog-search":
            body = (
                '<html><body><input placeholder="搜索服务" '
                'oninput="document.getElementById(\'item\').hidden = !this.value">'
                '<div id="item" hidden onclick="window.open(\'/form\', \'_blank\')">'
                "在读证明申请</div></body></html>"
            )
        elif self.path == "/form":
            body = """<html><body><form action="/submit" method="post">
                <label for="reason">申请理由</label><input id="reason" name="reason">
                <button type="submit">提交</button></form>
                <script>fetch('/load', {method: 'POST'});</script></body></html>"""
        else:
            body = "<html><body>回执号 TEST-1</body></html>"
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if self.path == "/load":
            type(self).reads += 1
            data = b"{}"
        else:
            type(self).submissions += 1
            data = b"<html><body>receipt TEST-1</body></html>"
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@unittest.skipIf(os.getenv("PA_TEST_BROWSER", "1") == "0", "browser tests disabled")
class InteractiveEhallTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_field_select_does_not_mutate_unrelated_select(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        browser = InteractiveHeadedBrowser()
        try:
            await browser.start()
            for path in (
                "/unrelated-hidden-select",
                "/mislabeled-hidden-select",
                "/unlabeled-hidden-select",
                "/late-unrelated-menu",
                "/unowned-late-menu",
            ):
                with self.subTest(path=path):
                    await browser.page.goto(
                        f"http://127.0.0.1:{server.server_port}{path}"
                    )
                    flow = EhallInteractiveFlow(browser.context, browser.page)
                    with self.assertRaises(ValueError):
                        await flow.fill({"审核学院": "测试学院"})
                    self.assertEqual("", await browser.page.locator("#other").input_value())
                    self.assertEqual(
                        0, await browser.page.evaluate("window.unrelatedChangeEvents")
                    )
                    self.assertEqual(
                        "请选择",
                        await browser.page.locator("#other-display").input_value(),
                    )
                    if path in ("/late-unrelated-menu", "/unowned-late-menu"):
                        self.assertEqual(0, await browser.page.evaluate("window.targetClicks"))
        finally:
            await browser.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    async def test_direct_fill_uses_labeled_mirrors_and_dependent_hidden_selects(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        browser = InteractiveHeadedBrowser()
        try:
            await browser.start()
            await browser.page.goto(
                f"http://127.0.0.1:{server.server_port}/custom-hidden-select"
            )
            flow = EhallInteractiveFlow(browser.context, browser.page)
            before_submissions = _Site.submissions
            await flow.fill({"审核学院": "测试学院"})
            self.assertEqual(("请选择", "测试审核人"),
                             await flow.inspect_options("审核人"))
            await flow.fill({"审核人": "测试审核人"})
            self.assertEqual("college-1", await browser.page.locator("#college").input_value())
            self.assertEqual("reviewer-1", await browser.page.locator("#reviewer").input_value())
            self.assertEqual(
                "测试学院", await browser.page.locator("#college-display").input_value()
            )
            self.assertEqual(
                "测试审核人", await browser.page.locator("#reviewer-display").input_value()
            )
            self.assertEqual(before_submissions, _Site.submissions)
        finally:
            await browser.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    async def test_supervised_hidden_select_uses_visible_widget_and_dependent_options(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        browser = InteractiveHeadedBrowser()
        try:
            await browser.start()
            await browser.page.goto(
                f"http://127.0.0.1:{server.server_port}/custom-hidden-select"
            )
            driver = PlaywrightHeadedDriver(allowed_origins=(), origin_mode="open")
            driver._page = browser.page
            driver._deadline = 3
            before = await driver.snapshot()
            select = before["structure"]["controls"][2]
            self.assertEqual(["请选择", "测试学院"], select["option_labels"])
            await driver.fill((("ctl:1:0", "college-1"),))
            self.assertEqual("college-1", await browser.page.locator("#college").input_value())
            self.assertEqual(
                "测试学院", await browser.page.locator("#college-display").input_value()
            )
            await driver.snapshot()
            await driver.fill((("ctl:1:1", "reviewer-1"),))
            self.assertEqual("reviewer-1", await browser.page.locator("#reviewer").input_value())
            self.assertEqual(
                "测试审核人", await browser.page.locator("#reviewer-display").input_value()
            )
        finally:
            await browser.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    async def test_supervised_fill_selects_a_hidden_native_select(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        browser = InteractiveHeadedBrowser()
        try:
            await browser.start()
            await browser.page.goto(f"http://127.0.0.1:{server.server_port}/hidden-select")
            driver = PlaywrightHeadedDriver(allowed_origins=(), origin_mode="open")
            driver._page = browser.page
            driver._deadline = 1.5
            before_submissions = _Site.submissions
            await driver.snapshot()
            await driver.fill((("ctl:1:0", "college-1"),))
            self.assertEqual("college-1", await browser.page.locator("#college").input_value())
            self.assertEqual("college-1", await browser.page.locator("#display").input_value())
            self.assertEqual(before_submissions, _Site.submissions)
        finally:
            await browser.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    async def test_supervised_driver_can_bind_and_fill_one_nested_form_frame(self) -> None:
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("Playwright unavailable")
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        browser = InteractiveHeadedBrowser()
        try:
            await browser.start()
            await browser.page.goto(
                f"http://127.0.0.1:{server.server_port}/form-frame-shell"
            )
            driver = PlaywrightHeadedDriver(
                allowed_origins=[f"http://127.0.0.1:{server.server_port}"],
                origin_mode="allowlist",
                test_mode=True,
            )
            driver._page = browser.page
            before_submissions = _Site.submissions
            with patch(
                "personal_assistant.infrastructure.browser.driver.evaluate_navigation",
                return_value=SimpleNamespace(allowed=True),
            ):
                selected = await driver.select_frame(
                    origin=f"https://127.0.0.1:{server.server_port}",
                    path="/volunteer-form",
                )
            self.assertTrue(selected["url"].endswith("/volunteer-form"))
            before = await driver.snapshot()
            self.assertEqual("hours", before["structure"]["controls"][0]["name"])
            await driver.fill((("ctl:0:0", "1.0"),))
            after = await driver.snapshot()
            self.assertEqual("1.0", after["structure"]["controls"][0]["value"])
            self.assertEqual(before_submissions, _Site.submissions)
        finally:
            await browser.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    async def test_div_sibling_labels_identify_real_form_shape(self) -> None:
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("Playwright unavailable")
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        browser = InteractiveHeadedBrowser()
        try:
            await browser.start()
            await browser.page.goto(
                f"http://127.0.0.1:{server.server_port}/form-div-labels"
            )
            flow = EhallInteractiveFlow(browser.context, browser.page)
            expected = ["名称", "开始时间", "结束时间", "团队职务", "服务时长", "审核学院"]
            self.assertEqual(expected, [field.label for field in await flow.observe_fields()])
            self.assertEqual(expected, [control.label for control in await flow.controls()])
        finally:
            await browser.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    async def test_table_cell_labels_identify_unnamed_and_duplicate_controls(self) -> None:
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("Playwright unavailable")
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        browser = InteractiveHeadedBrowser()
        try:
            await browser.start()
            await browser.page.goto(
                f"http://127.0.0.1:{server.server_port}/form-table-labels"
            )
            flow = EhallInteractiveFlow(browser.context, browser.page)
            expected = ["名称", "开始时间", "团队职务", "服务时长", "审核学院"]
            self.assertEqual(expected, [field.label for field in await flow.observe_fields()])
            self.assertEqual(expected, [control.label for control in await flow.controls()])
        finally:
            await browser.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    async def test_canonical_capture_uses_live_page_without_exporting_value(self) -> None:
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("Playwright unavailable")
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        browser = InteractiveHeadedBrowser()
        try:
            await browser.start()
            await browser.page.goto(
                f"http://127.0.0.1:{server.server_port}/volunteer-form"
            )
            await browser.page.locator("#hours").fill("private-answer")
            raw = await browser.snapshot_page(browser.page)
            captured = _redacted_capture(raw)
            self.assertEqual(64, len(captured["fingerprint"]))
            self.assertNotIn("private-answer", str(captured))
            self.assertEqual("ctl:0:0", captured["controls"][0]["locator"])
            self.assertEqual("button", captured["actions"][0]["tag"])
            self.assertEqual("submit", captured["actions"][0]["html_type"])
        finally:
            await browser.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    async def test_scoped_apply_clicks_only_the_matching_item(self) -> None:
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("Playwright unavailable")
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        browser = InteractiveHeadedBrowser()
        try:
            await browser.start()
            await browser.page.goto(
                f"http://127.0.0.1:{server.server_port}/online-application"
            )
            flow = EhallInteractiveFlow(browser.context, browser.page)
            page = await flow.open_scoped_action(
                "志愿服务经历", "申请", timeout_seconds=2
            )
            self.assertTrue(page.url.endswith("/volunteer-form"))
            self.assertEqual(
                ["志愿服务时长"],
                [item.label for item in await flow.observe_fields()],
            )
        finally:
            await browser.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    async def test_scoped_apply_follows_a_page_opened_after_previous_step(self) -> None:
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("Playwright unavailable")
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        browser = InteractiveHeadedBrowser()
        try:
            await browser.start()
            await browser.page.goto(f"http://127.0.0.1:{server.server_port}/portal")
            flow = EhallInteractiveFlow(browser.context, browser.page)
            late_page = await browser.context.new_page()
            await late_page.goto(
                f"http://127.0.0.1:{server.server_port}/online-application"
            )
            result = await flow.open_scoped_action(
                "志愿服务经历", "申请", timeout_seconds=2
            )
            self.assertIs(result, late_page)
            self.assertTrue(result.url.endswith("/volunteer-form"))
        finally:
            await browser.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    async def test_popup_search_and_option_list_are_not_form_fields(self) -> None:
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("Playwright unavailable")
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        browser = InteractiveHeadedBrowser()
        try:
            await browser.start()
            await browser.page.goto(
                f"http://127.0.0.1:{server.server_port}/form-popup-inventory"
            )
            flow = EhallInteractiveFlow(browser.context, browser.page)
            self.assertEqual(
                ["借用用途描述", "校区"],
                [field.label for field in await flow.observe_fields()],
            )
            self.assertEqual(
                ["借用用途描述"], [control.label for control in await flow.controls()]
            )
        finally:
            await browser.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    async def test_combobox_probe_uses_keyboard_when_click_is_obscured(self) -> None:
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("Playwright unavailable")
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        browser = InteractiveHeadedBrowser()
        try:
            await browser.start()
            await browser.page.goto(
                f"http://127.0.0.1:{server.server_port}/form-combobox-keyboard"
            )
            flow = EhallInteractiveFlow(browser.context, browser.page)
            self.assertEqual(("仙林校区",), await flow.inspect_options("校区"))
        finally:
            await browser.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    async def test_combobox_probe_closes_focus_driven_overlay(self) -> None:
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("Playwright unavailable")
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        browser = InteractiveHeadedBrowser()
        try:
            await browser.start()
            await browser.page.goto(
                f"http://127.0.0.1:{server.server_port}/form-combobox-blur-overlay"
            )
            flow = EhallInteractiveFlow(browser.context, browser.page)
            self.assertEqual(("辅导员",), await flow.inspect_options("单位"))
            self.assertTrue(await flow.page.locator("#overlay").is_hidden())
            self.assertEqual(("仙林校区",), await flow.inspect_options("校区"))
        finally:
            await browser.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    async def test_dependent_combobox_options_appear_after_prior_selection(self) -> None:
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("Playwright unavailable")
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        browser = InteractiveHeadedBrowser()
        try:
            await browser.start()
            await browser.page.goto(
                f"http://127.0.0.1:{server.server_port}/form-combobox-dependent"
            )
            flow = EhallInteractiveFlow(browser.context, browser.page)
            self.assertEqual((), await flow.inspect_options("开始节次"))
            await flow.fill({"校区": "仙林校区"})
            self.assertEqual(("第一节", "第二节"), await flow.inspect_options("开始节次"))
        finally:
            await browser.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    async def test_combobox_probe_dismisses_overlay_before_next_field(self) -> None:
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("Playwright unavailable")
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        browser = InteractiveHeadedBrowser()
        try:
            await browser.start()
            await browser.page.goto(
                f"http://127.0.0.1:{server.server_port}/form-combobox-overlay"
            )
            flow = EhallInteractiveFlow(browser.context, browser.page)
            self.assertEqual(("辅导员",), await flow.inspect_options("单位"))
            self.assertTrue(await flow.page.locator("#overlay").is_hidden())
            self.assertEqual(("仙林校区",), await flow.inspect_options("校区"))
        finally:
            await browser.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    async def test_combobox_options_are_scoped_when_older_menus_remain_visible(self) -> None:
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("Playwright unavailable")
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        browser = InteractiveHeadedBrowser()
        try:
            await browser.start()
            await browser.page.goto(
                f"http://127.0.0.1:{server.server_port}/form-combobox-overlap"
            )
            flow = EhallInteractiveFlow(browser.context, browser.page)
            self.assertEqual(
                ("辅导员", "学生工作处"), await flow.inspect_options("指导教师所在单位")
            )
            self.assertEqual(("鼓楼校区", "仙林校区"), await flow.inspect_options("校区"))
            self.assertEqual(("是", "否"), await flow.inspect_options("是否用于考试"))
            self.assertEqual(("是", "否"), await flow.inspect_options("是否需活动桌椅"))
            await flow.fill({"是否需活动桌椅": "否"})
            self.assertEqual(
                "否", await flow.page.get_by_role("combobox", name="是否需活动桌椅").inner_text()
            )
        finally:
            await browser.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    async def test_form_group_label_wins_over_helper_placeholder(self) -> None:
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("Playwright unavailable")
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        browser = InteractiveHeadedBrowser()
        try:
            await browser.start()
            await browser.page.goto(f"http://127.0.0.1:{server.server_port}/form-group-labels")
            flow = EhallInteractiveFlow(browser.context, browser.page)
            controls = await flow.controls()
            self.assertEqual(["借用用途描述", "总人数"], [item.label for item in controls])
            values, _skipped = sample_values(controls)
            await flow.fill(values)
            self.assertEqual(
                "测试填写（未提交）", await flow.page.locator("#purpose").input_value()
            )
            self.assertEqual("10", await flow.page.locator("#people").input_value())
        finally:
            await browser.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    async def test_custom_combobox_lists_and_selects_visible_option(self) -> None:
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("Playwright unavailable")
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        browser = InteractiveHeadedBrowser()
        try:
            await browser.start()
            await browser.page.goto(f"http://127.0.0.1:{server.server_port}/form-combobox")
            flow = EhallInteractiveFlow(browser.context, browser.page)
            self.assertEqual(("鼓楼校区", "仙林校区"), await flow.inspect_options("校区"))
            await flow.fill({"校区": "仙林校区"})
            self.assertEqual("仙林校区", await flow.page.get_by_role("combobox").inner_text())
        finally:
            await browser.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    async def test_dynamic_field_inventory_includes_custom_and_readonly_fields(self) -> None:
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("Playwright unavailable")
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        browser = InteractiveHeadedBrowser()
        try:
            await browser.start()
            await browser.page.goto(f"http://127.0.0.1:{server.server_port}/portal-fields")
            flow = EhallInteractiveFlow(browser.context, browser.page)
            await flow.open_service("字段测试")
            fields = await flow.wait_for_fields(timeout_seconds=5)
            self.assertEqual(4, len(fields))
            self.assertEqual(
                {"借用事由", "借用日期", "借用教室", "借用时段"},
                {field.label for field in fields},
            )
            self.assertEqual(1, sum(field.fillable for field in fields))
            self.assertNotIn("secret", repr(fields))
        finally:
            await browser.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    async def test_two_step_route_reaches_borrow_form(self) -> None:
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("Playwright unavailable")
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        browser = InteractiveHeadedBrowser()
        try:
            await browser.start()
            await browser.page.goto(f"http://127.0.0.1:{server.server_port}/portal-borrow")
            flow = EhallInteractiveFlow(browser.context, browser.page)
            await flow.open_service("教室借用")
            page = await flow.open_service(
                "临时借用（仅借1天）", current_page_only=True, catalog=False
            )
            self.assertTrue(page.url.endswith("/borrow-form"))
            values, _skipped = sample_values(await flow.controls())
            await flow.fill(values)
            self.assertEqual("测试填写（未提交）", await page.locator("#reason").input_value())
        finally:
            await browser.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    async def test_sample_fill_stops_before_submission(self) -> None:
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("Playwright unavailable")
        _Site.submissions = 0
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        browser = InteractiveHeadedBrowser()
        try:
            await browser.start()
            await browser.page.goto(f"http://127.0.0.1:{server.server_port}/portal")
            flow = EhallInteractiveFlow(browser.context, browser.page)
            await flow.open_service("在读证明申请")
            values, skipped = sample_values(await flow.controls())
            self.assertEqual((), skipped)
            await flow.fill(values)
            self.assertEqual("测试填写（未提交）", await flow.page.locator("#reason").input_value())
            self.assertEqual(0, _Site.submissions)
        finally:
            await browser.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    async def test_opens_service_after_entering_all_services(self) -> None:
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("Playwright unavailable")
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        browser = InteractiveHeadedBrowser()
        try:
            await browser.start()
            await browser.page.goto(f"http://127.0.0.1:{server.server_port}/portal-home")
            flow = EhallInteractiveFlow(browser.context, browser.page)
            page = await flow.open_service("在读证明申请", timeout_seconds=10)
            self.assertTrue(page.url.endswith("/form"))
        finally:
            await browser.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    async def test_searches_deep_service_after_entering_catalog(self) -> None:
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("Playwright unavailable")
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        browser = InteractiveHeadedBrowser()
        try:
            await browser.start()
            await browser.page.goto(f"http://127.0.0.1:{server.server_port}/portal-search")
            flow = EhallInteractiveFlow(browser.context, browser.page)
            page = await flow.open_service("在读证明申请", timeout_seconds=10)
            self.assertTrue(page.url.endswith("/form"))
        finally:
            await browser.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    async def test_background_post_loads_and_submit_waits_for_click(self) -> None:
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("Playwright unavailable")
        _Site.reads = 0
        _Site.submissions = 0
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        browser = InteractiveHeadedBrowser()
        try:
            await browser.start()
            await browser.page.goto(f"http://127.0.0.1:{server.server_port}/portal")
            flow = EhallInteractiveFlow(browser.context, browser.page)
            await flow.open_service("在读证明申请")
            await flow.fill({"申请理由": "毕业用途"})
            self.assertEqual(1, _Site.reads)
            self.assertEqual(0, _Site.submissions)
            task = asyncio.create_task(flow.confirm_and_submit("提交"))
            for _ in range(50):
                await asyncio.sleep(0.1)
                if task.done():
                    await task
                if len(browser.context.pages) > 2:
                    break
            preview = browser.context.pages[-1]
            await preview.get_by_text("毕业用途").wait_for(timeout=10000)
            self.assertIn("毕业用途", await preview.locator("body").inner_text())
            self.assertIn("本机点击前确认", await preview.title())
            self.assertIn("仅列出本次脚本填写的字段", await preview.locator("body").inner_text())
            self.assertEqual(0, _Site.submissions)
            await preview.get_by_role("button", name="确认提交到 ehall").click()
            self.assertTrue(await asyncio.wait_for(task, 15))
            self.assertEqual(1, _Site.submissions)
        finally:
            await browser.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    async def test_cancel_leaves_form_unsubmitted(self) -> None:
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("Playwright unavailable")
        _Site.submissions = 0
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        browser = InteractiveHeadedBrowser()
        try:
            await browser.start()
            await browser.page.goto(f"http://127.0.0.1:{server.server_port}/portal")
            flow = EhallInteractiveFlow(browser.context, browser.page)
            await flow.open_service("在读证明申请")
            await flow.fill({"申请理由": "毕业用途"})
            task = asyncio.create_task(flow.confirm_and_submit())
            for _ in range(50):
                await asyncio.sleep(0.1)
                if task.done():
                    await task
                if len(browser.context.pages) > 2:
                    break
            preview = browser.context.pages[-1]
            await preview.get_by_role("button", name="取消").click()
            self.assertFalse(await asyncio.wait_for(task, 15))
            self.assertEqual(0, _Site.submissions)
        finally:
            await browser.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
