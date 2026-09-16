# 实现接力清单

本清单把详细设计拆成较小、可独立验收的工作包。状态只允许 `TODO / IN_PROGRESS / DONE / BLOCKED`。后续模型一次领取一个任务，完成后在本文件写入测试命令和结果。

简要状态见 `../TODO.md`，冻结的接口、事务与 F01 验收契约见 `CONTRACTS_AND_INTERFACES.md`。F01 已通过最终复验；当前唯一允许领取的是 F02，不得并行或提前开始 F03。

## 基架状态

| 编号 | 状态 | 内容 | 验收证据 |
|---|---|---|---|
| F00 | DONE | 核心、API、PWA 壳、扩展 SDK、内存适配器和测试基架 | `scripts/test.ps1`：42 passed；Ruff 与 Mypy 通过；`pip check` 无冲突；wheel 含核心/SDK/PWA/配置/迁移；Uvicorn/API/CLI/Worker 子进程烟测通过 |
| F01 | DONE | 迁移运行器 + 真实 PostgreSQL 仓储/队列/outbox/审计/扩展状态适配器 + composition root 与启动生命周期 | 最终复验：`./scripts/test-postgres.ps1` 24 passed；`./scripts/test.ps1` 44 passed、24 skipped；Ruff/Mypy/`pip check`/`git diff --check` 通过；旧库 checkpoint 续写、Extension 完整回填、PostgreSQL Gateway 四类终态和内存审批单次消费均独立验证通过 |

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

### F02 — Extension Supervisor（TODO，当前唯一可领取）

范围：把现有 Manifest/确认屏障/生命周期/Registry/JSON-RPC 契约接到真实暂存目录、venv 和进程适配器；完成排空、升级和回滚。不要重新设计已有协议。

能力边界：进程/依赖隔离，不承诺防御同用户恶意代码。

验收：确认之前没有 build/install/import/execute；示例扩展可安装、启用、调用、禁用、卸载后保留数据；故障升级原子回滚。

失败红线：确认前执行第三方代码；半个 Registry Snapshot 生效；升级失败丢失旧版本。

禁区：后台自动升级；从任意 URL 拉取未固定 revision；将 venv 称为安全沙箱。

### F03 — Cloudflare Access 边界（TODO）

范围：实现 Access JWT 的签名、issuer、audience、expiry 和代理头白名单。Origin/custom-header CSRF 基架已经存在；当前 Cloudflare 模式故意对全部请求返回 503。

能力边界：只保护用户 API；本机 Admin API 不经 Tunnel。

验收：伪造/过期/错误 audience JWT 均拒绝；生产配置缺失时 fail closed；安全测试证明无法从用户 API 调扩展安装/升级/卸载。

失败红线：仅信任请求头里的 email；生产环境绕过认证；Admin 监听非回环地址。

禁区：增加应用内密码/MFA 并声称消除被盗 Access 会话风险。

### F04 — 模型适配器与披露许可（TODO）

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
