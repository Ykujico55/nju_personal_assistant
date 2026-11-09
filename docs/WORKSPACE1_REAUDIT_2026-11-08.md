# WORKSPACE.1 修复后独立复验（2026-11-08）

结论：**未通过，NEXT / BLOCKED 保持**。五个原失败参数实例本轮全部通过，A01–A03 关闭；A04 原重复取消反例已修好，但领取租约阶段还有一个未纳入清理的取消窗口。该残余问题分别由内存反例和真实 PostgreSQL + 生产扩展 shutdown 路径复现，按 **A04 残余 / P2** 记录，不另算两个问题。没有修产品、安装或操作原实例。

## 基线与范围

依据 `WORKSPACE1_FIXES_2026-11-08.md`，沿用首次验收限制：合成目录、临时数据库、受影响专项；不重复完整套件，不做手机、真实模型、邮件或网页操作。实际 TUI 人工检查仍以候选通过并另获安装授权为前提，未进入该阶段。

HEAD 仍为 `a35cf8a1f8d259cf2823cadfed6877c81505f0ae`，候选未提交。状态文档更新前，修复报告 `files.sha256.json` 的 **61 个文件全部匹配**。对照首次审计摘要，产品改动确实仅五文件：宿主 workspace/TUI app、知识 store/worker/indexing。没有改弱原五个反例的关键断言。候选材料重算一致：

- files 0.1.1：`sha256:8622e7f7bb9fbe56258907c123fc404cb5d3e7ae10de9c3b2d74ad55e9fd6c87`。
- knowledge 0.1.7：`sha256:0e28d619013225bcf7724633a19d6a427c82253747690cb01a8a5d97e641be2f`。

证据目录：`var/verification/workspace1-reaudit-2026-11-08/`。本轮只新增四个独立审计脚本、日志、资源记录和本报告，更新 README/TODO/NEXT_STEPS 状态；产品、既有测试、原反例和历史报告未修改。

## 唯一剩余阻断：A04 领取提交与响应之间取消会漏收尾（P2）

位置：`extensions/personal_knowledge/src/personal_knowledge/indexing.py:32–33`。`claim_build(...)` 的 await 在第 57 行开始的 try/finally 之外。

最小过程：

1. 用真实 SQL broker 领取构建租约，数据库事务已经提交。
2. 暂缓这次领取结果的交付；此时未开始扫描。
3. 调用生产 `PersonalKnowledgeExtension.shutdown()`，它取消后台自动索引任务。
4. 取消从领取 await 抛出，未进入现有受保护清理，也没有执行 owner CAS 终结。

真实 PostgreSQL 反例实际输出：

```text
shutdown_returned=True
committed_lease_state=RUNNING
committed_lease_active=True
successor_claimed=False
indexed_sources=0
```

`test_claim_cancel_postgres_reaudit.py::ClaimCancelPostgresReaudit::test_shutdown_after_claim_commit_before_reply_releases_lease`：**1 failed，4.21 秒**，日志 `claim-cancel-postgres.log`。使用生产 KnowledgeStore、PostgresExtensionDataAccess、自动索引与扩展 shutdown，延迟的是已经真实提交的事务结果，没有伪造领取成功。它是进程内生产扩展加真实数据库的确定性故障注入，不是已安装 Worker/真实服务的现场故障记录。

对应轻量反例 `test_cleanup_reaudit.py::test_cancel_after_claim_commit_before_response_releases_owner`：**1 failed，0.99 秒**，日志 `cleanup-claim-window.log`，使用合成文件和 FakeKnowledgeStore，同样观察到任务已取消但继任领取失败。

生产边界依据：`infrastructure/database/extension_data.py:154–171` 在事务退出提交后才交付结果；SDK `host.py:188–201` 等待响应的 future 可以取消，取消不会撤销已经提交的 SQL；知识 `worker.py:208–211` 在停止时取消自动索引。这使提交后、响应前取消成为实际可达窗口。

影响限于取消收尾和可用性：正常 shutdown 返回后，无主有效租约仍可阻挡继任索引，最多至原 **60 秒**租期到期。本轮没有观察到失租写入、文件损坏、覆盖继任索引或无限阻塞；不扩大为数据安全破坏。但它未满足“取消完成前完成租约收尾”的已接受契约，与原 A04 属于同一类未完成清理。

为什么原反例仍全绿：原 A04 及新增永久用例在领取已返回之后取消，包括扫描 IO 和 finish 等待；新的保护区确实覆盖这些路径，但未覆盖领取本身。

建议：把已发出的领取请求纳入取消保护，先等待/确定该 owner 的领取结果，再完成 owner CAS 收尾并传播取消。**仅把 try 向前移动不充分**，否则 finish 可能先于迟到的 claim 提交，再次遗留租约。修复后保留两份原样失败反例，并覆盖领取返回 false、提交前取消、提交后响应取消、重复取消及继任已接管的收尾，不改变生产租期或原断言。

