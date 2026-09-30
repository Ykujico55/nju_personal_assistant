# F07 修复与阶段性验证报告

日期：2026-09-27
范围：本次对话中从第五轮独立审查反馈到真实 ehall 下拉试填成功的 F07 工作。
状态：**阶段性修复完成；F07 保持 `IN_PROGRESS`，未作正式验收通过声明。**

## 1. 结论与用户决定

本轮已经在真实 ehall 中验证：用户完成统一认证后，助手可打开“成绩查询”；可按“教室借用 → 临时借用（仅借1天）”两步进入表单；可读取 16 个可见字段候选，试填部分文本/数字字段；下拉选项可展开、按页面变化重新读取；用户最后确认 `借用方式=按节次借用` 的程序化选择成功。真实页面验证证据是用户在本次对话中的终端输出和反馈，未保存页面截图、动态 `gid_` URL、Cookie 或回执。

**用户决定暂缓完整真实填报。** 当前没有接入 LLM，`--sample` 只用固定测试值或用户显式给出的 `字段=值`；等资料库与 LLM 的逐步决策接入后，再验证完整表单能否自主填写。此决定改变测试时间安排，不等于完成原阶段 8 的“真实表单完整填写至提交前预览”验收。F07 不标记 `DONE`；真实提交也没有执行，不为验收制造不需要的校园事务。

## 2. 对话起点：第五轮审查与阻断项

用户带来的第五轮复审报告指出：隐藏值隔离和 tracking 重定向防护已有实现及反例，但发现 PostgreSQL CAS 的 P1、HTML 表单序列化的 P2，并记录一次全量 **1103 passed / 123 skipped / 1 failed**；该失败随后单独重跑 4 次通过，故当时不能称全量全绿。报告本身只审查、未修改文件。

| 问题 | 本轮修复 | 验证 |
| --- | --- | --- |
| CAS 把包版本 `0.1.0` 与 `manifest_version`（格式版本 `1`）比较，导致隔离失败。Registry 已被禁用后，同版本 CAS 失败也未恢复。 | `LifecycleStore.save(expected=...)` 在 PostgreSQL 比较 `active_version`；`_quarantine` 失败后重读持久记录，只要最新记录仍为 `ENABLED` 就恢复对应 Registry，包括同包版本。 | 真实 PostgreSQL 版本列反例与 Supervisor 同版本并发反例；2026-09-26 `test-postgres.ps1` **124 passed**。 |
| checkbox/radio/disabled 控件的批准模板与浏览器实际 POST 不一致，可能误拒正常表单。 | 按 HTML successful controls 规则生成实际 body 模板：未选 checkbox/radio 省略、选中提交实际 value（无显式值时 `on`）、disabled 省略；批准字段语义哈希与完整实际 body 哈希分别核验。 | 真实 headed Chromium 本地表单场景覆盖勾选、未选、radio 组和禁用字段；`test.ps1` 当轮 **1110 passed / 124 skipped**。 |

第五轮较早的隐藏/密码值不出浏览器进程、tracking 基线与对账的精确 URL 绑定、静态 hidden/CSRF 字段处理，在对话开始前已有实现；本轮报告将它们作为审查背景，不重复认领为新的真实站点验收结果。契约与反例见 [CONTRACTS_AND_INTERFACES.md](CONTRACTS_AND_INTERFACES.md) 第 16.13–16.15 节。

## 3. 真实站点打不开：定位与两条浏览器路径

1. 用户确认具体事务从大厅在**新标签页**打开，目标 origin 为 `ehallapp.nju.edu.cn`。为旧受监督 Companion/采集链路增加可手动选择的 `PA_BROWSER_ORIGIN_MODE=allowlist|open`，按用户要求默认 `open`；采集 CLI 同样支持 `--origin-mode`。`open` 去掉 origin 白名单比较，仍保留 HTTPS/URL 结构、R3、非 GET 默认阻断、审批和目标绑定等原链路约束。
2. 用户在采集器 Chromium 中遇到“网络错误”，普通 Chrome/Edge 同一事务可正常打开。真实采集报告的 `blocked_origin_requests=0`，但有被阻断的非 GET 请求；用户提供的 Network 信息出现 `(failed) net::ERR_FAILED`。单个日志端点的 404 不能证明事务页本身不可达。为诊断补充有界、脱敏的被阻断请求摘要；随后报告显示会话累计 30 次非 GET 拦截、15 种摘要，包含查询型和疑似写入的 `.do` 请求。因接口语义未核验，没有把所有 POST 在旧 Companion 中一概放行。
3. 按用户希望直接验证“打开页面、自动填值、最终由本人确认”的需求，新增独立本机 `assistantctl ehall` 交互入口。它使用可见 Chromium 的正常页面网络行为，等待用户亲自完成 SSO，按指令点击事项、填写可见字段，并在独立本机点击前确认页等待用户点击确认或取消。该入口与原 Desktop Companion/Tool Gateway 受监督链路**并存**；它不是后者的安全验收替代品。`--sample` 只试填并保持页面，代码路径不点击最终提交；页面自身的后台 POST 仍可能发生，不能把“未点提交”等同于“没有任何服务器写请求”。

