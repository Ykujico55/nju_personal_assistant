# 项目接力 TODO

状态只使用 `DONE / NEXT / WAITING / BLOCKED`。每个 Agent 一次只推进一个编号；完成后写入可复现的命令与结果，不得顺手开始下一项。

## 已完成

- [x] **F00 — 基础框架（DONE）**
  - Python 3.12 + FastAPI 三入口、PWA 壳、Agent Plan/Act/Observe、上下文管理、Tool Gateway、精确审批、扩展 Registry/Lifecycle/RPC 契约、扩展 SDK、队列/租约/Outbox 端口、内存参考适配器与示例扩展已经建立。
  - 证据：`scripts/test.ps1` 为 42 passed，Ruff/Mypy 通过；`pip check` 无冲突；wheel 含核心、SDK、PWA、配置和迁移。
  - 有意锁定：生产 PostgreSQL、真实 Agent Worker、扩展 Supervisor、Cloudflare JWT、真实模型和业务扩展尚未实现；不得把当前 `QUEUED` 状态描述成任务已经执行。
- [x] **F01 — PostgreSQL 持久化内核（DONE）**
  - 已交付迁移运行器、`0002_f01_persistence.sql`、Task/Run/Checkpoint/Observation/Approval/Job/Event/Audit/Side-effect Outbox/Extension lifecycle 的真实 PostgreSQL 适配器，以及生产 composition root 和启动/关闭生命周期；生产数据库不可达或迁移失败时 fail closed。
  - 三轮验收发现的 9 项缺陷均已修复并由反例覆盖，包括审批过期/绑定/终态原子性、带数据 0001 升级、Run/Checkpoint 事务、checkpoint 序列、Extension 回填和内存 Outbox 单次审批语义。
  - 最终复验：`./scripts/test-postgres.ps1` 为 24 passed；`./scripts/test.ps1` 为 44 passed、24 skipped；Ruff、Mypy、`pip check`、`git diff --check` 通过。迁移 `0002` SHA-256 为 `5fdc67aaff07dbcd486fc62774ad5745cbef871332433dfb8535790a0a18a580`。
  - 已知边界：外部动作完成到 `finalize` 之间崩溃会保留 `EXECUTING/PREPARED` 未决态，后续必须通过 reconciliation 处理，禁止自动重试。

