# 核心契约与接口定义

版本：F01 + F02 + F03 completed baseline / contract v1.3（F02 增补见第 11 节，F03 增补见第 12 节）
适用范围：核心、生产适配器、扩展 SDK，以及从 F01 开始的后续实现。

本文将已经存在的代码边界整理为接力契约。关键词“必须”“不得”“仅”具有规范含义。若本文与现有类型签名或测试不一致，实施者必须先记录冲突并做最小兼容修正，不得静默改变风险、审批或状态语义。

## 1. 契约优先级与变更规则

1. 已执行测试和公开类型签名是当前行为事实；本文定义后续实现必须保持的语义。
2. HTTP `/api/v1`、扩展协议、持久化枚举值和迁移历史属于版本化契约。
3. 允许在当前任务范围内增加生产适配器、事务协调器和测试；不得借机加入具体业务功能。
4. 修改公共契约必须同时更新类型、契约测试、本文、`docs/NEXT_STEPS.md`，并写兼容/迁移说明。
5. `migrations/0001_core.sql` 视为不可变历史。缺失字段或表只能通过新的编号迁移补充。

## 2. 不可破坏的依赖和所有权边界

依赖方向只能是：

```text
api / workers / infrastructure  ->  core  ->  domain
extensions/*                    ->  personal_assistant_sdk
```

- `domain` 和 `core` 不得导入 FastAPI、SQLAlchemy、asyncpg、Win32、SMTP、Playwright 或具体扩展。
- `extensions/*` 不得导入 `personal_assistant.core` 或 `personal_assistant.infrastructure`。
- 所有外部副作用必须经过 `ToolGateway`；R2 还必须经过精确审批和 Side-effect Outbox。
- 核心表只由核心迁移管理。业务扩展只能迁移自身 `ext_*` Schema。
- 扩展 venv/Worker 只隔离依赖与崩溃，不是恶意代码沙箱；本项目按用户选择信任已安装扩展。
- 凭据不进入 PostgreSQL、RPC、模型输入、日志或 fixture；扩展只能拿短期 `CapabilityHandle`。

## 3. 通用数据契约

### 3.1 序列化

- 所有持久化和跨进程时间必须是带时区的 UTC 时间；读回后不得成为 naive `datetime`。
- 枚举持久化其 `.value`，不得持久化 Python member name。
- ID 是不透明字符串；调用方不得从 ID 推断类型之外的信息。
- RPC 只允许严格 JSON 值：null、boolean、number、string、array、object。
- 内容、payload、附件和迁移校验使用 SHA-256；十六进制摘要统一为小写 64 字符。
- 存储层返回独立对象，不得把数据库可变映射泄漏给领域层。

### 3.2 稳定状态值

- Task：`CREATED / QUEUED / RUNNING / WAITING_USER / WAITING_APPROVAL / WAITING_RECONCILIATION / PAUSED_SAFETY / PAUSED_EXTENSION / SUCCEEDED / FAILED / CANCELLED`。
- Approval：`DRAFT / PREPARED / WAITING_APPROVAL / APPROVED / EXECUTING / SUCCEEDED / FAILED / UNKNOWN / EXPIRED / CANCELLED`。
- Job：`READY / LEASED / SUCCEEDED / FAILED / DEAD_LETTER / WAITING_RECONCILIATION`。
- Side effect：`PREPARED / EXECUTING / SUCCEEDED / FAILED / UNKNOWN`。
- Extension：`DISCOVERED / STAGED / REJECTED / INSTALLED_DISABLED / STARTING / ENABLED / QUARANTINED / DRAINING / DISABLED / UNINSTALLING / UNINSTALLED / UPGRADING / ROLLED_BACK`。
- Risk：`READ`(R0)、`INTERNAL_WRITE`(R1)、`EXTERNAL_WRITE`(R2)、`PROHIBITED`(R3)。R3 永远不得到达 executor。

### 3.3 乐观锁与幂等

- 所有带 `version` 的聚合使用 compare-and-swap；当前版本不等于 `expected_version` 时抛 `ConcurrentModificationError`，不能覆盖写。
- 成功变更必须递增版本；当前调用者均按一次变更 `+1`。
- 同一命令作用域内，同一 `Idempotency-Key` 加相同规范化输入必须返回第一次结果；相同键加不同输入必须冲突，不能静默返回旧结果。
- 数据库唯一约束只负责挡并发；适配器仍必须比较原始请求哈希并返回正确的领域错误。

## 4. HTTP 控制面契约

现有路由、状态码与响应模型在 F01 中保持兼容：

| 接口 | 契约 |
|---|---|
| `POST /api/v1/tasks` | 必须带 `Idempotency-Key`；相同 objective 重放返回同一任务；成功为 202。 |
| `GET /api/v1/tasks/{id}` | 返回 Task 与按创建顺序排列的 messages；不存在为 404。 |
| `POST /api/v1/tasks/{id}/messages` | 必须带幂等键和期望 task version；插入消息与 task version 递增必须原子。 |
| `POST /api/v1/tasks/{id}/cancel` | 必须带幂等键和期望版本；终态不可取消；并发冲突为 409。 |
| `GET /api/v1/approvals/{id}` | `Cache-Control: no-store`；nonce 只在 `WAITING_APPROVAL` 返回。 |
| `POST .../approve`、`POST .../reject` | 一次性状态转换；过期、错误 nonce、重复消费不得成功。 |
| `GET /api/v1/events` | SSE event ID 单调递增；`after` 与 `Last-Event-ID` 都表示严格读取更大 sequence。 |
| `GET /api/v1/extensions` | 只读发现/状态，不得提供安装管理入口。 |
| Local Admin `8001` | 只允许 loopback；F02 前变更操作继续明确返回 501。 |
| Health-only `8010` | 只能有 `/healthz`；不得出现 docs、任务、审批或管理路由。 |

命令接口必须保留统一错误信封：`error.code/message/request_id/retryable/details`。持久化实现不得把 SQL、连接串、traceback 或原始 payload 暴露给客户端。

## 5. F01 持久化端口契约