## 已关闭事项与新增验证

| 项目 | 独立结果 |
|---|---|
| A01 | 原两参数反例通过；正文和摘要来自一致材料。验证后另存 B 被最终 CAS 拒绝；确认等待中另存 B 不会被应用；回执变为其他目标时 TUI 提示重新读取，不误报本次成功 |
| A02 | 原真实 PG 反例显示续租 false、写入 false。实际等租约锁、元数据目标锁、数据目标锁以及未提交 INSERT 唯一键冲突跨过期均被拒绝或事务回滚；继任能领取并写入 |
| A03 | 原反例现在返回 FAILED / INDEX_BUILD_BUSY；状态采用数据库 active，不以宿主时钟替代，不再把过期 RUNNING 作为进度 |
| A04 已修部分 | 原三次取消反例通过；扫描 IO 和 owner CAS 结束后才返回取消，清理失败原因保留。旧 owner 晚收尾不会覆盖继任 READY；领取返回后、扫描前取消能释放租约 |
| 正常 A/B 流程 | 源码双 Worker 自动索引 A、实际创建总结、保存 B 仍用 A、应用 B 后只检索 B、旧计划 CONFIGURATION_CHANGED、重启复用 READY、撤销拒访全部通过，无 reindex 命令 |

A02 的不存在目标键 INSERT 冲突用生产 scoped store 验证；没有将裸 helper 的理论窗口夸大为生产漏洞。源 Worker 流程只更换启动器来使用已有解释器，没有安装、打包、升级或写入原实例扩展登记；也不冒称实际 TUI 新对话人工验收。

## 实际命令与结果

以下组分开记录，有重叠不相加冒称总套件。全部在仓库根执行，先清除子进程继承的 `PA_*`、`PYTHONPATH`：

```powershell
Get-ChildItem Env: | Where-Object { $_.Name -like 'PA_*' -or $_.Name -eq 'PYTHONPATH' } |
  ForEach-Object { Remove-Item -LiteralPath ('Env:' + $_.Name) }
$old = 'var/verification/workspace1-audit-2026-11-08'
$new = 'var/verification/workspace1-reaudit-2026-11-08'
```