- [x] **F02 — Extension Supervisor（DONE）**
  - 已交付：受控暂存与静态检查（本地目录/zip/whl，拒绝 URL 与未固定 lock；路径穿越、符号链接、越界 Manifest 引用安全失败）、一次性确认屏障（重新校验 staged hash，确认后 artifact 变化即失效）、每扩展版本独立 venv 与真实 Worker 子进程、JSON-RPC framing/超时/超限加固、handshake 精确校验后才发布原子 Registry Snapshot、enable/disable（有界排空）/upgrade（旁路安装+原子切换+失败保持旧版）/rollback（数据 Schema 兼容校验）/uninstall（保留 `ext_*` 数据与 tombstone）、operation 状态与安全诊断持久化（PostgreSQL `extension_operations`，无需新迁移）、启动恢复（ENABLED 重启 Worker，瞬时态收敛，RUNNING operation 标记 `SUPERVISOR_RESTART`）、Local Admin API 管理端点与 CLI（inspect→确认→operation 轮询）。
  - 第二轮复验修复：新增迁移 `0003_f02_operations.sql`（幂等键/命令指纹 + 部分唯一索引，SHA-256 `28cbda227918bfcd80366208b59713eb0dbb0b0cbdaa582cb5e55a91ce2e1e03`）；Worker 最小环境白名单；暂存/哈希/安装统一忽略集合；lockfile 精确版本+哈希与 `--require-hashes`；升级基线重查与全链路补偿；安装最终持久化失败回滚代码目录；拒绝计划语义与重新安装；有界 drain 与 `DrainReport` 校验；契约验证比较完整工具描述符/风险/Schema/槽位；恢复失败先停 Worker；RPC 写入/取消类型化；`data_namespace` 单射；诊断码允许列表强制；公共扩展列表读持久 store；`RUNNING` 转换失败原子落 `FAILED`。
  - 第三轮复验修复：新增迁移 `0004_f02_operation_request_scope.sql`（request_scope + 部分唯一索引，SHA-256 `6b6bb9f5cea83b443c9ca345f7a9d134f6ebca69969f01e23557ecff706af6fc`）；取消统一“先清理后重抛”（shielded cleanup + 子进程回收）；drain 单调 deadline 覆盖等锁+写入+读取并严格校验 DrainReport；写入期取消破坏 RPC 流；安装取消清理版本目录/staging/plan 并收敛 REJECTED；升级自禁用旧版起全链路补偿（含 UPGRADING 保存与 DRAIN_TIMEOUT）；install/upgrade 重放先查持久 request_scope，跨重启返回原 operation；`interrupt_running` 强制诊断码白名单。
  - 第四轮复验修复：回滚先解析候选再禁用当前版本，失败时补偿保持当前版本 ENABLED 且可调用；`run_blocking` 取消时等待复制线程结束，删除后的版本目录不会被线程重建；`call()` 的 deadline 覆盖锁等待；契约验证 finally shield 关闭 Worker；新增文档-实现一致性测试。
  - 第五轮复验修复：回滚候选的启动/健康/发布/持久化全部纳入 `LifecycleManager.rollback` 同一补偿边界（Supervisor 删除额外 `enable()`），候选失败时撤销发布、停止 Worker 并重新启用原版本（四类反例）；`run_blocking` 首次取消优先，线程在取消后抛异常不覆盖取消信号（支持重复取消）。
  - 第六轮验收材料修复：取消反例直接取消单个 `manager.rollback()` 任务（不再用 `service.stop_all()`），测试 Runtime 维护活动 Worker 映射且未启动时 `invoke_tool` 失败；另增 `stop_all` 全停后 `recover()` 按持久 ENABLED 记录重启的测试；文档数字同步。
  - 证据：`./scripts/test.ps1` 153 passed、28 skipped；`./scripts/test-postgres.ps1`（F01+F02）28 passed；F02 相关集合 110 collected（无数据库时 106 passed + 4 skipped；cancel_cleanup 11、supervision_service 34；含真实 `python -m venv` + 真实 Worker 子进程集成、契约漂移、崩溃/超时/框架错误注入、取消资源回收、失败回滚保持当前版本、升级失败矩阵、并发启动、PostgreSQL 重启恢复与跨重启幂等重放）；测试后无残留 python/worker 子进程；Ruff、Mypy、`pip check`、`git diff --check` 通过；wheel 含 Supervisor/SDK/PWA/config/四份迁移。
  - 有意未实现：永久 purge（API 明确 501，需独立影响预览与二次确认）、扩展自有 `ext_*` migration 执行入口、扩展配置表单注入、Worker 崩溃后的自动重启退避（当前崩溃即 QUARANTINED，重启恢复只在宿主启动时）、远程 URL/Git 制品（默认拒绝）、跨进程操作互斥与 `NotificationProvider` 启用前行为验证。
  - 已知风险：宿主被强杀时子进程可能成为孤儿（Windows 需要 F09 的 Job Object 管护）；待确认的 staged 计划只存在于 Admin 进程内存，重启后按 `REJECTED` 收敛并清理暂存目录；带哈希锁的第三方依赖需要可达包索引/缓存。

## 接下来几步

