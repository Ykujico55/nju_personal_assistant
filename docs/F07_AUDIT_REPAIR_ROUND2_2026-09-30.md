# F07 隐藏下拉第二轮复审修复（2026-09-30）

## 结论

第二轮复审确认首轮 P2 文档测试已修，但指出首轮 P1 修复仍允许“点击后才出现的无关菜单”先改错字段再报错。本轮已修通用交互层并通过定向本地 Chromium 回归。**全量测试按用户最新指令中途停止，交由审计 agent 独立执行；本报告不宣称本轮全量通过。**F07 继续 `IN_PROGRESS`，不推进 F08。

## 反例与修复

在本地真实 Chromium 夹具中，目标“审核学院”是只读输入，同容器另有“其他字段”标签与无标签隐藏 `select`。目标被点击后，无关同名菜单才出现。修复前执行 `fill({"审核学院": "测试学院"})` 最终抛出 `ValueError`，但测试先观察到无关 `select` 从空值变为 `college-1`。第二个夹具让隐藏 `select` 明确标为目标字段，但让新出现的菜单没有指向目标控件的所有权关系，隔离验证菜单选择规则。两种夹具都记录目标点击次数、无关字段值、镜像值和 `change` 事件数。

修复后的 [交互执行层](../src/personal_assistant/infrastructure/browser/interactive.py)：

- 无标签隐藏 `select` 不再靠“同容器、唯一可见输入”推断归属；必须由 `aria-label`、`aria-labelledby` 或关联 `<label>` 明确匹配目标字段。无关联时，`fill` 在打开菜单前报错。
- 写入前要求目标控件以 `aria-controls` 或 `aria-owns` 指向同一文档中唯一的菜单元素；菜单若声明 `aria-labelledby`，还须与目标控件或其标签关联。打开后再次核验该关系。
- 选项点击只在已验证的菜单内进行。删除了“同容器新出现的同名选项”及全页面同名文本点击兜底。对明确归属的菜单，仍支持普通 `li`、ARIA option 和现有组件选项。

[Chromium 回归](../tests/integration/test_ehall_interactive_browser_f07.py) 的两个新布局均要求 `fill` 报错、目标点击 0 次、无关 `select` 值不变、镜像不变、`change` 事件 0 次。保留并通过明确关联的“审核学院 → 审核人”动态选项联动测试，未禁用正常自动选择。

## 可复现验证

```powershell
.venv-win\Scripts\python.exe -m pytest tests\integration\test_ehall_interactive_browser_f07.py::InteractiveEhallTests::test_missing_field_select_does_not_mutate_unrelated_select -q
.venv-win\Scripts\python.exe -m pytest tests\integration\test_ehall_interactive_browser_f07.py tests\unit\test_ehall_interactive_f07.py -q
.venv-win\Scripts\python.exe -m ruff check src\personal_assistant\infrastructure\browser\interactive.py tests\integration\test_ehall_interactive_browser_f07.py
.venv-win\Scripts\python.exe -m mypy src\personal_assistant\infrastructure\browser\interactive.py
```

- 第一条反例在修复前退出码 1，失败断言为无关 `select` 实际值 `college-1`，预期空值；修复后退出码 0。
- 最终交互层 25 项、单元 15 项，共 **40/40 通过**；Ruff 通过，Mypy 对改动模块 **1 个源文件**通过。
- 曾启动未经排除的 `./scripts/test.ps1`；收到用户“跳过全量测试，交给审计 agent”指令后在约 16% 处中断（退出码 1），**不是失败用例结论，也不是全绿证据**。本轮未运行 PostgreSQL/Worker 独立套件。

## 范围与待审项

旧 [真实 ehall 烟测](evidence/real_ehall_volunteer_auto_route_smoke_2026-09-30.json) 发生在本轮修复前，且仅覆盖本机直接交互入口；没有重新登录实站或制造申请。若真实“志愿服务经历”页的下拉缺少可验证关联，新通用层会在选项点击前停止；应先采集脱敏 DOM 关系，再为该事务提供明确映射，或由用户确认一次，不能恢复同容器猜测。

正式事务 Adapter、实际提交目标与材料/后果绑定的受监督权威预览，以及 Gateway/Outbox 路径仍未通过阶段 8 验收。无需为本次复审提交真实申请。推荐下一编号任务仍为 F07 完成这些提交前条件和独立复审；F08 暂不启动。
