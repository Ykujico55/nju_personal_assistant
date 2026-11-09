# WORKSPACE.1 四项验收阻断修复（2026-11-08）

状态：**NEXT / BLOCKED，四项修复已完成实施者定向复验，待独立复验**。不据本报告关闭阶段或授权安装。对应原证据见 [独立验收报告](WORKSPACE1_AUDIT_2026-11-08.md)；原报告和原样反例保留。本轮仅修 W1-A01～A04，不推进 FILES.BACKUP.1。

## 修复行为

| 编号 | 修复 | 永久覆盖 |
|---|---|---|
| A01 / P1 | 宿主读取草稿和状态后比较正文摘要，错配最多重读3次，持续变化报CONFIG_REVISION_CONFLICT；不把较新摘要贴在旧正文上。首次未保存返回真实空对象，不替换成授权模板。TUI确认前再次校验正文/摘要、refresh_required及材料形状，不一致不弹确认、不应用；变化回执不误报成功 | 两个磁盘存储副本，另一方只保存B／保存并应用B；持续竞争拒绝、未保存摘要一致；TUI错配、要求刷新、未保存及畸形回执 |
| A02 / P2 | 同一数据库事务先锁定租约行，锁定语句不含owner/时间谓词；下一语句才用clock_timestamp()判断到期和计算新期限。领取、续租、元数据发布/CAS均核对锁后的真实时间，旧owner过期后不能续活 | 数据库确认实际等锁，跨expiry后放行；续租失败、发布/CAS失败、继任领取、旧owner无法写入；旧续租与新领取同时等待的竞争 |
| A03 / P2 | 由数据库返回租约是否仍有效。过期RUNNING不再掩盖已结束的错误：本副本失败显示FAILED，无人在构建显示PENDING/INDEX_LEASE_EXPIRED；持久READY仍优先 | 实际后台等待失败后外部lease过期；无本地错误的停滞租约；数据库活性与宿主看到的expires_at相矛盾时以数据库为准 |
| A04 / P2 | 完整保护“取消扫描/续租子任务 → 等待IO回收 → owner CAS终结”，重复取消不打断收尾；收尾完成后传播取消，清理异常保留为原因 | 实际扫描线程阻塞时三次取消；finish等待中首次及重复取消；finish失败仍保留原因；继任能立即领取 |

未调整生产超时、租期、风险等级、审批/UNKNOWN规则或迁移历史。文件插件产品代码本轮未改，旧计划修订保护保留。自动索引失败不转成成功，也不忽略取消。

## 本轮实际验证

执行目录：`C:/Users/kunji/Desktop/homework/生成式软件工程/lab1`。证据目录：`var/verification/workspace1-fixes-2026-11-08/`。以下组有交叉，不相加冒充总套件结果。

| 组 | 结果 | 日志 |
|---|---|---|
| 原样A01两个参数修复前 | 2 failed | confirmation-red.log |
| 新增正文/摘要永久回归修复前 | 4 failed | confirmation-permanent-red.log |
| A01原样反例及宿主存储/运行/API回归 | 29 passed、2 warnings，24.85秒 | confirmation-green.log |
| TUI确认及相邻配置 | 20 passed，21.59秒；新增防御先有9 failed/5 passed | tui-confirmation-green.log；红测工具摘录tui-confirmation-red-excerpt.log |
| 原样A02真实PG修复前 | 1 failed，11.83秒 | a02-red.log |
| PG元数据13项 + 原样A02 | 14 passed，129.44秒 | a02-pg-green.log |
| 后补旧续租/继任领取同时等锁竞争 | 1 passed，8.72秒 | a02-successor-race.log |
| A03原样反例及状态/自动索引 | 11 passed，3.47秒；原样修复前1 failed/1.47秒（工具输出） | a03-green.log |
| A04原样反例及取消/索引回归 | 12 passed，5.92秒；原样修复前1 failed，新永久回归先3 failed | a04-targeted.log、a04-command-results.txt |
| 知识受影响九文件专项 | 132 passed，23.61秒 | knowledge-regression.log |
| 真实源码双Worker正常A/B流程 | 1 passed，10.66秒 | source-worker-green.log |
| 改动路径Ruff、五个产品文件Mypy、差异 | 通过；Mypy5源码 | ruff-final.log、mypy-final.log；diff-final.log |

五个原失败参数实例（A01×2、A02、A03、A04）分别原样复现后均通过，没有删除或改弱审计断言。PG的13项+反例先运行，后补的继任竞争单独执行；不声称一次运行15项。数据库原反例现在输出renew_returned=false、renewed_expiry_in_future=false、expired_owner_fenced_write_committed=false。