- [x] **F03 — Cloudflare Access 验证（DONE — 三轮独立验收通过）**
  - 已交付：`core/auth/ports.py` 的传输无关身份端口；`infrastructure/auth` 的 Cloudflare Access JWT verifier（含总 deadline、禁重定向、取消安全刷新；未知 `kid` 成功刷新确认不存在为 401、节流窗口内未检查为可重试 503）与有界 JWKS 缓存/轮换；public middleware 的载体提取（含跨原始 Cookie 头重复检测）与稳定 401/503；`PA_CF_ACCESS_TEAM_DOMAIN`/`PA_CF_ACCESS_AUD`/`PA_PUBLIC_ORIGIN` 在所有 Settings 构造路径写回规范化值并校验；可注入 verifier；运行时依赖 `PyJWT`/`cryptography`/`httpx`。
  - 第一轮独立验收的 6 项缺陷（手工 Settings 绕过、无总 deadline/未禁重定向、取消污染节流、重复 Cookie 头、API→infrastructure 反向依赖、关闭顺序）已全部修复并补反例测试。
  - 第二轮独立验收的 2 项缺陷（节流窗口把合法密钥轮换误报为不可重试 401；接口归属/规范化文档过期）已修复：未检查的未知 `kid` 现在返回可重试 503，成功刷新确认不存在才 401；`Settings` 所有构造路径写回 canonical 值再校验。
  - 证据：F03 目标集合 `124 passed`；`./scripts/test.ps1` `269 passed、28 skipped`；`./scripts/test-postgres.ps1` `28 passed`（F01+F02 无回归）；Ruff、Mypy（138 files）、`pip check`、`git diff --check` 通过；wheel 含 `core/auth`、`infrastructure/auth` 与四份迁移。详见 `docs/NEXT_STEPS.md`。
  - 最终独立复验：F03 目标集合 `124 passed`；全量 `269 passed、28 skipped`；PostgreSQL `28 passed`；手工轮换复现、wheel 内容与四份迁移 checksum 均通过复核。
  - 未交付（有意，均属于 F03 范围之外）：自定义 Access 域名、应用内登录/MFA、Tunnel/Tailscale 编排；F04 披露许可已由 F04 单独实现（见下）。
- [x] **F04 — 模型适配器与敏感披露许可（DONE — 独立验收通过）**
  - 已实现：`core/models/disclosure.py` 的持久披露许可端口/服务（`DisclosureConsentService`/`DisclosureAuthorizer`/`canonical_field_digest`）、`core/models/errors.py` 类型化 provider 错误、`ModelRouter` 接入持久许可查询与显式本地回退；`infrastructure/models/` 的真实远端 OpenAI 兼容适配器与本地 Ollama 适配器（协议驱动 HTTP、SecretHandle 宿主解析 fail closed、loopback 强制、禁重定向、有界响应、总 deadline、取消安全、不重试）；`PostgresDisclosureConsentStore` + 内存测试替身；迁移 `0005_f04_model_disclosure.sql`（`model_disclosure_consents`/`model_disclosure_commands`，SHA-256 `012532834b281040d0031b48744ec7298e3c9b960bb247f51fd22c050f7534f0`）；`PA_MODEL_*` 配置在直接构造/`from_env`/`replace` 全部规范化并校验；public API 的 `preview/confirm/revoke` 最小接口；组合根接入真实适配器与 fail-closed 凭据占位。
  - 证据：两轮自我审查 16 项与第三至七轮验收修复后，F04 目标集合 `161 passed`（含 `test_model_disclosure.py`、`test_model_router_f04.py`、`test_model_adapters.py`、`test_bootstrap_f04.py`、`test_disclosures.py`、`test_f04_contract_consistency.py`、`test_settings.py`、`test_model_router.py`）；`./scripts/test.ps1` `409 passed, 37 skipped`；`./scripts/test-postgres.ps1` `37 passed`（F01 24 + F02 4 + F04 9）；Ruff、Mypy（150 files）、`pip check`、`git diff --check` 通过；wheel 含 `core/models/disclosure.py`、`infrastructure/models/*`、`PostgresDisclosureConsentStore` 与五份迁移。原始结果、逐项反例与残余风险见 `docs/NEXT_STEPS.md` 与契约 13.7。最终独立验收通过（七轮修复后）：F04 目标集合 `161 passed`；全量 `409 passed、37 skipped`；PostgreSQL `37 passed`；Ruff、Mypy、`pip check`、`git diff --check` 通过；`0001`–`0005` checksum 与报告一致，旧迁移未改动。
  - 仍未实现：Windows Credential Manager 真实凭据后端（F09，当前为 fail-closed 占位）、真实厂商端到端（无真实凭据，只有 HTTP transport 协议测试）、Embedding/结构化输出、PWA 披露界面与模型调用编排（F08/后续）。未 commit，未 push。
