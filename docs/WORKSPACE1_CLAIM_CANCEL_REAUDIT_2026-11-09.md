# WORKSPACE.1 A04 领取取消修复独立复验（2026-11-09）

结论：**通过本地候选独立复验，A04 剩余阻断关闭。** 结合此前已独立关闭的 A01–A03，WORKSPACE.1 当前没有已知本地验收阻断，状态更新为 **NEXT / WAITING（待安装授权及 TUI 关键检查）**，不标记整项 DONE，不据此授权安装或部署。

## 基线与授权范围

依据 `WORKSPACE1_CLAIM_CANCEL_FIX_2026-11-08.md`，只复核领取取消修复及受影响路径。使用合成目录、临时数据库，不接触原知识库、原数据库或原后台。没有安装扩展、真实模型调用、邮件或网页提交、手机测试、提交或推送。实际 TUI 人工检查仍未执行。

HEAD：`a35cf8a1f8d259cf2823cadfed6877c81505f0ae`，候选仍是未提交工作区改动。更新本轮状态前，交付清单 **65 个文件 SHA-256 全部匹配**，历史审计清单 **9 个脚本全部匹配**；上一轮两份领取取消失败反例未改动。相对上一轮候选，唯一产品修复为 `extensions/personal_knowledge/src/personal_knowledge/indexing.py`。

候选摘要独立重算一致：

- personal.files 0.1.1：`sha256:8622e7f7bb9fbe56258907c123fc404cb5d3e7ae10de9c3b2d74ad55e9fd6c87`。
- personal.knowledge 0.1.7：`sha256:bbf686b2e2e894bcd97b04021f26536b4e8e399508a5ede636bb95297b648112`。

## 关闭依据

`indexing.py` 把已发出的领取请求放入独立 task 并 shield，领取循环已处于 try/finally 内。取消后，清理先等领取结果，或用本次唯一 owner 的权威回读证明提交，再执行 owner CAS。未证明提交时不推断失败、不提前收尾。最终取回领取任务结果与迟到异常；重复取消不能打断整个清理单元。

原真实 PostgreSQL 反例本轮输出：

```text
shutdown_returned=True
committed_lease_state=CANCELLED
committed_lease_active=False
successor_claimed=True
indexed_sources=0
```

新增反例补证：

1. owner 回读失败后，等待迟到的领取 False；重复取消不执行 finish、不改变 foreign owner。
2. 回读证明自己的 owner 后、CAS 前发生继任接管：旧 finish 返回 False，继任的 owner、状态和元数据完整保留。
3. **真实源码 Worker 子进程**领取提交前收到 drain：等待领取完成并收尾，最终 CANCELLED、无索引写入，继任可立即领取。
4. **真实源码 Worker 子进程**领取提交后、响应交付前收到 drain：同样完成收尾；随后再次调用 health 成功且返回 draining，证明 RPC 没有因迟到响应失去关联。

两个子进程反例使用生产 SDK、宿主 JsonRpcProcessClient、真实 SQL broker 和临时 PostgreSQL。只在宿主处理领取的提交前或响应交付前插入测试闸门；必须观察到 owner 验证请求，确保命中了领取尚未返回时的取消。没有安装或替换产品代码。

静态接线与动态证据一致：宿主按序处理同一 Worker 的能力请求并写回响应；SDK 的独立 reader 在 drain/shutdown 等待期间仍可解析响应。因此正常可通信且 deadline 内，后续 owner 查询完成意味着领取响应已先送达，不会因回收 claim Python task 制造未知响应 ID。finish 按唯一 owner 和 RUNNING 做 CAS，不覆盖继任。

## 本次实际验证

证据目录：`var/verification/workspace1-claim-reaudit-2026-11-09/`。各组分别记录，不混入历史数量：

| 选择 | 本次结果 | 日志 |
|---|---|---|
| 新领取取消永久回归、自动索引/取消/失租专项、两轮原审计轻量反例 | **26 passed，15.61s** | `unit26.log` |
| 新的回读失败/迟到 False、回读后继任接管 | **2 passed，0.28s** | `claim-edges.log` |
| 两个永久 PG 节点 + 原 PG 失败反例精确节点 + 正常双 Worker 操作序列 | **4 passed，65.44s** | `postgres4.log` |
| 新真实源码 Worker 提交前/提交后取消 | **2 passed，16.37s** | `source-cancel2.log` |
| 产品和两个永久测试文件 Ruff | 通过 | `ruff.log` |
| 唯一产品文件严格 Mypy | **1 source file 通过** | `mypy.log` |
| `git diff --check` | 通过，只有既有 CRLF 提示 | 工具输出 |

本轮 PG 明确只选相关节点，没有再次误收集 28 个继承元数据节点。没有测试失败、skip 或修改断言。迁移目录相对 HEAD 无变更。按用户限制未运行完整关键回归、完整本地/PG/Worker 套件，也未重复无变更文件插件和 A01–A03 专项；这些结论沿用此前独立验收证据。