| 选择 | 结果 | 日志 |
|---|---|---|
| 宿主三文件 + 原 A01 | 29 passed、2 warnings，30.17s | `confirmation-host.log` |
| TUI 两文件 | 20 passed，19.28s | `confirmation-tui.log` |
| 新 A01 组合三项 | 3 passed，4.04s | `confirmation-independent-final.log` |
| 知识九文件 + 取消专项 + 原 A03/A04 | 137 passed，55.24s | `knowledge-cleanup.log` |
| 失租晚取消/扫描前取消两个组合 | 2 passed，0.75s | `cleanup-independent.log` |
| 领取提交后取消，轻量 | 1 failed，0.99s | `cleanup-claim-window.log` |
| PG 现有14项 + 原 A02 + 双 Worker | 16 passed，113.19s | `postgres-original-worker.log` |
| PG 已有目标行等锁三项 | 3 passed，35.04s | `postgres-extra.log` |
| PG 不存在目标键 INSERT 冲突 | 1 passed，7.03s | `postgres-insert-conflict.log` |
| 领取提交后取消，真实 PG | 1 failed，4.21s | `claim-cancel-postgres.log` |
| 改动路径 Ruff / 五产品文件 Mypy | 均通过 | `ruff.log` / `mypy.log` |

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/unit/test_instance_workspace_store.py tests/unit/test_workspace_runtime.py tests/api/test_admin_workspace.py "$old/test_races_audit.py" -q -s -o addopts= -p no:cacheprovider --tb=short
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/unit/test_tui_workspace.py tests/unit/test_tui_configuration_apply.py -q -o addopts= -p no:cacheprovider --tb=short
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest "$new/test_confirmation_reaudit.py" -q -s -o addopts= -p no:cacheprovider --tb=short
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/unit/test_knowledge_auto_index.py tests/unit/test_knowledge_lease_status.py tests/unit/test_knowledge_lease_fencing.py tests/unit/test_knowledge_binding.py tests/unit/test_knowledge_workspace.py tests/unit/test_knowledge_documents.py tests/unit/test_knowledge_index.py tests/unit/test_knowledge_index_metadata.py tests/unit/test_knowledge_instance_workspace.py tests/unit/test_knowledge_index_cancellation.py "$old/test_index_audit.py" -q -s -o addopts= -p no:cacheprovider --tb=short
# 两个正向组合先运行；新失败窗口单独执行，不将其冒充原组合通过。
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest "$new/test_cleanup_reaudit.py::test_late_cancelled_owner_cleanup_preserves_successor_ready" "$new/test_cleanup_reaudit.py::test_cancellation_during_postclaim_status_releases_lease" -q -s -o addopts= -p no:cacheprovider --tb=short
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest "$new/test_cleanup_reaudit.py::test_cancel_after_claim_commit_before_response_releases_owner" -q -s -o addopts= -p no:cacheprovider --tb=short
```

真实 PG 复现须另建专属无挂载临时服务，使用已有镜像，不使用原实例 DSN。本轮端口 **45623 已回收，不可复用**：

```powershell
docker run --rm -d --name pa-workspace1-reaudit-20261108 --label pa.audit=workspace1-reaudit-20261108 -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=workspace1_reaudit_test -p 127.0.0.1::5432 pgvector/pgvector:pg17
docker port pa-workspace1-reaudit-20261108
# PORT仅替换为本次专属临时容器实际回环端口。
$env:PA_TEST_DATABASE_URL='postgresql://postgres@127.0.0.1:PORT/workspace1_reaudit_test'
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/integration/test_knowledge_meta_postgres.py "$old/test_lease_clock_audit.py::LeaseClockAudit::test_expired_owner_cannot_renew_after_lock_wait" "$old/test_source_worker_audit.py" -q -s -o addopts= -p no:cacheprovider --tb=short
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest "$new/test_lease_reaudit.py::LeaseTargetLockReaudit::test_publish_checks_expiry_after_metadata_target_lock" "$new/test_lease_reaudit.py::LeaseTargetLockReaudit::test_cas_checks_expiry_after_metadata_target_lock" "$new/test_lease_reaudit.py::LeaseTargetLockReaudit::test_fenced_data_target_wait_rolls_back_after_expiry" -q -s -o addopts= -p no:cacheprovider --tb=short
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest "$new/test_lease_reaudit.py::LeaseTargetLockReaudit::test_fenced_publish_uncommitted_missing_target_rolls_back" -q -s -o addopts= -p no:cacheprovider --tb=short
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest "$new/test_claim_cancel_postgres_reaudit.py::ClaimCancelPostgresReaudit::test_shutdown_after_claim_commit_before_reply_releases_lease" -q -s -o addopts= -p no:cacheprovider --tb=short
docker stop pa-workspace1-reaudit-20261108
docker ps -a --filter name=pa-workspace1-reaudit-20261108
```

静态复验：

```powershell
.\.venv-win\Scripts\python.exe -m ruff check --no-cache src/personal_assistant/infrastructure/extensions/workspace.py src/personal_assistant/cli/tui/app.py extensions/personal_knowledge/src/personal_knowledge/store.py extensions/personal_knowledge/src/personal_knowledge/worker.py extensions/personal_knowledge/src/personal_knowledge/indexing.py tests/unit/test_instance_workspace_store.py tests/unit/test_tui_workspace.py tests/integration/test_knowledge_meta_postgres.py tests/unit/test_knowledge_lease_status.py tests/unit/test_knowledge_index_cancellation.py tests/unit/fake_knowledge_store.py
.\.venv-win\Scripts\python.exe -m mypy src/personal_assistant/infrastructure/extensions/workspace.py src/personal_assistant/cli/tui/app.py extensions/personal_knowledge/src/personal_knowledge/store.py extensions/personal_knowledge/src/personal_knowledge/worker.py extensions/personal_knowledge/src/personal_knowledge/indexing.py
git diff --check
```

## 收尾与限制

- 首轮新 TUI 夹具 **1 failed / 2 passed** 是直接适配器未经过 HTTP 中文错误映射，误期待“重新读取”文案；更正为精确 `CONFIG_REVISION_CONFLICT` 后 3 项通过，活动目录/摘要断言不变。`confirmation-independent.log` 保留，不当产品失败或删除记录。
- 沿用上轮已证实的 Windows 沙箱 asyncio socketpair 限制，本轮异步专项直接获准在解除限制的环境执行；所有数据库仍仅本轮临时容器。
- 各随机测试数据库均清理。停止容器前只有 postgres/template0/template1/专属 base；匹配测试/Worker Python 进程 **0**，容器停止后复查 **0**。证据 `resources-final.json`。
- `git diff --check` 通过，迁移目录相对 HEAD 无改动。无改动文件插件78项及原独立文件9项沿用上轮证据，未重复；未运行完整关键回归、完整本地/PG套件或打包安装。代码摘要一致证明本轮没有修产品，但不等于产品验收通过。
- 下一步仍是 **WORKSPACE.1 / A04 残余修复与定向复验**，不启动 FILES.BACKUP.1，不授权安装。其他编号状态不变。
