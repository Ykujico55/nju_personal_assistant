# DELIVERY.2 阻断缺陷修复报告（2026-10-19）

> 后续独立结论：原三项修复已通过独立定向复验；另发现 F07 创建超时后迟到会话回收 P2，DELIVERY.2 继续 BLOCKED。本报告门禁为修复该 P2 之前的历史结果；最新局部实现及串行门禁见 [创建失败清理报告](F07_SESSION_CREATION_CLEANUP_2026-10-19.md)。

## 1. 状态与边界

DELIVERY.2 **BLOCKED：三项缺陷修复待独立复验，全量另有 F07 会话创建超时待稳定性复核**。F08.6 仍 BLOCKED；F07/F08 整体 IN_PROGRESS；F06.P2 与最小 F09.1 DONE 不变。本轮仅处理独立验收指出的三个问题及其相邻反例，不启动新开发阶段。

实际工作目录：`C:\Users\kunji\Desktop\homework\生成式软件工程\lab1`，HEAD `9891102`，原集中开发改动全部保留。没有迁移原库、重启原服务、调用真实模型、连接真实邮箱/站点、发件、提交、部署、提交 Git 或推送。本地自动化的模拟工具调用不代表真实集成。

## 2. 修复内容

### P1：未决外部动作被普通继续绕过

`TaskRuntime` 用真实风险枚举 `EXTERNAL_WRITE` 判断，兼容旧 `R2` 检查点。派发检查点持久保存审批 ID，异常和恢复都检查未决状态；进入 WAITING_RECONCILIATION 时保留未决标记，普通继续在记录、投影或入队之前拒绝。不会因阶段从 dispatching 改成 waiting 而丢掉未决事实，也不会调用模型规划完成。

新增测试从真实 `TaskRuntime → ApprovalService → ToolGateway → Outbox` 生成检查点；合成执行器只产生 UNKNOWN，没有网络副作用。覆盖 UNKNOWN 已落库后回执丢失、Worker 中断恢复，以及内存和临时 PostgreSQL 适配器。没有再手写 R2 冒充实际持久值。另补确定 FAILED 的回执丢失路径，允许人工继续且原执行器仍只调用一次。

### P2：不兼容工作流被接受并伪装成功

接受命令前比较完整已绑定工作流摘要；另一合法流程、定义版本、扩展版本、相同身份但内容变化以及改成 Agent 模式均拒绝 409，明确提示新建任务。原任务、Run、检查点、输入、消息和待运行作业不改，原定义继续仍成功。

新增专用 `WorkflowBindingConflict`，Worker 执行入口再次复核模式/摘要，旧已入队坏请求以 `WORKFLOW_BINDING_CONFLICT` 标为 FAILED，任务/Run 转 WAITING_USER，保留原工作流绑定与 cursor/pending_input 等业务进度；暂停诊断及 run_version 可更新。真实 CAS superseded 分支保持原语义。

### P2：恢复 EXECUTING 审批丢失 UNKNOWN 与来源

`freeze_snapshot` 将 EXECUTING 审批变为 UNKNOWN，保留批准人、批准时间、消费时间、绑定材料及已知完成事实，轮换 nonce、推进版本。未开始授权仍过期。临时 PostgreSQL 组合反例通过真实 prepare/approve/原子消费、邮件账本执行、导出/恢复核对审批/Outbox/邮件 UNKNOWN 一致，旧 nonce、再次消费以及换幂等键均不能复用授权。无迁移变更。

## 3. 改动文件

- 产品：`core/agent/runtime.py`、`core/workflows.py`、`infrastructure/operations/recovery.py`（均在 `src/personal_assistant/` 下）。
- 反例/回归：`tests/api/test_delivery_remediation.py`，`tests/unit/test_runtime_remediation_delivery.py`、`test_runtime_crosscheck_delivery.py`、`test_recovery_delivery.py`，`tests/integration/test_runtime_remediation_postgres.py`、`test_recovery_remediation_delivery.py`。
- 门禁：`scripts/test-postgres.ps1` 显式加入新增 PostgreSQL 组合反例；`scripts/test-delivery-postgres.py` 支持独立日志目录，保留旧轮日志。
- 文档：TODO、NEXT_STEPS、契约、本报告和历史报告的后续验收提示；本轮摘要另列，不覆盖 10-13 的 107 项历史清单。

## 4. 先失败后修复证据