下列 Protocol 的签名以对应源码为准。生产实现放在 `src/personal_assistant/infrastructure/database/`，不得把 SQL 放进 Service 或 API route。

### 5.1 TaskRepositoryPort

源码：`core/tasks/service.py`

```python
async def create(task: Task, *, idempotency_key: str) -> Task
async def get(task_id: str) -> Task
async def save(task: Task, *, expected_version: int) -> Task
async def add_message(message: TaskMessage, *, expected_version: int,
                      idempotency_key: str) -> tuple[Task, TaskMessage]
async def cancel(task_id: str, *, expected_version: int,
                 idempotency_key: str) -> Task
async def messages(task_id: str) -> tuple[TaskMessage, ...]
```

必须保持：

- `create`、`add_message`、`cancel` 分别拥有独立命令作用域的幂等记录，并绑定规范化输入。
- `add_message` 的插入、task CAS 和幂等结果写入属于同一事务。
- `cancel` 的状态检查、task CAS 和幂等结果写入属于同一事务。
- `messages` 稳定按 `created_at, id` 排序。
- 当前产品是单用户；F01 可由适配器注入固定 `owner_id`，不得擅自扩大为多租户 API。

任务创建当前跨 repository、queue、event、audit 多次调用，存在崩溃窗口。F01 必须用一个明确的 Unit of Work/事务命令，或等价的 durable outbox + 可证明修复机制，使“任务创建、初始 `agent.start` Job、QUEUED 状态和必要事件”不会一半提交。只把各仓储换成 PostgreSQL但保留不可恢复窗口，不算完成。

### 5.2 EventStreamPort

```python
async def publish(event_type: str, task_id: str,
                  data: dict[str, object]) -> TaskEvent
async def after(sequence: int) -> tuple[TaskEvent, ...]
```

- `sequence` 必须由数据库生成并在所有进程间全局单调。
- `after(n)` 只返回 `sequence > n`，按 sequence 升序。
- PostgreSQL 适配器应提供与内存实现同形的 `wait_after(sequence, timeout_seconds)`，可用 LISTEN/NOTIFY 或有界轮询；事件事实必须先落表，NOTIFY 不能作为真相源。
- 重连和重复读取允许重复投递，客户端以 event ID 去重；不得漏掉已提交事件。

### 5.3 Run、Checkpoint 与 Observation

源码：`core/agent/checkpoint.py`

```python
RunRepositoryPort.get(run_id) -> TaskRun
RunRepositoryPort.save(run, *, expected_version) -> None
CheckpointStorePort.load(run_id) -> dict[str, Any]
CheckpointStorePort.save(run, snapshot) -> None
ObservationStorePort.append(Observation) -> None
```

- 必须持久化 `TaskRun` 的全部字段，包括 segment、熔断计数、两个 fingerprint、waiting reference、pause reason 和版本。
- Run save 是 CAS；旧 Worker 在 lease/CAS 失效后不能覆盖新 Worker。
- Checkpoint 是 append-only 序列；`load` 返回最新已提交快照，无记录返回 `{}`。快照必须记录对应 `run_version`。
- Observation append-only；相同运行恢复后不能丢失已提交 observation。
- `0001_core.sql` 的 `agent_runs` 还不含全部领域字段，也没有 observations 表；F01 必须通过后续迁移补齐。

### 5.4 ApprovalRepository

源码：`core/approvals/service.py`

```python
async def create(record: ApprovalRecord) -> None
async def get(approval_id: str) -> ApprovalRecord
async def save(record: ApprovalRecord, *, expected_version: int) -> ApprovalRecord
```

- `create` 遇到重复 ID 抛 `AlreadyExistsError`；`get` 不存在抛 `NotFoundError`。
- `save` 必须是 CAS，并返回 `version == expected_version + 1` 的完整记录。
- 必须保存 `ApprovalRecord` 的所有字段；nonce 不写日志，action 用稳定 canonical JSON 恢复。
- TTL 最大五分钟；过期转换、approve、reject、consume 之间的并发只能有一个成功者。
- target、payload、附件哈希、tool/extension version 或 form version 任一变化必须烧毁旧审批。
- R2 执行前，审批从 `APPROVED -> EXECUTING` 与 Side-effect Intent 插入必须在同一数据库事务中。禁止先提交审批、再单独插 outbox。

### 5.5 JobQueuePort

源码：`core/jobs/queue.py`

```python
enqueue(...); claim(...); heartbeat(...); release(...)
complete(...); fail(...); mark_unknown(...); get(...)
```

- 幂等唯一键为 `(kind, idempotency_key)`；重放 payload 不同必须失败。
- `claim` 必须在短事务中用等价于 `FOR UPDATE SKIP LOCKED` 的方式选择一个到期 READY Job；排序为 `available_at, created_at, id`。
- claim 需要原子设置 LEASED、owner、lease_until，递增 attempts/version；两个 Worker 不得同时拥有有效 lease。
- 过期 lease 可回收，但调用 `complete/fail/release/mark_unknown/heartbeat` 时必须同时校验 state、owner 和未过期时间，否则抛 `LeaseConflict`。
- heartbeat 只续自己的有效 lease。网络调用不得持有数据库事务或行锁。
- retryable 且 attempts 未达上限时回 READY；达到上限为 DEAD_LETTER；明确不可重试为 FAILED。
- `mark_unknown` 进入 `WAITING_RECONCILIATION`，永不自动回 READY。

### 5.6 SideEffectOutboxPort

源码：`core/jobs/outbox.py`

```python
async def create_with_approval_consumption(intent: SideEffectIntent) -> None
async def mark_succeeded(intent_id: str, receipt: dict[str, Any]) -> None
async def mark_failed(intent_id: str, error_code: str) -> None
async def mark_unknown(intent_id: str, diagnostic_code: str) -> None
```

