# F07 创建失败会话清理实现与复验报告（2026-10-19）

> 2026-10-20 后续独立验收：资源回收修复通过定向及10项额外反例，但新增集成测试的统一150ms预算不稳定，独立全量1402 passed/135 skipped/2 failed；整体验收仍BLOCKED。本报告原门禁属于上一轮实施证据，不作撤回或冒充本轮。仅测试时序补修与新门禁见 [测试时序报告](F07_SESSION_CREATION_TEST_TIMING_2026-10-20.md)。

## 1. 状态与授权

用户独立复验确认此前 DELIVERY.2 的未决动作、工作流切换、执行中审批恢复三项修复通过，但新增 F07 相邻 P2：创建调用超时后，服务端迟到启动的会话没有 capability 可供宿主取消，而且 TTL 只在后续访问时被动检查。本轮只修复这一清理缺口并执行串行完整门禁。

工作目录为 `C:\Users\kunji\Desktop\homework\生成式软件工程\lab1`，基于 HEAD `9891102` 的既有未提交交付工作区；未覆盖原三项已验收修复。实现完成不等于独立验收：**DELIVERY.2 继续 BLOCKED**；**F08.6 继续 BLOCKED**；F07/F08 整体 IN_PROGRESS；F06.P2 与最小 F09.1 DONE。没有提交、推送、部署、操作原库/原运行服务或调用真实模型、邮箱、ehall。

## 2. 修复

- 客户端创建超时、取消、传输异常或坏成功回执时，以已知 session ID 和宿主根 capability 调用现有 `/v1/revoke`，不等会话 capability；没有会话 token 的显式 close/cancel 也使用此路径。空 ID 直接拒绝，防止误变成撤销全部。保持原创建失败，不吞异常返回成功。清理使用单个受保护任务，重复取消也先等待清理完成再传播取消。
- 服务端记录本进程已用/撤销 ID。撤销先于创建注册时，迟到的创建不能启动；撤销发生在启动期间时，先关闭当时已分配的资源，启动返回后再次关闭精确的原 driver，拒绝 capability。启动未退出的撤销会话仍占活动名额，不用取消绕过最大活动会话数。
- 每个会话注册时创建独立 TTL 任务，无后续请求也回收；关闭流程和记录清理由 shield 与会话关闭锁保护。关闭失败保留失效记录、返回清理失败，允许根撤销重试；shutdown 尝试全部会话，不因首个失败跳过其他项。TTL 并发回收时 diagnostics 迭代快照，避免字典改变导致诊断失败。
- 未增加 30 秒命令超时；未改审批、Outbox、UNKNOWN、数据库或迁移。仍是可信桌面代码及进程回收能力，不是恶意代码沙箱。

生命周期限制明确写入契约：ID 1–200 字符，最多 65,536 个历史 ID，满额拒绝新 ID并要求重启 Companion，不淘汰撤销记录；重启使旧根 capability 同时失效。若服务不可达、底层启动永不返回或物理关闭持续失败，不能宣称即时回收已确认；显式根重试及独立 TTL 是可用收尾路径，不是 OS 强杀保证。

## 3. 本轮文件

产品仅两份：

- `src/personal_assistant/infrastructure/browser/companion_client.py`
- `src/personal_assistant/infrastructure/browser/companion.py`

新增测试四份：

- `tests/integration/test_companion_failed_creation_f07.py`：真实回环 HTTP、可控延迟合成 driver，共 7 项；不启动真实站点。
- `tests/unit/test_companion_failed_create_client_f07.py`：坏回执、缺 token、重复取消、清理失败后显式重试、空 ID，共 5 项。
- `tests/unit/test_companion_creation_lifecycle_f07.py`：部分启动失败、活动容量、历史容量、清理失败后 TTL、shutdown 继续清理、诊断并发到期，共 6 项。
- `tests/unit/test_companion_cleanup_cancellation_f07.py`：关闭期间取消、二次取消，共 2 项。

文档为 TODO、NEXT_STEPS、契约、本报告、新摘要，以及原 DELIVERY.2 报告的历史提示。相邻已通过 F06 三份文件摘要保留，旧迁移历史不改。

## 4. 先失败后实现

1. 真实回环 HTTP 新反例在旧实现上 **6 failed、1 passed、1 teardown error**；错误同样来自未回收 driver，随后安全收尾先断言无残留再在 finally 紧急释放，不能吞掉断言。
2. 客户端最初替身缺 base_url，先修正 fixture，再对旧实现得到实际产品失败 **4 failed**；不把 fixture 错误算产品证据。
3. 生命周期首三项旧实现 **3 failed**。首轮实现后，清理再次取消反例 **2 failed**；关闭失败的 TTL/全会话收尾反例 **2 failed**。均保留断言后修复。
4. 独立 TTL 引入的 diagnostics 相邻反例先 **1 failed**（dictionary changed size during iteration），快照迭代修复后纳入合并。
5. 最终合并定向 **159 passed、2 warnings，15.29s**。既有真实本机浏览器取消回收用例另 **1 passed，11.54s**。不证明用户原全量超时根因，也不替代真实 ehall 或手机验收。

