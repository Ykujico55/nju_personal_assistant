# 实现接力清单

本清单把详细设计拆成较小、可独立验收的工作包。状态只允许 `TODO / NEXT / IN_PROGRESS / DONE / BLOCKED`。后续模型一次领取一个任务，完成后在本文件写入测试命令和结果。

简要状态见 `../TODO.md`，冻结的接口、事务与验收契约见 `CONTRACTS_AND_INTERFACES.md`。F01–F05 已通过最终复验并标记 `DONE`；F06 是唯一 `NEXT`，尚未开始；F07+ 保持 `TODO`，不得提前开始。

## 基架状态

| 编号 | 状态 | 内容 | 验收证据 |
|---|---|---|---|
| F00 | DONE | 核心、API、PWA 壳、扩展 SDK、内存适配器和测试基架 | `scripts/test.ps1`：42 passed；Ruff 与 Mypy 通过；`pip check` 无冲突；wheel 含核心/SDK/PWA/配置/迁移；Uvicorn/API/CLI/Worker 子进程烟测通过 |
| F01 | DONE | 迁移运行器 + 真实 PostgreSQL 仓储/队列/outbox/审计/扩展状态适配器 + composition root 与启动生命周期 | 最终复验：`./scripts/test-postgres.ps1` 24 passed；`./scripts/test.ps1` 44 passed、24 skipped；Ruff/Mypy/`pip check`/`git diff --check` 通过；旧库 checkpoint 续写、Extension 完整回填、PostgreSQL Gateway 四类终态和内存审批单次消费均独立验证通过 |
| F02 | DONE | Extension Supervisor、确认屏障、每版本 venv/Worker、生命周期、Admin API/CLI、操作持久化与恢复 | 六轮独立审计通过；`./scripts/test.ps1` 153 passed、28 skipped；`./scripts/test-postgres.ps1` 28 passed；F02 相关集合 110 collected；Ruff/Mypy/`pip check`/`git diff --check` 与 wheel 内容核验通过 |
| F03 | DONE | Cloudflare Access JWT 验证、JWKS 缓存/轮换、public/Admin/health 边界回归 | 三轮独立验收通过；前两轮 6+2 项缺陷均修复并补反例；F03 目标集合 124 passed；`./scripts/test.ps1` 269 passed、28 skipped；PostgreSQL 28 passed；Ruff/Mypy/`pip check`/`git diff --check`、手工轮换复现与 wheel 内容核验通过 |
| F04 | DONE | 真实本地/远程模型适配器、canonical 披露许可（持久 + 内存）、迁移 0005、`PA_MODEL_*` 配置、public 披露接口 | 两轮自我审查 16 项与第三至七轮验收修复后目标集合 161 passed；`./scripts/test.ps1` 409 passed、37 skipped；`./scripts/test-postgres.ps1` 37 passed（F01 24 + F02 4 + F04 9）；Ruff/Mypy（150 files）/`pip check`/`git diff --check` 通过；wheel 含模型模块、许可模块与 0005 迁移；独立验收通过 |
| F05 | DONE | 个人知识扩展：授权目录扫描、Markdown/TXT/PDF 抽取、版本化索引、PostgreSQL FTS + pgvector 混合检索、可验证引用、删除传播；通用宿主数据能力与扩展配置通道 | 完整独立审计和修复后自审计通过；全量 597 passed、81 skipped；PostgreSQL/真实 Worker 81 passed；其余证据见下方 F05 章节 |
| F06 | NEXT | smail 扩展：IMAP 读取、草稿、R2 SMTP 发送与对账 | 尚未开始，只允许下一位 Agent 领取本项 |

F00 已冻结的公共边界见 `docs/IMPLEMENTATION_MAP.md`。不要重写基架；后续任务应替换端口适配器或新增业务扩展。当前任务进入 `QUEUED` 后不会被假 Worker 消费，这是有意的 fail-closed 行为。

## 建议实现顺序

### F01 — PostgreSQL 仓储与迁移（DONE）

范围：实现 `infrastructure/database` 中的任务、运行、审批、作业、审计和扩展状态仓储；接入 `migrations/0001_core.sql`。Schema、队列端口、lease keepalive 和开发用内存参考实现已经存在。

能力边界：只替换端口适配器，不改领域状态机或 API Schema。

验收：在临时 PostgreSQL 上迁移、重启、并发 claim、lease 过期回收、幂等冲突及审计追加测试通过；生产启动不再报 `POSTGRES_ADAPTER_NOT_IMPLEMENTED`。

完成与验收证据（2026-09-16）：

- 新增迁移 `0002_f01_persistence.sql`，补齐 task 命令幂等、task_messages、task_events、agent_runs 全字段、run_checkpoints.run_version、run_observations、approvals.version 及批准/完成字段、jobs.metadata、side_effect_intents、extensions 的 manifest/artifact/install/tombstone。
- 迁移运行器：数字版本顺序、每迁移 SHA-256 checksum、`pg_advisory_lock(743192235100001)` 串行化、失败不登记、checksum 漂移拒绝启动。
- 真实适配器：`PostgresTaskRepository`/`PostgresEventStream`/`PostgresJobQueue`/`PostgresApprovalRepository`/`PostgresRunRepository`/`PostgresCheckpointStore`/`PostgresObservationStore`/`PostgresAuditWriter`/`PostgresSideEffectOutbox`/`PostgresLifecycleStore`，由 `build_postgres_adapters` 组合并接入 `Container` 与 public/admin lifespan；导入期不连接数据库。
- 首轮验收缺陷修复（全部已补反例测试）：
  1. Outbox 消费强制校验 `expires_at`：过期审批原子置 `EXPIRED` 后抛 `ApprovalExpiredError`，绝不进入 `EXECUTING`。
  2. `action_fingerprint` 改为必填；缺失或漂移时原子取消审批（`CANCELLED`）且不插入 Intent，分别抛 `ValidationError`/`ApprovalBindingMismatchError`。
  3. 新增 `SideEffectOutboxPort.finalize`：审批与 Intent 的终态在同一事务提交，消除双写窗口；`ToolGateway` 的 SUCCEEDED/FAILED/UNKNOWN 全部经此边界。
  4. 迁移 `0002` 从 `tasks.objective` 回填 `agent_runs.objective`、从 `started_at` 回填 `active_started_at` 并加 `NOT NULL`；从 `extension_versions` 回填 extension manifest；新增带数据 0001 快照升级测试。
  5. `AgentEngine` 新增可选 `unit_of_work`：Run CAS + Checkpoint（及 Observation）在同一事务提交，并新增注入 checkpoint 失败验证回滚的测试。
  6. 修正文档状态冲突；最终复验通过后，F01 已统一标记为 DONE，F02 提升为唯一 NEXT。
- 第二轮验收缺陷修复：
  7. 迁移 `0002` 在创建 `run_checkpoint_sequence` 后用 `setval` 推进到既有 `max(sequence)`；升级测试实际为旧 Run 追加 checkpoint 并断言序列不冲突。
  8. Extension 回填补齐 `extension_versions.install_path`，`manifest_version` 从 manifest JSON 的 `manifest_version` 读取，而不再误用扩展包版本；升级测试断言安装路径、包版本与 manifest 格式版本同时恢复。
  9. 内存 Outbox 绑定 `ApprovalService`，强制校验审批状态/过期/完整指纹并真正消费审批；同一审批无法用不同幂等键再次执行。新增网关级反例测试（重复使用与漂移）。
- 命令证据：`./scripts/test-postgres.ps1` → 24 passed（真实 PostgreSQL 17.11 + pgvector，临时库自动创建/删除，仅操作 `*_test` 目标的独立临时数据库）；`./scripts/test.ps1` → 44 passed + 24 skipped，`ruff`/`mypy`/`pip check` 全通过；`PA_ENVIRONMENT=production` 下 FastAPI lifespan 成功迁移并启动。
- 最终独立复验：旧库 checkpoint 最大序号为 7 时升级后正确追加为 8；Extension 的 manifest 格式版本、安装路径和 artifact hash 正确回填；PostgreSQL Gateway 的 SUCCESS/FAILED/UNKNOWN/Cancelled 路径保持 Approval/Intent 终态一致；实际内存 Container 中同一审批第二次执行被拒绝且 executor 仅调用一次。
- 公共契约变化：`SideEffectIntent.action_fingerprint` 由可选改为必填；新增 `SideEffectOutboxPort.finalize`；新增 `core.unit_of_work.UnitOfWorkPort`；`ToolGateway`/`AgentEngine` 增加可选 `outbox`/`unit_of_work` 参数。已同步 `CONTRACTS_AND_INTERFACES.md`。
- 已知边界：外部动作完成到 `finalize` 之间崩溃会留下 `EXECUTING/PREPARED` 未决态，必须由后续 reconciliation 处理且不得自动重试；真实 Extension Supervisor/Worker 与外部集成不属于 F01。

