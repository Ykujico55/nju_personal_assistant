# F07 会话清理测试时序修复报告（2026-10-20）

> 2026-10-20最终独立复验通过：用户实际运行相关160 passed、原两项串行5轮各2 passed，额外真实总deadline/清理失效边界检查通过；目标Ruff/Mypy/差异、118摘要匹配、测试运行器0。用户核对本报告实施者1405 passed/135 skipped/6 warnings日志，本次未重跑全量或PostgreSQL/Worker。**DELIVERY.2本地验收DONE，F08.6仍BLOCKED，F07/F08整体IN_PROGRESS**。下文“待复验/BLOCKED”保留为修复交接时快照；本次只更新状态文档，没有运行测试、修改产品、提交、推送或部署。

## 1. 结论与范围

用户独立确认上轮资源清理产品修复通过定向159项及额外10项反例，但新增集成测试把所有命令共用150ms预算，正常根撤销自己超时，主动取消的创建也可能早已自动超时；独立完整门禁为1402 passed、135 skipped、2 failed（19分18秒）。这证明测试不稳定，不证明产品资源泄漏复发。

本轮只改 `tests/integration/test_companion_failed_creation_f07.py` 及状态/报告/摘要文档。两份 Companion 产品文件、生产30秒命令截止时间、契约、数据库、迁移与此前已通过的其他修复全部保留。工作目录为 `C:\Users\kunji\Desktop\homework\生成式软件工程\lab1`，HEAD `9891102`，保留既有未提交批次。

**DELIVERY.2 继续 BLOCKED，待独立复验；F08.6 继续 BLOCKED；F07/F08 整体 IN_PROGRESS；F06.P2/最小 F09.1 DONE。** 本轮没有部署、重启原服务、操作原库、调用真实模型、邮箱或ehall，未提交/推送。

## 2. 测试修正

- 常规本机HTTP命令预算5秒；原两项增加可控300ms请求延迟，明确覆盖正常请求大于150ms的情况。主动取消必须等真实服务端driver进入且创建任务仍pending，再由测试调用cancel；正常根撤销保持真实回环HTTP。
- 故意丢创建回执只在真实HTTP已使driver进入启动后注入150ms响应读取超时（`httpx.ReadTimeout`）；只影响 `POST /v1/sessions`，自动根撤销仍使用正常预算。这是**真实回环HTTP后注入创建响应超时**，不是证明生产30秒总deadline自行到期，也不是实际浏览器启动超时复现。
- `wait_for_start` 每轮同时检查创建任务是否已结束；提前失败立即await原任务，保留原异常；提前成功也不能冒充已到pending边界。新增一项提前失败反例。
- 创建任务登记到统一列表，`tracked_creation` 的finally放开启动门闩并gather取回异常；超时注入内部的真实HTTP任务也在finally取消/取异常。finally不改变主断言结果，teardown仍先断言产品已释放，再做紧急测试清理。
- 正常测试会话TTL设30秒，避免一秒TTL提前替代被测的根撤销/取消动作；仅idle TTL用例在创建时给该实例注入1秒TTL，保留原2.5秒关闭事件上限，关闭前不查询状态或新建会话。

全部迟到driver.started、closed/alive=false、session_count=0、capability不返回/不保存、鉴权401/零close、重复撤销及独立TTL断言保留。没有增加生产超时或用shutdown后的状态替代主断言。

## 3. 先失败后修复及本轮结果

| 范围 | 实际结果 | 日志（位于 `var/verification/f07-cleanup-test-timing-2026-10-20/`） |
|---|---|---|
| 旧预算/旧等待 + 300ms正常请求及提前失败反例 | **3 failed、5 deselected**，10.26s | `red.log` |
| 修复后的单文件 | **8 passed**，11.74s | `target.log` |
| 原两项串行重复5轮 | **每轮2 passed**，共10次，无并行 | `repeat-1.log` 至 `repeat-5.log` |
| 相关合并回归 | **160 passed、2 warnings**，25.52s | `directed.log` |
| 完整本地门禁 | **1405 passed、135 skipped、6 warnings**，1306.38s（21:46），Ruff/Mypy263通过，exit0 | `full.log`、`gate-exits.json` |
| 静态检查 | Ruff通过；测试Mypy1文件、完整Mypy269文件；三份JS、pip check、diff check通过 | 第4节命令 |
| 材料核验与收尾 | 118项摘要通过，仅测试文件变化；活动测试运行器0，测试标签容器0，查询exit0 | `material-verification.json`、`active-test-processes-after.json`、`container-inventory-after.json` |