测试夹具的首次自动审批拒绝了无条件兜底关闭，因为它可能掩盖产品泄漏；改为先断言产品已释放，再在 finally 仅保障测试资源收尾，自动审批通过。没有删除/跳过/放松产品回收断言换取绿色。

## 5. 精确命令

所有命令在上述原仓库执行，Python 为 `.venv-win\Scripts\python.exe`。合并定向：

```powershell
$env:PYTHONUTF8='1'
.\.venv-win\Scripts\python.exe -m pytest -p no:cacheprovider -p no:faulthandler -o addopts=--strict-markers -q --tb=short tests/unit/test_companion_failed_create_client_f07.py tests/unit/test_companion_creation_lifecycle_f07.py tests/unit/test_companion_cleanup_cancellation_f07.py tests/integration/test_companion_failed_creation_f07.py tests/unit/test_companion_client_redaction_f07.py tests/unit/test_browser_host_capability_f07.py tests/unit/test_browser_session_broker_f07.py tests/unit/test_browser_executor_f07.py tests/unit/test_runtime_remediation_delivery.py tests/unit/test_runtime_crosscheck_delivery.py tests/unit/test_runtime_delivery.py tests/unit/test_workflows_delivery.py tests/api/test_delivery_remediation.py tests/unit/test_recovery_delivery.py
```

真实浏览器取消回收：

```powershell
.\.venv-win\Scripts\python.exe -m pytest -p no:cacheprovider -p no:faulthandler -o addopts=--strict-markers -q --tb=short tests/integration/test_browser_companion_real_f07.py::BrowserCompanionRealTests::test_cancel_reaps_the_browser_process
```

完整门禁严格串行，先第一条结束后第二条。禁用 pytest 写缓存和 Windows 不稳定的 faulthandler 诊断插件，不跳过业务用例：

```powershell
$env:PYTHONUTF8='1'
$env:PYTEST_ADDOPTS='-vv --tb=short -p no:faulthandler -p no:cacheprovider'
Remove-Item Env:PA_TEST_DATABASE_URL -ErrorAction SilentlyContinue
& ./scripts/test.ps1 *> var/verification/f07-creation-cleanup-2026-10-19/full.log
# 保存首项退出码后继续，即使首项失败也保留第二项证据
Remove-Item Env:PYTEST_ADDOPTS -ErrorAction SilentlyContinue
.\.venv-win\Scripts\python.exe -Xutf8 scripts/test-delivery-postgres.py --all --log-dir var/verification/f07-creation-cleanup-2026-10-19 *> var/verification/f07-creation-cleanup-2026-10-19/postgres-runner.log
```

完整 PostgreSQL 入口读取 `scripts/test-postgres.ps1` 显式枚举，使用已缓存 pgvector:pg17、独立标签、随机回环端口及临时合成口令，finally 只删除本次容器。不会读取原助手 DSN 或迁移原库。

静态检查（当前已全部通过）：

```powershell
.\.venv-win\Scripts\python.exe -m ruff check src extension_sdk extensions tests scripts
.\.venv-win\Scripts\python.exe -m mypy src/personal_assistant extension_sdk/src/personal_assistant_sdk extensions/example_echo/src/example_echo extensions/personal_knowledge/src/personal_knowledge extensions/nju_smail/src/nju_smail extensions/nju_ehall/src/nju_ehall extensions/assistant_workflows/src/assistant_workflows scripts/assistant-backup.py scripts/assistant-upgrade.py scripts/configure-backup.py scripts/configure-model.py scripts/startup-task.py scripts/test-delivery-postgres.py
D:/nodejs/node.exe --check web/pwa/app.js
D:/nodejs/node.exe --check web/pwa/service-worker.js
D:/nodejs/node.exe --check web/admin/app.js
.\.venv-win\Scripts\python.exe -m pip check
git diff --check
```

Ruff 通过、Mypy **269 source files**、三份 JS、pip check 和 diff check 通过；Git 的既有 CRLF 提示不代表检查失败。

## 6. 本轮完整门禁与交付物