失败红线：一次作业被两个有效 lease 同时持有；审批消费与副作用意图不能在同一事务落库；重启丢状态。

禁区：SQLite 冒充生产数据库；在仓储中写业务扩展判断；存储凭据明文。

### F02 — Extension Supervisor（DONE）

范围：把现有 Manifest/确认屏障/生命周期/Registry/JSON-RPC 契约接到真实暂存目录、venv 和进程适配器；完成排空、升级和回滚。不要重新设计已有协议。

完成与验收证据（2026-09-16，六轮独立审计通过）：

- 新增 `core/extensions/operations.py`（`ExtensionOperation`/`OperationState`/`ExtensionOperationStore`/`DIAGNOSTIC_CODES`）与 `core/extensions/supervision.py`（`ExtensionSupervisorService`：inspect、install/upgrade/enable/disable/rollback/uninstall、`wait_operation`、`recover`、`invoke_tool`、`stop_all`）。
- 新增 `infrastructure/extensions/`：`LocalArtifactStager`（本地目录/zip/whl 复制解包，拒绝 `scheme://`、`git+`、路径穿越、符号链接与超限解包）、`VenvArtifactInstaller`（每扩展版本 `<root>/<id>/<version>/{payload,venv}`，`python -m venv --without-pip` 离线装配 SDK + payload `.pth`，lockfile 有内容时用 venv pip 安装已钉版本，失败回滚自身目录）、`ProcessRuntimeSupervisor`/`ProcessContractVerifier`（真实子进程、handshake/health/slots 契约检查、超时有界 drain、崩溃隔离）、`CompatibleVersionOperator`（保留版本与 `state_schema_version` 兼容回滚）、数据保留适配器（`ext_*` namespace 只读；purge 明确不可用）。
- 新增持久化适配器：`PostgresExtensionOperationStore`（复用 0001 `extension_operations` 表，无新迁移）、`PostgresVersionCatalog`（读 `extension_versions`）、`InMemory*` 对应实现；`PostgresLifecycleStore`/`InMemoryLifecycleStore` 新增 `all()`，`manifest_to_dict`/`manifest_from_dict` 公开（保留旧私有别名供 F01 测试）。
- 确认屏障加固：`InstallCoordinator` 新增 `prepare_auto`/`prepare_upgrade`/`install_candidate`（升级候选不覆盖活动记录）/`validate`/`preview_for`/`discard_candidate`/`discard_staged_record`；安装前重新计算 staged hash，确认后变化即 `ConfirmationRequiredError`；验证失败会删除已安装版本目录并清理暂存，绝不会留下可被 Registry 采用的代码。
- 生命周期：`LifecycleManager.recover`、`enable` 接受 `QUARANTINED`（修复路径）、健康检查失败抛 `ExtensionOperationError("HEALTHCHECK_FAILED")`；`QUARANTINED` 由崩溃/健康失败进入且 Registry 立即撤销。
- RPC 加固：`JsonRpcProcessClient` 对畸形帧、超限行、id 不匹配统一终止进程并抛 `RpcCallError(-32700/-32092/-32093)`，超时 `RpcTimeoutError` 后进程停止且不静默重试。
- Admin API（仅 8001）：`inspect`（纯数据预览）、`install`（需精确绑定 `plan_id`+`nonce`+`preview_hash`+`accepted_warning`）、`enable|disable|rollback|uninstall`（返回 operation id，202）、`upgrade`（需同一确认）、`purge-data` 501、`GET /admin/v1/extension-operations/{id}`；lifespan 启动 `recover()`、关闭 `stop_all()`。公共 API 无任何管理路由。
- CLI：`inspect` / `install <source>` / `upgrade <id> <source>` 先展示预览并取得显式确认，再调用 Admin API 并轮询 operation；`enable|disable|rollback|uninstall` 同样轮询；`purge` 维持明确未实现。CLI 不导入基础设施或数据库（契约测试 `tests/contract/test_extension_independence.py` 拦截）。
- 第二轮独立复验修复（11 项 P1 + 4 项 P2，全部补反例测试）：Worker 最小环境白名单（不继承数据库 URL/Token/Cookie）；暂存/哈希/安装统一忽略集合（确认后篡改 `.venv` 不影响哈希也不进入 payload）；lockfile 要求精确版本 + `--hash=sha256` 且 pip 使用 `--require-hashes`；升级执行时重查活动版本基线（`PLAN_BASELINE_CHANGED`）；升级禁用旧版后的全部步骤统一补偿（候选持久化失败会恢复并重新启用旧版）；安装最终持久化失败删除未登记版本目录；`reject` 按计划模式处理（upgrade 不覆盖活动记录）、REJECTED 可重新安装、新预览作废旧计划、CLI 拒绝会清理计划；drain 全程受 deadline 限制并校验 `DrainReport`（`DRAIN_TIMEOUT`）；契约验证比较完整工具描述符/风险/Schema 与全部槽位能力 ID；恢复失败先停止 Worker 再隔离；`stdin.write/drain` 管道错误、读 EOF 与取消统一类型化；`data_namespace` 单射编码消除碰撞；operation store 强制诊断码允许列表；公共扩展列表改读持久 lifecycle store；`RUNNING` 转换失败原子落 `FAILED`。
- 第三轮独立复验修复（A01–A08，全部补反例测试）：`shield_cleanup`/`terminate_process` 统一“先清理后重抛取消”；drain 使用一次性单调 deadline 覆盖等锁+写入+读取并严格校验 `DrainReport`；写入期取消破坏 RPC 流；安装命令取消回收子进程、安装取消清理版本目录/staging/plan 并收敛 REJECTED；升级全链路（含禁用旧版与保存 UPGRADING）纳入同一补偿边界；install/upgrade 重放先查持久 `request_scope`，跨重启仍返回原 operation 且不与内存 plan 耦合；`interrupt_running` 也强制诊断码白名单；文档同步 0003/0004 与取消语义。
- 第四轮独立复验修复（3 项 P1 + 文档一致性）：`rollback` 先解析并校验保留候选再禁用当前版本，失败时通过 `_compensate_failed_rollback` 保持当前版本 ENABLED 且可调用（不兼容/无候选反例）；`run_blocking` 在取消时等待后台复制线程结束再传播取消，版本目录删除后不可能被线程重建；`call()` 与 drain 一样使用单一 monotonic deadline 覆盖等锁+写入+读取；契约验证 Worker 的 finally 改为 shield 关闭；新增 `tests/contract/test_f02_contract_consistency.py` 锁定文档与实现一致性。
- 第五轮独立复验修复（2 项 P1）：`LifecycleManager.rollback` 把候选启动、健康检查、Registry 发布与 ENABLED 持久化全部纳入同一补偿边界，Supervisor 删除二段式 `enable()`；`_compensate_failed_rollback` 撤销候选发布、停止候选 Worker 并重新启用原版本；反例覆盖候选启动失败、健康失败、ENABLED 保存失败与取消四类，均断言原版本仍为 Registry owner 且可调用。`run_blocking` 保存首次取消并消费线程终态，线程在取消后抛异常不再覆盖取消信号；反例覆盖“取消后线程抛异常”与“等待期间再次取消”。
- 第六轮验收材料修复（2 项 P2，未改核心实现）：取消反例不再调用 `service.stop_all()`，而是直接取消单个 `manager.rollback()` 任务并断言 Runtime 中候选 Worker 已停止、原版本 Worker 在运行；测试 `FakeRuntime` 维护 `active` 活动 Worker 映射，`stop`/`stop_all` 真正清空，未启动时 `invoke_tool` 抛 `RpcCallError(-32090)`；另增 `stop_all` 关闭全部 Worker 后 `recover()` 按持久 ENABLED 记录重启的独立测试。`NEXT_STEPS`/`TODO` 证据行更新为第六轮实际数字。
- 新增迁移：`0003_f02_operations.sql`（幂等键/命令指纹 + 部分唯一索引，SHA-256 `28cbda227918bfcd80366208b59713eb0dbb0b0cbdaa582cb5e55a91ce2e1e03`）与 `0004_f02_operation_request_scope.sql`（request_scope + `(request_scope, idempotency_key)` 部分唯一索引，SHA-256 `6b6bb9f5cea83b443c9ca345f7a9d134f6ebca69969f01e23557ecff706af6fc`）。F01 的两个迁移测试断言更新为四迁移集合。
- 命令证据：`./scripts/test-postgres.ps1` → F01+F02 共 28 passed；`./scripts/test.ps1` → 153 passed、28 skipped；F02 相关集合 110 collected（`tests/unit/test_extension_staging.py` 12、`test_extension_install_barrier_f02.py` 11、`test_extension_rpc_robustness.py` 12、`test_extension_cancel_cleanup.py` 11、`test_extension_supervision_service.py` 34、`test_extension_operations_model.py` 8、`test_cli_extension_flow.py` 2、`tests/api/test_admin_extensions.py` 7、`tests/integration/test_extension_supervisor_real.py` 5、`tests/integration/test_postgres_f02.py` 4、`tests/contract/test_f02_contract_consistency.py` 4；无数据库时 106 passed + 4 skipped）；另有 `tests/contract/test_extension_independence.py` 新增 CLI/数据库边界检查；真实集成用临时 `python -m venv` + 真实 `example_echo` Worker 子进程，并以 import 日志进程 ID 证明确认前 0 次执行、超时后不重试，覆盖契约漂移（risk/schedule）、并发启动不泄漏 Worker；取消测试覆盖 venv/pip 子进程回收、契约验证、staging 删除与最终保存；测试后无残留 python/worker 子进程；Ruff、Mypy、`pip check`、`git diff --check` 通过；wheel 构建包含 Supervisor 模块、SDK、PWA、config 与四份迁移。