- P1 实际 Gateway/Outbox UNKNOWN 两个反例：修复前 **2 failed**，因普通继续未拒绝；真实风险值及 UNKNOWN 断言已通过后才触发失败。
- 工作流 API→Worker：初始五项 **5 failed**；同身份内容变化另 **1 failed**；旧已入队 Agent 模式另 **1 failed**。产品失败为 202 而非 409，或作业被错标 SUCCEEDED。
- 恢复审批：新增真实执行中组合单元反例修复前 **1 failed**（UNKNOWN != EXPIRED），修复后相关集合 7 passed、1 skipped（未配置数据库）；数据库反例交由完整临时 PostgreSQL 门禁执行。
- 确定 FAILED 回执丢失相邻反例：先 **1 failed**，修复后纳入下述合并集合。
- 首次合并运行的 4 项失败仅为中文“新任务”与测试 English `new` 提示断言差异，保留断言并统一可操作错误提示；后续 36 passed，再加入两个相邻边界后为 **38 passed、2 warnings（24.95s）**。

## 5. 本轮命令与实际结果

所有命令在上述原仓库及 `.venv-win` 执行。历史 1373 等数字不计作本轮证据。

定向合并命令：

```powershell
.\.venv-win\Scripts\python.exe -m pytest -p no:cacheprovider -p no:faulthandler -o addopts=--strict-markers -q --tb=short tests/unit/test_runtime_remediation_delivery.py tests/unit/test_runtime_crosscheck_delivery.py tests/unit/test_runtime_delivery.py tests/unit/test_workflows_delivery.py tests/api/test_delivery_remediation.py tests/unit/test_recovery_delivery.py
```

结果 **38 passed、2 warnings**，警告为既有 TestClient 依赖弃用提示。

完整本地门禁已结束：**1383 passed、135 skipped、1 failed、6 warnings，2192.04s（36:32），exit1**。失败为既有 F07 `test_a_load_time_action_rewrite_is_refused_as_unknown_version`，在 `reach_authenticated → create_session` 阶段报 `BrowserUnavailableError: the desktop companion timed out`，尚未到页面改写断言；本轮未修改其实现或计时断言。该失败原始日志完整保留，单例复跑与数据库/收尾结果另记，不能称全量全绿。pytest 非零使 test.ps1 后续 Ruff/Mypy 阶段未执行；下方单独静态命令已实际通过。

首轮全量只在收集阶段中断：新增 integration/unit 模块同名，未执行测试；已将 integration 文件改为唯一名称 `test_runtime_remediation_postgres.py`，保留全部断言后重启完整门禁。原 `full.log` 保留，不计为通过。完整本地命令禁用 pytest 缓存写入和既有 Windows 不稳定 faulthandler 诊断插件，不跳过业务测试。

### 完整门禁与收尾汇总

| 范围 | 本轮实际结果 | 日志（均在 `var/verification/delivery-remediation-2026-10-19/`） |
|---|---|---|
| 三项修复及相邻定向回归 | **38 passed、2 warnings** | 上述精确命令，24.95s |
| 完整本地 pytest | **1383 passed、135 skipped、1 failed、6 warnings**，exit1 | `full-final.log`，2192.04s |
| F07 失败单例隔离复查 | **1 passed**，不改原失败事实 | `f07-recheck.log`，22.54s |
| 完整临时 PostgreSQL／真实 Worker | **135 passed**，exit0 | `postgres-full.log`、`postgres-runner.log`，2875.47s（47:55） |
| 独立静态检查 | Ruff、Mypy **269 source files**、三份 JS、依赖、差异检查通过 | 下方精确命令 |
| 离线 wheel | **265 条目、10 web 资源、12 迁移**，源字节一致 | `build.log`、`wheel-verification.json` |
| 测试资源回收 | 活动 pytest/测试运行器 **0**；`pa.delivery-test` 标签容器 **0**，查询 exit0 | `active-test-processes-after.json`、`container-inventory-after.json` |

PostgreSQL 完整门禁确实包含本轮新增的两项运行时 UNKNOWN 持久化反例及一项执行中审批恢复组合；没有用内存结果替代。运行中的测试模块更名未改变已收集的测试内容，最终脚本枚举采用唯一新文件名。临时容器启动/删除路径均完成，收尾再次查询已确认清空，不沿用上一轮“未确认零残留”的说明；没有操作原有服务或容器。记录的测试运行会话均已结束，Worker/浏览器关闭断言属于测试证据，不据此宣称其他系统进程全为零。

完整本地与数据库门禁在独立测试资源上并行运行；F07 创建浏览器会话超时的根因尚未确证，不仅凭并行负载作归因。没有为获得绿色放宽断言或重复整套抽取成功结果。

### 完整门禁命令

```powershell
$env:PYTHONUTF8='1'
$env:PYTEST_ADDOPTS='-vv --tb=short -p no:faulthandler -p no:cacheprovider'
Remove-Item Env:PA_TEST_DATABASE_URL -ErrorAction SilentlyContinue
& ./scripts/test.ps1 *> var/verification/delivery-remediation-2026-10-19/full-final.log
```

