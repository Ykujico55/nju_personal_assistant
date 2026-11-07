# CORE.RECONCILE.1：外部动作对账后的任务恢复

当前交接状态：WAITING（本地实现完成，待正式验收及部署），以TODO.md顶部为准。下文的BLOCKED、失败门禁与后续修正按发生时间保留；不将历史失败改写成全量通过。2026-11-08提交仅保存这些已有成果，未部署此核心功能。

## 行为与使用

外部动作回执丢失时，任务保持 `WAITING_RECONCILIATION`。先由现有宿主只读对账流程取得权威证据，再在本机 TUI 的任务页选择该任务，点击 **核对外部结果**。

- 证据仍缺失或不匹配：保持等待，不调用模型、不发送邮件、不点击网页。
- 证据确认原动作成功：记录独立对账事实，任务转 `WAITING_USER`，提示用户另点 **继续处理**。旧审批和 Outbox 的 UNKNOWN 历史保留，旧批准不复用。
- 继续处理后，Agent 得到原动作已核实的结果；工作流从该动作之后继续，不重新执行原步骤。
- 请求回执丢失：沿用 TUI 的“重试原请求”，使用同一请求键和原版本。已经收到“仍未决”后，未来取得新证据可发起一次新的核对。
- 后台尚无 `task_reconciliation` 能力时，TUI 不调用不存在的接口，需先更新后台。

该入口只消费已经持久保存的证据，不会自动向第三方网站或邮箱发起新的查询。首批支持邮件 `SENT_RECONCILED` 证据、宿主已持久保存的完整 SMTP 成功回执（最终 2xx 且原收件人全部 ACCEPTED），以及浏览器 `RECONCILED / host_tracking` 证明；未知、部分成功、证据不全、已删除或已改变的工具版本均不强行放行。不得把“查不到”当作“没有执行”。

## 接口与持久性

`POST /api/v1/tasks/{task_id}/reconcile`（本机工作台复用相同控制面）：

```json
{"version": 12}
```

沿用认证、`Idempotency-Key` 与 `Cache-Control: no-store`。请求不接受用户声明的成功结果或证明。响应包含 `task_id/state/resolved/version`，成功另有 `reconciliation_id`。

宿主校验原任务、工具及扩展版本、授权能力、原审批完整 action fingerprint、Outbox 幂等键与 payload 哈希，再核对邮件台账或浏览器会话证明。通过后，将原调用绑定及最小回执写入 `runtime_records` 的 `reconciliations` 桶。该记录、任务/Run/检查点、消息事件及命令回执在生产 PostgreSQL 同一事务中提交；CAS 冲突或持久化失败整体回滚。无需新增迁移或修改历史迁移。

## 实现范围

- Core：权威对账端口、显式恢复命令、任务版本 CAS、工作流结果投影与继续门闩。
- Infrastructure：只读 Outbox 查询、邮件/浏览器证据解析器。
- Control plane：API 和本机 TUI 入口、后台能力声明。
- 不扩展业务插件，不安装扩展，不改变原实例数据库或服务；PWA 本轮未增加按钮。

## 验证

已执行的目标门禁（当时BLOCKED，后续门禁与修正结果见末节）：

1. 新增 Runtime / API / TUI / capabilities 合集：**23 passed、2 warnings**。
2. 宿主证据解析器：**14 passed**，包括真实 MailSendExecutor / BrowserSessionBroker 配合合成 transport / Companion 产证；执行始终只有一次。
3. 隔离 PostgreSQL：**5 passed**（新增恢复3例、原 UNKNOWN 阻断2例）。首次新增反例 **3 failed**，缺失恢复方法导致失败；实施后已转绿。加入旧作业门闩后复跑最终版本 **5 passed**。
4. Ruff 全项目通过；Mypy **290源文件**通过，最后的邮件证据分支另经目标Mypy通过；差异检查通过。