能力边界：进程/依赖隔离，不承诺防御同用户恶意代码。

有意未实现（不得当作已完成）：永久 purge（501）、扩展自有 `ext_*` Schema migration 执行、扩展配置注入、Worker 崩溃后的自动重启退避（当前失败即 QUARANTINED，仅在宿主启动时恢复 ENABLED）、远程 URL/Git 来源（默认拒绝，只接受本地目录/zip/whl）、多 Admin 进程共享待确认计划与跨进程 `OPERATION_IN_PROGRESS` 互斥（DB 仅保证命令键唯一与单次 save 串行）、`NotificationProvider` 的启用前行为验证（无枚举 RPC，`deliver` 有副作用）。

剩余风险：宿主被强杀（非正常关闭）时 Windows 子进程可能成为孤儿，需 F09 的 Job Object/进程管理；待确认计划保存在 Admin 进程内存，重启后按 `REJECTED` 收敛；`extension_operations` 只保留单一诊断码，不含多步诊断；带 hash 锁的第三方依赖安装需要可达的包索引/缓存，离线时该扩展安装会安全失败。

失败红线复核：确认前无 build/install/import/execute（探针证据）；Registry 快照只整体发布（单元测试断言 snapshot 不出现半套）；升级失败旧版本保持 ENABLED 且可调用（单元 + 服务级注入故障）。

禁区：后台自动升级；从任意 URL 拉取未固定 revision；将 venv 称为安全沙箱。

### F03 — Cloudflare Access 边界（DONE，三轮独立验收通过）

范围：实现 Access JWT 的签名、issuer、audience、expiry 和代理头白名单。Origin/custom-header CSRF 基架已经存在；Cloudflare 模式此前故意对全部请求返回 503，现已替换为真实验证。

完成与验收证据（2026-09-17，三轮独立验收通过）：

- 新增 `core/auth/ports.py`：`AccessIdentity`、`AccessTokenVerifier` 注入协议、`AccessTokenError`/`AccessTokenRejectedError`/`AccessTokenUnavailableError`、`MAX_TOKEN_BYTES`（api 只依赖 `core.auth`）；`infrastructure/auth/contract.py` 只保留 `JwksProvider` 与 `UnknownSigningKeyError`，密码学实现位于 `infrastructure/auth/cloudflare_access.py`。
- 新增 `infrastructure/auth/cloudflare_access.py`：`CloudflareJwksProvider`（有界缓存、single-flight、未知 kid 受控刷新、超时/响应上限/HTTPS 固定地址）与 `CloudflareAccessTokenVerifier`（`RS256` 白名单、`kid` 精确命中、issuer/audience/exp/nbf/iat 校验、固定 30 秒 leeway、可注入时钟、`sub` -> actor）。
- 重写 `api/middleware/cloudflare_access.py`：只接受 `Cf-Access-Jwt-Assertion` 头与 `CF_Authorization` cookie 两种官方载体；载体冲突/重复/超限拒绝；仅用已验证 claims 建立身份；稳定错误码 401（缺失/无效）与 503（不可用）；开发模式保持 `development-owner`。
- `settings.py` 增加 `PA_CF_ACCESS_TEAM_DOMAIN`、`PA_CF_ACCESS_AUD`、`PA_PUBLIC_ORIGIN` 的规范化与启动校验：`__post_init__` 对所有构造路径写回 canonical 值再校验，非法 team domain（scheme/端口/userinfo/path/自定义域）与缺失生产配置一律拒绝启动。
- `app.py` 新增可注入 `access_verifier` 并在 composition root 构建/关闭；Admin 与 health app 未接线，未改动其路由。
- 运行时依赖新增 `PyJWT`、`cryptography`，`httpx` 提升为运行时依赖；`pyproject.toml`、`dependency.lock`、`.env.example` 同步。
- 测试：`tests/unit/test_cloudflare_access_verifier.py`（RSA 运行时生成 + in-memory JWKS transport；算法混淆、kid、签名、时间声明、缓存/轮换/并发/失败矩阵）、`tests/api/test_cloudflare_access.py`（载体冲突、伪造 email 头、401/503、JWKS 故障、CSRF 不回归、public 无 Admin 路由）、`tests/unit/test_settings.py`、`tests/api/test_boundaries.py`、`tests/contract/test_f03_contract_consistency.py`（依赖声明、无硬编码 JWT、core/domain 不导入密码学栈、文档一致性）。
- 第一轮独立验收修复（6 项，全部补反例测试）：
  1. **P1 手工 Settings 绕过域名校验**：`Settings.__post_init__` 使 from_env、直接构造与 `replace` 全部写回规范化值并校验（第二轮进一步写回 canonical 值）；`create_app`/`build_container` 再校验一次；`cloudflare_access_verifier_from_settings` 只使用 `normalize_team_domain`/`normalize_audience` 的规范化结果。反例：foreign team domain 直接构造/`replace`/`create_app` 全部拒绝，工厂对非规范 team domain 仍构造官方 HTTPS certs URL。
  2. **P2 JWKS 无总 deadline 且未禁止重定向**：`_fetch_keys` 外层 `asyncio.timeout` 覆盖整个 stream（慢速滴流在 deadline 内失败），每次请求显式 `follow_redirects=False`（注入客户端的重定向设置无效）。反例：注入 `follow_redirects=True` 客户端遇 302 只请求官方主机一次并 503；慢速分块在 0.1s 预算内失败。
  3. **P2 取消污染节流状态**：刷新取消（`BaseException`）时恢复 `_last_attempt_at`/`_last_unknown_refresh_at` 后重抛，等待者立即接管刷新。反例：首个刷新持锁时取消，等待中的第二个请求完成取钥并成功验签（2 次请求）。
  4. **P2 重复原始 Cookie 头未检测**：`_cookie_values` 遍历 `getlist("Cookie")` 聚合全部原始首部。反例：两个 `Cookie` 头各带一个 `CF_Authorization` 拒绝；一个无关 Cookie 头加一个令牌头接受。
  5. **P2 API 反向依赖 infrastructure**：`AccessIdentity`/`AccessTokenVerifier`/通用异常与 `MAX_TOKEN_BYTES` 移至 `core/auth/ports.py`；middleware 只导入 `core.auth`；`JwksProvider` 与 `UnknownSigningKeyError` 留在 `infrastructure/auth/contract.py`。契约测试阻止 `api/**` 出现 `personal_assistant.infrastructure`，并锁定 middleware 的 `core.auth` 导入。
  6. **P2 verifier 关闭失败跳过数据库关闭**：lifespan `finally` 用嵌套 `try/finally` 保证两个关闭都尝试。反例：`aclose()` 抛错的 verifier 下 `storage.close()` 仍被调用。
