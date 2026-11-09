# WORKSPACE.1 A04 领取取消窗口修复（2026-11-08）

结论：剩余 A04/P2 已完成局部实现与目标验证，原轻量及真实 PostgreSQL 反例转绿。**WORKSPACE.1 继续 NEXT / BLOCKED，等待独立复验；本报告不是独立验收或部署记录。** A01–A03 维持上一轮独立关闭结论，未重新修改它们。

## 范围与行为

HEAD：`a35cf8a1f8d259cf2823cadfed6877c81505f0ae`。工作目录为 Desktop 的原开发仓库，当前 Codex worktree 仍是旧版本，未在旧 worktree 编辑。相对上一轮修复摘要，本轮唯一产品变更是 `extensions/personal_knowledge/src/personal_knowledge/indexing.py`；另新增两个永久回归文件，并将轻量文件加入关键回归清单。没有改 store、SQL、宿主、SDK、数据库迁移、生产租期或 RPC 超时。

- 领取循环纳入完整 try/finally；已发出的 claim 使用独立 task 与 shield，外层取消不丢弃它。
- 取消后先确定领取结果。原响应还未到达时，可通过数据库回读本次唯一 owner 证明提交；没有自己的 owner 不能推断领取失败，更不能提前结束收尾，须等待已发出的请求。
- 已确认领取后，完成扫描/IO回收及 owner CAS 终结，再回收迟到的响应 task；领取 false 不终结别人的租约，继任已接管时 CAS 不覆盖继任状态。
- 不取消尚未完成的验证查询。当前宿主对同一 Worker 的请求串行处理；后续回读完成意味着前一条领取请求已处理，避免主动取消未决验证 RPC 而造成迟到响应 ID 问题。
- 重复取消仍保护整个清理；最终始终取回 claim 结果，只忽略本次主动回收迟到响应产生的 CancelledError。回读失败不被当作未领取，转而等待原claim；未能确认或完成收尾的异常保留，不能被当作正常清理成功。

这是正常取消与可通信宿主的收尾保证。宿主断链、SDK deadline 到期、数据库不可用或进程被强制终止，仍可能使收尾失败；不承诺这些情况下立即无租约，原 60 秒租约兜底与错误语义保留。没有用增加超时或削弱断言修复。

## 先失败再修复

证据目录：`var/verification/workspace1-claim-fix-2026-11-08/`；原独立审计脚本、失败证据和历史报告保留。

| 红测 | 实际结果 | 证据 |
|---|---|---|
| 原样轻量领取已提交/回执未到取消 | 1 failed，1.08s | `claim-unit-red.log` |
| 原样真实 PG 生产 shutdown 反例 | 1 failed，4.32s | `claim-pg-red-tool-excerpt.txt`（工具回显摘录；不是完整日志） |
| 新轻量文件首批 7 节点 | 5 failed、2 passed，7.05s | `claim-unit-permanent-red.log` |
| 新 PG 两节点 | 2 failed，16.15s | `claim-pg-permanent-red.log` |
| 清理期间迟到异常两种时序 | 2 failed，0.76s | `claim-late-error-red.log` |
| 主动回收响应时抛出非取消异常 | 1 failed，0.80s | `claim-reply-cleanup-error-red.log` |

新 PG 测试最初过度要求提交后仍必须等原响应、保持租约 active；这不适用于“权威回读已确认 owner 后先 CAS 收尾”的合法实现。已去掉这两个中间约束，保留提交前不得提前 shutdown、最终 CANCELLED/非活跃、零索引写入、继任立即可领取及重复取消断言。因此该新 PG 红绿文件并非逐字相同；原独立反例未改。原 PG 红测本轮完整输出文件未保留，仅保存工具回显摘录；独立复验目录下完整红测 `claim-cancel-postgres.log` 未改。

## 最终实际验证

| 选择 | 结果 | 日志 |
|---|---|---|
| 10 个新轻量节点 + 自动索引、取消、失租及两轮原审计反例 | **26 passed，2.42s** | `claim-unit-final.log` |
| 新 PG 两节点 + 原 PG 审计文件 + 源码双 Worker 正常 A/B/重启/撤销 | **32 passed，190.33s** | `claim-pg-final.log` |
| 改动的产品和两个新测试文件 Ruff | 通过 | `ruff.log` |
| 唯一产品文件严格 Mypy | 1 source file 通过 | `mypy.log` |
| Git 差异空白检查 | 通过 | `diff-check.log` |

