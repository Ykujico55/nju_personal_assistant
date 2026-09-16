# 实现接力清单

本清单把详细设计拆成较小、可独立验收的工作包。状态只允许 `TODO / IN_PROGRESS / DONE / BLOCKED`。后续模型一次领取一个任务，完成后在本文件写入测试命令和结果。

简要状态见 `../TODO.md`，冻结的接口、事务与验收契约见 `CONTRACTS_AND_INTERFACES.md`。F01、F02、F03 已通过最终复验；当前唯一允许领取的是 F04，不得并行或提前开始 F05。

## 基架状态

| 编号 | 状态 | 内容 | 验收证据 |
|---|---|---|---|
| F00 | DONE | 核心、API、PWA 壳、扩展 SDK、内存适配器和测试基架 | `scripts/test.ps1`：42 passed；Ruff 与 Mypy 通过；`pip check` 无冲突；wheel 含核心/SDK/PWA/配置/迁移；Uvicorn/API/CLI/Worker 子进程烟测通过 |
| F01 | DONE | 迁移运行器 + 真实 PostgreSQL 仓储/队列/outbox/审计/扩展状态适配器 + composition root 与启动生命周期 | 最终复验：`./scripts/test-postgres.ps1` 24 passed；`./scripts/test.ps1` 44 passed、24 skipped；Ruff/Mypy/`pip check`/`git diff --check` 通过；旧库 checkpoint 续写、Extension 完整回填、PostgreSQL Gateway 四类终态和内存审批单次消费均独立验证通过 |
| F02 | DONE | Extension Supervisor、确认屏障、每版本 venv/Worker、生命周期、Admin API/CLI、操作持久化与恢复 | 六轮独立审计通过；`./scripts/test.ps1` 153 passed、28 skipped；`./scripts/test-postgres.ps1` 28 passed；F02 相关集合 110 collected；Ruff/Mypy/`pip check`/`git diff --check` 与 wheel 内容核验通过 |
| F03 | DONE | Cloudflare Access JWT 验证、JWKS 缓存/轮换、public/Admin/health 边界回归 | 三轮独立验收通过；前两轮 6+2 项缺陷均修复并补反例；F03 目标集合 124 passed；`./scripts/test.ps1` 269 passed、28 skipped；PostgreSQL 28 passed；Ruff/Mypy/`pip check`/`git diff --check`、手工轮换复现与 wheel 内容核验通过 |

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

### F04 — 模型适配器与披露许可（TODO，当前唯一 NEXT）

范围：为现有 `ModelRouter`、字段分类、精确 disclosure consent 和本地回退策略实现真实本地/远程模型适配器与持久 consent receipt。

能力边界：路由不执行工具，不自行扩大上下文。

验收：敏感字段发往远程前展示接收方/目的/字段并取得许可；凭据、token、cookie、私钥在所有路径硬阻断。

失败红线：缺少 consent 仍远传；redaction 失败后 fail open。

禁区：把整份资料作为“方便上下文”发送；在日志记录原始模型请求。

### F05 — 个人知识扩展（TODO）

范围：增量扫描、内容哈希、抽取、PostgreSQL FTS + pgvector、证据引用、删除传播。

能力边界：只读用户授权目录；原文件是事实源。

验收：修改/删除/重命名后索引正确；检索能返回文件与页/段位置；不同格式契约测试通过。

失败红线：索引结果无出处；删除原文后长期返回旧片段；修改用户原文件。

禁区：把向量库当唯一副本；把个人正文提交 Git。

### F06 — smail 扩展（TODO）

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