- 第二轮独立验收修复（2 项，全部补反例测试）：
  1. **P2 节流窗口把合法轮换误报为 401**：`CloudflareJwksProvider` 现在区分“本次成功刷新并确认 `kid` 不存在”（含等待其他调用者完成的刷新）→ `UnknownSigningKeyError`（401）与“因节流/失败未执行检查”→ `AccessTokenUnavailableError`（503, `retryable=true`）。反例：`test_throttled_unknown_kid_is_temporarily_unavailable`、`test_rotated_key_during_throttle_window_is_unavailable_then_recovers`（窗口内 503、旧 key 仍可用、30 秒后刷新成功）、`test_concurrent_unknown_kid_refresh_is_coalesced`（合并刷新下 401/503 均安全且只联网一次）、API 层 `test_rotated_key_during_throttle_window_is_retryable_503`。
  2. **文档归属与规范化声明不准**：`NEXT_STEPS.md` 接口归属改为 `core/auth/ports.py` 与 `infrastructure/auth/contract.py` 的分工；`Settings.__post_init__` 现在对 team domain/audience/public origin 写回 canonical 值再校验（所有构造路径满足规范化不变量），factory 保留防御性规范化；`CONTRACTS` 12.3 明确 401/503 分类与文档；新增契约检查 `test_identity_contract_lives_in_core_auth`、`test_next_steps_names_the_correct_interface_owner`，并有 `test_direct_construction_writes_back_normalized_values`、`test_replace_writes_back_normalized_values`、`test_factory_defensively_normalizes_bypassed_settings`。
- 命令证据（两轮修复后）：F03 目标集合 124 passed（`tests/unit/test_cloudflare_access_verifier.py` + `tests/api/test_cloudflare_access.py` + `tests/unit/test_settings.py` + `tests/api/test_boundaries.py` + `tests/contract/test_f03_contract_consistency.py`）；`./scripts/test.ps1` 269 passed、28 skipped；`./scripts/test-postgres.ps1` 28 passed（F01+F02 无回归）；Ruff、Mypy（138 files）、`pip check`、`git diff --check` 通过；临时目录重建 wheel 并解包确认 `core/auth`、`infrastructure/auth`、既有四份迁移与三项依赖声明齐全。
- 最终独立复验：上述目标集、全量与 PostgreSQL 结果全部独立复现；手工验证未知 key 401、节流窗口内轮换 key 可重试 503、旧 key 继续可用且窗口后刷新恢复；wheel、工作树边界与四份迁移 checksum 均复核通过，未发现新的 P0/P1/P2 缺陷。
- 契约同步：`CONTRACTS_AND_INTERFACES.md` 第 12 节（contract v1.3）、`IMPLEMENTATION_MAP.md`、`README.md`。

能力边界：只保护用户 API/PWA（8000）；本机 Admin API（8001）不经 Tunnel，健康只读侧车（8010）保持不变。

有意未实现：自定义 Access 域名或团队域之外的 JWKS 地址、应用内登录/密码/MFA、Access 会话撤销与登录重定向、Tunnel/Tailscale 部署编排（F08/F10）。

剩余风险：无法抵御被盗的 Cloudflare/浏览器会话（产品接受）；Cloudflare Access 边缘可见传输内容；密钥轮换暂态：新 `kid` 在 30 秒节流窗口内无法检查时返回 503（`retryable=true`），成功刷新后确认不存在才是 401；TTL 内已缓存 key 始终可用。

失败红线复核：不信任 email/代理头（伪造头反例）；生产配置缺失/非法启动失败；Admin 仍只允许回环且 public app 无管理路由；JWKS 失败不放行；未记录原始 JWT/cookie/私钥。

禁区：增加应用内密码/MFA 并声称消除被盗 Access 会话风险。

### F04 — 模型适配器与披露许可（DONE，独立验收通过）

范围：为现有 `ModelRouter`、字段分类、精确 disclosure consent 和本地回退策略实现真实本地/远程模型适配器与持久 consent receipt。

能力边界：路由不执行工具，不自行扩大上下文。

验收：敏感字段发往远程前展示接收方/目的/字段并取得许可；凭据、token、cookie、私钥在所有路径硬阻断。

失败红线：缺少 consent 仍远传；redaction 失败后 fail open。

禁区：把整份资料作为“方便上下文”发送；在日志记录原始模型请求。

完成与实现证据（2026-09-17，等待独立验收，未标 DONE）：