正常双 Worker 序列再次证明：A 自动 READY、正文和来源正确、实际创建子目录及总结；只保存 B 时仍用 A；应用 B 后检索 B 且不能继续检索 A；A 旧计划返回 `NEEDS_USER_ACTION / CONFIGURATION_CHANGED`；重启复用 READY；撤销后两插件拒访。它是源码/生产 RPC 集成证据，不代替安装和真实 TUI 新对话验收。

## 可复现命令

仓库根 PowerShell，执行前清除子进程继承的实例配置：

```powershell
Get-ChildItem Env: | Where-Object { $_.Name -like 'PA_*' -or $_.Name -eq 'PYTHONPATH' } |
  ForEach-Object { Remove-Item -LiteralPath ('Env:' + $_.Name) }
$old = 'var/verification/workspace1-audit-2026-11-08'
$re = 'var/verification/workspace1-reaudit-2026-11-08'
$new = 'var/verification/workspace1-claim-reaudit-2026-11-09'
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/unit/test_knowledge_claim_cancellation.py tests/unit/test_knowledge_index_cancellation.py tests/unit/test_knowledge_auto_index.py tests/unit/test_knowledge_lease_fencing.py "$re/test_cleanup_reaudit.py" "$old/test_index_audit.py" -q -s -o addopts= -p no:cacheprovider --tb=short
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest "$new/test_claim_edges.py" -q -s -o addopts= -p no:cacheprovider --tb=short
```

真实数据库仅允许另建专属临时服务。本轮端口 **46549 已回收，不能直接复用，也不能换成原实例 DSN**：

```powershell
docker run --rm -d --name pa-workspace1-claim-reaudit-20261109 --label pa.audit=workspace1-claim-reaudit-20261109 -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=workspace1_claim_reaudit_test -p 127.0.0.1::5432 pgvector/pgvector:pg17
docker port pa-workspace1-claim-reaudit-20261109
# PORT替换为本次专属容器的实际回环端口。
$env:PA_TEST_DATABASE_URL='postgresql://postgres@127.0.0.1:PORT/workspace1_claim_reaudit_test'
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/integration/test_knowledge_claim_cancellation_postgres.py "$re/test_claim_cancel_postgres_reaudit.py::ClaimCancelPostgresReaudit::test_shutdown_after_claim_commit_before_reply_releases_lease" "$old/test_source_worker_audit.py" -q -s -o addopts= -p no:cacheprovider --tb=short
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest "$new/test_source_claim_cancel.py" -q -s -o addopts= -p no:cacheprovider --tb=short
docker stop pa-workspace1-claim-reaudit-20261109
docker ps -a --filter label=pa.audit=workspace1-claim-reaudit-20261109

.\.venv-win\Scripts\python.exe -Xutf8 -B -m ruff check --no-cache extensions/personal_knowledge/src/personal_knowledge/indexing.py tests/unit/test_knowledge_claim_cancellation.py tests/integration/test_knowledge_claim_cancellation_postgres.py
.\.venv-win\Scripts\python.exe -Xutf8 -B -m mypy --strict --follow-imports=silent --cache-dir "$new/mypy-cache" extensions/personal_knowledge/src/personal_knowledge/indexing.py
git diff --check
```

沿用已知 Windows 沙箱 asyncio socketpair 限制，异步专项在获准解除限制的环境运行；这不改变测试数据库和目录隔离。

## 收尾、限制与下一步

全部随机测试数据库清理，停止前只剩 postgres/template0/template1/专属 base。匹配本轮测试/候选 Worker 的 Python 进程 **0**；专属容器 **0**；镜像自动创建的唯一匿名卷 **0**。没有绑定原目录。完整标识见 `resources-final.json`。

本轮新增两份审计脚本、日志/资源记录和本报告；更新 README/TODO/NEXT_STEPS。没有改产品、既有测试、原审计反例或迁移。

通过结论限于本地候选和可通信、未超既有 deadline 的正常取消路径。宿主断链、数据库不可用、RPC 超时、强制终止仍可能留下租约并依靠原 60 秒兜底。清理异常的 cause 在 `build_index` 层保留；当前 `_stop_indexing()` 使用 `gather(return_exceptions=True)`，不能据此宣称 shutdown 会向宿主展示所有内部清理错误。这是本报告明确保留的故障边界，不冒称本轮验证了现场故障运维表现。

下一步仍限 **WORKSPACE.1**：另获安装授权后核对上述候选材料，安装并完成用户指定的 A/B 合成目录 TUI 关键检查。当前不启动安装、不把源 Worker 验证算作 TUI 人工验收、不启动 FILES.BACKUP.1，也不改变 CORE.RECONCILE.1、F08.6 等其他编号状态。