2个合并警告为TestClient/anyio依赖弃用。完整门禁6个警告为TestClient/anyio/cookie弃用5条及算法混淆负向用例的短HMAC测试键1条，完整保留原日志。135个跳过项为未配置测试数据库时的条件用例；本轮没有新增跳过。没有删除断言、无条件skip、扩展生产超时或重跑整套挑选绿色。

五轮耗时依次为2.75、2.47、2.57、3.60、2.64秒，每轮两个原节点均通过。修复文件8项在同一次完整本地门禁中也全部通过；`gate-exits.json`记录directed_exit=0/full_exit=0/repeat_rounds=5，测试运行会话已退出。活动pytest/临时数据库测试运行器为0；只读测试标签容器查询成功且为0，本轮没有创建数据库容器。资源回收主断言由测试支持，不据这些查询宣称所有系统进程或异步transport问题均已消除。

本轮不重跑完整PostgreSQL/Worker：只改测试夹具，没有改生产、共享状态、审批、Outbox或数据库。上一轮实施135 passed已获用户核对，仍仅作历史证据，不称本轮运行；完整本地将照常记录数据库条件skip。没有向原助手安装/升级扩展或操作真实外部服务；完整门禁的临时Worker夹具与原安装目录隔离。

## 4. 精确命令

失败先行，在仍保留旧150ms预算及旧等待循环时运行：

```powershell
$env:PYTHONUTF8='1'
.\.venv-win\Scripts\python.exe -m pytest -p no:cacheprovider -p no:faulthandler -o addopts=--strict-markers -q --tb=short tests/integration/test_companion_failed_creation_f07.py -k 'cancel_without_returned or cancelled_create or wait_for_start'
```

修复后单文件：

```powershell
.\.venv-win\Scripts\python.exe -m pytest -p no:cacheprovider -p no:faulthandler -o addopts=--strict-markers -q --tb=short tests/integration/test_companion_failed_creation_f07.py
```

原两项重复与完整门禁严格串行：

```powershell
$timingLogDir='var/verification/f07-cleanup-test-timing-2026-10-20'
$timingPython='.\.venv-win\Scripts\python.exe'
$timingNodes=@('tests/integration/test_companion_failed_creation_f07.py::CompanionFailedCreationTests::test_cancel_without_returned_capability_uses_host_authority','tests/integration/test_companion_failed_creation_f07.py::CompanionFailedCreationTests::test_cancelled_create_reclaims_late_start')
for ($timingRound=1; $timingRound -le 5; $timingRound++) {
    & $timingPython -m pytest -p no:cacheprovider -p no:faulthandler -o addopts=--strict-markers -q --tb=short @timingNodes *> "$timingLogDir/repeat-$timingRound.log"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
```

相关合并：

```powershell
.\.venv-win\Scripts\python.exe -m pytest -p no:cacheprovider -p no:faulthandler -o addopts=--strict-markers -q --tb=short tests/unit/test_companion_failed_create_client_f07.py tests/unit/test_companion_creation_lifecycle_f07.py tests/unit/test_companion_cleanup_cancellation_f07.py tests/integration/test_companion_failed_creation_f07.py tests/unit/test_companion_client_redaction_f07.py tests/unit/test_browser_host_capability_f07.py tests/unit/test_browser_session_broker_f07.py tests/unit/test_browser_executor_f07.py tests/unit/test_runtime_remediation_delivery.py tests/unit/test_runtime_crosscheck_delivery.py tests/unit/test_runtime_delivery.py tests/unit/test_workflows_delivery.py tests/api/test_delivery_remediation.py tests/unit/test_recovery_delivery.py
```

