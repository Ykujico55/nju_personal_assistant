# WORKSPACE.1 独立验收（2026-11-08）

结论：**未通过，WORKSPACE.1 为 NEXT / BLOCKED**。发现 1 项 P1、3 项 P2；保留 5 个失败参数实例，未修改产品实现。不能据既有专项通过进入候选安装。下一项仍是 WORKSPACE.1 修复与定向复验，FILES.BACKUP.1 不启动。

## 范围与基线

- 依据 `WORKSPACE1_2026-11-08.md` 逐项复核目录授权、配置应用竞态、自动索引、失租禁写和旧计划失效。
- HEAD：`a35cf8a1f8d259cf2823cadfed6877c81505f0ae`；候选是未提交工作区改动。记录状态之前，交付 `files.sha256.json` 的 **57 个文件全部匹配**；两个候选材料摘要亦与报告一致：files `8622e7f7bb9fbe56258907c123fc404cb5d3e7ae10de9c3b2d74ad55e9fd6c87`，knowledge `8a0c7b78d5d5020c46ea64f7819e1a7941c7dc2bf89ea26186b91929a83209c7`。本次文档状态变更后文档摘要自然变化，产品源码未变。
- 全程合成 A/B 目录、临时 SQLite、专属无挂载 PostgreSQL/pgvector 容器。未读取个人知识库、原配置或原数据库。
- 按用户明确限制，只运行受影响专项及关键反例，不运行日常完整关键回归、完整本地/PG 套件，不重复 Ruff/Mypy/依赖全量检查。迁移目录相对 HEAD 无改动，`git diff --check` 通过（仅 Git CRLF 提示）。未构建或安装扩展、提交、部署、调用真实模型、邮件或网页提交。
- 证据目录：`var/verification/workspace1-audit-2026-11-08/`。审计脚本位于该目录，未混入产品测试或放宽已有断言。

## 阻断问题

### W1-A01 / P1：应用确认显示 A，实际可授权 B

位置：`src/personal_assistant/infrastructure/extensions/workspace.py:140–142,159–167`；消费位置 `src/personal_assistant/cli/tui/app.py:1448,1474–1488`。

`get_workspace()` 分别读取草稿和状态。若另一个宿主在读出 A 草稿后保存 B，返回值可能是 `config=A`、`saved_digest=hash(B)`。结尾仅比较前后状态，没有核对返回草稿与摘要。TUI 展示 A，却把同一返回值里的 B 摘要交给 apply，服务端 CAS 校验通过，实际应用 B。另一个宿主同时应用 B 时还可能显示 A 已 APPLIED 且 `refresh_required=false`。

独立反例 `test_races_audit.py::test_workspace_view_binds_displayed_draft_to_apply_digest` 两个参数均失败；使用两个真实磁盘配置存储实例，在 await 边界插入真实保存/应用，无伪造摘要。实际证据包含：

```text
displayed_root=.../A
applied_root=.../B
displayed_digest_matches=false
refresh_required=false
```

影响：目录授权确认材料与生效目标错配，不能认为用户确认了实际应用的目录。现有竞态测试覆盖运行副本、初始化与调用，并未绑定 GET 返回的草稿正文与 apply 摘要。

建议：从同一锁定快照返回正文和摘要；或发现摘要不匹配就重新读取/拒绝确认。TUI 对不一致材料也应拒绝。保留“另一宿主只保存 B”和“保存并应用 B”两个回归。

### W1-A02 / P2：等锁跨过到期时间后，旧 owner 可续活并写入

位置：`extensions/personal_knowledge/src/personal_knowledge/store.py:567–575`（`renew_build`）。

续租条件和新期限使用 PostgreSQL `now()`，它固定在事务开始时。续租事务在旧租约有效时开始，等待 lease 行锁直至旧租约过期；释放锁后仍按旧时刻检查并续租成功。随后 `LeaseDataClient` 的真实时间栅栏接受已被续活的 owner。

真实 PostgreSQL 反例 `test_lease_clock_audit.py::LeaseClockAudit::test_expired_owner_cannot_renew_after_lock_wait`：领取 3 秒租约，另一连接锁住 lease 行；确认 renew 已在数据库等锁，等到原 expiry 后释放。实际输出：

```text
expired_before_unlock=True
renew_returned=True
renewed_expiry_in_future=True
expired_owner_fenced_write_committed=True
```

结果 **1 failed，7.31 秒**，见 `lease-clock-final.log`。写入真实临时表并经过生产 scoped SQL broker 和租约事务栅栏。没有证明覆盖已成功接管的新 owner；已证明“租约一旦过期，旧 owner 禁止继续写”不成立。

现有测试在调用 renew 前直接令租约过期，未覆盖“调用时有效、等锁期间过期”。建议锁定后用数据库实际当前时间复核且不允许过期 owner 复活，同时核对领取、元数据发布等同类时间条件；增加等锁过期及继任 owner 竞争回归。

### W1-A03 / P2：过期 RUNNING 租约掩盖已经结束的索引失败

位置：`extensions/personal_knowledge/src/personal_knowledge/worker.py:485–490`。