PG 选择误用了整个审计文件：该文件导入并继承元数据测试，额外收集了 28 个继承/导入节点。32 是此次实际运行结果，不代表 32 个全新独立场景；没有再次跑完整项目或完整 PostgreSQL/Worker 套件。后续复验用下方精确节点即可。早先 19 项轻量、2 项新 PG 的中间绿测有重叠，不与最终结果相加。 差异检查最终使用仓库默认行尾配置并以0退出；一次临时覆盖autocrlf=false将既有CRLF误报为空白，输出保留在diff-check-autocrlf-override.log，未据此批量改写文件。

原 PG 反例修复后实际输出：`shutdown_returned=True, committed_lease_state=CANCELLED, committed_lease_active=False, successor_claimed=True, indexed_sources=0`。

正常源码双 Worker 流程：A 自动 READY并读到正文、创建子目录/总结；保存B仍使用A；应用B后索引切换、旧文件计划CONFIGURATION_CHANGED；重启复用READY；撤销目录后拒访。它使用已有解释器、合成目录、生产SQL broker及源码Worker，不是原实例安装或实际TUI人工检查。

可复现命令（Desktop仓库根；使用隔离 `*_test` 数据库，不得指向原库）：

```powershell
$e = 'var/verification/workspace1-claim-fix-2026-11-08'
$old = 'var/verification/workspace1-audit-2026-11-08'
$re = 'var/verification/workspace1-reaudit-2026-11-08'
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/unit/test_knowledge_claim_cancellation.py tests/unit/test_knowledge_index_cancellation.py tests/unit/test_knowledge_auto_index.py tests/unit/test_knowledge_lease_fencing.py "$re/test_cleanup_reaudit.py" "$old/test_index_audit.py" -q -s -o addopts= -p no:cacheprovider --tb=short

# 本轮真实执行的32节点命令（无需再次重复）：
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/integration/test_knowledge_claim_cancellation_postgres.py "$re/test_claim_cancel_postgres_reaudit.py" "$old/test_source_worker_audit.py" -q -s -o addopts= -p no:cacheprovider --tb=short

# 后续复验建议：预先提供临时 PA_TEST_DATABASE_URL，只选4个相关节点：
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/integration/test_knowledge_claim_cancellation_postgres.py "${re}/test_claim_cancel_postgres_reaudit.py::ClaimCancelPostgresReaudit::test_shutdown_after_claim_commit_before_reply_releases_lease" "$old/test_source_worker_audit.py" -q -s -o addopts= -p no:cacheprovider --tb=short
.\.venv-win\Scripts\python.exe -Xutf8 -B -m ruff check extensions/personal_knowledge/src/personal_knowledge/indexing.py tests/unit/test_knowledge_claim_cancellation.py tests/integration/test_knowledge_claim_cancellation_postgres.py
.\.venv-win\Scripts\python.exe -Xutf8 -B -m mypy --strict --follow-imports=silent --cache-dir "$e/mypy-cache" extensions/personal_knowledge/src/personal_knowledge/indexing.py
git diff --check
```

本轮用 `pgvector/pgvector:pg17` 创建独立 `--rm` 容器，映射 `127.0.0.1:45617`，各节点再建随机临时数据库。收尾确认额外数据库0、带本轮标签容器0、镜像创建的独有匿名卷0、匹配测试/源码Worker进程0；详见 `resources.json`。没有挂载原资料目录，没有读写原5432数据库，没有调用模型或外部业务。

## 材料与下一步

- personal.files **0.1.1**：`sha256:8622e7f7bb9fbe56258907c123fc404cb5d3e7ae10de9c3b2d74ad55e9fd6c87`（不变）。
- personal.knowledge **0.1.7**：`sha256:bbf686b2e2e894bcd97b04021f26536b4e8e399508a5ede636bb95297b648112`。
- 本轮文件摘要见证据目录 `files.sha256.json`，包含65个未提交交付文件；历史摘要保留，不拿新摘要覆盖旧证据。原审计脚本摘要另存 `audit-scripts.sha256.json`。

**下一步仍是 WORKSPACE.1 的 A04 独立定向复验，未获其结果前保持 BLOCKED。** 通过后才准备候选安装及 TUI检查；不启动 FILES.BACKUP.1，不借本轮关闭 CORE.RECONCILE.1、F08.6 或其他阶段。未安装/升级、重启/解冻原实例、恢复旧任务、提交、推送或部署。