可复现命令：

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/unit/test_task_reconciliation.py tests/api/test_task_reconciliation_api.py tests/unit/test_tui_task_reconciliation.py tests/api/test_workbench_capabilities.py -q -o addopts='' -p no:cacheprovider --tb=short
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/unit/test_host_reconciliation.py -q -o addopts='' -p no:cacheprovider --tb=short
.\.venv-win\Scripts\python.exe -Xutf8 -B scripts/test-delivery-postgres.py --tests tests/integration/test_task_reconciliation_postgres.py tests/integration/test_runtime_remediation_postgres.py --log-dir var/verification/core-reconcile-2026-11-07/final
.\.venv-win\Scripts\python.exe -m ruff check src extension_sdk extensions tests
.\.venv-win\Scripts\python.exe -m mypy src/personal_assistant extension_sdk/src/personal_assistant_sdk extensions/example_echo/src/example_echo extensions/personal_knowledge/src/personal_knowledge extensions/nju_smail/src/nju_smail extensions/nju_ehall/src/nju_ehall extensions/assistant_workflows/src/assistant_workflows
```

完整本地命令及日志：`var/verification/core-reconcile-2026-11-07/run-full.py` / `full.log`。运行器只在本次测试进程环境中移除PA配置变量，避免沿用原实例参数。初始默认导入因两份既有 `test_extension_recovery_concurrency.py` 同名而收集失败；直接改importlib后，既有测试间裸模块导入又无法找到。两次都未执行测试，日志分别保留为 `full-collection.log` 与 `full-import-collection.log`。最终以 `--import-mode=importlib` 配合 `PYTHONPATH` 加入 `tests/unit`、`tests/integration` 运行，保留全部测试/断言，未改既有测试文件。默认统一测试入口的收集配置问题另列后续维护。PostgreSQL脚本使用新建容器、随机回环端口和独立临时数据库并自行回收。

测试使用合成外部动作及隔离临时 PostgreSQL，不能解释为真实邮件或网页提交验收。未跑完整PostgreSQL/Worker套件：本轮只对新增共享持久事务及相邻UNKNOWN回归做真实数据库验证，避免重复无关完整门禁。未部署、重启、解冻或操作原实例，也未提交推送本轮改动。

### 历史：首次完整门禁阻断与收尾

完整运行出现既有架构契约失败：`cli/tui/model_settings.py:12` 直接导入 `infrastructure.models.setup.KEYS`。已核对提交前HEAD相同；独立单例 **1 failed**，日志 `baseline-architecture.log`。本轮未调整模型配置模块、删除或弱化架构断言。需先做最小依赖方向修复，再补完整门禁。

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/contract/test_extension_independence.py::ExtensionIndependenceContract::test_cli_never_touches_the_database_or_infrastructure -q -o addopts='' -p no:cacheprovider --tb=short
```

确认阻断后停止剩余全套，不报告全量通过或完整计数。本轮精确匹配run-full.py的两个测试进程按PID和创建时间核实后回收，`test-process-cleanup.json`显示owned_remaining=0；另查无pytest运行器。未停止原助手/数据库。隔离PG脚本已完成finally回收本次容器。

上述为授权修复前的历史阻断记录，后续处理见下一节。首次未完成的运行不计作完整通过；F08.6及其他真实验收状态保持。

### 后续授权：模型配置常量依赖修复（2026-11-07）

本人明确同意后，已将六个公开模型配置键迁至 `settings.MODEL_CONFIG_KEYS`；TUI和初始化模块均从配置模块读取。初始化模块保留显式 `KEYS = MODEL_CONFIG_KEYS` 兼容导出，现有host配置消费者无需修改。配置键和值、凭据句柄处理、配置文件格式均保持。

原架构断言先复现 **1 failed**，随后架构契约、模型初始化、TUI配置三文件合计 **13 passed**；最终导出写法再次经同组13项验证。三修改文件Ruff通过；Mypy覆盖三修改文件及既有host消费者 **4文件通过**。没有修改任何测试断言。

恢复完整门禁使用 `run-full-after-constant.py`，日志 `full-after-constant.log`；沿用已验证的测试导入配置，`--maxfail=1` 遇新失败立即停，不重复执行已知必失败的完整套件。完整结果见下一节；该运行已退出。