- `(tool_id, idempotency_key)` 必须唯一，并绑定 `canonical_payload_sha256`、task 和 approval。
- `create_with_approval_consumption` 是真实原子事务，不是方法名承诺：锁定并验证 APPROVED 且未过期的审批、强制核对完整 `action_fingerprint` 绑定摘要、写入 intent、消费审批，然后一次提交。
- `action_fingerprint` 是必填字段；缺失即拒绝。绑定漂移（target/payload/附件/扩展版本任一变化）时必须在同一事务内把审批原子置为 `CANCELLED` 并抛出 `ApprovalBindingMismatchError`，不得插入 intent。过期审批必须原子置为 `EXPIRED` 并抛出 `ApprovalExpiredError`。
- `finalize(intent_id, approval_id, *, state, ...)` 是 R2 执行后的唯一完成边界：审批的终态与 intent 的终态必须在同一事务提交，禁止先提交一方再提交另一方。
- ToolGateway 的生产 R2 路径必须实际调用消费与完成事务边界。允许 F01 为此做最小依赖注入调整，但不得改变风险判定、审批 canonicalization 或 UNKNOWN 语义。
- 外部请求开始后发生超时、取消或非确定异常，intent 与 approval 都进入 UNKNOWN；不得自动重试。
- receipt 只能保存必要的可对账字段，不能保存凭据或完整敏感正文。

### 5.7 AuditWriterPort

```python
async def append(event: AuditEvent) -> None
```

- Audit 表只追加，不提供 update/delete 业务接口。
- 入库前必须调用既有 `core.audit.redact`；原始 payload、token、cookie、密码和 traceback 不得落库。
- 对未来 R2 路径，审计/intent 关键事实写入失败时必须 fail closed。
- 查询辅助方法可放在生产适配器，不得扩大公共写接口。

### 5.8 LifecycleStore

源码：`core/extensions/lifecycle.py`

```python
async def get(extension_id: str) -> ExtensionRecord | None
async def save(record: ExtensionRecord) -> None
```

- F01 只持久化 lifecycle state、版本 Manifest、artifact hash、install path、data retained 和 tombstone；不实现 F02 的安装与进程管理。
- 同一 extension 的并发 lifecycle 操作必须串行化，可使用事务行锁或 advisory lock。
- Registry 仍只发布健康的 ENABLED 版本；持久化失败后的撤销/QUARANTINED 语义不得削弱。
- 卸载默认保留扩展数据；purge 必须保留独立精确确认流程。

### 5.9 适配器集合与生命周期

`build_postgres_adapters(PostgresAdapterConfig)` 在 F01 后必须返回真实适配器集合，不再抛 `POSTGRES_ADAPTER_NOT_IMPLEMENTED`。集合至少暴露：

```text
task_repository, event_stream, job_queue, approval_repository,
run_repository, checkpoint_store, observation_store,
audit_writer, side_effect_outbox, lifecycle_store
```

- 所有适配器共享受控连接池和明确的事务工厂，但不得共享未受保护的长事务 Connection。
- 导入模块不得连接数据库或运行迁移。
- FastAPI/Worker 启动生命周期负责：连接检查 -> advisory migration lock -> 校验并应用迁移 -> readiness。
- 关闭生命周期必须释放 LISTEN 连接与连接池。
- public/admin 进程可各自创建池；迁移锁和 checksum 必须使并发启动安全。
- `PA_STORAGE_BACKEND=memory` 只允许 development/test；production 继续拒绝任何内存回退。

### 5.10 F01 实施后的兼容补充（contract v1.1）

下列变更在 F01 落地，均为向后兼容或纯新增：

- `build_postgres_adapters(PostgresAdapterConfig)` 返回 `PostgresAdapters` 数据类，暴露 5.9 要求的全部适配器，并额外提供 `database`、`async startup()`、`async close()`。`PostgresAdapterNotImplemented` 仅为导入兼容保留，不再抛出。
- `Container` 新增 `storage`、`run_repository`、`checkpoint_store`、`observation_store`、`audit_writer`、`side_effect_outbox`、`lifecycle_store`。public/admin 的 FastAPI lifespan 调用 `container.storage.startup()`/`close()`；启动失败即服务启动失败，不回退内存。
- `TaskService` 新增可选 `unit_of_work: UnitOfWorkPort | None`；生产适配器传入数据库事务协调器，使“创建任务 + 初始 `agent.start` Job + QUEUED + event + audit”单事务提交。内存模式不传该参数，行为不变。
- `core.unit_of_work.UnitOfWorkPort` 新增为事务协调接口。
- `SideEffectIntent` 新增必填字段 `action_fingerprint: str`；`create_with_approval_consumption` 必须在同一事务内强制比对审批已存指纹（缺失即拒绝），过期审批原子置 `EXPIRED`，漂移审批原子置 `CANCELLED` 且不插入 intent。
- `SideEffectOutboxPort` 新增 `finalize(...)`：审批终态与 intent 终态在同一事务提交。
- `ToolGateway` 新增可选 `outbox: SideEffectOutboxPort | None`。提供时，R2 的消费与完成分别调用 `create_with_approval_consumption` 与 `finalize`；否则沿用 `ApprovalService.consume_for_execution` 与 `mark_*`。风险判定、canonicalization 和 UNKNOWN 语义未变。
- `AgentEngine` 新增可选 `unit_of_work: UnitOfWorkPort | None`：Run CAS、Checkpoint（及同一轮的 Observation）在同一事务提交。
- 迁移 `0002` 会回填既有 0001 数据：`agent_runs.objective` 取自 `tasks`、`active_started_at` 取自 `started_at` 并加 `NOT NULL`；extensions 从 `extension_versions` 恢复 manifest、manifest 格式版本、`install_path` 与 artifact hash；`run_checkpoint_sequence` 用 `setval` 推进到既有 `max(sequence)`，升级后仍可为旧 Run 追加 checkpoint。
- `InMemorySideEffectOutbox` 构造时必须绑定 `ApprovalService`，并保持一次性审批、过期与完整指纹校验语义，开发/测试环境不得掩盖审批缺陷；它仍不是崩溃安全的生产实现。
- `PostgresDatabase.transaction()` 是进程内 Unit of Work：事务期间所有嵌套适配器调用通过 contextvar 复用同一连接；未在事务中时各自独立提交。
- 迁移 `0002_f01_persistence.sql` 为新的编号迁移，`0001_core.sql` 未改动；运行器将其 checksum 登记在 `schema_migrations`。