旧只读采集报告只输出静态端点/路径哈希/计数，不输出请求头、Referer、body、原始完整 URL、控件当前值或动态会话参数。直接交互入口也不保存 SSO 凭据。

## 4. 本机交互入口的迭代与真实反馈

| 顺序 | 现象与改动 | 对话中的证据 |
| --- | --- | --- |
| 大厅查找 | “成绩查询”不在首页可见区域，增加“全部服务”与站内搜索，并轮询所有标签页/框架。 | 用户后来确认新标签页的“成绩查询”内容正常，无网络错误。 |
| SSO 跳转 | 登录 iframe 消失导致 `Locator.count: Frame was detached`；只对 frame detach/执行上下文销毁跳过旧 frame 并继续查找，其余异常继续抛出。 | 用户重新运行后成功打开事务页；本地先失败后通过反例覆盖。 |
| 事务层级 | 用户说明教室借用要先点父事项，再点“临时借用（仅借1天）”；指令支持 `>` 明确两步导航，第二步只在新页面/框架查找。 | 用户真实运行到达目标表单。 |
| 字段发现 | 异步加载后读取原生、只读、ARIA 与自定义标签字段；`--sample` 输出候选数与类型，`r` 可重扫；不打印已有字段值。 | 用户提供真实输出：**16 个候选、6 个原生可直接填写**。 |
| 固定值试填 | 修正“借用用途描述”的表单组标签优先级；HTML `text` 型人数字段按语义识别。固定测试值只用于空白、可唯一定位的用途与人数/容量/数量字段；联系方式、日期及已有值默认跳过。 | 用户再次运行确认用途描述、总人数、教室容量已试填；未把“教室借用数量”未填擅自归因为代码错误。 |
| 下拉选项串项 | 旧弹层的单位选项混进“校区”和布尔下拉；优先用 `aria-controls`/`aria-owns` 关联当前弹层，缺少关联时只读取新出现的可见选项。 | 用户复测显示选项串项减少。 |
| 遮挡、依赖与重扫 | Escape 不足以关闭部分弹层；探测前后使活动元素失焦，点击受挡超时可尝试键盘 `Alt+ArrowDown`；弹层内部 listbox/搜索输入不再计入表单字段；`r` 同时重扫字段与选项。没有写死“日期联动”或“校区 → 节次”。 | 用户确认先前超时的多个下拉能展开；手动选择前置项后 `r` 仍为 16 个字段，并能看到校区、节次的新选项。 |
| 程序化选择 | `--sample` 接受显式 `字段=页面实际选项文字`，仍不进入最终提交分支；多个字段按指令顺序执行。 | 用户运行 `借用方式=按节次借用` 的指令后回复“成了”。这是用户反馈，未保存独立截图或回执。 |

这些修复作用于通用控件识别和交互代码，不为“教室借用”硬编码日期、单位或节次规则。固定 `10`/`30`/`1` 只是 `--sample` 测试值；后续 LLM 才负责结合资料库、当前页面选项和任务目标选择值与步骤。

## 5. 测试与可复现命令

| 时间/范围 | 命令或来源 | 结果与解释 |
| --- | --- | --- |
| CAS/表单语义修复后的全量 | `./scripts/test.ps1`、`./scripts/test-postgres.ps1` | 2026-09-26 分别 **1110 passed / 124 skipped**、**124 passed**；Ruff、Mypy、`pip check`、`git diff --check` 通过。 |
| 跨域、诊断、交互入口的增量 | `./scripts/test.ps1` | 历次为 1119、1122、1127、1129、1130、1133、1135、1136、1141 passed（均 124 skipped）；每次结果对应当时快照，不能替代最新结果。 |
| 下拉串项初次全量 | `./scripts/test.ps1` | **1142 passed / 124 skipped / 1 failed**。失败为 F06 TLS 握手超时测试的 `_abort` 断言，单独重跑通过；当次不能宣称全绿。 |
| 审计前代码快照的目标集合 | `.venv-win\Scripts\python.exe -m pytest tests/unit/test_ehall_interactive_f07.py tests/integration/test_ehall_interactive_browser_f07.py -q` | **26 passed**；包含真实 headed Chromium 连接本地模拟站点，覆盖弹层遮挡、键盘展开、依赖项、字段清单过滤和不提交试填。 |
| 审计前代码快照的全量（原报告本机运行） | `./scripts/test.ps1` | **1148 passed / 124 skipped**；Ruff `All checks passed!`，Mypy `Success: no issues found in 216 source files`。 |
| 随后的独立复验（审计汇总记录） | `./scripts/test.ps1` | **1147 passed / 124 skipped / 1 failed**；RPC 锁等待计时断言测得 0.750 秒，超过 0.700 秒阈值；该用例单独复跑 3/3 通过。完整套件这次未全绿；Ruff、Mypy、`pip check`、`git diff --check` 随后单独通过。详见 `docs/F07_AUDIT_SUMMARY_AND_CORRECTION_GUIDE_2026-09-27.md`。 |
| 审计交接修正后的本机复验（本次） | `./scripts/test.ps1` | 首轮 pytest **1150 passed / 124 skipped**，随后 Ruff 因新增 HTML 行超长失败；拆行后完整脚本 **1150 passed / 124 skipped**，Ruff 通过、Mypy 216 个源文件无问题。`pip check`、`git diff --check` 单独通过。真实 ehall 和 PostgreSQL/Worker 独立脚本本次未运行。 |
| 文档与格式 | `.venv-win\Scripts\python.exe -m pytest tests/contract/test_f07_contract_consistency.py -q`、`git diff --check` | 契约集合 **36 passed**；diff check 退出码 0（仅 Windows LF/CRLF 提示）。 |