- **核心端口与服务**：`core/models/disclosure.py` 新增 `DisclosureConsentState`（`ACTIVE/REVOKED/EXPIRED`）、`DisclosureConsentRecord`、`DisclosurePreview`、`DisclosureFieldSummary`、`DisclosureConsentStore`（`create/get/save/revoke/find_active/list_for_user`）、`DisclosureAuthorizer`、`DisclosureConsentService`（preview/confirm/authorize/get/revoke）与 `canonical_field_digest`/`redacted_field_preview`/`effective_consent_state`。摘要采用 CANONICAL JSON：每字段 `{name, value_sha256, classification, source}` 按 `(name, source, classification, value_sha256)` 排序后 SHA-256；provider、purpose、字段值、分类、来源或字段集合任一变化都会使旧许可失效。TTL 1 分钟–7 天；到期即 EXPIRED；撤销是 `version` CAS 终态；`(scope, idempotency_key)` + `command_fingerprint` 保证同键同内容重放返回原记录、同键异内容 `idempotency_conflict`。存储只含元数据与摘要，不含字段原文。
- **路由器接入**：`ModelRouter` 默认只接受持久 `DisclosureAuthorizer`（`consent_id`+`user_id`），调用栈里的临时 `DisclosureConsent` 仅在显式 `allow_ephemeral_disclosure=True` 的测试构造下可用，且与持久授权互斥；SECRET 在任何许可查询/供应商调用之前阻断；本地回退只在显式配置时触发且必须是非远程 provider；远程调用失败直接抛出，绝不改投其它远程供应商；可选审计只记录 provider/model/purpose/字段摘要/许可 ID/输出哈希，不含字段值。
- **真实适配器**：`infrastructure/models/openai_compatible.py`（远程 OpenAI 兼容 chat-completions，`is_remote=True`）与 `infrastructure/models/ollama.py`（本地 `/api/chat`，强制 loopback）。公共传输层 `base.py`：每次请求 `follow_redirects=False`（注入客户端也不能改变）、单一 monotonic 总 deadline 覆盖整个流、响应字节上限、URL 禁止内嵌凭据/query/fragment、非 2xx → `ModelProviderRejectedError`（用固定状态枚举 `rejection_code`，不读取厂商正文）、超时/连接失败/畸形 JSON/空完成 → 类型化错误、取消传播并关闭流、绝不重试；远端 API key 每次经 `SecretStorePort.resolve_for_broker` 解析，失败在发请求前 `ModelCredentialUnavailableError` fail closed，`SecretHandle` 不进入请求体；适配器在发送前再次拒绝 SECRET。
- **持久化与迁移**：新增 `PostgresDisclosureConsentStore` 与内存替身 `InMemoryDisclosureConsentStore`，语义一致（唯一约束、CAS、幂等重放、撤销）。新增迁移 `0005_f04_model_disclosure.sql`（`model_disclosure_consents` + `model_disclosure_commands`，主键 `(scope, idempotency_key)`），SHA-256 `012532834b281040d0031b48744ec7298e3c9b960bb247f51fd22c050f7534f0`；`0001`–`0004` 未改动（契约测试锁定旧 checksum）。
- **配置与组合根**：`Settings` 新增 `PA_MODEL_*` 并在直接构造/`from_env`/`replace` 全部写回规范化值再校验：远端必须 https + model + SecretHandle 句柄 ID，本地必须 loopback，半配置启动失败，回退必须显式且等于已配置本地 provider。`build_container(settings, *, secret_store=None)` 接入真实适配器；生产远程凭据后端是 fail-closed 的 `UnavailableSecretStore`（F09 前无 Windows Credential Manager，绝不使用内存明文）；`environment=production` 仍拒绝内存存储。
- **API**：public API 新增 `POST /api/v1/disclosures/preview`、`POST /api/v1/disclosures`、`GET /api/v1/disclosures/{id}`、`POST /api/v1/disclosures/{id}/revoke`；统一 `no-store`，响应与错误不含原始字段值；SECRET 403 `DISCLOSURE_DENIED`，预览漂移 409 `DISCLOSURE_PREVIEW_MISMATCH`，撤销状态/版本/幂等冲突 409，不存在 404。
- **测试证据**：F04 目标集合 `161 passed`（`test_model_disclosure.py` 25、`test_model_router_f04.py` 34、`test_model_adapters.py` 45、`test_bootstrap_f04.py` 5、`test_disclosures.py` 7、`test_f04_contract_consistency.py` 15、`test_settings.py` 27、`test_model_router.py` 3；无数据库时另有 `test_postgres_f04.py` 9 skipped）。`./scripts/test.ps1` → `409 passed, 37 skipped`，Ruff `All checks passed!`，Mypy `Success: no issues found in 150 source files`。`./scripts/test-postgres.ps1` → `37 passed`（F01 24 + F02 4 + F04 9，真实 PostgreSQL 17.11 临时库）。`python -m pip check` → `No broken requirements found.`；`git diff --check` → 干净。临时目录构建 wheel 并解包确认 `core/models/disclosure.py`、`core/models/errors.py`、`infrastructure/models/{base,openai_compatible,ollama}.py`、`infrastructure/database/disclosure_consents.py`、`infrastructure/secrets/unavailable.py`、五份迁移（含 `0005_f04_model_disclosure.sql`）与 `httpx/PyJWT/cryptography` 运行时依赖齐全。
- **第一轮自我审查（2026-09-17，交接前对抗性复核）**：用独立探针复现并修复了 6 项缺陷，均先补失败测试再最小修复：① httpx 层 connect/read/write/pool 超时曾落入 `MODEL_PROVIDER_UNAVAILABLE`，现统一为 `ModelProviderTimeoutError`（`test_http_level_timeout_is_typed_as_timeout`）；② 远端/本地 provider ID 相同时 `ModelRouter` 会静默覆盖一个 provider，现 `Settings` 与 `ModelRouter` 双重拒绝重复 ID（`test_duplicate_local_and_remote_provider_ids_are_rejected`、`test_duplicate_provider_ids_are_rejected`）；③ endpoint path 曾接受 `.`/`..` 段，现启动即拒绝（`test_model_endpoint_path_may_not_contain_dot_segments`）；④ `ACTIVE` 许可记录可携带 `revoked_at`，现记录校验拒绝（`test_active_record_cannot_carry_revocation_time`）；⑤ 临时 `DisclosureConsent` 的 naive 过期时间曾抛裸 `TypeError`，现构造即 `ValidationError`（`test_ephemeral_consent_requires_aware_expiry`）；⑥ `authorize` 原样信任存储层过滤，现服务层自行复核完整绑定，存储返回不完整匹配一律无许可（`test_authorize_rejects_a_store_that_ignores_expiry`）。审计写入失败会向上传播、调用方拿不到输出（`test_audit_failure_fails_closed`）。
- **第二轮自我审查（2026-09-17，交接前第二轮对抗性复核）**：再修复 8 项（时间类型化、绑定长度约束、编码绕过、usage 边界、store limit 一致性、生产凭据回归、PostgreSQL 并发撤销）：
  ① 临时许可路径与 `effective_consent_state` 收到 naive `now` 时曾抛裸 `TypeError`，现一律 `ValidationError`（`test_naive_now_is_rejected_with_typed_error`、`test_effective_state_requires_aware_now`）；② `confirm` 曾不校验 `provider_id`/`purpose` 长度，可持久化 5000 字符值，现与 preview/authorize 一致按 `_validate_scoped_text` 拒绝（`test_confirm_rejects_oversized_provider_id`、`test_confirm_rejects_oversized_purpose`）；③ endpoint path 的 `.`/`..` 检查曾可被多层百分号编码绕过（`%2e%2e`、`%252e%252e`、`%2f` 拆分），现解码到稳定值后校验（`test_model_endpoint_path_may_not_contain_dot_segments`）；④ 供应商返回的负数 usage 计数被静默保留，现丢弃负值（`test_negative_usage_counters_are_dropped`）；⑤ `list_for_user(limit<=0)` 内存实现曾静默丢弃记录、PostgreSQL 会直接报 SQL 错，现两侧一致 `ValidationError`（`test_list_for_user_rejects_nonpositive_limit`）；⑥ 新增组合根 fail-closed 回归：即使存在合法许可，无凭据后端时远程调用在发请求前以 `MODEL_CREDENTIAL_UNAVAILABLE` 失败（`test_composition_root_fails_closed_when_credentials_unavailable`）；⑦ 新增真实 PostgreSQL 并发撤销反例：两个连接用不同幂等键同时撤销同一许可，恰好一个成功、另一个 `ConcurrentModificationError`，数据库终态 `REVOKED`（`test_concurrent_revokes_allow_exactly_one_winner`）。
- **独立验收修复（2026-09-17，验收未通过后的第三轮修复）**：按验收意见修复 6 类缺陷，均先补失败测试：
  ① 本地/远程适配器自建 client 曾读取 `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY`（实测 loopback 请求正文抵达外部代理），现固定 `trust_env=False`，真实 loopback 代理反例断言代理零流量（`test_local_adapter_ignores_environment_proxies`、`test_remote_adapter_ignores_environment_proxies`）；
  ② 许可曾只绑定可复用的 `provider_id`，同一 ID 改指其他 endpoint/model 后旧许可仍有效，且可为未配置 provider 预生成，现新增 `recipient_fingerprint`（provider_id + adapter + 规范化 endpoint + model），preview/confirm 只接受当前已注册的远端 provider，authorize 与服务层复核都精确比对，迁移 0005 同步新增 `recipient_fingerprint char(64) NOT NULL` 并更新 SHA-256（`test_repointed_provider_cannot_reuse_consent`、`test_repointed_provider_cannot_reuse_an_old_consent`、`test_unregistered_recipient_is_rejected_before_preview`、API `test_unregistered_recipient_is_rejected`）；
  ③ 类型化错误曾用 `from exc` 链入 broker/httpx/JSON 原始异常（`__cause__.request` 携带 Authorization 与敏感正文、`JSONDecodeError.doc` 携带响应正文），现适配器内无 `from exc`，错误在异常作用域外生成且 `__cause__`/`__context__` 为空（`test_credential_broker_failure_keeps_no_exception_chain`、`test_transport_failure_keeps_no_request_in_exception_chain`、`test_timeout_failure_keeps_no_exception_chain`、`test_malformed_json_keeps_no_response_body_in_exception_chain`，契约测试禁止适配器出现 `from exc`）；
  ④ `hmac.compare_digest` 直接比较 Unicode 字符串会在 authorize 抛裸 `TypeError`，现统一 UTF-8 bytes 比较（`test_unicode_binding_values_do_not_crash_authorize`、`test_unicode_ephemeral_consent_is_compared_without_crashing`）；
  ⑤ 过期许可曾可被成功撤销为 `REVOKED`，现在内存锁与 PostgreSQL 行锁内先原子落 `EXPIRED`（version+1）再抛 `DisclosureStateError`（`test_expired_consent_cannot_be_revoked` 单元 + PostgreSQL 集成同名测试）；
  ⑥ 响应 close 可被第二次取消打断，且一个 provider 关闭失败会跳过后续 provider，现关闭统一走 `core/models/cleanup.run_cleanup` 抗重复取消，`ModelRouter.aclose` 关闭全部 provider 后再抛首个错误（`test_repeated_cancellation_still_closes_the_response_stream`、`test_aclose_closes_every_provider_before_raising_first_error`、`test_aclose_is_resistant_to_repeated_cancellation`）。