## 6. F01 数据库迁移契约

F01 至少新增迁移登记机制及新的编号迁移，不能编辑 `0001_core.sql`。新迁移需要补齐：

- migration version/checksum/applied time；
- task command idempotency、task messages、durable task events；
- TaskRun 全字段、checkpoint sequence、observations；
- ApprovalRecord 缺失的 version、批准/完成/结果/失败字段；
- side-effect intents/outbox；
- ExtensionRecord 缺失的 artifact hash、install path、data retained、tombstone 和完整 Manifest/version 关联；
- 支持上述 claim、CAS、pending lookup 的必要唯一约束和索引。

迁移运行器必须：

1. 按数字版本顺序执行，并记录 SHA-256 checksum。
2. 已记录版本 checksum 变化时拒绝启动。
3. 使用 PostgreSQL advisory lock 防止多进程同时迁移。
4. 每个迁移失败时不记录为已应用；数据库保持可诊断状态。
5. 同时通过“空数据库”与“只应用 0001 的数据库”两种升级测试。

禁止自动 drop、truncate、清理未知数据，禁止连接真实生产/个人数据库执行测试。

## 7. 扩展 SDK 与 RPC 稳定契约

公开 SDK 位于 `extension_sdk/src/personal_assistant_sdk`。业务能力只能实现下列槽：

- `ToolProvider`
- `ContextProvider`
- `EventSource`
- `WorkflowProvider`
- `ScheduleProvider`
- `NotificationProvider`
- `MigrationProvider`
- `FormSchemaProvider`

Worker 必须先完成 `system.handshake`，其 id/version/protocol/slots/schema hash 与 Manifest 完全一致后才能调用其他能力。稳定 RPC method 为：

```text
system.handshake, system.health, system.drain, system.shutdown
tool.list, tool.invoke, context.retrieve
event_source.list, event_source.poll
workflow.list, schedule.list, notification.deliver
migration.list, form.list
```

- framing 是 UTF-8 newline-delimited JSON-RPC 2.0；默认输入上限 1 MiB、宿主进程客户端上限 4 MiB。
- RPC timeout 后相关流视为不可继续关联，Worker 必须停止，调用不得静默重试。
- `Outcome.UNKNOWN` 必须向上传播为不确定结果；宿主不能改写成失败后重试。
- Handle 不得泄露绝对宿主路径或原始凭据。
- 新业务扩展不得要求修改核心路由、核心状态机或核心表。

F01 不得实现或改写 Supervisor/RPC；它只负责 lifecycle state 的持久化。

## 8. F01 测试契约

必须使用真实 PostgreSQL 验证 SQL 语义；mock、SQLite 和内存适配器不能替代以下验收：

1. 空库迁移、0001 快照升级、重复运行 no-op、checksum 漂移拒绝。
2. 服务/连接池重建后 Task、messages、events、Run、checkpoint、approval、Job、audit、extension state 均可恢复。
3. 两个独立连接并发 claim，同一时刻一个 Job 最多一个有效 owner；lease 过期后可回收。
4. stale version 的 Task/Run/Approval CAS 必须失败且不覆盖新值。
5. task/message/job 幂等重放返回首次结果；相同键不同 payload 冲突。
6. approval consume 与 side-effect intent 注入任一中途失败时共同回滚；成功时共同提交。
7. UNKNOWN Job/intent 重启后仍保持 reconciliation 状态，不能再次 claim。
8. Audit 入库前脱敏；测试数据库中不得出现 fixture secret 原文。
9. production 配置使用 PostgreSQL 成功启动；数据库不可达或迁移失败时 fail closed，绝不回退内存。
10. 原有 `scripts/test.ps1` 全绿，Ruff/Mypy 无新增错误。

集成测试只能使用明确的测试数据库 URL。测试装置必须检查目标数据库/Schema 是临时测试目标，再创建或清理；不得对默认数据库或不明 URL 执行 destructive cleanup。

## 9. F01 完成红线

出现任一项都不得标记 DONE：

- 生产路径仍实例化任何 InMemory 仓储或队列。
- 修改 `0001_core.sql` 代替新增迁移。
- 用 SQLite/mock 证明 PostgreSQL 并发语义。
- Job 能被两个有效 lease 同时持有，或 UNKNOWN 被自动重试。
- 审批消费和 side-effect intent 分成两个可独立提交的事务。
- 幂等键复用但 payload 漂移仍返回成功。
- 重启丢失状态，或启动失败后偷偷回退内存。
- SQL/日志/API 暴露凭据、完整敏感 payload 或 traceback。
- 为 F01 顺带实现 F02、具体邮件/ehall 功能，或改动扩展 RPC 协议。

## 10. 接力时的变更报告格式

```text
任务：F01
状态：DONE 或 BLOCKED
修改文件：...
新增迁移：版本、checksum、升级路径
公共契约变化：无 / 逐项说明
执行命令与结果：...
真实 PostgreSQL 并发/重启证据：...
仍未实现：...
风险：...
下一建议：F02（只建议，不开始）
```

## 11. F02 实施后的兼容补充（contract v1.2）

F02 把既有 Manifest/确认屏障/生命周期/Registry/JSON-RPC 契约接到真实暂存目录、每版本 venv、Worker 子进程与 Local Admin API。未重写任何既有协议；以下为新增或收紧，均已由 `tests/unit/test_extension_*`、`tests/api/test_admin_extensions.py`、`tests/integration/test_extension_supervisor_real.py` 和 `tests/integration/test_postgres_f02.py` 覆盖。

### 11.1 新增核心类型与端口