| 范围 | 本轮实际结果 | 日志 |
|---|---|---|
| 合并定向回归 | **159 passed、2 warnings**，15.29s | 第5节命令 |
| 真实本机浏览器取消回收 | **1 passed**，11.54s | 第5节命令 |
| 完整本地 | **1404 passed、135 skipped、8 warnings**，909.01s；Ruff/Mypy263通过 | `var/verification/f07-creation-cleanup-2026-10-19/full.log` |
| 完整临时 PostgreSQL/真实 Worker | **135 passed**，614.74s，exit0 | 同目录 `postgres-full.log`、`postgres-runner.log` |
| 独立静态检查 | Ruff、Mypy269、三份JS、依赖和差异检查通过 | 第5节命令 |
| 交付材料 | wheel265条目、10web/12迁移、118项摘要通过 | 同目录 `build.log`、`wheel-verification.json` |
| 资源收尾 | 活动 pytest/数据库测试运行器 **0**；测试标签容器 **0**，查询exit0 | 同目录 `active-test-processes-after.json`、`container-inventory-after.json` |

本轮完整本地 F07 原超时节点在首次串行门禁即通过；不据此证明超时唯一根因或取消时序缺口是该超时根因。135 个 skipped 均为无测试数据库时的条件集，全部由随后完整临时数据库门禁运行，没有新增无条件跳过。8 个警告保留原日志：TestClient/anyio及cookie弃用5条、算法混淆负向用例的短HMAC测试键1条、既有Supervisor测试中Windows异步pipe/subprocess传输对象析构异常2条。后两条的底层来源本轮未定位，不据测试通过宣称所有资源问题均已消除；不扩展本次P2范围修改Supervisor，也不升级依赖或压掉警告换取绿色。

本轮完整门禁均为首次冻结代码后的串行执行，`gate-exits.json` 记录 `full_exit=0` 与 `postgres_exit=0`；运行会话已退出。测试标签查询已确认无残留容器，只读进程查询已确认无活动 pytest/临时数据库运行器。浏览器/Worker回收由各用例断言支持，不据此宣称其他系统进程或所有异步transport对象均无残留；原库/原服务未操作。

材料与收尾可复核命令：

```powershell
.\.venv-win\Scripts\python.exe -c "import hashlib,json,pathlib; items=json.loads(pathlib.Path('docs/F07_SESSION_CREATION_CLEANUP_FILELIST_2026-10-19.json').read_text(encoding='utf-8'))['items']; assert len(items)==118; assert all(hashlib.sha256(pathlib.Path(item['path']).read_bytes()).hexdigest()==item['sha256'] for item in items); print('118 hashes match')"
docker ps --all --filter label=pa.delivery-test --format "{{.ID}} {{.Names}}"
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match '(?i)(-m\s+pytest|test-delivery-postgres\.py)' } | Select-Object ProcessId,ParentProcessId,CreationDate
Get-Content -LiteralPath var/verification/f07-creation-cleanup-2026-10-19/gate-exits.json
```

旧轮的 38/135 与 1383 passed/135 skipped/1 failed 是历史报告结果，不计入本轮。用户独立复验的 38+11+3 及 F07 节点 3/3 同样单列为用户证据，不冒充实施者本轮执行。

### 离线打包与材料核验

```powershell
.\.venv-win\Scripts\python.exe -m pip wheel --no-index --no-build-isolation --no-deps --wheel-dir var/verification/f07-creation-cleanup-2026-10-19/wheel . *> var/verification/f07-creation-cleanup-2026-10-19/build.log
```

实际 **265 条目、10 web 资源（7 PWA + 3 Admin）、12 迁移**，两份 Companion 修复模块及所有网页/迁移与源码逐字节相等，不含业务扩展源码。wheel SHA-256：`317c5406fc0015a9dfcf5ba8eb276543f6689c9ecabcbc1c7c5b5c2f423739a4`；核验结果保留于同目录 `wheel-verification.json`。

本轮 [118 项摘要](F07_SESSION_CREATION_CLEANUP_FILELIST_2026-10-19.json) 包括保留的原 112 项材料与新增的 2 产品/4 测试文件。原 112 项中只有 Companion 生命周期契约更新；F06 已独立通过的 3 份文件摘要一致，0001–0011 历史迁移无差异，0012 与原摘要一致；未覆盖旧摘要文件。

## 7. 未完成项、风险与下一步

- 等待独立复验本 P2；完整门禁通过也不自动把 DELIVERY.2 改 DONE。
- 不证明原 F07 全量创建超时的唯一根因；该问题与资源回收缺口分别记载。
- F07 正式事务 Adapter、实际目标/完整材料/后果绑定的权威预览、正式 Gateway/Outbox 实站链路尚未验收；真实完整填报因无确有需要事务延期，不制造申请或点击最终提交。
- F08.6 的手机实际新脚本、真机丢回执重试、正式审批及敏感缓存、独立 Cloudflare 中断、真实通知完整流程仍缺证据。华为无 Google 环境的 ntfy 接线保持本地范围，手机客户端/外网配置待本人配合。
- 下一编号任务仍为 **DELIVERY.2 本次清理修复的独立复验**；通过后再回 F08.6 剩余验收，不因本轮实现启动其他功能。