### 历史：修复架构阻断后的首次完整门禁结果

`run-full-after-constant.py` 已完成：**347 passed、1 failed、5 warnings，971.97秒**，首个失败即停止。失败节点为 `tests/integration/test_configuration_process_sync.py::test_save_then_apply_synchronizes_two_real_host_processes`；其第85行轮询配置应用状态时，`config_store.py:275` 无法打开隔离临时 `.runtime/example.echo/<instance>.json`，操作系统返回PermissionError(errno13)，随后被转为ExtensionConfigError。

失败后只复跑该单项一次：**1 passed，3.20秒**，日志 `config-process-recheck.log`。这说明本次单例没有再触发问题，不证明并发问题消失；不能将失败的完整运行视为通过。相关config_store/processes与该测试没有本轮改动。现阶段只确认运行状态文件读取与更新存在未协调窗口，具体Windows异常诱因仍待针对性验证，不能声称已证实根因。

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B var/verification/core-reconcile-2026-11-07/run-full-after-constant.py
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/integration/test_configuration_process_sync.py::test_save_then_apply_synchronizes_two_real_host_processes -q -o addopts='' -p no:cacheprovider --tb=short
```

当前CORE.RECONCILE.1保留BLOCKED。下一步范围应限配置运行状态文件并发读写及对应回归，须先确认跨范围修改；不增加手机验收步骤，不重启原实例。FILES.1仅保留为原计划后继，不在本轮实现。完整PostgreSQL/Worker套件与真实外部动作未运行，既有5项隔离PG证据仍按原范围记录。

只读核对确认配置存储、进程同步实现与失败测试均与HEAD `662af1b` 相同。`_status`持每扩展跨进程锁读取运行状态，`_report`只持对象内部锁并通过os.replace发布；实例lease只表示存活，未互斥状态文件替换。最小候选修复为报告写入也取同一扩展锁，保留原子替换、lease及真正不可读时的报错，并增加确定性跨store并发回归。此处尚未实施，不能将窗口推断当成已修复。

最终只读进程核验记录 `final-test-process-check.json`：按本次记录的运行器PID及精确配置测试子进程标识检查，owned_remaining=0；不宣称枚举了整个系统的所有浏览器进程。最终git diff --check通过。原助手、数据库和用户浏览器未停止。

### 后续授权：配置运行状态文件并发修复

本人明确允许后，仅修改config_store.py的 `_report`：先取得与 `_status` 相同的每扩展跨进程锁，再取得原 `_lease_guard`，保留实例lease及os.replace原子发布。其他配置读取入口、超时、错误传播和状态语义均未改变。只读锁序检查未发现反向获取；_release不申请扩展锁，_status对实例lease只做非阻塞探测。

新增 `tests/unit/test_extension_runtime_status_lock.py` 用两独立store、真实OS锁与线程事件确定性暂停文件发布。修复前 **1 failed、3 passed**，失败为status读取越过发布边界；这次红测输出保留在执行工具记录，未重定向磁盘，不冒称有原始日志文件。修复后以下合集 **38 passed，11.48秒**，涵盖确定性并发、真正PermissionError、坏JSON/非对象JSON、原配置应用与真实双进程同步；Ruff、两文件Mypy和git diff --check通过。

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/unit/test_extension_runtime_status_lock.py tests/unit/test_extension_config.py tests/unit/test_extension_config_application.py tests/integration/test_configuration_process_sync.py -q -o addopts='' -p no:cacheprovider --tb=short
.\.venv-win\Scripts\python.exe -m ruff check src/personal_assistant/infrastructure/extensions/config_store.py tests/unit/test_extension_runtime_status_lock.py
.\.venv-win\Scripts\python.exe -m mypy src/personal_assistant/infrastructure/extensions/config_store.py tests/unit/test_extension_runtime_status_lock.py --follow-imports=silent
```

最终完整本地运行使用 `run-full-after-runtime-lock.py` / `full-after-runtime-lock.log`，与上一运行相同的环境隔离和收集配置、首错停止。该运行结果见下一节；未部署，不把目标通过当作完整门禁完成。