- **独立验收修复（2026-09-17，验收未通过后的第四轮修复）**：再修复 4 项（2×P1、1×P2、1×文档），均先补失败测试：
  ① 响应元数据曾被信任：`ModelOutput.model_id` 现恒为已配置值，usage 只保留适配器固定键白名单（`usage_keys`），非 2xx 只映射固定 `rejection_code` 枚举、不再解析厂商正文，异常字段 `vendor_code` 更名为 `rejection_code`；路由器审计的 `resource_id`/`data.model_id` 改用已注册接收方 `provider.recipient.model_id`，`data.usage` 再按核心 `AUDITABLE_USAGE_KEYS` 过滤（`test_response_metadata_cannot_inject_audit_identity`、`test_rejection_code_is_a_fixed_enum_not_echoed_vendor_text`、`test_audit_uses_configured_model_id_not_provider_metadata`）；
  ② 凭据泄露路径：远端凭据在构造 header 前限定为 ≤4096 个可见 ASCII 字符，非 ASCII/控制字符转 `ModelCredentialUnavailableError`（不再抛出保存原始凭据的裸 `UnicodeEncodeError`）；传输 helper 在构造异常前删除 `payload`/`headers`/`request`/`response` 等敏感局部引用，反例断言出错异常的 traceback frame locals 取不到 API key、Authorization 或请求正文（`test_non_ascii_credential_is_rejected_before_http_encoding`、`test_transport_failure_leaves_no_secret_in_traceback_locals`）；
  ③ 响应 `aclose` 失败曾覆盖取消或原始类型化错误并泄露裸关闭异常，现优先级固定为“取消 > 原始类型化错误 > 已安全脱敏的关闭错误”，流关闭异常也统一转类型化错误（`test_close_failure_does_not_override_cancellation`、`test_close_failure_does_not_override_original_typed_error`、`test_close_failure_without_other_error_is_sanitized_typed`）；
  ④ 契约 13.2 的存储最小化字段清单补齐 `recipient_fingerprint`（与 0005 及 13.6 一致）。
- **独立验收修复（2026-09-17，验收未通过后的第五轮修复）**：再修复 3 项（2×P1、1×P2）并统一轮次文案，均先补失败测试：
  ① `ContextField` 未做运行时规范化，值为 `SECRET` 的外来 `StrEnum` 可绕过对象身份检查并把秘密发往远端：现构造时按值规范化为 `DataClassification`（字符串 `"SECRET"` 同样归一，非法值 `ValidationError`），Router/适配器/披露服务对未知分类 fail closed（`test_foreign_or_string_secret_classification_cannot_bypass_block`、`test_duck_typed_secret_field_still_fails_closed`、适配器 `test_foreign_and_string_secret_classifications_are_rejected`、`test_duck_typed_secret_field_fails_closed`、`test_local_adapter_rejects_foreign_secret_classification`、披露服务 `test_preview_rejects_foreign_and_string_secret_classifications`、`test_invalid_classification_is_rejected_at_construction`）；
  ② 脱敏边界只覆盖 `_post_json`：凭据解析失败时 `complete` frame 仍保留含敏感字段的 `request`，解析失败时 `raw`/解析对象仍在 frame locals，深层 JSON 还会抛裸 `RecursionError`；现 `complete()` 统一覆盖凭据解析、payload 构建、传输与解析，抛出前删除 `request`/`payload`/`headers`/`raw`，解析失败（含 `RecursionError`）统一转 `ModelProviderProtocolError`（`test_credential_failure_leaves_no_request_fields_in_traceback_locals`、`test_parse_failure_leaves_no_response_body_in_traceback_locals`、`test_deeply_nested_json_is_a_typed_protocol_error`）；
  ③ 审计曾直接记录调用方传入的 `consent_id`，远端拒绝转本地回退后仍写入伪造 ID；现授权决策返回实际采用的持久许可记录，只有真实远端敏感披露使用持久许可时才记录其 ID，本地/回退/公开字段/临时许可一律不记录（`test_forged_consent_id_is_never_recorded_in_audit`、`test_ephemeral_consent_is_recorded_without_a_forged_id`）。
- **独立验收修复（2026-09-17，验收未通过后的第六轮修复）**：修复 2 项 P1，均先补失败测试：
  ① 接收端漂移：Router 原来只在构造时计算 fingerprint，且审计在调用后重读 `provider.recipient`；现注册时冻结完整 `RecipientIdentity` 快照，远端调用前重新计算当前 fingerprint 并与快照比较，不一致即 `DisclosureDenied` 且零远端请求，审计 `model_id` 只取快照；内置适配器让出站 URL、payload model 与 `recipient` 共用同一冻结身份，运行时换端点只能重建 provider（`test_repointed_recipient_invalidates_the_old_consent`、`test_repointed_model_invalidates_the_old_consent`、`test_audit_identity_comes_from_the_registration_snapshot`、适配器 `test_adapter_recipient_matches_the_sent_request`）；
  ② 请求构建失败泄露敏感 locals：`ModelRequest`/`ContextField` 现做完整运行时校验并把 duck-typed 字段强制转换为真实 `ContextField`（非 str instruction/value/source 与非法分类在构造时 `ValidationError`）；`complete()` 把 payload 构建纳入统一脱敏边界，构建失败先丢弃原始异常与敏感引用、再从干净帧抛无链 `ValidationError`（`test_malformed_request_is_rejected_before_any_call`、`test_duck_typed_public_field_is_coerced_safely`、适配器 `test_payload_build_failure_is_typed_and_scrubbed`、`test_malformed_instruction_is_rejected_before_any_request`、`test_malformed_field_components_are_rejected`）。
- **独立验收修复（2026-09-17，验收未通过后的第七轮修复）**：修复 2 项 P1 竞态与 1 项 P2 校验缺口，均先补失败测试：
  ① 接收端检查与发送之间的竞态：Router 现在在授权返回后、调用 provider 前再次复核 fingerprint（授权期间改指向即 `DisclosureDenied`、零调用）；内置适配器在 `complete()` 进入且尚未 `await` 时捕获请求级 `RecipientIdentity`，URL、payload model 与输出身份全程使用该快照，凭据解析期间替换 `_recipient` 不会改变发送目标或记录身份（`test_recipient_change_during_authorization_is_denied`、`test_recipient_snapshot_is_pinned_across_credential_awaits`）；
  ② 字段规范化曾被 duck field 的 `__eq__` 跳过：现规范化元组无条件写回，可变 duck 对象不会在许可绑定后继续存活（`test_field_with_lying_equality_is_still_copied`，契约测试禁止 `!= self.fields`）；
  ③ 非可迭代 `fields`（`None`/`123`）与字段属性访问异常统一安全归一为无链 `ValidationError`，不再抛裸 `TypeError`/`AttributeError`（`test_malformed_field_components_are_rejected`）。
- **独立验收结果（2026-09-17，通过）**：第七轮定向反例（接收端授权期间竞态、duck `__eq__` 绕过字段绑定、非可迭代 `fields`）均通过复验；F04 目标集合 `161 passed`；`./scripts/test.ps1` `409 passed, 37 skipped`；`./scripts/test-postgres.ps1` `37 passed`；Ruff、Mypy、`pip check`、`git diff --check` 通过；`0001`–`0005` checksum 与报告一致，旧迁移未改动。
- **逐项反例映射**：SECRET 本地/远程/回退 0 次 provider 调用（`test_secret_is_blocked_before_consent_lookup_on_every_path`、`test_secret_is_denied_even_for_local_provider`、适配器 `test_adapter_refuses_secret_fields_before_sending`、`test_local_adapter_refuses_secret_fields_before_sending`）；未授权敏感字段不外发（`test_remote_requires_persisted_consent`）；provider/purpose/值/分类/来源变化失效（`test_binding_changes_invalidate_persisted_consent`、`test_digest_is_order_independent_and_binds_every_component`）；过期/撤销拒绝（`test_expired_and_revoked_consents_are_denied`、`test_revoke_is_terminal_and_replay_safe`）；重启与 Container 重建后可用（`test_container_rebuild_keeps_persisted_consent_usable`）；幂等重放与冲突（`test_confirm_is_idempotent_and_conflicts_on_new_content`、`test_idempotency_conflict_on_changed_content`、`test_concurrent_connections_cannot_create_conflicting_consents`）；并发唯一（PostgreSQL 集成测试）；无原文泄漏（`test_audit_records_binding_without_sensitive_values_or_credentials`、`test_raw_values_never_reach_consent_tables`、`test_preview_never_contains_raw_values`）；HTTP 超时/慢速/畸形/超限/取消类型化且关闭流（`test_slow_response_times_out_and_closes_stream`、`test_oversized_response_is_rejected_and_stream_closed`、`test_cancellation_propagates_and_closes_stream`、`test_malformed_and_incomplete_responses_are_protocol_errors`）；远程失败不改投（`test_remote_failure_never_falls_back_to_another_provider`、`test_provider_is_never_retried_silently`）；显式本地回退与 SECRET 复核（`test_fallback_only_on_missing_consent_and_must_be_local`、`test_default_fallback_comes_from_constructor_configuration`）；工具样式输出不执行（`test_tool_style_model_output_is_text_not_execution`）；依赖方向（`test_core_and_domain_never_import_http_or_vendor_sdks`、`test_router_never_executes_tools_or_touches_gateways`）；旧迁移不变（`test_frozen_migration_files_are_byte_identical`、`test_upgrade_from_0004_adds_only_0005_and_keeps_old_checksums`）；wheel（`test_0005_is_registered_for_wheel_packaging` + 手工解包核验）。
- **未实现（不得当作已完成）**：Windows Credential Manager 等真实宿主凭据后端（F09；当前 fail-closed 占位，生产远程调用会以 `MODEL_CREDENTIAL_UNAVAILABLE` 失败）；真实厂商端到端（无真实凭据，只有运行时 HTTP transport 协议测试，不能宣称厂商 E2E 通过）；Embedding/结构化输出；PWA 披露界面与 Agent 模型调用编排（F08/后续）；模型调用 HTTP 路由。
- **剩余风险**：许可 TTL 内被盗的 Access/浏览器会话可继续使用既有许可（与 R2 审批共享的产品边界）；SSE/审计只记录摘要，无法从审计重建被披露内容（有意的隐私取舍）；`model_disclosure_commands` 永久保留命令指纹（不含原文）；本机 Admin 进程重启会丢弃未确认预览（预览无状态，重新生成即可）。
- **失败红线复核**：SECRET/D3 未到达任何适配器（路由器 + 适配器双重拒绝，反例断言 provider 调用次数为 0）；无许可不向远程发送 PERSONAL/SENSITIVE；许可记录不含敏感原文；API key 只在 `resolve_for_broker` 返回值中短暂存在，不进入请求体/日志/异常/数据库/审计/fixture；未修改 0001–0004；远程失败不改投；模型输出不执行工具；production 不自动使用内存许可存储；未删除或弱化任何测试。