完整本地：

```powershell
$env:PYTHONUTF8='1'
$env:PYTEST_ADDOPTS='-vv --tb=short -p no:faulthandler -p no:cacheprovider'
Remove-Item Env:PA_TEST_DATABASE_URL -ErrorAction SilentlyContinue
& ./scripts/test.ps1 *> var/verification/f07-cleanup-test-timing-2026-10-20/full.log
Remove-Item Env:PYTEST_ADDOPTS -ErrorAction SilentlyContinue
```

沿用已说明的Windows诊断插件与缓存写入限制，禁用faulthandler/cacheprovider，不跳过业务断言。只启动一次冻结后的完整门禁。

静态：

```powershell
.\.venv-win\Scripts\python.exe -m ruff check src extension_sdk extensions tests scripts
.\.venv-win\Scripts\python.exe -m mypy tests/integration/test_companion_failed_creation_f07.py
.\.venv-win\Scripts\python.exe -m mypy src/personal_assistant extension_sdk/src/personal_assistant_sdk extensions/example_echo/src/example_echo extensions/personal_knowledge/src/personal_knowledge extensions/nju_smail/src/nju_smail extensions/nju_ehall/src/nju_ehall extensions/assistant_workflows/src/assistant_workflows scripts/assistant-backup.py scripts/assistant-upgrade.py scripts/configure-backup.py scripts/configure-model.py scripts/startup-task.py scripts/test-delivery-postgres.py
D:/nodejs/node.exe --check web/pwa/app.js
D:/nodejs/node.exe --check web/pwa/service-worker.js
D:/nodejs/node.exe --check web/admin/app.js
.\.venv-win\Scripts\python.exe -m pip check
git diff --check
```

## 5. 文件与材料

- 测试唯一改动：`tests/integration/test_companion_failed_creation_f07.py`。
- 状态：`TODO.md`、`docs/NEXT_STEPS.md`。
- 交接：本报告、上轮清理报告的后续审计提示、[本轮118项摘要](F07_SESSION_CREATION_TEST_TIMING_FILELIST_2026-10-20.json)。保留原摘要与旧日志。
- 修复前118项摘要全部匹配；本轮新摘要中只有该测试文件变化，产品、契约、迁移及其余117项保持不变。本轮未重新构建wheel，测试不属于wheel打包内容；保留上轮离线wheel作为历史交付物，不冒称本轮新构建。

最终材料与收尾核验命令：

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B var/verification/f07-cleanup-test-timing-2026-10-20/verify-material.py
docker ps --all --filter label=pa.delivery-test --format "{{.ID}} {{.Names}}"
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match '(?i)(-m\s+pytest|test-delivery-postgres\.py)' } | Select-Object ProcessId,ParentProcessId,CreationDate
Get-Content -LiteralPath var/verification/f07-cleanup-test-timing-2026-10-20/gate-exits.json
```

材料脚本核对118项当前摘要、仅测试文件相较上轮变化、旧迁移无差异，以及未重建的上轮wheel SHA-256 `317c5406fc0015a9dfcf5ba8eb276543f6689c9ecabcbc1c7c5b5c2f423739a4`和两份Companion模块字节保持一致。该wheel校验不是新构建或新部署。

## 6. 剩余范围与推荐下一步

本地验证通过仍需独立复验，不能自行解除 DELIVERY.2 BLOCKED。下一编号任务仍为 DELIVERY.2 本次测试时序补修的独立验收；通过后回到F08.6剩余真实环境证据。

F08.6的手机实际新脚本、真机丢回执重试、正式审批及敏感缓存、独立Cloudflare中断、真实通知仍待验收；无Google环境的ntfy手机客户端/外网配置未在本轮推进。F07正式真实事务Adapter、完整材料与实际目标/后果的权威预览、Gateway/Outbox实站链路仍未验收，完整真实填报因无确有需要事务延期。本次测试时序缺陷不等于这些真实验收完成，也不证明资源泄漏复发或原F07超时的唯一根因。