### 最终完整门禁：测试CDP接入阻断

`run-full-after-runtime-lock.py` 已结束：**297 passed、1 failed、5 warnings，282.19秒**；首个失败即停止，日志 `full-after-runtime-lock.log`。节点为 `BrowserCompanionRealTests::test_a_rewritten_form_action_is_never_written`，调用user_logs_in中的connect_over_cdp时，测试端点 `/json/version/` 返回404。尚未执行该用例的fill/execute及安全断言，不能声称出现真实提交失败或安全保护失效。

只复跑该节点一次，**1 passed，22.26秒**，日志 `browser-cdp-recheck.log`。测试、`tests/support/companion_process.py`及浏览器实现与HEAD `662af1b` 相同。夹具选取空闲CDP号码后立即释放，Chromium到会话创建时才使用该号码；握手仅证明Companion RPC就绪，没有CDP归属校验。这是待排查窗口，但缺少当时RPC端口、占用者与浏览器stderr，不能断言本次根因就是端口冲突。

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B var/verification/core-reconcile-2026-11-07/run-full-after-runtime-lock.py
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/integration/test_browser_companion_real_f07.py::BrowserCompanionRealTests::test_a_rewritten_form_action_is_never_written -q -o addopts='' -p no:cacheprovider --tb=short
```

本次授权的配置状态并发修复已完成，新增确定性回归和真实配置双进程测试均通过；CORE.RECONCILE.1整项仍BLOCKED，不重复全套刷绿。下一步应限测试CDP发现/隔离问题定位，再确定必要修复；本轮未编辑浏览器或其夹具。完整PostgreSQL/Worker与真实业务验收仍未运行，未部署或操作原实例。

### 2026-11-07 继续：测试CDP端口预留与失败清理

按本人“继续下一步”要求，仅修改 `tests/support/companion_process.py`，新增 `tests/unit/test_companion_process_fixture.py`；生产浏览器驱动、API与审批规则未改。

先用独立真实Companion子进程，固定让RPC绑定到夹具选出的CDP号码：旧helper接受握手，实际GET `/json/version/` 返回404，finally确认子进程回收。该实验复现了端口复用机制，不冒称证明上一完整运行当时的占用者。新增8项确定性回归先 **7 failed、1 passed**，日志 `cdp-fixture-red.log`。

修复在启动RPC前持有CDP预留socket，直到握手完成才释放；Windows使用SO_EXCLUSIVEADDRUSE，避免RPC的SO_REUSEADDR重占，依据[微软Winsock说明](https://learn.microsoft.com/en-us/windows/win32/winsock/using-so-reuseaddr-and-so-exclusiveaddruse)。握手校验RPC/CDP不重号、返回号码与预留一致以及模式一致。取消、坏握手、超时路径均取回子进程句柄并kill/wait，再释放预留；包括spawn已创建进程但尚未把句柄交给调用方的窗口。正常模式显式清除继承的测试CDP参数。

验证：

- 新夹具8项与已有PID探针2项：**10 passed，0.67秒**。
- 原失败浏览器节点与握手即就绪节点：**2 passed，41.90秒**，日志 `cdp-real-directed.log`。
- 真实子进程强制RPC/CDP重号：拒绝启动、helper自身回收，零浏览器会话；记录 `cdp-collision-recheck.json`。
- 两修改测试文件Ruff与Mypy通过（Mypy使用--explicit-package-bases消除tests命名空间别名），git diff --check通过。

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/unit/test_companion_process_fixture.py tests/unit/test_companion_probe_delivery.py -q -o addopts='' -p no:cacheprovider --tb=short
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/integration/test_browser_companion_real_f07.py::BrowserCompanionRealTests::test_a_rewritten_form_action_is_never_written tests/integration/test_browser_companion_real_f07.py::BrowserCompanionRealTests::test_companion_is_reachable_immediately_after_its_handshake -q -o addopts='' -p no:cacheprovider --tb=short
.\.venv-win\Scripts\python.exe -m ruff check tests/support/companion_process.py tests/unit/test_companion_process_fixture.py
.\.venv-win\Scripts\python.exe -m mypy --explicit-package-bases --follow-imports=silent tests/support/companion_process.py tests/unit/test_companion_process_fixture.py
```