### F05 — 个人知识扩展（DONE，完整独立审计与修复后自审计通过）

范围：增量扫描、内容哈希、抽取、PostgreSQL FTS + pgvector、证据引用、删除传播。

能力边界：只读用户授权目录；原文件是事实源。

验收：修改/删除/重命名后索引正确；检索能返回文件与页/段位置；不同格式契约测试通过。

失败红线：索引结果无出处；删除原文后长期返回旧片段；修改用户原文件。

禁区：把向量库当唯一副本；把个人正文提交 Git。

#### 架构缺口清单（实现前检查，2026-09-17）

1. **扩展配置通道缺失**：`RuntimeContext.non_secret_config` 生产路径恒为空，无配置存储与注入。F05 新增通用 `ExtensionConfigStore` + `FileExtensionConfigStore` + Admin `GET/PUT /admin/v1/extensions/{id}/config`，由 `ProcessRuntimeSupervisor` 在 handshake 时注入；核心无 `personal.knowledge` 分支。
2. **扩展迁移未执行**：宿主只校验 `migration.list` 数量，没有 `ext_*` 迁移执行入口。F05 新增通用迁移机制：扩展通过 `host.data.migrate` 提交 `{version, path, checksum, description}`，宿主在已安装 payload 内解析、校验 SHA-256、经语句守卫后执行，并在扩展自有 Schema 内登记 `extension_data_migrations`（命名空间 advisory lock 串行化，批次单事务）。
3. **无数据库访问通道**：Worker 不能拿连接串/密码。F05 新增全双工 stdio（worker→宿主请求）与通用数据代理 `host.data.execute/transaction/migrate`：单语句守卫、参数/超时/行数上限、`search_path` 只含扩展 Schema + pgvector 类型 Schema + `pg_catalog`，并动态拒绝核心表未限定引用。`PA_STORAGE_BACKEND=memory` 时 fail closed。
4. **无 embedding 通道**：F04 只交付聊天模型。F05 在扩展内定义 `EmbeddingProvider` 端口与身份（provider/model/dim/version），默认 `none` 显式降级为仅 FTS；`ollama` 仅允许回环 HTTP；测试用确定性 hash 替身并明确标注非语义模型；身份随版本持久化，变化触发重建。

#### 交付与证据（2026-09-18，已标 DONE）

- **扩展制品**：`extensions/personal_knowledge`（Manifest + 4 个 JSON Schema + 扩展自有迁移 `migrations/0001_knowledge_index.sql` + `src/personal_knowledge/*`），工具风险仅 `READ`/`INTERNAL_WRITE`，无 `EXTERNAL_WRITE`。
- **SDK 兼容新增**：`HostDataClient`、`RuntimeContext.host_data`、`MigrationDescriptor.path`、`HostBroker`/`HostCapabilityError`、`rpc.decode_frame`；全双工宿主客户端在请求 id 不匹配时仍破坏流，未配置 `host_handler` 时返回 `DATA_UNAVAILABLE` 且流可用。
- **通用宿主能力**：`core/extensions/data_access.py`（守卫与协议）、`core/extensions/config.py`（JSON-Schema 子集校验与配置端口）、`infrastructure/database/extension_data.py`（真实执行 + 迁移 + 绑定校验）、`infrastructure/extensions/config_store.py`、`infrastructure/memory/extension_data.py`（fail closed）。
- **扩展能力**：路径安全（拒绝 `..`/绝对路径/盘符/根外符号链接与 junction/大小写绕过；遍历不跟随链接；只读）、Markdown/TXT/PDF 抽取与稳定 locator、SHA-256 版本化分块、`active_version` 原子切换、RRF 融合、引用复核与删除传播、`EventSource` 轮询式变更检测 + `reconcile` 计划。
- **契约文档**：`CONTRACTS_AND_INTERFACES.md` 第 14 节（contract v1.5）逐项冻结上述语义；`tests/contract/test_f05_contract_consistency.py` 锁定槽位/风险/依赖方向/迁移历史/文档表述。
- **独立验收修复（第二轮，2026-09-17，最终复验通过）**：独立验收提出 6 项 P1 + 2 项 P2，全部先补真实反例再修复：① `forbidden_relations` 统一进入 execute/transaction/migrate 三条路径（真实 PostgreSQL 证明 `SELECT/ALTER tasks` 与恶意迁移被拒绝且核心表未变、ledger 未登记）；② 授权根成为即时查询边界并删除已移除根（`search` 强制 root 交集、`reconcile` 读取全部 source 并 tombstone 未授权根）；③ `active_version IS NULL` 强制重试首次失败的构建；④ 取消/失败统一 shield 清理候选、reconcile 清理孤立 `BUILDING`，最终由第三轮收紧为 owner 绑定且 CAS 资格检查先于 READY 提升；⑤ 增量差异纳入 extractor 与完整 embedding 身份，Ollama 维度先 probe 再持久化；⑥ Ollama 改为 `asyncio.open_connection` 直连（无环境代理、无重定向、单调总 deadline、取消即关闭）；⑦ capability 授权门（未声明/不可用即拒绝 enable，`REQUIRED_CAPABILITY_UNAVAILABLE`）；⑧ `SearchResult` 只让 `CURRENT` 携带正文，STALE/DELETED 仅元数据且 `retrieve` 只返回 CURRENT。反例见 `tests/integration/test_postgres_f05.py` 的 acceptance counterexamples 段落、`tests/unit/test_extension_capability_gating.py`、`tests/unit/test_knowledge_embedding.py` 的代理/滴流/取消测试。
- **第三轮完整修复（2026-09-17，最终复验通过）**：针对再次审计的 8 项缺陷逐项补反例并修复：① execute/transaction/migrate 在设置 `search_path` 前以有界 advisory lock 创建专属 Schema，避免首次 DDL 落入 pgvector 所在 `public`，核心关系禁表每次请求刷新；② worker SDK 把 deadline 写入请求参数，数据库 statement timeout 统一映射 `DATA_TIMEOUT`；③ `begin_version` 不再覆盖既有 READY/他人 BUILDING，同一 `built_by` owner 贯穿 chunk 写入、无副作用 CAS 激活和失败清理；④移动后重用旧路径时稳定选择碰撞后备 source id；⑤向量查询只在 MATERIALIZED identity 匹配集上计算距离，模型/维度漂移在 reconcile 前安全退回 FTS；⑥超长单行/大 PDF 页使用可复算字符 locator 分片，embedding ≤512 条/批，数据库帧按字节有界批处理；⑦ Ollama 截断 Content-Length 转 `EMBEDDING_PROTOCOL_ERROR`；⑧ source generation 进入事件幂等键，重复真实转换不再永久丢事件。新增真实 PostgreSQL 反例覆盖 namespace、动态核心表、timeout、owner、路径复用、长单行、identity 漂移与重复事件。
- **迁移**：F05 未新增核心迁移（`0001`–`0005` 未改动，checksum 由契约测试锁定）；扩展自有迁移 `extensions/personal_knowledge/migrations/0001_knowledge_index.sql`，SHA-256 `0f8758f97cce7206ac0b5cd4aa159f1b7b43de79cf1fa77a811a67f8aecd759d`，由宿主在扩展 `ext_*` Schema 内执行并登记 `extension_data_migrations`。
- **制品核验**：`pip wheel . --no-deps --no-build-isolation` 产出框架 wheel（171 个条目），含 `core/extensions/{data_access,config,rpc}.py`、`infrastructure/database/extension_data.py`、`infrastructure/extensions/config_store.py`、`memory/extension_data.py`、`personal_assistant_sdk/host.py`、PWA、config 与 `0001`–`0005` 全部迁移，且不含任何 `personal_knowledge`/`extensions/` 条目；扩展以独立 zip 制品（`extension.toml` 位于制品根）经真实 Supervisor 完成 install → enable → reindex → search → uninstall 烟测（自动化测试 `test_zip_artifact_installs_and_serves`）。注：Python wheel 形式的扩展包会把 manifest 放在 `.data/data/` 下，不满足 Supervisor 的“manifest 位于制品根”要求，因此制品格式为目录或 zip。
- **失败/原子性反例**：注入抽取器故障后旧活动版本仍可查询、无 BUILDING 残留、下次 reconciliation 成功切换（`test_build_failure_keeps_old_version_queryable`）；手工插入的半成品版本对查询不可见（`test_building_version_is_invisible_to_queries`）；并发 reconciliation 收敛为每来源唯一活动版本（`test_concurrent_reconciliation_converges`）；删除后分块/版本/tombstone 语义与检索为空（`test_delete_removes_content_and_keeps_minimal_tombstone`）；引用在源文件变化后为 STALE、删除后为 DELETED（`test_stale_citation_after_change_is_not_current`、`test_worker_retrieve_marks_deleted_sources`）；索引过程不修改源文件（`test_indexing_never_modifies_source_files`）；越界符号链接不被索引（`test_out_of_root_symlink_is_never_indexed`）。
- **黄金查询集**：合成语料 9 个文件、12 条查询，FTS-only 与（确定性测试替身）混合模式均 `Recall@5 ≥ 0.90`，所有返回引用的 locator 切片与源文件逐字一致、哈希匹配（`test_golden_query_set_recall_and_citations`）；无证据查询返回 `unknown=true`（`test_search_is_unknown_without_evidence`）。
- **第四轮完整独立审计（8 组缺陷）**：① 配置校验拒绝非有限数值与隐藏在未支持结构内的 Schema 关键字；② 文件实际读取和 PDF Flate/页面聚合解压均在读后再次执行硬字节上限；③ `.txt`↔`.md` 跨媒体类型移动触发抽取器身份重建；④并发陈旧移动不能重复推进 generation/事件；⑤数据适配器首次向量 Schema 探测复用当前连接，不在单连接池内自锁；⑥ namespace advisory lock 超时统一类型化；⑦迁移禁止通过 `SET LOCAL search_path` 越权；⑧反例覆盖上述路径并在真实 PostgreSQL 上复验。
- **第五轮修复后自审计（严格同级）**：继续发现并修复配置存储严格 JSON/大小/并发替换、启动时按当前版本 Schema 复核、路径读后重新解析、来源激活/删除完整快照 CAS、删除消耗 generation、长构建 heartbeat 租约、事件轮询与 reconciliation 共享 rename/re-add/dedupe 语义、向量 JSON 帧与数据库结果总字节限制、参数深度/非有限值、迁移单次校验读取与逐语句白名单、PDF 页面聚合解压上限。修复后再次执行目标、全量、PostgreSQL/Worker、静态与制品验证。
- **最终命令证据（第五轮审计后）**：`./scripts/test.ps1` → `597 passed, 81 skipped`，Ruff `All checks passed!`，Mypy `Success: no issues found in 166 source files`；`./scripts/test-postgres.ps1` → `81 passed`（PostgreSQL 17.11 + pgvector，含真实 Worker）；`python -m pip check` 与 `git diff --check` 通过。

