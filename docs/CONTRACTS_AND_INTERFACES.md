# 核心契约与接口定义

版本：F01 completed baseline / contract v1.1
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
