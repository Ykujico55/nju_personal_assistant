# F07 一次登录、自动路由与提交前试填报告（2026-09-30）

> 此文记录审计前的真实页面运行与当时的代码快照。后续审计发现隐藏下拉可误改无关字段，已修复并完成全量复验；见 [审计修复补充报告](F07_AUDIT_REPAIR_2026-09-30.md)。下面的实站 JSON 没有在修复后重新采集。

## 本轮目标与结论

按用户指定的烟测范围：用户在脚本弹出的 Chromium 中亲自完成一次统一认证；助手自动走完“第二课堂 → 第二课堂成绩单（在线办理） → 志愿服务经历（申请）”，以合成值填写七个业务字段，停在最终“提交”按钮前。**本轮真实页面烟测通过，最终“提交”没有点击。** 这是本机直接交互链路的结果；F07 仍为 `IN_PROGRESS`，交由独立审计核验后决定是否满足后续推进条件。

本轮没有接入 LLM。路线和测试值写在一次性烟测脚本中，用来验证浏览器操作能力，不代表未来模型已能自行规划事务或生成真实申请内容。

## 真实站点执行证据

复现入口（需用户本人在新开的 Chromium 中完成 SSO，站点可用时运行）：

```powershell
.venv-win\Scripts\python.exe scripts\ehall_volunteer_smoke.py --out docs\evidence\real_ehall_volunteer_auto_route_smoke_2026-09-30.json
```

本次运行启动一个可见浏览器上下文；用户回复“已登录”后，脚本沿三层路线自动定位和导航。终端逐项打印名称、开始时间、结束时间、团队职务、服务时长、审核学院和审核人的填写进度。选“文学院”后，脚本读取更新的审核人选项并选择页面提供的一个具体选项。随后只读核对七项可见值、申请表 frame 和唯一的“提交”按钮。脚本停下让用户查看页面，用户回复“七项正常”；输入 `ok` 后脚本关闭浏览器，进程退出码为 0。

脱敏证据：[real_ehall_volunteer_auto_route_smoke_2026-09-30.json](evidence/real_ehall_volunteer_auto_route_smoke_2026-09-30.json)。其 SHA-256 为 `4bdfc61c66566901744a49f937f93b160d214f24ad5d8b9ba08ff915702ffd75`。关键字段为 `navigation_completed=true`、`all_fields_visible_and_match=true`、七个 `field_matches=true`、`form_frame_count=1`、`submit_button_visible=true`、`user_verified=true`、`submitted=false`。报告遮盖审核人选项文字，不含账号、Cookie、动态查询参数或带 `gid_` 的 URL。`one_browser_context=true` 由脚本单次创建浏览器上下文的路径给出；它不统计校方服务端的认证请求次数。

合成值分别为名称“自动化测试请勿提交”、开始时间 `2026-09-27 09:00`、结束时间 `2026-09-28`、团队职务“无”、服务时长 `1.0`、审核学院“文学院”，审核人取页面当前可选项。所有值仅为试填；没有制造真实志愿服务经历或提交申请。

## 本轮修复

1. 真实页面一度出现 500，用户刷新后已处于在线办理页。原导航仍等待上层入口，无法继续。新增当前页可见文本判断：页面已出现“志愿服务经历”时直接继续；否则以有界的 15 秒查找周期等待入口，允许刷新后恢复。
2. 真实表单的“审核学院”“审核人”是可见只读文本镜像加隐藏原生 `select`。当时的通用填写层增加了邻近隐藏下拉探测，并在镜像未更新时点击可见菜单。后续审计证明当时的邻近范围过宽、先改隐藏下拉会误触发其他字段；当前修正及反例见补充报告。审核人依赖学院，烟测脚本按页面从上到下填写，并在学院写入后重新读取审核人选项。
3. 新增直接交互烟测脚本及反例测试：覆盖三层路由、页面刷新后继续、从上到下填写、审核人无实际选项时失败停止、可见值不匹配不得报成功，以及本地 Chromium 模拟的标签镜像和依赖隐藏下拉。脚本没有调用最终提交方法。

代码与测试文件：[ehall_volunteer_smoke.py](../scripts/ehall_volunteer_smoke.py)、[interactive.py](../src/personal_assistant/infrastructure/browser/interactive.py)、[test_ehall_volunteer_smoke_f07.py](../tests/unit/test_ehall_volunteer_smoke_f07.py)、[test_ehall_interactive_browser_f07.py](../tests/integration/test_ehall_interactive_browser_f07.py)。交接记录同步于本报告、[NEXT_STEPS.md](NEXT_STEPS.md) 和 [TODO.md](../TODO.md)；脱敏 JSON 是本次真实运行生成的证据。

## 定向回归

```powershell
.venv-win\Scripts\python.exe -m pytest tests\unit\test_ehall_volunteer_smoke_f07.py tests\unit\test_ehall_interactive_f07.py tests\integration\test_ehall_interactive_browser_f07.py -q
.venv-win\Scripts\python.exe -m ruff check scripts\ehall_volunteer_smoke.py src\personal_assistant\infrastructure\browser\interactive.py tests\unit\test_ehall_volunteer_smoke_f07.py tests\integration\test_ehall_interactive_browser_f07.py
.venv-win\Scripts\python.exe -m mypy scripts\ehall_volunteer_smoke.py src\personal_assistant\infrastructure\browser\interactive.py
git diff --check
```

结果：目标测试 **44/44 通过**（24 个本地 Chromium 集成、15 个原交互单元、5 个新增烟测单元）；Ruff 通过；Mypy 对两个改动源文件通过；`git diff --check` 退出码 0，仅有现有文件的 LF/CRLF 提示。本轮是直接交互层局部改动，按用户要求和 `AGENTS.md` 的局部改动规则只运行受影响测试；**未运行全量 `scripts/test.ps1` 或 PostgreSQL/Worker 套件**。2026-09-28 的全量与数据库数字是旧快照，不算作本轮通过。

## 验收边界与审计关注点

- 这次证实了**这一个真实事务**在一次可见浏览器运行中，用户登录后由脚本自动导航、自动试填并停在最终按钮前。脚本路线与测试值是预设的；未验证 LLM 动态观察、决策或任意事务通用性。
- 运行走本机 `EhallInteractiveFlow`，页面在导航与填写时照常联网，可能自行发起 POST。`submitted=false` 只表示脚本没有点击用户指定的最终“提交”按钮，不能推导服务器在此前完全没有写入。
- 本轮未运行受监督 `BrowserSessionBroker → Tool Gateway → Outbox` 的真实路由，也未生成绑定真实提交目标、完整材料及后果的权威预览。此前 2026-09-28 的 Broker 七字段预览是[另一份证据](evidence/real_ehall_volunteer_supervised_final_preview_2026-09-28.json)，其中 `target_bound=false`、`acceptance_complete=false`。阶段 8 的正式条件见 [DESIGN_AND_DEVELOPMENT.md](../DESIGN_AND_DEVELOPMENT.md)。本报告不将直接交互烟测替代为正式受监督链路验收。
- 没有真实提交和回执；按用户本轮目标也不需要制造申请。独立审计应先复核本报告、脱敏 JSON、脚本中不存在最终提交调用及目标测试，再判断 F07 后续处理。审计通过前保持 `IN_PROGRESS`，F08 尚未开始。