- `core/extensions/operations.py`：`OperationState`（`PENDING/RUNNING/SUCCEEDED/FAILED`）、`ExtensionOperation`、`ExtensionOperationStore`（`create/update/get/interrupt_running`）、`DIAGNOSTIC_CODES` 安全诊断码允许列表；持久层只存允许列表中的码，不存第三方消息或堆栈。
- `core/extensions/errors.py`：`ExtensionOperationError(code, message)`；`code` 必须属于 `DIAGNOSTIC_CODES`。
- `core/extensions/supervision.py`：`ExtensionSupervisorService`（管理 API、CLI 与恢复共用的唯一状态机）与 `ExtensionRuntime` 运行时协议（`RuntimeSupervisor` + `invoke_tool`/`stop_all`）。
- `core/extensions/models.py`：`data_namespace(extension_id)` 公开（原 `rpc._data_namespace` 私有实现）。
- `LifecycleStore` 协议新增 `all()`（PostgreSQL 与内存适配器均已实现，纯新增）。
- `ArtifactInstaller` 协议新增 `remove_version(record)`（只删除 `record.install_path` 一个版本目录）；`uninstall_code(record)` 语义明确为删除该扩展全部版本目录。
- `InstalledArtifact` 新增 `runtime_root: Path | None = None`；`InstallationPreview` 新增 `mode: str = "install"` 与 `replaces_version: str | None = None`，二者都进入 `preview_hash`。
- `InstallCoordinator` 新增 `prepare_auto`/`prepare_upgrade`/`install_candidate`/`discard_candidate`/`discard_staged_record`/`preview_for`/`validate`；`prepare` 语义不变。`LifecycleManager` 新增 `recover(extension_id)`，`enable` 额外接受 `QUARANTINED`（启动与健康检查仍需通过）。

### 11.2 行为收紧（fail closed）

- 安装执行前重新计算 staged artifact 的 SHA-256；与预览不一致即 `ConfirmationRequiredError` 并丢弃计划。
- 验证失败时移除刚安装的版本目录并清理暂存；`install` 成功后删除暂存副本。持久化记录的 `manifest.root` 指向安装后的 payload 根目录，而不是来源或暂存路径。
- 升级候选通过 `install_candidate` 安装与契约测试但不写入生命周期记录；只有 `LifecycleManager.upgrade` 成功后才原子切换。失败时旧版本保持 enabled 且候选目录被尽力删除。
- 回滚仅允许 `state_schema_version >= 当前版本` 且安装目录仍存在的保留版本；否则 `ROLLBACK_INCOMPATIBLE`/`ROLLBACK_NO_CANDIDATE`。
- `JsonRpcProcessClient` 对畸形帧、超限响应行和 id 不匹配终止进程并抛 `RpcCallError(-32700/-32092/-32093)`（原实现会泄漏 `ValueError`）；错误流的错误码集合视为不可继续关联，调用方必须重启 Worker 或不重试。
- 恢复时：`STAGED/DISCOVERED → REJECTED`（并清理暂存树）、`STARTING → QUARANTINED`、`DRAINING/UPGRADING → DISABLED`、`UNINSTALLING` 按目录存在性收敛为 `DISABLED`/`UNINSTALLED`；持久化 `ENABLED` 记录会重启一次 Worker，失败进入 `QUARANTINED`；非终态 operation 标记 `FAILED/SUPERVISOR_RESTART`。

### 11.3 管理接口（仅 Local Admin 8001）

| 方法与路径 | 契约 |
|---|---|
| `POST /admin/v1/extensions/inspect` | `{source}`；只做暂存与静态检查；返回 `plan_id`、`confirmation_nonce`、`preview_hash`、`expires_at`、`mode`、`replaces_version`、slots/tool 风险/能力与 `executed_code: false`。 |
| `POST /admin/v1/extensions/install` | 必须提交精确预览绑定（`plan_id`+`confirmation_nonce`+`preview_hash`+`accepted_warning`）；202 返回 operation。 |
| `POST /admin/v1/extensions/{id}/upgrade` | 同上绑定，且预览必须属于该扩展的 upgrade 计划；202 返回 operation。 |
| `POST /admin/v1/extensions/{id}/{enable\|disable\|rollback\|uninstall}` | 无 body 或空 body；202 返回 operation。 |
| `POST /admin/v1/extensions/{id}/purge-data` | 明确 `501 EXTENSION_DATA_PURGE_NOT_IMPLEMENTED`。 |
| `GET /admin/v1/extension-operations/{id}` | 持久 operation 状态与安全诊断码；不存在 404。 |

公共 API (`/api/v1`) 不新增任何安装、升级、卸载、启停或 operation 路由；`assistantctl` 只调用上述接口并轮询 operation id，不导入基础设施或数据库。

### 11.4 持久化与部署边界

- 迁移 `0003_f02_operations.sql` 为 `extension_operations` 增加 `idempotency_key`、`command_fingerprint` 两列，并对 `(extension_id, operation, idempotency_key)` 建部分唯一索引（`WHERE idempotency_key IS NOT NULL`），作为跨进程的幂等重放/冲突护栏。SHA-256 为 `28cbda227918bfcd80366208b59713eb0dbb0b0cbdaa582cb5e55a91ce2e1e03`。既有行保持 NULL，未回填。
- 迁移 `0004_f02_operation_request_scope.sql` 增加 `request_scope` 列与 `(request_scope, idempotency_key)` 部分唯一索引，使 install/upgrade 的幂等重放不依赖进程内 plan（重启后仍可按 HTTP 路由 + canonical body 查询）。SHA-256 为 `6b6bb9f5cea83b443c9ca345f7a9d134f6ebca69969f01e23557ecff706af6fc`。既有行保持 NULL，未回填。
- 未确认安装计划仍在 Admin 进程内存中；Admin 必须单进程运行。进程重启会丢弃未确认计划（安全失败），已创建 operation 保存在 PostgreSQL 中，可用同一 `Idempotency-Key` 重放；已确认但中断的操作按 `SUPERVISOR_RESTART` 收敛。
- 未实现、不得声称完成：永久 purge、扩展 `ext_*` migration 执行、Worker 崩溃后自动退避重启、远程 URL/Git 制品、多 Admin 进程共享未确认计划与跨进程 `OPERATION_IN_PROGRESS` 互斥（DB 保证命令键唯一与单次 save 串行）。

### 11.5 复验修复补充（同 contract v1.2，2026-09-16 第二轮）

以下为独立复验反例的修复，均已由新增测试覆盖：