- [x] **F05 — 个人知识扩展（DONE — 完整独立审计与修复后通过）**：已交付 `extensions/personal_knowledge`（`knowledge.file_changes`/`knowledge.retrieve`/`knowledge.search` READ/`knowledge.reindex` INTERNAL_WRITE/`knowledge.reconcile`/`knowledge.roots`/`knowledge.index_schema`）、通用宿主能力（全双工 RPC、`host.data.execute/transaction/migrate` 数据代理、`ExtensionConfigStore` 配置通道与 Admin `GET/PUT .../config`）、授权根路径安全、Markdown/TXT/PDF 抽取与稳定 locator、SHA-256 版本化分块与来源快照 CAS、扩展自有 `ext_*` Schema 迁移、PostgreSQL FTS + pgvector RRF 混合检索、引用复核与删除传播、embedding 端口（默认显式降级为仅 FTS）。
  - 第二轮独立验收修复（6×P1 + 2×P2）已完成并在最终复验中通过：数据代理三路径统一核心表拦截、授权根即时边界与删除传播、首次失败重试、取消/孤立候选清理与激活 CAS、embedding 身份纳入增量差异（含维度探测）、Ollama 无代理总 deadline、capability 授权门、STALE/DELETED 不返回正文。
  - 第三轮完整修复（8 项）已完成：首次数据调用先串行创建专属 Schema 且动态刷新核心关系禁表；SDK deadline 传入宿主并把 PostgreSQL statement timeout 类型化为 `DATA_TIMEOUT`；同版本构建采用 `built_by` 全链路 owner + 无副作用 CAS；移动后旧路径可建立新 source；向量检索严格绑定活动版本 embedding identity；长单行/大 PDF 页可精确分块且 embedding/数据库写入有界批处理；Ollama 截断响应类型化；事件以单调 generation 区分重复 A→B→A→B 转换。
  - 最终独立审计与自审计修复：配置严格 JSON/Schema/并发原子写入与启动复核；读取和 PDF 解压双层字节上限；来源激活/移动/删除使用完整快照 CAS 且 generation 严格单调；长构建 heartbeat 租约；轮询事件与 reconciliation 共用转换语义；数据参数深度、向量帧和聚合结果有界；迁移单次校验读取、逐语句白名单与 search_path 越权阻断。每项均有反例测试。
  - 证据：`./scripts/test.ps1` → 597 passed、81 skipped，Ruff/Mypy（166 files）通过；`./scripts/test-postgres.ps1` → 81 passed（PostgreSQL 17.11 + pgvector，含真实 Worker）；`pip check`/`git diff --check` 干净。扩展迁移 `0001_knowledge_index.sql` SHA-256 为 `0f8758f97cce7206ac0b5cd4aa159f1b7b43de79cf1fa77a811a67f8aecd759d`；`0001`–`0005` 核心迁移未改。详见 `docs/NEXT_STEPS.md`。
  - 未实现（有意）：宿主自动执行 MigrationProvider（当前由扩展经数据能力触发）、OS 级文件监听（F09）、向量 ANN 索引（无维度约束列）、真实远程 embedding、PWA 表单渲染（F08）、永久 purge（501）。