概览只根据租约 `state=RUNNING` 返回 INDEXING，没有判断 `expires_at`，且优先于本副本 `_indexing_error`。另一 owner 失联后，本副本等待已结束，仍可永久显示“构建中”。

`test_index_audit.py::test_expired_foreign_lease_does_not_hide_finished_background_failure` 使用合成文件与 `FakeKnowledgeStore`，先实际执行自动构建等待失败，再令对方租约到期：

```text
task_done=True
background_error=INDEX_BUILD_BUSY
lease_expires_at=0
status=INDEXING / INDEX_BUILD_RUNNING
```

这是状态投影反例，不冒称真实 PostgreSQL 故障切换。现有用例只覆盖有效 RUNNING/显式 FAILED。建议用数据库权威时间判断租约是否仍有效；过期且无人构建时返回明确失败/待恢复，不能把停滞显示为进度。

### W1-A04 / P2：重复取消跳过租约终结

位置：`extensions/personal_knowledge/src/personal_knowledge/indexing.py:79–85`。

finally 中等待扫描任务的 `asyncio.gather` 可再次被取消，继而跳过 `finish_build`。`test_index_audit.py::test_repeated_cancel_joins_blocked_scan_io_and_releases_owner` 在实际文件扫描线程阻塞时连续取消三次，放行线程后检查：

```text
io_completed=True
task_done=True
lease_state=RUNNING
successor_claimed=False
```

线程被回收，但继任构建仍被无主租约挡住，最长至原 60 秒租期结束。测试存储为 `FakeKnowledgeStore`；根因是清理控制流未执行，与 PostgreSQL 原子性无关。未观察到文件或索引数据损坏。原测试只覆盖单次取消。

建议保护完整的“取消子任务、等待 IO 回收、owner CAS 终结”清理单元，完成后重抛取消。不得忽略取消或提前声称收尾成功。W1-A03 和 W1-A04 的两项最终反例合计 **2 failed，1.98 秒**，见 `index-counterexamples-confirm.log`。

## 已验证事实与边界

| 范围 | 本次独立结果 | 日志 |
|---|---|---|
| 宿主存储、运行副本、Admin 工作区及新竞态 | 23 passed / 2 failed / 2 warnings，41.03s | `workspace-races-final.log` |
| Admin 插件配置、TUI 和知识槽契约 | 61 passed / 2 warnings，97.80s，exit 0 | `ui-contract-escalated.log` |
| 知识索引、租约、绑定、SDK/RPC 十文件专项 | 138 passed，69.78s | `knowledge-sdk-escalated.log` |
| 文件插件完整专项 | 78 passed，57.97s | `files-existing-escalated.log` |
| 新文件授权/计划/CLI 反例 | 9 passed，13.69s | `files-independent-escalated.log` |
| 真实 PG 元数据、租约栅栏八项 | 8 passed；同次 Worker 审计夹具首次有字段错误 | `postgres-worker-escalated.log` |
| 源码双 Worker A/B 操作序列 | 1 passed；同次租约反例夹具统计快照未刷新而超时 | `pg-independent-final.log` |
| 新 PG 等锁过期反例 | 1 failed，7.31s | `lease-clock-final.log` |
| 新索引状态及重复取消反例 | 2 failed，1.98s | `index-counterexamples-confirm.log` |

各组分开记录，未把有失败的组合写成整组通过。TUI 日志包含 Windows `platform._wmi_query` 的 `0x8007000e` 诊断，随后 pytest 实际完成 61 项且退出 0；此环境诊断保留，未掩盖或当产品回归失败。

文件独立反例确认：A→B→A 后 write/mkdir/delete 旧计划均 `CONFIGURATION_CHANGED`；B 仅保存仍可读写 A；重建配置存储保持修订；撤销清空已缓存服务授权；read_write 降为 read_only 拒绝新旧写；CLI 确认期间变更任一权威快照拒绝删除。原文件未被失败计划修改。

真实源 Worker 验证只替换进程启动器，以已有解释器及明确源码路径启动两个候选；生产 RPC、监督器、配置快照、SQL broker、索引实现及文件 SQLite 保持真实。没有调用 installer 或写入持久扩展安装登记，因此不能证明打包/安装/生命周期恢复流程。

| 用户关键操作 | 本轮非 TUI 合成验证 |
|---|---|
| 两插件绑定，应用 A | 无 reindex 命令，自动 READY；搜索返回 A 正文/来源 |
| 创建子目录并保存总结 | 文件实际生成且正文精确匹配 |
| B 仅保存 | PENDING，仍搜索 A，scan_id 不变 |
| 应用 B | 自动建立新绑定；搜索 B 成功，搜索 A 为空，旧文件直读不返回 A 正文 |
| 执行 A 原写计划 | `NEEDS_USER_ACTION / CONFIGURATION_CHANGED`，A/B 均未生成目标文件 |
| 停止/启动源码 Worker | 工作区绑定保留，READY scan_id 相同且仍可搜索 B |
| 撤销并应用 | 两插件均 `CONFIG_APPLY_FAILED`，旧 Worker 已关闭，无原目录访问 |