本修复排除本夹具启动RPC抢占CDP的窗口；握手后释放到Chromium绑定之间仍可能受外部进程争用，不宣称端口永久独占，不靠重试用例掩盖失败。当前启动一次完整串行本地门禁 `run-full-after-cdp.py` / `full-after-cdp.log`，首错停止；完整结果见下一节。原实例、真实网站、手机和数据库未操作。

### CDP修复后的完整门禁结果：RPC计时断言待处理

本轮只启动一次完整串行运行 `run-full-after-cdp.py`，结果 **1070 passed、160 skipped、1 failed、8 warnings，1466.31秒**，首个失败即停止；日志 `full-after-cdp.log`。先前的CDP与配置同步节点已在此轮通过。新失败节点为 `RpcRobustnessTests::test_rpc_call_timeout_includes_lock_wait`，测得0.813秒，超过原断言0.7秒；同时有Windows BaseSubprocessTransport析构的closed-pipe警告。

仅复跑该节点一次：**1 passed，1.86秒**，日志 `rpc-lock-deadline-recheck.log`。未重跑整套刷绿，未修改原断言。

只读检查表明：rpc.py在拿锁前计算同一个monotonic deadline并向exchange传剩余预算；超时后仍等待_break/_terminate，后者单独等待OS退出最多0.25秒并处理reader/stderr取消。测试将整次await计入0.7秒，仅比0.5秒预算多0.2秒，因此当前记录不足以证明deadline被重新计算。另一方面，elapsed断言失败会跳过first结果取回及client.close，tearDown只删除临时目录；与管道警告相符，但缺少分段时刻及allocation trace，不能断言全部警告都源于该处。

失败测试、core/extensions/rpc.py与async_utils.py均与HEAD `662af1b` 相同。下一步优先只修测试：独立验证deadline触发/IO中断与资源回收，在finally无条件取回任务和原进程，保留Worker退出和不自动重试断言；不能简单扩大0.7阈值或生产timeout。如果新证据显示生产回收所有权有缺口，再单独确定必要实现范围。

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B var/verification/core-reconcile-2026-11-07/run-full-after-cdp.py
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/unit/test_extension_rpc_robustness.py::RpcRobustnessTests::test_rpc_call_timeout_includes_lock_wait -q -o addopts='' -p no:cacheprovider --tb=short
```

完整运行器在子进程清理PA_*，PYTHONPATH加入tests/unit及tests/integration；实际pytest参数为 `-q -o addopts=--strict-markers -p no:cacheprovider --tb=short --import-mode=importlib --maxfail=1 --durations=10`。沿用此前已核对的收集配置，不删除/跳过失败节点。

CORE.RECONCILE.1仍BLOCKED；本次CDP夹具修复已通过定向与完整运行中的相关节点，不等于整阶段完成。本轮没有重跑完整PostgreSQL/Worker套件，未操作原库/原服务或真实网站，也未提交推送。

本轮收尾（2026-11-07 21:17 +08:00）：按PID和创建时间核对已记录的测试进程，残留0；精确测试标记 `run-full-after-cdp.py` / `worker_slowresponses` 残留0。证据为 `var/verification/core-reconcile-2026-11-07/cdp-final-process-check.json`；此结论仅覆盖上述范围，不声称全系统资源归零。`git diff --check` 通过，未停止原服务。

### 后续测试维护：RPC测试阻断已修正

本人要求精简套件后，已删除17项无行为价值的文档/源码字符串检查，并完成RPC计时与收尾、旧知识版本断言及同名收集问题的测试修正。关键集合643 passed、6 warnings（97.20秒），Ruff/Mypy290文件通过。本次没有重复完整门禁，也未改变CORE整阶段状态。日常命令现默认关键集合；详细删除映射与完整/专项入口见[测试指南](TESTING.md)。以上旧失败记录仍保留，但不再把RPC测试写为未修。