- [x] **F06 — smail 扩展（DONE — 六轮独立审计及补充复验通过）**：已交付 `extensions/nju_smail`（`smail.poll_inbox`/`smail.thread_history`/`smail.search` READ/`smail.prepare_reply` INTERNAL_WRITE/`smail.sync`/`smail.send` EXTERNAL_WRITE/`smail.send_status`/`smail.reconcile_send`/`smail.reply_flow`/`smail.poll_every_5m`/`smail.account_settings`/扩展自有迁移）与通用宿主邮件能力：`core/mail` 端口（`MailTransportBroker`/`MailAccountBinding`/`MailReadSession`/`MailEnvelope`/`MailRecipientResult`/`MailDeliveryReceipt`/`MailReconciliationResult`/`MailPolicy`/`MailDeliveryLedger`）、SDK `host.mail.*` 只读客户端与 `host.artifact.*` 能力、真实 TLS IMAP 只读客户端（`EXAMINE`/`BODY.PEEK`，可证明不改变服务端状态）、真实 TLS SMTP 状态机（DATA 前/中/后断线分级、逐收件人结果）、`MailSendExecutor`（Tool Gateway + R2 审批 + Outbox + 扩展按当前草稿版本物化精确 MIME 字节）、迁移 `0006_f06_mail_transport.sql`（通用 `mail_delivery_actions` 传输台账）。读取与发送能力独立声明/配置/授权；扩展不持有密码、不建 SMTP/IMAP 连接；无审批时 executor 调用为 0；编辑草稿后旧审批绑定失效；UNKNOWN 不自动重发并由 Sent 只读对账收敛；账户换绑在跨进程 `ExclusiveFileLock` + 活体 fail-closed guard 下不再有 check-then-act（DATA 前换绑中止、DATA 已开始则等待提交），租约 guard 以单调 deadline 为权威并锚定数据库领取/续租时刻（心跳挂起、间隔大于租约或领取后慢复核均在到期即拒绝派发），组合 guard 在全部锁内复核，注册表写操作经 `run_blocking` 取消安全执行（调用方观察到取消前提交必已落地），真实 sender 抛类型化 `ACCOUNT_CHANGED`。
  - 最终独立验收证据：`./scripts/test.ps1` → 742 passed、103 skipped、Ruff `All checks passed!`、Mypy `Success: no issues found in 192 source files`；`./scripts/test-postgres.ps1` → 103 passed（真实 PostgreSQL、personal_knowledge Worker 与 smail 生产组合根 Worker）；`pip check` 与 `git diff --check` 通过；测试后无残留 Python Worker；框架 wheel 190 个条目，包含 F06 邮件模块与 `0006_f06_mail_transport.sql`，且不包含 `nju_smail` 扩展源码。核心新增迁移 SHA-256 为 `dbc5001bdd16981f2a17f36abe4ef3fcdd36c63c3461477f1a61e1ecb6f32a01`；扩展迁移 SHA-256 为 `6320af7791bd2805e36c70f7fa731417935e7bfd6ad875b95a6d42d179469d8c`；`0001`–`0005` 未改。
  - 真实外部验收未执行：没有用户提供的 smail 账号、SecretHandle 与受控收件地址，因此未连接真实邮箱、未发送真实邮件；协议级模拟服务器与真实 TLS/socket/PostgreSQL/Worker 测试不能替代真实 smail E2E。Windows Credential Manager 仍属 F09，扩展与宿主均保留通用 SecretHandle 端口并在生产后端不可用时 fail closed。

- [ ] **F07 — ehall 扩展（NEXT）**：有头 Playwright + Desktop Companion；用户亲自完成 SSO/验证码/扫码；首版只支持一个经确认的低风险事务，并在最终提交前展示完整预览。详细范围与红线见 `docs/NEXT_STEPS.md`。

F05–F10 和两条最终 E2E 的完整范围见 `docs/NEXT_STEPS.md`。F02–F06 均已完成独立验收；F07 是唯一 `NEXT`，F08+ 尚未开始。

## 每次交接必须留下

1. 本文件中的状态变更，只能把实际完成项标为 `DONE`。
2. `docs/NEXT_STEPS.md` 中对应任务的命令、测试数量、真实限制和剩余风险。
3. 目标测试、`scripts/test.ps1`、Ruff、Mypy 的原始结果摘要。
4. 新迁移、公共接口或行为变化的说明；既有迁移不得重写。