- **Worker 环境白名单**：`JsonRpcProcessClient.start` 不再复制 `os.environ`，只继承 Python 运行必需的少量系统键（PATH/SYSTEMROOT/TEMP 等）；数据库 URL、Token、Cookie 等一律不进入子进程。显式声明的非敏感值只能通过 `WorkerSpec.environment` 传入。
- **制品文件集合一致**：`.git/.venv/__pycache__/.pytest_cache` 在目录暂存、zip/whl 解包、`compute_artifact_hash` 与 `VenvArtifactInstaller.copy_payload` 中统一忽略；被忽略的路径既不能改变确认哈希，也不可能进入安装目录。
- **Lockfile 精确锁定**：每条有效行必须是 `name==exact.version`（可选 extras）并至少带一个 `--hash=sha256:<64 hex>`；通配符、范围、`!=`、环境标记、URL/VCS、可编辑安装与其他选项全部拒绝；安装命令使用 `pip install --require-hashes`。
- **升级基线绑定**：执行升级前重新读取活动记录，要求版本仍等于预览的 `replaces_version`、候选版本仍更新、数据 Schema 不倒退，否则 `PLAN_BASELINE_CHANGED`（409），不会降级或覆盖。
- **全链路补偿**：`LifecycleManager.upgrade` 自禁用旧版本之后的所有步骤（activate、候选持久化、重新启用）都在同一补偿边界内；失败时旧记录恢复并重新启用（补偿异常不掩盖原始错误）。安装的最终 `INSTALLED_DISABLED` 持久化失败会删除刚安装的版本目录并保留可恢复的 STAGED 记录。
- **拒绝语义**：`reject` 对 upgrade 计划只丢弃候选，绝不写活动记录；install 拒绝写入 `REJECTED`，之后允许重新 prepare；新预览会作废同扩展的旧未确认计划；CLI 拒绝时调用 `POST /admin/v1/extension-plans/{plan_id}/reject`。
- **有界排空**：`JsonRpcProcessClient.drain` 对整个等待（包括在途调用的锁）施加 deadline；超时终止 Worker 并抛 `RpcTimeoutError`。`LifecycleManager.disable` 校验 `DrainReport`（`drained=false` 或 `active_calls>0` 即 `DRAIN_TIMEOUT`），但状态仍安全落为 `DISABLED` 且 Worker 已停止，operation 以显式失败码暴露。
- **契约验证加严**：`ProcessContractVerifier` 比较完整工具描述符（id/risk/input schema/output schema，schema 取 Manifest 文件内容）、各槽能力 ID 集合、`context.retrieve` 探针与 `migration.list` 数量；`NotificationProvider` 无枚举 RPC 且 `deliver` 有副作用，仅在 handshake 校验声明。
- **操作串行化与幂等**：`ExtensionSupervisorService` 对同一扩展同一时间只允许一个生命周期操作（`OPERATION_IN_PROGRESS`，409）；同 `Idempotency-Key`+同命令指纹返回既有 operation。跨重启重放依赖 0004 的 `request_scope`（路由 + canonical body，先于内存 plan 查询），不同指纹返回 `IDEMPOTENCY_CONFLICT`。`ProcessRuntimeSupervisor.start` 另有每扩展锁，保证并发启用不会泄漏第二个 Worker。
- **恢复隔离**：恢复时健康检查失败或最终保存失败会先以 shield 停止已启动的 Worker，再落 `QUARANTINED`。
- **RPC 写入/取消映射**：`stdin.write/drain` 的 `OSError`（管道关闭）与读 EOF 会终止进程并抛类型化 `RpcCallError`，调用方不会看到原始 `BrokenPipeError`。取消（`CancelledError`）先终止并回收进程、标记流损坏，然后**重新抛出 `CancelledError`**，而不是伪装成普通 RPC 错误（详见 11.6）。
- **诊断与命名空间**：`ExtensionOperation`/两个 operation store 在写入前强制 `DIAGNOSTIC_CODES` 允许列表；`data_namespace()` 改为单射编码（`[a-z0-9]` 保留，其余 `_<hex>_`），`a.b`/`a_b`/`a-b` 不再碰撞，超长 ID 追加摘要且不超过 63 字节。
- **状态来源**：公共 `GET /api/v1/extensions` 与 Admin 列表都从持久 lifecycle store 读取状态，生产下不再依赖公共进程自己的空 Registry；`_execute` 在 `RUNNING` 转换失败时原子落 `FAILED/OPERATION_FAILED`，不会留下无任务的 `PENDING`。

### 11.6 复验修复补充（同 contract v1.2，2026-09-16 第三轮）

以下为第二轮独立审计 A01–A08 的修复，均已由反例测试覆盖：