```powershell
Remove-Item Env:PYTEST_ADDOPTS -ErrorAction SilentlyContinue
.\.venv-win\Scripts\python.exe -Xutf8 scripts/test-delivery-postgres.py --all --log-dir var/verification/delivery-remediation-2026-10-19 *> var/verification/delivery-remediation-2026-10-19/postgres-runner.log
```

135 个跳过项均为未配置测试数据库时的 PostgreSQL/Worker 条件用例，另由下方完整临时数据库门禁运行，没有新增无条件跳过。

数据库脚本从 `test-postgres.ps1` 显式枚举所有用例，本轮新增运行时 UNKNOWN 两项及执行中审批恢复一项。仅用已缓存 pgvector17 镜像、唯一容器标签、随机回环端口和合成短期凭据；创建、迁移和恢复的均为临时数据库，未使用原助手配置。首次数据库命令启动后将新测试文件更名以消除全量收集冲突，执行中的模块仍为同一测试内容；最终枚举使用新文件名。

失败单例复查命令：

```powershell
.\.venv-win\Scripts\python.exe -m pytest -p no:cacheprovider -p no:faulthandler -o addopts=--strict-markers -q --tb=short tests/integration/test_browser_companion_real_f07.py::BrowserCompanionRealTests::test_a_load_time_action_rewrite_is_refused_as_unknown_version
```

实际结果 **1 passed、22.54s、exit0**，日志 `var/verification/delivery-remediation-2026-10-19/f07-recheck.log`。复跑未删除或放宽断言，不更改原失败事实，不单凭一次通过判断超时根因；本轮未再次重跑第二遍全量，不用隔离结果改写全量结论。

### 静态和离线制品

```powershell
.\.venv-win\Scripts\python.exe -m ruff check src extension_sdk extensions tests scripts
.\.venv-win\Scripts\python.exe -m mypy src/personal_assistant extension_sdk/src/personal_assistant_sdk extensions/example_echo/src/example_echo extensions/personal_knowledge/src/personal_knowledge extensions/nju_smail/src/nju_smail extensions/nju_ehall/src/nju_ehall extensions/assistant_workflows/src/assistant_workflows scripts/assistant-backup.py scripts/assistant-upgrade.py scripts/configure-backup.py scripts/configure-model.py scripts/startup-task.py scripts/test-delivery-postgres.py
D:/nodejs/node.exe --check web/pwa/app.js
D:/nodejs/node.exe --check web/pwa/service-worker.js
D:/nodejs/node.exe --check web/admin/app.js
.\.venv-win\Scripts\python.exe -m pip check
git diff --check
.\.venv-win\Scripts\python.exe -m pip wheel --no-index --no-build-isolation --no-deps --wheel-dir var/verification/delivery-remediation-2026-10-19/wheel .
```

静态结果：Ruff 通过，Mypy **269 source files** 通过，三份 JavaScript 语法、依赖检查通过；差异检查通过（既有 LF/CRLF 提示保留）。离线 wheel **265 条目、10 份 web 资源、12 份迁移**，三份修复模块及 web/迁移均与当前源逐字节一致、不含业务扩展源码；未安装。SHA-256：`b4958c151b7dab21a5a6c52e9dfe9ae140c3901e8ed8d31affc80eb9011ed48e`。日志为 `build.log`、`wheel-verification.json`。核验首个临时命令错误假设 wheel 有 `/web/` 路径而失败，读取实际 `share/personal-assistant/pwa|admin` 后按真实路径核验通过，未改制品或放宽资源/字节断言。

当前完整实现摘要见 [112 项清单](DELIVERY_2_REMEDIATION_FILELIST_2026-10-19.json)：与原 107 项相比，仅本轮授权的 7 项既有文件变化，新增 5 个测试文件；排除报告/状态、清单自身及忽略的凭据/运行数据，旧清单保留为历史。

已验收 F06 三份源/测试文件 SHA-256 仍与此前独立通过的摘要完全一致，本轮没有修改它们。

## 6. 剩余范围与风险

- 三项缺陷的本地反例/回归通过仍不能代替独立验收；完整本地门禁另有 F07 创建会话超时未确证根因，单例通过不代表已根治。DELIVERY.2 继续 BLOCKED，下一项是本轮独立复验及该门禁失败的稳定性复核，不启动新的开发阶段。
- UNKNOWN 的普通继续保持阻断；本轮未新增自动对账或外部动作重放，须先取得结果证明。
- F08.6 真机最新版、真机回执丢失/重试、正式审批与相关缓存、独立 Cloudflare 中断、无 Google 设备 ntfy 投递/撤销/锁屏/点击仍缺真实证据，不要求 Google Play。
- F07 正式事务 Adapter、实际目标/完整 payload/材料/后果权威准备与 Gateway/Outbox 实站仍延期；不制造申请。
- 没有改写已有迁移、风险等级、审批 canonicalization、熔断器或 Service Worker 缓存边界。