“新开对话”和实际 TUI 点击未执行，不能用上述 RPC 流程冒充。按用户条件，只有候选通过且另获安装授权后才进入 TUI 检查；目前两条件未满足。没有手机测试、真实模型调用或真实外部提交。

## 可复现命令

均在仓库根 PowerShell 执行。先清理当前子进程继承的配置，不能读取或复用原实例 DSN：

```powershell
Get-ChildItem Env: | Where-Object { $_.Name -like 'PA_*' -or $_.Name -eq 'PYTHONPATH' } |
  ForEach-Object { Remove-Item -LiteralPath ('Env:' + $_.Name) }
$audit = 'var/verification/workspace1-audit-2026-11-08'

.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/unit/test_workspace_runtime.py tests/unit/test_instance_workspace_store.py tests/api/test_admin_workspace.py "$audit/test_races_audit.py" -q -s -o addopts= -p no:cacheprovider --tb=short
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/api/test_admin_extension_config.py tests/unit/test_tui_workspace.py tests/unit/test_tui_configuration_apply.py tests/unit/test_tui_service.py tests/unit/test_tui_app.py tests/contract/test_f05_contract_consistency.py -q -o addopts= -p no:cacheprovider --tb=short
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/unit/test_knowledge_auto_index.py tests/unit/test_knowledge_lease_fencing.py tests/unit/test_knowledge_binding.py tests/unit/test_knowledge_workspace.py tests/unit/test_knowledge_documents.py tests/unit/test_knowledge_index.py tests/unit/test_knowledge_index_metadata.py tests/unit/test_knowledge_instance_workspace.py tests/unit/test_workspace_sdk.py tests/unit/test_sdk_rpc.py -q -o addopts= -p no:cacheprovider --tb=short
.\.venv-win\Scripts\python.exe -Xutf8 -B -u -m pytest -c extensions/personal_files/pyproject.toml extensions/personal_files/tests -q -o addopts= -p no:cacheprovider --tb=short
.\.venv-win\Scripts\python.exe -Xutf8 -B -u -m pytest -c extensions/personal_files/pyproject.toml "$audit/test_files_audit.py" -q -o addopts= -p no:cacheprovider --tb=short
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest "$audit/test_index_audit.py" -q -s -o addopts= -p no:cacheprovider --tb=short
```

真实数据库命令：本轮使用已有 `pgvector/pgvector:pg17` 镜像，专属容器 `pa-workspace1-audit-20261108`，仅绑定 `127.0.0.1` 随机端口，无卷挂载。测试每项另建随机数据库并在 finally 删除。重新执行时必须重新创建临时服务、获取它的端口，不能沿用已回收的 44793 或指向原实例。

```powershell
docker run --rm -d --name pa-workspace1-audit-20261108 -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=workspace1_audit_test -p 127.0.0.1::5432 pgvector/pgvector:pg17
docker port pa-workspace1-audit-20261108
# 用上一命令实际端口替换 PORT，只有此无挂载临时服务使用无密码信任。
$env:PA_TEST_DATABASE_URL='postgresql://postgres@127.0.0.1:PORT/workspace1_audit_test'
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/integration/test_knowledge_meta_postgres.py -q -s -o addopts= -p no:cacheprovider --tb=short
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest "$audit/test_source_worker_audit.py" -q -s -o addopts= -p no:cacheprovider --tb=short
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest "$audit/test_lease_clock_audit.py::LeaseClockAudit::test_expired_owner_cannot_renew_after_lock_wait" -q -s -o addopts= -p no:cacheprovider --tb=short
docker stop pa-workspace1-audit-20261108
docker ps -a --filter name=pa-workspace1-audit-20261108
```

## 失败记录、资源与变更说明

- 最初沙箱内 asyncio `socketpair` 卡在回环 accept，未进入有效测试正文；这些会话已中止。解除该环境限制后重跑相同隔离专项，未用 skip 或改产品规避。
- 审计夹具首次误用 RPC `status` 而非 `outcome`，保留于 `postgres-worker-escalated.log`；修正夹具后完整 A/B 序列通过。PG 反例最初 URL 文本替换误改用户名（`lease-clock.log`），随后监视事务缓存统计快照超时（`pg-independent-final.log`）；分别修正 URL 构造及 `pg_stat_clear_snapshot()`，最终 `lease-clock-final.log` 才作为产品反例证据。没有修改生产租期或已有测试超时。
- 清理核验：临时容器停止前仅剩 postgres/template0/template1/专属空 base 数据库；所有随机测试数据库已删除。匹配本轮测试/候选 Worker 的 Python 进程 **0**。容器 stop 后初次即时列表仍有删除中的记录，再次查询已为 **0**，不把异步删除请求本身当作完成证明。
- 本轮新增本报告、五个审计脚本及日志/资源证据；更新 `TODO.md` 和 `docs/NEXT_STEPS.md` 当前 WORKSPACE.1 状态。未修改任何产品实现、既有测试、协议、迁移或交付报告。
- 当前工作仍限 WORKSPACE.1；后续先修复上述编号并复跑对应反例与受影响专项，再重新作安装决策。CORE.RECONCILE.1、F08.6 等既有状态不改变。