- **取消语义**：任何清理在 `CancelledError` 下都必须“先完成资源回收，再重新抛出 `CancelledError`”，不得伪装成普通 RPC 错误。`shield_cleanup(awaitable)`（`core/extensions/async_utils.py`）保证清理在二次取消下仍运行到完成；`terminate_process` 依次 terminate → 有界等待 → kill → wait，确保子进程被回收。
- **RPC deadline 与取消（A04/A05）**：`drain` 把 wall-clock deadline 一次性转换为 event-loop monotonic deadline，用 `asyncio.timeout_at` 覆盖“等锁 + 写入 + 读取”；拿到锁后重新计算剩余预算，≤0 立即终止并抛 `RpcTimeoutError`。写入期与读取期的 `CancelledError` 都会先 `shield_cleanup(_break())`（标记损坏、终止并回收进程）再重抛；`DrainReport` 必须是 `drained is True` 且 `active_calls` 为 0 的整数，否则 `DRAIN_TIMEOUT` 失败关闭。
- **安装取消清理（A02/A03）**：`VenvArtifactInstaller._run_capture` 取消时回收子进程并重抛；`install` 的失败/取消路径 shield 删除半成品版本目录。`InstallCoordinator._execute_install` 对普通异常与取消使用同一 `shield_cleanup(_abort_install)`：删除版本目录、清理 staging、终结 plan，fresh install 收敛为 `REJECTED`；契约验证 Worker 在 `verify` 的 finally 中 shield 关闭。
- **升级补偿边界（A01）**：`LifecycleManager.upgrade` 把禁用旧版、保存 `UPGRADING`、activate、候选持久化、候选启动/健康、Registry 发布与最终保存全部纳入同一 `except BaseException` 补偿；补偿内撤销候选 Registry、停止候选 Worker、恢复旧记录并（原本 ENABLED 时）重新启用旧版本，然后重抛原始错误（含 `CancelledError` 与 `DRAIN_TIMEOUT`）。补偿失败不会被描述为升级成功。
- **跨重启幂等（A06）**：install/upgrade 在访问内存 plan 之前先按 `request_scope` + `Idempotency-Key` 查询持久 operation；命中同指纹直接返回原 operation，不同指纹返回 `IDEMPOTENCY_CONFLICT`。`request_scope` 由路由与 canonical body 决定（`admin:extensions:install`、`admin:extensions:{id}:{operation}`）。0004 的部分唯一索引保证跨连接/跨进程只产生一个 operation；重放路径不调用 `preview_for`、installer 或 verifier。RUNNING operation 重启后收敛 FAILED，仍可被同一命令重放。
- **诊断边界（A07）**：`create`/`update`/`interrupt_running` 在内存与 PostgreSQL 两个 store 中都显式调用 `validate_diagnostic_code`，批量 UPDATE 不会绕过白名单。
- **文档（A08）**：本文件、`docs/NEXT_STEPS.md`、`TODO.md`、`docs/IMPLEMENTATION_MAP.md` 同步说明 0003/0004、取消语义、未确认 plan 与持久 operation 的区别；F02 在独立验收前始终保持 `IN_PROGRESS`，现已通过六轮审计并标记 `DONE`；`tests/contract/test_f02_contract_consistency.py` 锁定这些表述。
- **失败回滚不下线当前版本**：`LifecycleManager.rollback` 先解析并校验保留候选（含 Schema 兼容），再禁用当前版本；禁用、保存 `ROLLED_BACK`、启动、健康检查、Registry 发布与启用持久化全部在同一补偿边界内。任一步失败（含候选启动/健康/ENABLED 保存失败与取消）由 `_compensate_failed_rollback` 撤销候选 Registry、停止候选 Worker、恢复原记录并重新启用。`ROLLBACK_INCOMPATIBLE`/`ROLLBACK_NO_CANDIDATE` 时当前版本保持 `ENABLED`、Registry 与调用不受影响。Supervisor 不得在 `rollback()` 之外再次 `enable()`。
- **阻塞复制的取消安全**：`run_blocking(func, ...)` 让 `to_thread` 的取消等待线程真正结束后再重抛 `CancelledError`；首次取消优先，线程在取消后无论成功还是抛异常都被消费，异常不得覆盖取消信号；重复取消不会中断等待。安装/卸载的复制与删除线程不会在清理之后继续重建文件。
- **普通 `call()` 的 deadline**：与 drain 相同，单一 monotonic deadline 覆盖等待 RPC 锁、写入与读取；排队调用的总耗时不得超过调用方预算。

## 12. F03 实施后的契约补充（contract v1.3）

F03 用真实、可测试、fail-closed 的 Cloudflare Access JWT 验证替换了公共 API 的 503 占位边界。未改动 Admin API 暴露策略、CSRF 门、风险等级、审批 canonicalization、F01/F02 迁移或扩展 Supervisor 行为。传输无关的身份契约位于 `core/auth/`（`ports.py`），Cloudflare 取钥/JWT 验证实现位于 `infrastructure/auth/`（`cloudflare_access.py`），`api/middleware/cloudflare_access.py` 只依赖 `core.auth`，由 `app.py` composition root 接线；`core/` 与 `domain/` 不导入 `jwt`、`httpx` 或 `cryptography`，依赖方向保持 `api/infrastructure/workers -> core -> domain`，`api` 不导入 `infrastructure`（契约测试锁定）。

### 12.1 令牌来源与载体

- 只接受 Cloudflare 官方载体：`Cf-Access-Jwt-Assertion` 请求头（官方推荐）与浏览器 `CF_Authorization` cookie。
- 两个载体同时存在且内容不一致时拒绝；同名头或同名 cookie 出现多次（歧义）时拒绝，cookie 重复检测跨所有原始 `Cookie` 首部字段聚合，第二个 `Cookie` 头不会被忽略；不存在的载体不产生身份。
- 令牌 UTF-8 长度上限 8192 字节；超限、空值畸形一律 401。
- 原始 JWT、cookie、claims 与签名密钥不写入日志、数据库、审计、响应或测试快照；错误响应只含稳定错误码。

### 12.2 密码学与 claim 验证

- 使用 `PyJWT` + `cryptography`；不手写 RSA/ASN.1/签名算法。算法白名单只有 `RS256`，`alg=none`、对称/非对称混淆与未声明算法全部拒绝。
- `kid` 必填（≤256 字符），必须精确命中可信 JWKS；重复或歧义 `kid`、错误 `kty`、非 `sig` 用途、非 `RS256` 或畸形 JWKS 都使本次取钥不可用（503）。
- `iss` 必须与规范化后的 `https://<team>.cloudflareaccess.com` 完全一致；`aud` 必须包含配置的 `PA_CF_ACCESS_AUD`（支持字符串或数组形态）。
- `exp` 必须存在；`nbf`/`iat` 存在时必须为有限数值。未来 `nbf`/`iat`、非正数、`exp <= nbf`、`exp <= iat` 等明显异常时间声明拒绝。时钟偏差使用固定的 30 秒上限，验证器允许测试注入时钟。
- 身份只来自已验签 claims：`sub` 必填、非空、≤256 字符，写入 `request.state.actor_id`；可选 `email` 写入 `request.state.actor_email`，但绝不信任 `Cf-Access-Authenticated-User-Email`、`X-Forwarded-*` 或任意代理身份头。
- `request.state.access_identity` 保存经过验证的 `AccessIdentity`；`api/dependencies.get_actor` 继续读取 `request.state.actor_id`。

### 12.3 JWKS 取钥、缓存与轮换