未实现（有意）：宿主在 enable 时自动执行 MigrationProvider（当前由扩展经数据能力触发）；OS 级文件监听（F09）；无维度约束向量列的 HNSW 索引（当前精确检索）；真实远程 embedding 与语义向量质量验收；PWA Schema 表单渲染（F08）；永久 purge 仍 501。

剩余风险：本机进程创建延迟会影响 F02 既有的亚秒级 RPC 计时测试（与 F05 代码无关，环境负载敏感）；`plainto_tsquery('simple')` 对无空格中文长句只能整句匹配（黄金查询集使用词边界清晰的内容）；向量维度未约束时无法建 ANN 索引，规模上限依赖精确检索。

### F06 — smail 扩展（NEXT，未开始）

范围：IMAP SSL 轮询、UIDVALIDITY/UID 去重、线程历史、草稿、受控 SMTP SSL 发送及对账。

能力边界：读取与发送能力分离；客户端专用密码仅进系统凭据库。

验收：五分钟轮询；重扫不重复处理；并发发送同一幂等键最多一封；手机编辑使旧审批失效；UNKNOWN 不自动重发。

失败红线：未确认最终 MIME 发送；记录密码；不确定结果自动重试。

禁区：默认自动回复；绕过统一 Tool Gateway；fixture 使用真实邮件。

### F07 — ehall 扩展（TODO）

范围：有头 Playwright + Desktop Companion；用户亲自完成 SSO/验证码/扫码；只读枚举后选择一个低风险事项；填写至提交前预览。

能力边界：首版只支持一个经确认的低风险事务。R3 事务永远禁止。

验收：登录挑战不被绕过；最终字段/后果在 Android 预览确认；真实低风险提交（若启用）有独立审批和回执；DOM 变化安全失败。

失败红线：退课/撤回/付款/选课变更可执行；隐藏浏览器提交；结果不明自动重复点击。

禁区：逆向私有 XHR、绕过验证码、保存会话 cookie 到日志/模型。

### F08 — PWA 与 Web Push（TODO）

范围：扩展现有响应式 PWA 壳：补齐任务列表/详情、草稿编辑、Schema 表单、SSE 可靠续传和最小元数据 Web Push。

能力边界：Schema 驱动 UI，不加载扩展 JS；Service Worker 不缓存敏感 API。

验收：Android Chrome 完成两条 E2E 流；离线后重连恢复状态；通知不含邮件正文/表单字段；编辑冲突返回 409/412。

失败红线：确认页被缓存；断线导致重复审批；任意扩展脚本进入页面。

禁区：把业务编排放进前端；在 localStorage 保存凭据或完整敏感正文。

### F09 — Windows 生产适配器（TODO）

范围：Credential Manager、服务守护、交互桌面桥、进程管理、文件监听；保留 portable test doubles。

能力边界：Win32 代码只位于 `infrastructure/platform/windows`。

验收：Windows 冷启动恢复；服务态任务能请求交互桌面；核心测试在非 Windows test doubles 上仍通过。

失败红线：核心/扩展 SDK import Win32；服务把密钥放环境变量/命令行。

禁区：用 UI 自动化代替稳定协议接口；把交互桌面权限开放给所有扩展。

### F10 — 部署、备份与双入口安全测试（TODO）

范围：Cloudflare Tunnel、Access、Tailscale health-only、6 小时恢复点、异地加密副本与恢复演练。

能力边界：常开主机与普通笔记本功能一致，只承诺可用性差异。

验收：黑盒证明 8010 只有 health；8001 仅回环；RPO <= 6h、RTO <= 2h 恢复演练；D2 受管制品纳入备份，D3 调试数据排除。

失败红线：Tailscale 可达任务/审批/管理 API；备份从未恢复验证；笔记本睡眠后自动重放发送/提交。

禁区：将 Git 当数据备份；把 cloudflared 指向 Admin API。

## 两条最终 E2E

- E2E-A：邮件通知 → 知识检索 → 缺失材料 → 手机补充 → ehall 填写 → Android 最终预览确认 → 可选低风险真实提交 → 归档。
- E2E-B：邮件 → 历史/知识 → 草稿 → Android 编辑 → 精确确认 → 受控真实 SMTP 发送 → 归档。

两条都必须通过；模拟外部系统的测试不能替代最后一次受控真实验收。
