# 剩余开发集中交付（2026-10-13）

> 2026-10-19 更新：本报告为 10-13 实施历史。后续独立验收发现 1 项 P1、2 项 P2，DELIVERY.2 已改为 BLOCKED；本轮修复、当前命令及摘要见 [修复报告](DELIVERY_2_REMEDIATION_2026-10-19.md)，不得以本报告历史数字替代当前验证。

## 1. 结论与授权

用户明确要求完成全部后续开发、非仅F08.6，保留本地验证，延期真实邮箱/通知/站点配置和联调；随后明确授权本批次提前完成真实模型配置/试运行。当前开发实现已形成可启动、可检查的本地交付包；最终门禁结果在第5节，不用旧轮数量冒充本轮。独立验收尚未进行，F07/F08整体IN_PROGRESS、F08.6 BLOCKED、已独立通过的F06.P2/F09.1保持DONE。

本轮代码全部保留在 `C:/Users/kunji/Desktop/homework/生成式软件工程/lab1`，HEAD为9891102。继承已有F06/ntfy改动，未提交、推送或部署；未迁移原助手库、重启原服务、访问真实邮箱或ehall、注册Windows启动任务或安装新的业务扩展。

## 2. 已实现文件与能力

| 开发包 | 主要新增/改动文件 | 行为与边界 |
|---|---|---|
| 通用持久命令 | core/records.py、infrastructure/memory/records.py、database/records.py、migrations/0012_runtime_records.sql、bootstrap.py | owner范围CAS、整数/布尔严格摘要、同键原回执、事务回滚；不改0001–0011 |
| 任务执行 | core/agent/{decisions,planner,runtime}.py、runtime.py、workers/{agent_worker,scheduler_worker}.py、tasks/service.py | 真实队列/租约/检查点、模型严格JSON提案、人工恢复/取消、审批后恢复精确材料、UNKNOWN只对账、原熔断器 |
| 声明工作流 | core/workflows.py、event_delivery.py、scheduling.py、extensions/assistant_workflows/、extensions/supervision.py、infrastructure/extensions/processes.py | 版本/工具/权限快照、ask/tool/archive、源游标/事件去重、有限misfire；新组合扩展只做静态验证，未安装/执行 |
| 模型接入 | infrastructure/models/{setup,openai_compatible}.py、scripts/configure-model.py、settings.py | OpenAI兼容DeepSeek、隐藏输入/WCM、公开参数/句柄、PUBLIC单次512token上限试运行；任务材料另须精确披露确认 |
| 规则审核 | core/governance.py、infrastructure/operations/rules.py、core/operations.py | 有限偏好/作用域、候选正反例、人工启用、专用Git历史和候选回滚；不改变风险/授权 |
| 产品与管理 | api/v1/delivery.py、api/admin_operations.py、admin_app.py、app.py、web/pwa/、web/admin/ | 任务运行/工作流材料/披露/冲突/原键重试/规则审核；Admin回环静态UI、扩展静态检查→精确确认→安装、调度/冻结 |
| Windows主机 | infrastructure/platform/windows/{operations,jobs,desktop}.py、operations/{host,control,logging}.py、host_entry.py、scripts/assistant-host.ps1、startup-task.py、deployment/ | 自有Job后代回收、交互用户登录启动、轮询文件变化、有限重启/磁盘冻结/元数据日志；未注册或实测生产冷启动 |
| 恢复/升级 | operations/{backups,recovery,upgrade}.py、database/connection.py、scripts/{configure-backup,assistant-backup,assistant-upgrade}.py | 6h数据库快照+增量加密制品、本机7日/4周/6月、空隔离目标/迁移校验/冻结恢复、固定材料副本和备份后离线升级 |
| 验证/文档 | tests/*/*delivery.py、tests/support/companion_process.py、scripts/test*.ps1、test-delivery-postgres.py、dependency.lock、README/TODO/NEXT/契约/本手册 | 新失败反例与实际证据；新增真实form.list枚举入PostgreSQL脚本；运行方式见RUNBOOK_LOCAL_BETA.md |

完整改动路径和摘要以本目录DEVELOPMENT_DELIVERY_FILELIST.json清单为准。已验收F06三份源/测试文件的摘要在本轮核对均保持不变，既有状态文件只续记，没有重写历史测试数字。

## 3. 安全边界与关键行为

- API只通过核心端口和组合根访问文件控制与Git历史，未向core引入Win32/数据库/模型厂商实现。
- PWA不加载扩展JavaScript；静态Worker v16只缓存原四条固定静态路径，API/正文/审批不进入CacheStorage或本地草稿持久化。
- 发送邮件仍只有Tool Gateway/审批/Outbox通路；发信配置关闭或权威材料缺失时明确暂停。没有新增自动发送外部副作用的旁路。
- 原命令回执与当前草稿/运行状态分开；同键重放不自动覆盖用户已采用的新版本或本机输入。
- 模型校验严格JSON/字段类型/工具版本/能力来源。远端PERSONAL/SENSITIVE输入需要当前精确请求的许可；SECRET硬阻断。取消R2进入UNKNOWN，不重规划或重发。
- 恢复排除凭据/会话/Push订阅/披露许可，审批全部失效、作业不重放、扩展/调度停用。新数据库名与空表检查在迁移前；不同句柄指向原库也拒绝。
- 新浏览器R2准备仍因正式F07权威绑定未齐而拒绝，试验预览不能批准。新表单组合声明不代表正式实站操作已可用。

## 4. 先失败后实现的本地证据

本批次逐项先运行红例，再最小实现。覆盖持久命令1/True/CAS/回滚、工作流材料/快照/工具版本、调度误触副作用、取消与审批恢复、模型披露、390px输入/坏回执/冲突、规则审核/Git回滚、Job后代关闭、加密损坏/路径穿越/空目标恢复、固定升级材料。

收尾新增隔离目标/材料复制反例：2 failed→8 passed相关回归；全量暴露API反向依赖及新目录/迁移预期：隔离集合6 failed/200 passed，修复后相关47 passed/2 warnings，未放宽安全断言。目录断言按example.echo的ID查找，允许新扩展加入；迁移枚举新增0012，冻结旧文件SHA断言保留。

Windows UTF8模式下tasklist输出使用本地编码，回收夹具抛UnicodeDecodeError/NoneType；新增2失败反例后按字节精确PID列解析，探针失败不能假称已回收：本地2单元+原真实浏览器取消检查共3 passed。没有修改F07浏览器产品代码或删除回收断言。

## 5. 本轮最终命令、日志与结果

命令在原Desktop仓库和 `.venv-win` 中实际执行。本轮完整本地门禁已经结束；数据库完整运行的4项枚举失败经下述影响集合复验关闭，没有重跑第二次完整PostgreSQL/Worker门禁，不称全部门禁一次全绿。

| 命令/范围 | 实际结果 | 证据 |
|---|---|---|
| `scripts/test.ps1`，设置下方PYTEST_ADDOPTS | **1373 passed、132 skipped、7 warnings**；Ruff通过；Mypy263文件通过；exit0 | `var/verification/development-delivery/full-final.log`，1938.86s |
| `python -Xutf8 scripts/test-delivery-postgres.py --all` | **128 passed、4 failed**；四项仅为0012不在迁移精确枚举 | `postgres-full.log`，1473.20s；finally已执行删除，独立复查超时 |
| 下方7项临时PostgreSQL复验 | **7 passed**；覆盖原4失败、新持久命令/恢复2项、真实Worker form.list1项；exit0 | `postgres-directed.log` / `postgres-migration-recheck.log`，74.07s |
| API/分层/迁移相邻回归 | **47 passed、2 warnings** | 实际目标命令见下；此后完整本地门禁也通过 |
| 恢复空目标、升级固定材料 | **8 passed**，新增反例先2 failed | 单元定向命令见下 |
| Windows PID编码/回收 | **3 passed**，新增2失败反例后修复 | `browser-cancel-fixed.log` |
| `ruff check src extension_sdk extensions tests scripts` | exit0 | 无诊断 |
| 下方完整Mypy源+SDK+扩展+脚本 | **269 source files**；exit0 | 无诊断 |
| `node --check` 三份JS、`pip check`、`git diff --check` | 均exit0 | JS语法通过、No broken requirements；差异仅既有LF/CRLF提示 |
| 离线pip wheel及ZIP逐字节核验 | exit0；**265条目/10web资源/12迁移**、无业务扩展源码 | `build-final.log`、`wheel-verification.json`；下方SHA-256 |

完整本地命令：

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTEST_ADDOPTS = '-vv --tb=short -p no:faulthandler'
Remove-Item Env:PA_TEST_DATABASE_URL -ErrorAction SilentlyContinue
& ./scripts/test.ps1 *> var/verification/development-delivery/full-final.log
```

132个跳过项为未在本地完整命令中设置数据库地址的既有数据库/Worker条件用例；通过单独隔离数据库命令运行，不新增跳过或删除业务断言。诊断插件停用是因为此前带60s堆栈诊断的一轮发生Windows access violation并中断；不宣称其底层原因已根治。初次全量在约14%停止定位，和诊断中断均不是完整通过证据；相应 `full.log/full-rerun.log` 保留。

临时库完整运行及修复后的精确复验：

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 scripts/test-delivery-postgres.py --all
$pgTargets = @(
  'tests/integration/test_postgres_f01.py::PostgresF01Tests::test_database_with_only_0001_upgrades'
  'tests/integration/test_postgres_f01.py::PostgresF01Tests::test_empty_database_migrates_and_reruns_are_noops'
  'tests/integration/test_postgres_f04.py::PostgresF04Tests::test_empty_database_applies_0005_and_rerun_is_noop'
  'tests/integration/test_postgres_f04.py::PostgresF04Tests::test_upgrade_from_0004_adds_only_later_migrations_and_keeps_checksums'
  'tests/integration/test_postgres_delivery.py'
  'tests/integration/test_pwa_form_catalog_worker_f08.py'
)
.\.venv-win\Scripts\python.exe -Xutf8 scripts/test-delivery-postgres.py --tests @pgTargets
```

脚本使用已有镜像pgvector17，随机回环端口/短期测试凭据/唯一容器标签，仅临时库，finally清理。第一次--all在创建容器时45s超时，未执行测试；只读检查Docker恢复及该精确名称不存在后重试，未启动或迁移原库。132项完整运行与7项复验均使用临时服务，不能当真实业务环境验收。复验只改F01/F04的精确0012枚举、12迁移数量及新增文件checksum断言，不修改任何迁移历史。

目标与静态命令（pytest除全量外均使用 `-p no:cacheprovider -o addopts=--strict-markers -q --tb=short`）：

```powershell
.\.venv-win\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts=--strict-markers -q --tb=short tests/api/test_api.py tests/api/test_boundaries.py tests/contract/test_f03_contract_consistency.py tests/contract/test_f05_contract_consistency.py tests/api/test_delivery_api.py tests/api/test_admin_operations_delivery.py
.\.venv-win\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts=--strict-markers -q --tb=short tests/unit/test_recovery_delivery.py tests/unit/test_upgrade_delivery.py
.\.venv-win\Scripts\python.exe -m pytest -p no:cacheprovider -p no:faulthandler -o addopts=--strict-markers -q --tb=short tests/unit/test_companion_probe_delivery.py tests/integration/test_browser_companion_real_f07.py::BrowserCompanionRealTests::test_cancel_reaps_the_browser_process
.\.venv-win\Scripts\python.exe -m ruff check src extension_sdk extensions tests scripts
.\.venv-win\Scripts\python.exe -m mypy src/personal_assistant extension_sdk/src/personal_assistant_sdk extensions/example_echo/src/example_echo extensions/personal_knowledge/src/personal_knowledge extensions/nju_smail/src/nju_smail extensions/nju_ehall/src/nju_ehall extensions/assistant_workflows/src/assistant_workflows scripts/assistant-backup.py scripts/assistant-upgrade.py scripts/configure-backup.py scripts/configure-model.py scripts/startup-task.py scripts/test-delivery-postgres.py
D:/nodejs/node.exe --check web/pwa/app.js
D:/nodejs/node.exe --check web/pwa/service-worker.js
D:/nodejs/node.exe --check web/admin/app.js
.\.venv-win\Scripts\python.exe -m pip check
git diff --check
.\.venv-win\Scripts\python.exe -m pip wheel --no-index --no-build-isolation --no-deps --wheel-dir var/verification/development-delivery/wheel .
```

Python build模块入口不可用，实际构建采用已安装setuptools的离线pip wheel。最终wheel：`personal_assistant_framework-0.1.0-py3-none-any.whl`，SHA-256 **30a0e3410b3d27b3f64af4bf69c34d83f038c110f9427fe18522b1b90442e213**。旧轮、失败构建或空结果不计作通过。ZIP核验确认7份PWA+3份Admin资源、12份迁移与当前源字节一致，业务扩展源码未进入核心wheel；本批次没有安装它。

收尾只读核对：本轮pytest进程均结束。临时容器删除路径已执行，之后Docker标签查询先后8s、20s超时，因此**不能独立确认临时容器剩余0**；后续审计只需按`pa.delivery-test`标签核查，不操作原容器。Worker/浏览器关闭断言通过；没有遍历并结束其他系统进程，不声称原线上服务已经验证健康。现有F06三份已验收源/测试摘要保持，当前源码文件清单见 [DEVELOPMENT_DELIVERY_FILELIST.json](DEVELOPMENT_DELIVERY_FILELIST.json)。清单含原工作区保留的F06/ntfy改动，排除忽略凭据、运行数据及会继续续记的报告/状态文档。

## 6. 真实模型证据

用户本人在交互PowerShell执行 `scripts/configure-model.py` 并反馈：Configured deepseek-flash / credential retained by Windows Credential Manager；ok=true，total_tokens104（prompt74/completion30），回复为“助手已成功连接模型，当前仅作说明，不执行任何外部动作”。这是用户运行的一次PUBLIC合成调用，未读取任务/邮箱，非真实邮件或完整Agent场景验收。官方接口资料：https://api-docs.deepseek.com/quick_start/pricing-details-cny/ 。生产进程未在本轮重启，不能据此称线上已加载新模型。

## 7. 未完成、风险及下一步

1. F08.6真实手机最新版、丢回执、审批/缓存、独立Cloudflare中断、无Google设备ntfy完整投递/撤销/锁屏/点击仍BLOCKED。不要求Google Play，不借模拟结果冒充真机。
2. F07正式事务Adapter、实际提交目标/完整payload/材料/后果权威准备、Gateway/Outbox实站验收仍延期；真实填报须确有需要，不制造申请。
3. 真实邮件账号/受控发件地址、启用新组合扩展、持久host profile与生产启动/24h/磁盘故障/备份恢复/升级演练由下一配置与验收批次执行，均未自行执行。
4. 文件监听当前为有界轮询；登录启动为InteractiveToken任务，非Session0服务。venv/Worker只做依赖和崩溃隔离；Job关闭不承诺防御同用户恶意代码。
5. 备份最大256MB、只支持当前Schema校验和匹配和无循环FK的导入；扩展Schema须先批准安装。异地加密副本尚无自动保留裁剪，需管理容量；凭据必须本人另备离线恢复材料。
6. 内存Admin/公共进程不共享状态，生产管理需PostgreSQL。开发模板只供本机体验，不能当持久生产。新源码和wheel仍待独立复验，不宣称Beta真实验收DONE。

下一编号：按本次排期例外收尾集中交付的独立本地复验，随后回到F08.6的真实配置/验收。实际操作请用 [运行手册](RUNBOOK_LOCAL_BETA.md)。