- 只访问由合法 `PA_CF_ACCESS_TEAM_DOMAIN` 推导出的 `https://<team>.cloudflareaccess.com/cdn-cgi/access/certs`。
- `PA_CF_ACCESS_TEAM_DOMAIN` 只接受 `team`、`team.cloudflareaccess.com` 或 `https://team.cloudflareaccess.com`；其它 scheme、credentials、端口、path/query/fragment、多级域名或非 `cloudflareaccess.com` 主机导致启动失败（防 SSRF）。自定义 Access 域名与团队域外的 JWKS URL 不在 F03 支持范围。
- `Settings.__post_init__` 让 from_env、直接构造与 `replace` 都先把 team domain/audience/public origin 写回 canonical 值再校验，因此任何 `Settings` 实例都满足规范化不变量；`create_app`/`build_container` 再调用一次 `validate()`。`cloudflare_access_verifier_from_settings` 仍防御性使用 `normalize_team_domain`/`normalize_audience`，绝不直接使用原始字段，因此被绕过不变量的实例也无法把 issuer/JWKS URL 指向任意域名。
- `CloudflareJwksProvider` 只使用注入的 `httpx.AsyncClient`（默认懒创建、单例复用），并**每次请求显式传 `follow_redirects=False`**，注入客户端自身的重定向设置不能改变行为；请求外层由单一 monotonic 总 deadline 约束整个 stream（默认 5 秒），httpx 分块 read timeout 不能替代它，慢速滴流响应在 deadline 内失败；另有 64 KiB 响应上限、只接受 2xx JSON。
- 公钥缓存 TTL 为 300 秒；TTL 内命中不联网。未知 `kid` 触发一次受控刷新，同一 30 秒窗口内最多一次；并发刷新通过锁与 generation 合并为一次网络请求。
- 刷新在写入节流时间后进行网络等待；若刷新被取消（`CancelledError` 或任何 `BaseException`），节流时间恢复为原值并重新抛出取消，等待者或后续请求可立即接管刷新，不会被错误映射为未知 key。
- 未知 `kid` 的结果必须区分两种情况：**本次成功刷新并确认该 `kid` 不在可信集合**（含等待其他调用者完成的刷新）→ `UnknownSigningKeyError`（401）；**因 30 秒节流未能执行检查**或刷新失败 → `AccessTokenUnavailableError`（503, `retryable=true`）。因此密钥轮换在节流窗口内只会是临时 503，绝不会被误报为永久 401。
- 网络失败、超时、非 2xx、超大响应、非 JSON、无有效 key 时抛 `AccessTokenUnavailableError`（503）。**过期缓存不放行**：刷新失败时即使缓存中存在旧 key 也拒绝。
- 取钥失败不会清除已缓存但仍未过期的 key：TTL 内的正常请求不受瞬时网络故障影响；已在缓存中的旧 key 在轮换窗口内仍可正常验证。

### 12.4 HTTP 结果与配置边界

| 场景 | 状态码 | `error.code` |
|---|---|---|
| 未提供令牌 | 401 | `CLOUDFLARE_ACCESS_TOKEN_MISSING` |
| 畸形/过期/签名错误/issuer/audience/kid/claims/载体冲突 | 401 | `CLOUDFLARE_ACCESS_TOKEN_INVALID` |
| verifier 缺失或 JWKS 暂不可用 | 503 | `CLOUDFLARE_ACCESS_UNAVAILABLE` |

- 统一错误信封保持 `error.code/message/request_id/retryable/details`；错误消息不包含令牌、claims、密钥或第三方异常文本。
- `PA_TRUST_CLOUDFLARE_ACCESS=false`（开发模式）保持现有 `development-owner` 行为不经 verifier；生产缺少 team domain、audience 或 `PA_PUBLIC_ORIGIN`、或配置非法时启动即拒绝，绝不自动降级为无认证模式。不新增应用内密码、登录页或 MFA。
- `CloudflareAccessBoundaryMiddleware` 只挂载在 public API/PWA（8000）。Admin API（8001）继续只允许回环且带有效 JWT 也无法从 public app 访问安装/升级/启停/卸载路由（public 路由表本身没有这些端点）；health-only（8010）除 `/healthz` 外无任何路由。
- CSRF（Origin + `Sec-Fetch-Site` + `X-Requested-With`）与 JWT 验证相互独立：有效 JWT 不能替代 CSRF 门。中间件顺序保持 request id 与安全响应头覆盖 401/503 错误响应。
- 关闭顺序：public app lifespan 在 `finally` 中先尝试 `verifier.aclose()`，再在嵌套 `finally` 中调用 `container.storage.close()`；verifier 关闭失败不会跳过数据存储关闭。

### 12.5 新增公共接口

- `core/auth/ports.py`：`AccessIdentity(subject, email)`、`AccessTokenVerifier`（`verify(token) -> AccessIdentity`、`aclose()`）、`AccessTokenError`、`AccessTokenRejectedError`、`AccessTokenUnavailableError`、`MAX_TOKEN_BYTES`；不依赖 HTTP/JWT/密码学库。
- `infrastructure/auth/contract.py`：`JwksProvider`（`public_key(kid) -> RSAPublicKey`）与 `UnknownSigningKeyError`（`AccessTokenRejectedError` 子类）。
- `infrastructure/auth/cloudflare_access.py`：`CloudflareJwksProvider`、`CloudflareAccessTokenVerifier`、`cloudflare_access_verifier_from_settings(settings, *, http_client=None, clock=None)`。
- `app.create_app` 新增可注入 `access_verifier: AccessTokenVerifier | None`；未注入时由 composition root 按 settings 构建。测试使用运行时生成的 RSA 密钥与 in-memory transport，不连接真实 Cloudflare。
- 新增运行时依赖 `PyJWT`、`cryptography`、`httpx`（`pyproject.toml` 与 `dependency.lock`），wheel 必须包含 `core/auth` 与 `infrastructure/auth`。

### 12.6 有意未实现

- 自定义 Access 团队域/自定义 JWKS 域名、应用内登录/密码/MFA、Access 会话撤销与登录重定向、Web Push 与 Tunnel/Tailscale 部署编排（F08/F10）、F04 模型披露许可。不得把这些描述为 F03 已交付。
