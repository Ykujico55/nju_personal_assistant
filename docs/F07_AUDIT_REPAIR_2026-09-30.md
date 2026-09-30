# F07 自动试填烟测后的审计修复（2026-09-30）

> 历史快照：第二轮独立复审随后发现“点击后才出现的无关菜单”残余 P1。本报告中的 1185 passed 是首轮修复时的测试结果，不能证明该残余路径安全。后续代码、反例和当前验证范围见 [第二轮修复报告](F07_AUDIT_REPAIR_ROUND2_2026-09-30.md)。

## 结论与范围

审计确认 [2026-09-30 本机真实页面烟测](F07_AUTO_ROUTE_SMOKE_REPORT_2026-09-30.md)有效，同时发现隐藏下拉可能改动无关字段（P1）及文档契约测试因旧措辞失败（P2）。两项问题本轮已修复，**未经排除的完整 `scripts/test.ps1` 退出码为 0**。本轮没有重新登录或试填真实 ehall；旧烟测 JSON 是修复前的真实运行记录，不能当作修复后实站复测。F07 继续 `IN_PROGRESS`，F08 未启动。

## P1：字段关联与零误写

原 `_nearby_hidden_select` 向上查找时会搜索到整个 `<form>`，把目标只读输入与另一个字段的隐藏 `select` 错配。旧 `fill` 还会先对选中的隐藏 `select` 调用 `select_option`，触发 `change`，再因可见镜像不匹配而报错。审计反例所述的外部自动保存风险成立于代码路径；本轮没有证据表明真实 ehall 发生了该写入。

修复：[interactive.py](../src/personal_assistant/infrastructure/browser/interactive.py) 只在独立字段容器内考虑唯一隐藏下拉；遇到 `form`、表格行等聚合节点即停止，且容器只能有一个可见输入控件。隐藏下拉若声明了 `aria-label`、`aria-labelledby` 或关联 `<label>`，必须与目标字段名相同。无法确认关联的只读字段在操作前报错。对于已关联的隐藏下拉，助手先通过目标可见菜单选择，再检查可见镜像与隐藏选中项均保持所选值；不直接调用隐藏 `select_option`。无显式弹层 ID 时，兜底点击仅接受目标字段容器里**本次展开后新出现的唯一选项**，不会点页面上原本可见的同名项。

新增真实 Chromium 本地站点反例：[test_ehall_interactive_browser_f07.py](../tests/integration/test_ehall_interactive_browser_f07.py) 的 `test_missing_field_select_does_not_mutate_unrelated_select`。它覆盖三个布局：其他字段在同表单另一容器、同容器隐藏下拉明确标为“其他字段”、同容器另有预先可见的无关同名选项。修复前反例分别能观察到无关 `select` 被改为 `college-1`；修复后对每个布局都断言无关值未变、镜像未变、`change` 事件计数为 0。原正常依赖下拉测试仍通过。

## P2：文档契约

[test_f07_contract_consistency.py](../tests/contract/test_f07_contract_consistency.py) 原要求 `NEXT_STEPS.md` 含固定旧句“完整填写至权威预览尚未验收”；文档已用其他措辞明确同一未验收事实。现测试只检查 README 与 NEXT_STEPS 的 F07 章节，分别包含真实教室借用部分试填、权威预览、未验收及 `IN_PROGRESS` 四项语义，不再绑定一整句旧文案。该用例修复前单独运行失败，修复后通过。

## 验证

```powershell
.venv-win\Scripts\python.exe -m pytest tests\integration\test_ehall_interactive_browser_f07.py::InteractiveEhallTests::test_missing_field_select_does_not_mutate_unrelated_select tests\integration\test_ehall_interactive_browser_f07.py::InteractiveEhallTests::test_direct_fill_uses_labeled_mirrors_and_dependent_hidden_selects -q
./scripts/test.ps1
.venv-win\Scripts\python.exe -m pip check
git diff --check
```

- 两条目标 Chromium 用例通过；P1 反例新增三个无关控件布局，改动前复现到实际误改，改动后保持零误写/零 `change`。
- **最终完整套件**：`1185 passed, 125 skipped, 6 warnings`，耗时 807.27 秒；脚本中的 Ruff `All checks passed!`，Mypy `Success: no issues found in 216 source files`，整体退出码 0。第一次全量运行在发现剩余的兜底点击风险后主动中断，不作为通过证据；上述数字来自修复后的第二次、未经排除的完整运行。
- `pip check`：`No broken requirements found.`；`git diff --check`：退出码 0，仅现有文件的 LF/CRLF 提示。
- 独立 PostgreSQL/Worker 脚本本轮未运行；本次未改数据库、Worker 或 Gateway。

## 未覆盖的正式 F07 条件

旧 [本机烟测 JSON](evidence/real_ehall_volunteer_auto_route_smoke_2026-09-30.json) 的 `supervised_gateway_chain=false`；[旧 Broker 预览](evidence/real_ehall_volunteer_supervised_final_preview_2026-09-28.json) 的 `target_bound=false`、`acceptance_complete=false`。真实事务的正式扩展 Adapter、绑定实际提交目标与完整材料/后果的受监督权威预览，以及真实 Gateway/Outbox 路径仍未验收。这些是阶段 8 的提交前要求；**无需制造真实提交**。因此本报告只证明 P1/P2 代码与测试缺陷已修，不能把 F07 标记为 `DONE`。
