# F07 审计阻断项修复与真实页面复验报告

修复与实测：2026-09-28；报告整理：2026-09-29。

范围：2026-09-27 [阶段性报告](F07_REPAIR_REPORT_2026-09-27.md)之后的提交未决 P1、终态持久化取消窗口，以及“第二课堂 → 第二课堂成绩单（在线办理） → 志愿服务经历（申请）”真实提交前试填。此前 PostgreSQL CAS、successful-controls 表单模板、跨域模式和本机入口修复详见阶段性报告，不把早期测试结果冒充当前结果。

## 结论

**F07 继续 `IN_PROGRESS`，不得标为 `DONE`，F08 不启动。** 提交结果未决的两个本地反例已修复，并有 Broker→Gateway→Outbox 与真实 PostgreSQL/Worker 的历史测试证据。真实第二课堂表单已通过受监督 Broker 填入七项合成测试值，用户目视确认七项正常且页面无报错，脚本没有点击最终“提交”。当前预览报告仍明确 `target_bound=false`、`acceptance_complete=false`：真实提交目标、完整材料与后果、正式扩展 Adapter 和生产 Gateway/Outbox 的真实站点路径未验收。根据 [阶段 8 标准](../DESIGN_AND_DEVELOPMENT.md#阶段-8受监督-ehall-适配器)，这不是完整权威预览的验收通过。

## 一、审计 P1：结果不明误记为确定失败

原反例是“页面写操作可能已发生，Companion 点击返回畸形结果”。旧代码只保护 `click` 调用，返回解析抛出的异常会越过未决处理，导致浏览器会话滞留 `EXECUTING`，Executor/Gateway 可将 Outbox 记为 `FAILED`。点击期间取消也可能留下 `EXECUTING`。这些是本地模拟反例，不代表真实 ehall 曾误提交。

- [`core/browser/session.py`](../src/personal_assistant/core/browser/session.py)：点击调用、结果结构与 `outcome` 解析纳入同一异常边界。点击后的异常及取消先持久化 `UNKNOWN`，再做一次宿主只读跟踪页对账；对账仅在出现唯一新增权威回执时收敛为成功，否则保持 `UNKNOWN`。不再次点击。未决状态写入失败时抛 `UnknownBrowserOutcomeError`，重启恢复可将残留 `EXECUTING` 转为 `UNKNOWN`。
- [`infrastructure/browser/executor.py`](../src/personal_assistant/infrastructure/browser/executor.py)：上述类型化未决异常映射为 `OUTCOME_UNKNOWN`，避免落入确定失败。
- [`core/tools/gateway.py`](../src/personal_assistant/core/tools/gateway.py)：Executor 返回成功或未决后，Outbox/审批终态持久化使用 shield 等待已启动的写入完成，再传播重复取消；修复“浏览器已成功、Outbox 仍为 `PREPARED`、审批仍为 `EXECUTING`”的第二个取消窗口。

审查反例：[`test_browser_session_broker_f07.py`](../tests/unit/test_browser_session_broker_f07.py) 覆盖畸形返回、点击取消、未决写入期间取消和只读对账；[`test_browser_executor_f07.py`](../tests/unit/test_browser_executor_f07.py) 覆盖 Broker→Gateway→Outbox 的 `UNKNOWN`、会话库故障和成功/未决终态写入期间连续取消；[`test_ehall_worker_real.py`](../tests/integration/test_ehall_worker_real.py) 的 `test_unknown_submit_reconciles_read_only` 在真实 PostgreSQL/Worker 链路核对会话与 intent 均为 `UNKNOWN`、重试不增加点击、后续只读对账命中。另有 [`test_postgres_f01.py`](../tests/integration/test_postgres_f01.py) 的终态写入连续取消用例。

## 二、真实页面发现、试填与修复

1. **新标签与嵌套 frame**：[`driver.py`](../src/personal_assistant/infrastructure/browser/driver.py) 仅接管唯一新开的页面，并按精确 HTTPS origin/path 选中唯一内层 frame；多标签或多 frame 命中会拒绝。真实脱敏观察显示 `adopted_new_tab=true`，选中的表单 frame 为 `/tw/xssq/xssq/create`。本地 Companion 反例覆盖选帧与停在提交前的 Broker 预览。
2. **隐藏下拉与可见控件**：真实页面的原生 `select` 隐藏，旁边有只读显示控件。仅 `select_option` 可能改变底层值而不更新可见控件。driver 现在在底层选择后检查唯一可见镜像；若未更新，再经可见控件点击唯一精确选项，复核两者的值。快照为原生下拉提供有界 `option_labels`。有头 Chromium 模拟站点反例覆盖学院选择后才出现审核人选项、底层与显示值一致。
3. **从上到下处理依赖**：[`ehall_real_acceptance.py`](../scripts/ehall_real_acceptance.py) 的一次性验收流程先填名称、起止时间、团队职务、时长和审核学院“文学院”，重扫选项和页面指纹，再填审核人。实际审核人选项数由 64 变成 8；指纹从 `410e527f…` 变为 `04878a71…`，必须经操作端复核后继续。结束时间在真实页面显示为日期，测试样例改成 `2026-09-28` 并校验实际值；通用框架没有硬编码日期联动。
4. **实际结果**：初始页面有 9 个底层控件、7 个业务字段和一个“提交”按钮。Broker 产生 `PREVIEW_READY`，`missing_fields=[]`，附件观察为 `[]`，风险为 `EXTERNAL_WRITE`；用户确认七项均显示测试值、审核学院为“文学院”、审核人有选中项且无页面报错。最终按钮未点击。该表单 DOM 的 `action` 为空、`method=get`，实际 JavaScript 提交目标不能从它们推断；“未见附件控件”也不能替代权威材料清单。

真实证据与复现命令见 [受监督试填记录](evidence/real_ehall_volunteer_supervised_2026-09-28.md)。三个脱敏 JSON 的 SHA-256 分别为：初始结构 `3f45d12ea5071ca56a60398ebd601abee9868cf5a3ab39c53ca554360f746f25`；选择学院后 `97409a447acf12e95ed583083534998cf74b4523caeb8d2cfcc0bf0865ff5ebb`；最终预览 `aab5c15c6a65eb1ec6ec2085aa52019bc529705f631f7c146744bbb463a8ba02`。持久报告遮盖审核人选项值，不保存含 `gid_` 的 URL、Cookie 或认证信息；因此不能仅凭脱敏 JSON 重算包含该字段的 canonical hash。

## 三、测试证据的时间边界

下表命令中的 `pytest` 均指项目根目录下运行 `.venv-win\Scripts\python.exe -m pytest`；`test.ps1` 是完整脚本，PostgreSQL 测试需可达的 `_test` 数据库。

| 证据 | 命令/来源 | 结果与限制 |
| --- | --- | --- |
| 提交未决修复后的历史完整快照 | `./scripts/test.ps1`；`./scripts/test-postgres.ps1` | **1159 passed / 125 skipped**；真实 PostgreSQL/Worker **125 passed**，Ruff/Mypy、`pip check`、`git diff --check` 通过，wheel 211 条目。这是后续真实表单代码之前的快照，不代表当前所有文件的全量测试。 |
| 真实表单结构采集后的历史完整快照 | `./scripts/test.ps1` | **1167 passed / 125 skipped**，Ruff、Mypy 通过；此后又修改了隐藏下拉与验收脚本，不能直接转述为最终代码全量全绿。 |
| 最新试填修复定向回归 | `pytest tests/unit/test_ehall_real_acceptance_script_f07.py tests/unit/test_browser_driver_route_f07.py tests/contract/test_f07_contract_consistency.py -q`；`pytest tests/integration/test_ehall_interactive_browser_f07.py -q`；`pytest tests/integration/test_browser_companion_real_f07.py -q -k "companion_selects_nested_transaction_frame or nested_frame_reaches_broker_preview_without_submit"` | 分别 **63、23、2 passed**；Ruff 对相关文件通过、Mypy 对 `src/personal_assistant` **185 source files** 通过。 |
| 本报告前再次运行的 P1 定向单元反例 | `pytest tests/unit/test_browser_session_broker_f07.py tests/unit/test_browser_executor_f07.py -q -k "malformed_click_result or cancelled_click or cancel_during_success_finalization or cancel_during_unknown_finalization or unknown_save_failure or reconciliation_crash or cancellation_during_unknown_save"` | **9 passed**。 |
| 本报告前尝试真实 Worker 单例 | `pytest tests/integration/test_ehall_worker_real.py -q -k unknown_submit_reconciles_read_only` | **1 skipped**，因为本次 shell 未设置 `PA_TEST_DATABASE_URL`；不能算作当前 PostgreSQL 复验。 |
| 工作区格式与文件边界 | `git diff --check`、`git status --short` | diff check 退出码 0，仅有 LF/CRLF 提示；当前 HEAD 为 `f3be2cd`，修复与报告仍在未提交工作区，本轮没有创建提交或 PR。 |

## 四、剩余验收与审查重点

- 通过只读方式核实真实最终提交的 method/origin/path、材料清单和提交后果；不能点击合成申请的“提交”来猜测目标。
- 将经核验的真实页面版本与字段规范写入 `extensions/nju_ehall` 正式 Adapter，处理学院选择后合法的动态页面版本；不能以验收脚本运行时注册的内存 Adapter 充当正式交付。
- 用正式扩展与 Tool Gateway/Outbox 再做真实提交前预览，确认目标、字段差异、附件/材料、后果与页面指纹都绑定；仍停在最终提交前。真实最终提交仅在确有合法办理需要时单独验收，并非阶段 8 的默认必测项。
- 独立审计应重点复现：畸形点击响应、点击期取消、终态持久化期连续取消、未决写入失败及只读对账，核验点击次数始终为 1 且不能把不明结果记为 `FAILED`；并核查真实报告中的 `target_bound=false`、`acceptance_complete=false` 未被当成验收通过。

当前真实站点证据只证明七项可见值的受监督试填与停止在最终按钮前；没有真实回执，也不能声称任何服务器写入绝对未发生。F07 仍需独立复审与完整提交前验收。