双Worker验证使用候选源码及生产RPC/监督器/SQL broker，不经过安装：自动索引A、保存B仍用A、应用B后检索B、A旧计划拒绝、重启复用READY、撤销后拒绝两插件访问。不能据此声称原实例安装或TUI人工验收通过。

依据本人避免过度验证的要求，本轮未重跑完整关键回归、完整本地/PG/Worker套件或无改动文件插件78项；旧78项属于前次证据，不计为本轮执行。没有手机步骤或真实模型/邮箱/网页操作。

## 复现命令

先按测试指南确保子进程不继承原实例PA_*配置；以下数据库测试只允许独立 *_test 数据库，本轮临时46201端口已经回收，不得复用或替换为原库。`-o addopts=`清空默认选项。

```powershell
$audit = 'var/verification/workspace1-audit-2026-11-08'
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/unit/test_instance_workspace_store.py tests/unit/test_workspace_runtime.py tests/api/test_admin_workspace.py "$audit/test_races_audit.py" -q -o addopts= -p no:cacheprovider --tb=short
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/unit/test_tui_workspace.py tests/unit/test_tui_configuration_apply.py -q -o addopts= -p no:cacheprovider --tb=short
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/unit/test_knowledge_auto_index.py tests/unit/test_knowledge_lease_status.py tests/unit/test_knowledge_lease_fencing.py tests/unit/test_knowledge_binding.py tests/unit/test_knowledge_workspace.py tests/unit/test_knowledge_documents.py tests/unit/test_knowledge_index.py tests/unit/test_knowledge_index_metadata.py tests/unit/test_knowledge_instance_workspace.py -q -o addopts= -p no:cacheprovider --tb=short
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/unit/test_knowledge_index_cancellation.py tests/unit/test_knowledge_auto_index.py tests/unit/test_knowledge_lease_fencing.py "$audit/test_index_audit.py" -q -s -o addopts= -p no:cacheprovider --tb=short
# 以下需先自行建立隔离PG并设置PA_TEST_DATABASE_URL；当前metadata文件已包含后补竞争用例。
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/integration/test_knowledge_meta_postgres.py "$audit/test_lease_clock_audit.py::LeaseClockAudit::test_expired_owner_cannot_renew_after_lock_wait" -q -s -o addopts= -p no:cacheprovider --tb=short
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest "$audit/test_source_worker_audit.py" -q -s -o addopts= -p no:cacheprovider --tb=short
.\.venv-win\Scripts\python.exe -m mypy src/personal_assistant/infrastructure/extensions/workspace.py src/personal_assistant/cli/tui/app.py extensions/personal_knowledge/src/personal_knowledge/store.py extensions/personal_knowledge/src/personal_knowledge/worker.py extensions/personal_knowledge/src/personal_knowledge/indexing.py
git diff --check
```

取消专项首次结果和精确命令另存a04-command-results.txt。本报告给出的组合便于复验，不将合并后的选择伪装成已经执行的一次命令。两份新永久状态/取消测试纳入日常关键回归选择。

## 改动文件、材料与收尾

本次产品修复限五文件：宿主 `infrastructure/extensions/workspace.py`、`cli/tui/app.py`；知识插件 `store.py`、`worker.py`、`indexing.py`。对应永久测试在test_instance_workspace_store、test_tui_workspace、test_knowledge_meta_postgres、新test_knowledge_lease_status、新test_knowledge_index_cancellation；FakeKnowledgeStore补数据库活性接口，core-suite加入实际P2回归。协议/设计和接力记录同步，原审计报告不改。

候选尚未安装，因此保持文件插件0.1.1、知识插件0.1.7版本号。新材料：

- personal.files：`sha256:8622e7f7bb9fbe56258907c123fc404cb5d3e7ae10de9c3b2d74ad55e9fd6c87`，与首次候选相同。
- personal.knowledge：`sha256:0e28d619013225bcf7724633a19d6a427c82253747690cb01a8a5d97e641be2f`，取代旧候选摘要。

`candidates.json`与`files.sha256.json`位于本轮证据目录；首次交付的旧摘要文件保留。无新增依赖或迁移。临时容器ID/专属标签确认后精确停止，删除复核为0；停止前随机测试库0，匹配测试/源码Worker进程0，见resources-final.json。未改个人资料、原库、原配置、原后台；未安装、重启、解冻、提交或推送。

下一步仍为 **WORKSPACE.1 独立复验本次修复**。通过并获准安装后，再做原实例升级和TUI冒烟；不启动后继功能。