最新真实 PostgreSQL/Worker 的 124 passed 结果来自 2026-09-26；随后新增的是本机交互、浏览器和文档改动，未再次声称运行 PostgreSQL 独立套件。自动化 Chromium 使用本地测试站点；真实 ehall 结论只来自用户在本次对话中亲自运行的命令和反馈。

**审计后术语更正**：本机 `assistantctl ehall` 的确认页仅展示脚本记录的已填字段和指定按钮，属于本机点击前确认，不是受监督链路绑定完整 payload、材料和后果的权威预览。页面在导航、观察、填写期间可自行发起 POST，故不能保证确认前没有服务器写入。该入口只在脚本准备点击用户指定的最终提交按钮时暂停；受监督 Companion 的 Tool Gateway、R2/R3、Outbox 和 `UNKNOWN` 规则保持原契约。

## 6. 改动位置

- 生命周期 CAS 与 Registry 恢复：`src/personal_assistant/infrastructure/database/lifecycle_store.py`、`src/personal_assistant/infrastructure/memory/extensions.py`、`src/personal_assistant/core/extensions/{lifecycle,supervision}.py`；相关真实 PostgreSQL 与单元反例。
- 原受监督浏览器链路及模式/诊断：`src/personal_assistant/core/browser/{policy,ports,session,models,fingerprint}.py`、`src/personal_assistant/infrastructure/browser/{driver,companion,companion_app,companion_client,companion_entry,host}.py`、`src/personal_assistant/settings.py`、`src/personal_assistant/bootstrap.py`、`.env.example`、`scripts/ehall_real_acceptance.py`；相应 contract/unit/integration 与 mock 站点测试。
- 本机交互入口：`src/personal_assistant/ehall_interactive_entry.py`、`src/personal_assistant/infrastructure/browser/interactive.py`、`src/personal_assistant/cli/main.py`；测试为 `tests/unit/test_ehall_interactive_f07.py`、`tests/integration/test_ehall_interactive_browser_f07.py`、`tests/unit/test_ehall_real_acceptance_script_f07.py`。
- 项目说明与状态：`README.md`、`TODO.md`、`docs/NEXT_STEPS.md`、`docs/CONTRACTS_AND_INTERFACES.md`。完整工作区文件清单以 `git status --short` 为准；本次对话未创建提交或 PR，现有改动仍在 `main` 工作区。

## 7. 延期项与移交

1. 真实页面只确认**至少一个**自定义下拉的程序化选择成功；其他下拉、日期/只读控件、跨字段依赖、完整值保持尚无逐项实测结论。
2. 真实事务的完整字段填写、字段差异/材料/后果预览、真实适配器指纹、最终提交和回执均未完成。原阶段 8 的标准验收止于最终提交前；确有合法办理需求时，真实提交再单独验收。
3. 资料库驱动的页面观察 → LLM 决定一步 → 浏览器执行 → 重新观察循环尚未接入；Android PWA 确认属 F08，邮件/模型/ehall 自动组合属阶段 10。用户已决定将完整真实填报测试推迟到 LLM 接入后。
4. 下次恢复该验证时，以当时真实页面重新采集字段与选项、核对表单版本，并用真实需要的低风险事项停在最终提交前。不得把本次固定样例值、用户的“成了”反馈或本地 mock 测试扩写成完整真实交易验收。

**交接状态：F07 保持 `IN_PROGRESS`；本报告是修复与阶段性证据报告，不是正式验收通过报告。**
