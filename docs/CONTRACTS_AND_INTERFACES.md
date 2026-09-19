# 核心契约与接口定义

版本：F01 + F02 + F03 + F04 + F05 completed baseline + F06 in progress / contract v1.6（F02 增补见第 11 节，F03 增补见第 12 节，F04 增补见第 13 节，F05 增补见第 14 节，F06 增补见第 15 节）
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

## 13. F04 实施后的契约补充（contract v1.4）

F04 用真实、协议驱动的本地/远程模型适配器与持久化披露许可替换了仅有调用栈对象的占位实现。依赖方向保持 `api/infrastructure/workers -> core -> domain`：`core/models/` 只定义端口、canonical 绑定与路由规则，不导入 `httpx`、厂商 SDK 或 `infrastructure`（契约测试锁定）。R2 外部动作审批与模型披露许可是两个独立状态机：允许模型读取内容不等于允许发送，批准发送也不等于允许交给另一个模型。

### 13.1 新增公共端口与类型

- `core/models/errors.py`：`ModelProviderError` 与子类 `ModelProviderUnavailableError`（`MODEL_PROVIDER_UNAVAILABLE`）、`ModelProviderTimeoutError`（`MODEL_PROVIDER_TIMEOUT`）、`ModelProviderRejectedError`（`MODEL_PROVIDER_REJECTED`，含 `status_code` 与固定枚举 `rejection_code`）、`ModelProviderProtocolError`（`MODEL_PROVIDER_PROTOCOL_ERROR`）、`ModelProviderResponseTooLargeError`（`MODEL_PROVIDER_RESPONSE_TOO_LARGE`）、`ModelCredentialUnavailableError`（`MODEL_CREDENTIAL_UNAVAILABLE`）。错误消息不含提示词、字段值、凭据或厂商响应正文。
- `core/models/disclosure.py`：`DisclosureConsentState`（`ACTIVE/REVOKED/EXPIRED`）、`DisclosureConsentRecord`、`DisclosurePreview`、`DisclosureFieldSummary`、`DisclosureConsentStore`、`DisclosureAuthorizer`、`DisclosureConsentService`，以及 `canonical_field_digest`、`redacted_field_preview`、`effective_consent_state`、`DISCLOSURE_POLICY_VERSION`。
- `ModelRouter` 构造新增可选 `disclosure: DisclosureAuthorizer`、`default_local_fallback_id`、`audit`；`complete` 新增可选 `consent_id`、`user_id`。既有 `DisclosureConsent`（临时对象）与 `permits` 保留兼容，但只有在显式 `allow_ephemeral_disclosure=True` 时才被接受，且与持久 `disclosure` 互斥（同时提供即构造失败）。生产组合根始终注入 `DisclosureConsentService`。
- `core/secrets/store.py` 新增 `SecretUnavailableError`；`infrastructure/secrets/unavailable.py` 新增 fail-closed 的 `UnavailableSecretStore`（F09 前的生产凭据后端占位，绝不用内存明文替代）。

### 13.2 CANONICAL 字段绑定与许可失效规则

- 许可绑定接收方 `provider_id`、用途 `purpose`、策略版本 `policy_version` 与字段集合摘要 `field_digest`；字段值、分类、来源或字段集合任一变化都会改变摘要，旧许可立即不可再用。许可还绑定不可复用的 `recipient_fingerprint`（provider_id + 适配器类型 + 规范化 endpoint + model 的 canonical 摘要）；同一个 `provider_id` 改指其他 endpoint/model 后，旧许可立即失效。绑定摘要采用确定性 **CANONICAL** JSON：每个字段取 `{name, value_sha256, classification, source}`，按 `(name, source, classification, value_sha256)` 排序后序列化并做 SHA-256，因此字段顺序不影响结果。
- 预确认绑定：`preview()` 返回 `preview_hash`（provider + purpose + policy version + digest + 受保护字段数 + TTL 秒数）；`confirm()` 必须提交完全一致的 `preview_hash` 与字段集合，否则 `DisclosurePreviewMismatchError`（`disclosure_preview_mismatch`），需要重新预览。
- 授权条件：`state = ACTIVE`、未过期、`provider_id`/`purpose`/`field_digest`/`policy_version`/`user_id` 与 `consent_id` 全部精确匹配；SECRET 字段在查询许可之前就被 `ModelRouter` 与适配器双重拒绝（`disclosure_denied`）。审计只记录授权服务实际返回并被该次远端调用采用的持久许可 ID：调用方传入的 `consent_id`、本地/回退调用、公开字段调用与临时许可都不会在审计中产生许可关联。`ModelRouter` 在注册时冻结每个 provider 的完整 `RecipientIdentity` 快照；授权前重新计算当前 fingerprint，与注册快照不一致即 `disclosure_denied` 且不发起任何远端请求，审计的 `model_id` 也只取注册快照。远端调用前会在授权返回后再次复核 fingerprint（授权过程中的 `await` 不能让中途改指向的 provider 漏过），运行时改 endpoint/model 必须重建 provider 与 router。
- 服务层自己复核完整绑定（consent_id/user_id/provider_id/purpose/field_digest/policy_version/state/expires_at），不把授权判断委托给存储适配器；存储若返回不完整匹配的记录，`authorize` 一律返回无许可（fail closed）。
- TTL 边界：1 分钟 ≤ TTL ≤ 7 天；到期即 `effective_consent_state == EXPIRED` 并拒绝授权，不做“宽限”。
- 绑定字段约束：`provider_id`、`purpose`、`consent_id`、`user_id` 均非空且 ≤128 字符；所有时间参数必须是 aware 时间，naive `datetime` 一律 `ValidationError`（`ModelRouter.complete`、`authorize`、`effective_consent_state` 同样处理），不得抛出裸 `TypeError`；标识比较统一为 UTF-8 bytes 的常量时间比较，中文 `purpose`/`user_id`/`consent_id` 不会抛 `TypeError`。
- 撤销：`revoke(consent_id, expected_version)` 是做 CAS 的终态转换（version+1，记录 `revoked_at`），此后授权永久拒绝；对已撤销许可再次撤销（不同幂等键）返回 `DisclosureStateError`（`disclosure_state_error`），错误版本返回 `ConcurrentModificationError`。撤销时若 `expires_at <= now`，许可先在同一事务/锁内原子落为 `EXPIRED`（version+1），再抛 `DisclosureStateError`；过期许可永远不会被撤销为 `REVOKED`。
- 幂等：`model_disclosure_commands` 以 `(scope, idempotency_key)` 为主键并绑定 `command_fingerprint`。同键同内容重放返回第一次的记录（跨进程、跨重启）；同键不同内容返回 `DisclosureIdempotencyConflictError`（`idempotency_conflict`）。
- 存储最小化：`model_disclosure_consents` 只保存 `id/owner_id/provider_id/purpose/field_digest/field_count/policy_version/recipient_fingerprint/state/created_at/expires_at/revoked_at/version`；`model_disclosure_commands` 只保存命令作用域、幂等键、指纹与许可 ID。原始字段值、模型请求正文、凭据和令牌永不入库、入审计、入日志或入异常。

### 13.3 HTTP 接口（public API 8000）

| 接口 | 契约 |
|---|---|
| `POST /api/v1/disclosures/preview` | 接收 `provider_id/purpose/instruction/fields[{name,value,classification,source}]/ttl_seconds`；返回脱敏字段摘要与 `preview_hash`；`Cache-Control: no-store`；SECRET 一律 403 `DISCLOSURE_DENIED`；未在当前组合根注册的远端 provider 返回 404 `DISCLOSURE_RECIPIENT_UNKNOWN（disclosure_recipient_unknown）`（不允许为未配置接收端预生成许可）。响应同时返回 `recipient`（adapter/endpoint/model）与 `recipient_fingerprint`。控制面中间件对所有 POST 要求 `Idempotency-Key`，preview 不写状态、忽略其值。 |
| `POST /api/v1/disclosures` | 必须带 `Idempotency-Key` 与 `preview_hash`；202 返回持久许可；同键同内容重放返回原记录，同键异内容 409 `IDEMPOTENCY_CONFLICT`。 |
| `GET /api/v1/disclosures/{id}` | 只返回元数据与有效状态（到期显示 `EXPIRED`）；`no-store`；不存在 404。 |
| `POST /api/v1/disclosures/{id}/revoke` | 必须带 `Idempotency-Key` 与期望 `version`；成功返回 `REVOKED`；过期状态、版本冲突、幂等冲突分别 409。 |

所有披露接口只属于用户 API；Admin API 与 health-only 侧车不新增任何披露路由。响应与错误信封（`error.code/message/request_id/retryable/details`）不包含原始字段值。

### 13.4 模型适配器

- `infrastructure/models/openai_compatible.py`：`OpenAICompatibleChatProvider`（远程，`is_remote=True`，OpenAI chat-completions 线协议）。API key 每次调用经 `SecretStorePort.resolve_for_broker` 从 `SecretHandle` 解析，解析失败在发请求前抛 `ModelCredentialUnavailableError`；`SecretHandle` 绝不进入请求体。
- `infrastructure/models/ollama.py`：`OllamaChatProvider`（本地，`is_remote=False`，Ollama `/api/chat` 线协议）。构造时强制 loopback 主机，禁止把“本地”端点指向远端以绕过披露许可。
- 公共传输语义（`infrastructure/models/base.py`）：https 远端（本地允许 http 但必须 loopback）、禁止 URL 内凭据/query/fragment、每次请求 `follow_redirects=False`（注入客户端也不能改变）、单一 monotonic 总 deadline 覆盖整个响应流、响应字节上限、非 2xx 转 `ModelProviderRejectedError`（只给出固定状态枚举 `rejection_code`，绝不读取厂商正文）、整体 deadline 超时与 httpx 层 connect/read/write/pool 超时都转 `ModelProviderTimeoutError`、连接失败/畸形 JSON/空完成转对应类型化错误、取消原样传播并关闭流。适配器不重试，也不会切换供应商；路由器在错误后只向调用方抛出，绝不改投其它远程 provider。
- `ContextField` 在构造时把 `classification` 规范化为 `DataClassification`：字符串 `"SECRET"` 与外来 `StrEnum` 成员按值归一，非法值抛 `ValidationError`；`ModelRouter`、真实适配器与披露服务对未知分类一律 fail closed（按 SECRET 处理），外来枚举无法绕过硬阻断。`ModelRequest` 与 `ContextField` 在构造时完成完整运行时校验（purpose/instruction 必须为 str，字段对象的 name/value/source 必须是 str 且分类可规范化，duck-typed 字段被强制转换为真实 `ContextField`），非法请求在发出任何调用前 `ValidationError`。字段容器不可迭代、字段属性访问抛异常、duck 字段用 `__eq__` 伪装相等都统一安全归一为无链 `ValidationError`；规范化后的字段元组无条件写回，不依赖相等比较，可变 duck 对象不会在许可绑定后继续存活。
- 内置适配器在 `complete()` 进入且尚未 `await` 时捕获一次请求级 `RecipientIdentity`：出站 URL、payload 里的 model、`ModelOutput.provider_id/model_id` 全程使用该快照，凭据解析或传输期间替换 `_recipient` 不会改变发送目标或记录身份。
- 适配器在发送前再次拒绝 SECRET 字段（纵深防御）。供应商 usage 计数只接受适配器固定键白名单内的非负整数，其它键与负值一律丢弃。响应中的 `model` 字段不被信任：`ModelOutput.model_id` 恒为已配置值，审计的 `resource_id` 与 `data.model_id` 取自已注册接收方（`provider.recipient.model_id`）而非响应元数据，`data.usage` 再次按核心白名单过滤。
- 自建 `AsyncClient` 固定 `trust_env=False`：绝不读取 `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY`，本地 loopback 请求与带凭据的远程请求都不会经过环境代理（真实 loopback 代理反例覆盖）。
- 类型化错误与 broker/httpx/JSON 异常完全解耦（无 `from exc`，`__cause__`/`__context__` 为空）：格式化 traceback 不包含凭据、Authorization 头、请求正文或响应正文。`complete()` 的脱敏边界覆盖凭据解析、payload 构建、传输与响应解析全部阶段，构造异常前删除 `request`/`payload`/`headers`/`raw`/`response` 等敏感局部引用，出错的 traceback frame locals 同样取不到凭据或请求正文；payload 构建失败（含手写畸形请求）同样先丢弃原异常与敏感引用，再从干净帧抛无链 `ValidationError`；响应解析失败（含恶意深层 JSON 的 `RecursionError`）统一转 `ModelProviderProtocolError`，不向外泄漏裸异常。远端凭据限定为 ≤4096 个可见 ASCII 字符，非 ASCII/控制字符在构造 header 前即转 `ModelCredentialUnavailableError`，不会抛出保存原始凭据的裸 `UnicodeEncodeError`。
- 响应流与客户端关闭统一走 `core/models/cleanup.run_cleanup`（抗重复取消）；`ModelRouter.aclose` 会尝试关闭全部 provider，完成清理后才抛出首个错误。关闭失败绝不覆盖取消或原始类型化错误，优先级固定为：取消 > 原始类型化错误 > 已安全脱敏的关闭错误。

### 13.5 配置与组合根

- 新增 `PA_MODEL_REMOTE_PROVIDER_ID`、`PA_MODEL_REMOTE_BASE_URL`、`PA_MODEL_REMOTE_MODEL`、`PA_MODEL_REMOTE_SECRET_HANDLE`、`PA_MODEL_REMOTE_TIMEOUT_SECONDS`、`PA_MODEL_LOCAL_PROVIDER_ID`、`PA_MODEL_LOCAL_BASE_URL`、`PA_MODEL_LOCAL_MODEL`、`PA_MODEL_LOCAL_TIMEOUT_SECONDS`、`PA_MODEL_LOCAL_FALLBACK_PROVIDER_ID`。所有构造路径（直接构造、`from_env`、`replace`）写回规范化值再校验。
- 远端端点必须 https 且同时提供 model 与 SecretHandle 句柄 ID；本地端点必须 loopback；缺失或半配置一律启动失败，不退化为不安全默认值。回退 provider 必须显式配置且必须等于已配置的本地 provider。
- 远端与本地 provider ID 必须互不相同（重复 ID 会让 `ModelRouter` 的 provider 表静默覆盖，`ModelRouter` 构造时也会直接拒绝重复 ID）；endpoint path 不允许 `.`/`..` 段（先把百分号编码解码到稳定值再检查，覆盖 `%2e%2e`、`%252e%252e`、`%2f` 拆分）或非法空白。违反者启动/构造失败。
- `build_container(settings, *, secret_store=None)` 接入真实适配器：远程凭据默认经 `UnavailableSecretStore` fail closed；`environment=production` 仍拒绝内存存储，`PA_STORAGE_BACKEND=memory` 只允许 development/test，生产绝不自动回退内存许可存储。

### 13.6 迁移与持久化

- 新增迁移 `0005_f04_model_disclosure.sql`：`model_disclosure_consents`（主键 `id`，状态约束、`expires_at > created_at`、REVOKED 必须带 `revoked_at`）（含 `recipient_fingerprint char(64) NOT NULL` 列）与 `model_disclosure_commands`（主键 `(scope, idempotency_key)`，`consent_id` 外键）。SHA-256 为 `012532834b281040d0031b48744ec7298e3c9b960bb247f51fd22c050f7534f0`。
- `0001`–`0004` 未改动；F04 契约测试与冻结清单锁定其 SHA-256，PostgreSQL 集成测试在“只应用到 0004 的数据库”上验证升级只新增 0005 且旧 checksum 不变。
- `PostgresDisclosureConsentStore` 在单事务内完成“命令日志 + 许可创建”；并发同键只在唯一约束竞争中产生一个赢家，落败连接重读并比较指纹；撤销用行锁 + `version` CAS 防止两个连接同时成功（并发反例：恰好一个成功，另一个 `ConcurrentModificationError`）。重启/重建 Container 后许可与撤销状态仍有效。`list_for_user` 的 `limit` 必须 ≥1（内存与 PostgreSQL 适配器一致抛 `ValidationError`），只存元数据。

### 13.7 有意未实现与已知残余

- Windows Credential Manager 等真实宿主凭据后端（F09）、真实厂商端到端调用（缺少真实凭据，仅以运行时 HTTP transport 验证协议）、PWA 披露许可界面与模型调用编排（F08/后续 Agent 阶段）、Embedding 与结构化输出能力。不得把上述任何一项描述为 F04 已交付。
- 许可绑定的是数据字段集合、接收方与用途；`instruction` 控制文本不在许可绑定内（DESIGN 8.3 的披露范围是数据字段）。后续 Agent 接入必须保证 `instruction` 来自工作流定义而非外部内容，并在上下文合成层处理提示注入。
- `PA_MODEL_REMOTE_SECRET_HANDLE` 只接受不透明句柄 ID；F09 的宿主凭据实现必须提供可区分的句柄格式，并明确禁止把密钥原文写入该字段。
- 披露 API 只接受当前组合根注册的远端 provider（未注册 404），不允许为未配置接收端预生成许可；本地 provider 不产生披露许可。
- 披露 preview/confirm 的请求体只有字段级上限（≤200 字段、单值 ≤200 KiB），未设整体正文上限；F08 接入前应补全局正文限制。
- 审计只保存字段摘要与输出哈希，无法从审计重建被披露内容（有意的隐私取舍）；`model_disclosure_commands` 永久保留命令指纹（不含原文）。

## 14. F05 实施后的契约补充（contract v1.5，DONE，完整独立审计与修复后自审计通过）

F05 交付 `personal.knowledge` 扩展与它需要的三个通用宿主能力：全双工 RPC、通用扩展数据代理、通用扩展配置通道。核心没有出现任何 `personal.knowledge` 分支、业务路由或业务表；`0001`–`0005` 未改动；扩展业务数据全部位于 `ext_personal_2e_knowledge` Schema。

### 14.1 SDK 新增公共接口（向后兼容）

- `personal_assistant_sdk.models.HostDataClient`（Protocol）：`execute(statement, parameters, *, timeout_seconds)`、`transaction(statements, *, timeout_seconds)`、`migrate(migrations, *, timeout_seconds)`、`aclose()`。宿主是唯一执行者，扩展永远拿不到连接串、密码或主机路径。
- `RuntimeContext.host_data: HostDataClient | None = None`：由 worker 运行时在 handshake 后注入，不经过 JSON 序列化；旧的 worker 代码不受影响。
- `MigrationDescriptor.path: str | None = None`：扩展自有迁移文件的 payload 相对路径；宿主用它解析并校验迁移，不使用扩展提供的绝对路径。
- `personal_assistant_sdk.host.HostBroker` / `HostCapabilityError`：worker 侧的宿主能力客户端，方法名固定为 `host.data.execute` / `host.data.transaction` / `host.data.migrate`；错误码经 JSON-RPC `error.data.code` 传递。
- `personal_assistant_sdk.rpc.decode_frame`：全双工帧判定 helper（含 `method` 为请求，否则为响应）。
- 通道语义：stdio 现在同时承载宿主→worker 请求和 worker→宿主能力请求；请求 id 不匹配、未知响应 id、畸形帧仍立即破坏流。宿主侧 `WorkerSpec.host_handler` 为可选异步处理器，未提供时 worker 请求得到 `DATA_UNAVAILABLE` 错误且流保持可用。

### 14.2 通用扩展数据能力（宿主唯一执行者）

- 宿主协议 `core.extensions.data_access.ExtensionDataAccess.handle(method, params, *, context)`；`ExtensionDataContext` 绑定 `extension_id/version`、`namespace` 与已安装 payload 根目录。适配器暴露 `available: bool`，宿主只在能力已声明且可用时向 Worker 注册 handler；`required = ["extension.data.sql"]` 不可用时 `enable` 失败并落 `REQUIRED_CAPABILITY_UNAVAILABLE`。
- 语句守卫（**execute、transaction、migrate 三条路径一致**）：单语句（字符串/美元引用/注释内的 `;` 不算分隔符）、首关键字白名单（`SELECT/WITH/VALUES/INSERT/UPDATE/DELETE/CREATE/ALTER/DROP/COMMENT/TRUNCATE`）、拒绝 `public.`/`information_schema`/`pg_catalog`/`pg_toast`/`pg_temp`、其他 `ext_*` Schema、`pg_read_file`/`lo_import`/`dblink`/`set_config`/`CREATE ROLE`/`ALTER SYSTEM` 等构造；迁移先安全拆分字符串/注释/美元引用之外的语句，再逐条执行同一首关键字白名单，`COPY/GRANT/DO/CALL` 等不得借迁移绕过；语句 ≤64 KiB、参数 ≤256 个、单参数 ≤256 KiB、参数嵌套深度 ≤32，非有限浮点拒绝。
- 命名空间隔离：每条语句在单一事务内执行；宿主先在同一有界事务中以 namespace advisory lock 串行创建专属 Schema，再把 `search_path` 设为扩展 Schema、pgvector 类型所在 Schema 与 `pg_catalog`，因此首次 execute/transaction 也不可能把未限定 DDL 落入 `public`。宿主每次请求重新读取非扩展 Schema 的关系名（核心表），对启动后新增的未限定核心表引用同样 fail closed。
- 事务批：`host.data.transaction` 在一个事务内顺序执行 ≤512 条语句，任一条失败则整体回滚。
- 迁移：`host.data.migrate` 只接受 `{version, path, checksum, description}`；路径必须位于已安装 payload 内，文件经一次有界读取后以同一份已校验字节计算 SHA-256 并执行，禁止校验/执行之间二次读取的 TOCTOU；已登记版本 checksum 漂移即 `DATA_MIGRATION_INVALID`。迁移登记表 `extension_data_migrations` 位于扩展自己的 Schema，迁移按命名空间 advisory lock 串行化，整个批次在一个事务内应用或全部回滚；`SET/RESET search_path` 及等效越权构造拒绝。
- 结果：`{rows, rowcount}`；行数上限 10 000，单次 execute 或 transaction 聚合 JSON 结果 ≤512 KiB，非有限数值不得进入 RPC；SDK 的 execute/transaction/migrate 必须同时把 `timeout_seconds` 写入 RPC 参数并作为本地等待上限，宿主以其设置 `statement_timeout`（默认 30/120/300 秒，上限 900 秒）。PostgreSQL `QueryCanceledError` 和 advisory-lock timeout 统一映射为 `DATA_TIMEOUT`。错误码固定为 `DATA_UNAVAILABLE`/`DATA_STATEMENT_REJECTED`/`DATA_TIMEOUT`/`DATA_MIGRATION_INVALID`/`DATA_RESULT_TOO_LARGE`/`DATA_PROTOCOL_ERROR`/`DATA_INTERNAL_ERROR`。
- 边界声明：扩展是用户信任代码；该能力是凭据与凭据边界代理，不是恶意代码沙箱。`PA_STORAGE_BACKEND=memory` 时宿主实现 fail closed（`DATA_UNAVAILABLE`），扩展必须显式降级而不是假装持久化。

### 14.3 通用扩展配置通道

- `core.extensions.config.ExtensionConfigStore`（`get`/`save`）与 `validate_extension_config`：只接受严格 JSON 对象（≤64 KiB，拒绝 NaN/Infinity），按 manifest `config_schema` 的显式 JSON-Schema 子集校验（`type/properties/required/additionalProperties/items/minItems/maxItems/minLength/maxLength/pattern/enum/minimum/maximum`）；Schema 自身 ≤256 KiB，任意深度出现不支持关键字均 fail closed。
- `FileExtensionConfigStore` 把非秘密配置写入 `PA_EXTENSION_ROOT/config/<extension_id>.json`（扩展 ID 白名单校验、实际读后大小复核、唯一临时文件、flush+fsync、同扩展进程内写锁与原子替换）；并发保存不得互相覆盖临时文件或留下半成品；凭据不得进入该文件。
- Admin API（仅回环 8001）：`GET /admin/v1/extensions/{extension_id}/config` 返回配置与 `config_schema`；`PUT` 校验后持久化并返回 `restart_required: true`（下一次 enable/recover 注入）。public API 无该路由。
- 注入：`ProcessRuntimeSupervisor` 在启动 worker 前读取配置；非空持久配置必须再次按**当前已安装版本**的 `config_schema` 校验，通过后才在 handshake 中作为 `non_secret_config` 注入，防止升级后的旧配置绕过；配置缺失/空配置时扩展自报 `awaiting_configuration`，`system.health` 仍返回 healthy，避免安装时因未配置而失败。

### 14.4 personal.knowledge 数据模型、版本语义与引用

- 扩展自有迁移 `migrations/0001_knowledge_index.sql`（随扩展制品发布，不在核心 `migrations/`）：`knowledge_sources`、`knowledge_versions`、`knowledge_chunks`（`to_tsvector('simple', text)` 生成列 + GIN FTS 索引、无维度约束的 `vector` 列）、`knowledge_tombstones`、`knowledge_events`、`knowledge_meta`。
- `version_id = sha256(content_hash + extractor_name:version + embedding_identity)`；内容、抽取器或 embedding 身份任一变化都会产生新版本。`embedding_identity = provider|model|dim|version` 随版本持久化，模型/维度变化触发重建，不混用向量空间。
- 原始文件字节是唯一事实源：`knowledge_sources.content_hash` 只在版本完整构建并原子激活时才前进。分块先写入旁路 `BUILDING` 版本且对查询不可见；最后由单个 CAS 事务把候选改为 READY 并切换 `active_version`。查询只 join `active_version`，旧活动版本在新版本 READY 前持续可查。
- 失败/取消/持久化失败：候选 owner 是不可复用的 reconcile `run_id`；`begin_version` 对既有 READY 或他人 BUILDING 只返回未取得 owner，不重置状态。分块写入、heartbeat、激活和 `fail_version` 都要求同一 `built_by`；失败清理只能删除自己仍为 BUILDING 的版本（FK 级联分块），绝不能清理已 READY 的版本或他人候选。长构建在 embedding/分块批次间刷新 `heartbeat_at`，孤立清理只回收超过租约的候选；扫描失败不修改已登记哈希。
- 幂等与并发：激活在同一 CTE 中锁定并同时验证 source 的 expected root/path/generation/active_version 快照、候选 BUILDING 状态与 owner，只有 eligible 候选才会提升 READY 和切换指针；CAS 失败不产生 READY 副作用。并发或重复 reconciliation 收敛为每来源一个活动版本。重命名/移动保留原 `source_id`；旧路径重用若与移动来源 ID 碰撞，使用稳定的内容绑定后备 ID 建立独立来源。
- 删除传播：删除由单条 CAS CTE 完成，仅当 root/path/generation 快照仍匹配才删除分块、FTS、向量与版本行；成功删除消耗一个 generation 并把递增值写入不含正文的 tombstone，重现文件从 tombstone 继续单调 generation；陈旧删除无副作用。检索不再返回旧正文，旧引用复核为 DELETED，并写入 `knowledge.file_deleted` 事件。
- locator：一般 Markdown/TXT 使用 1 基闭区间行号，PDF 使用 1 基页码 + 页内 0 基字符区间；无法用整行表达的超长文本使用文档内 0 基字符区间 `document_fragment`。任一返回片段都必须由 locator 从抽取文本逐字复算。单 chunk 同时受字符数与 64 KiB UTF-8 上限约束。
- Evidence/搜索输出至少包含 `source_uri`、`source_version`、`content_hash`、`locator`（kind/page/line）、`heading_path`、`media_type`、`extension_id/version`、`sensitivity=PERSONAL`、`trust=USER_SOURCE`、`status`；展示为 CURRENT 之前重新读取源文件并核对哈希，变化/删除/越界/不可读一律 STALE/DELETED 并安排 reconciliation，没有证据时返回空结果与 `unknown=true`。
- 混合检索：PostgreSQL FTS（`plainto_tsquery('simple', ...)`）与 pgvector 精确检索独立排名，使用固定 `k=60` 的 RRF 融合，稳定并列规则为 `(source_id, version_id, ordinal)`；结果去重且顺序可复现；query ≤512 字符、limit 1–50、filters 只允许 `root_keys/media_types/source_ids`。向量距离只在 MATERIALIZED 的活动版本 identity 匹配集上计算；配置的 provider/model/dim/version 与索引不一致时不得触碰旧向量空间，安全退回 FTS 并标记 `vector_mode=disabled`。
- Embedding：默认 `provider=none`，检索显式降级为仅 FTS 并在输出标记 `vector_mode=disabled`；`provider=ollama` 只允许回环 HTTP，个人正文绝不发往远程服务；为保证一小时 BUILDING 租约，串行 Ollama embedding 每批最多 16 条并在批次间 heartbeat；向量语句批按实际 JSON（含向量字面量）计入帧预算。测试使用的确定性 hash 向量明确标记为测试替身，不代表生产语义 embedding。

### 14.4b 独立验收修复（第二轮，2026-09-17）

第一轮独立验收发现的 6 项 P1 + 2 项 P2 已修复，均有真实 PostgreSQL / 真实 Worker 反例：

- **数据代理越权**：`forbidden_relations` 现在同时传入 execute、transaction 与 migration 三条路径；`test_transaction_and_migration_cannot_touch_core_tables` 证明 `SELECT/ALTER tasks` 与迁移文件中的 `ALTER TABLE tasks` 被拒绝且 `tasks` 未被修改、迁移 ledger 未登记。
- **授权根即时边界**：`search` 总是把当前授权 root key 并入查询过滤（调用方提供的 root 过滤只做交集，越权/已移除 root 立即返回空）；`reconcile` 读取全部 source，将不再授权的 root 的 source 删除并写 tombstone，不依赖该 root 是否被扫描。反例：`test_removed_root_is_immediately_unsearchable_and_reconciled`。
- **首次构建失败可重试**：增量差异把 `active_version IS NULL` 视为必须重建；`test_first_build_failure_is_retried_on_the_next_scan`（注入激活失败后下一轮成功）。
- **取消/失败清理与激活 CAS**：`_build` 捕获 `BaseException`，用抗重复取消的 shield 清理候选后重抛；reconcile 开始时只清理其他 run 遗留且超过 1 小时的 `BUILDING`。最终实现以 `built_by` 作为不可复用 owner：`fail_version` 只删匹配 owner 的 BUILDING，`activate_version` 在 `eligible` CTE 中先锁定并同时验证 source CAS、候选状态和 owner，随后才提升 READY/切换指针；被更新版本取代的旧构建失败时无 READY 副作用，也不会删除活动版本或他人候选。反例：`test_cancelled_build_leaves_no_building_rows`、`test_activation_never_destroys_another_build_candidate`、`test_same_version_cannot_be_reclaimed_or_cleaned_by_another_run`、`test_orphan_building_version_is_cleaned_up`。
- **embedding 身份变化触发重建**：未变化文件也要比较活动版本的 extractor 名/版本与完整 embedding 身份（provider/model/dim/version），任一不同即重建；Ollama 维度在写入任何身份之前先通过 probe 学得，避免把 `dim=0` 固化。反例：`test_embedding_identity_change_rebuilds_versions`（model-a/16 → model-b/8 重建，且第二次运行 unchanged）。
- **Ollama 本机边界**：改为 `asyncio.open_connection` 直连（stdlib），不读取任何环境代理、不跟随重定向，单一 monotonic 总 deadline 覆盖连接/写入/读取，取消时同步关闭 socket（无后台线程继续传输）。反例：`test_environment_proxies_are_never_consulted`（代理零连接）、`test_slow_trickle_respects_the_total_deadline`、`test_cancellation_closes_the_socket`。
- **capability 授权**：`ProcessRuntimeSupervisor` 只在 Manifest 声明 `extension.data.sql` 且数据能力 `available` 时注册 `host.data.*` handler；`required` capability 不可用（未配置/内存后端）时启动/启用失败并落 `REQUIRED_CAPABILITY_UNAVAILABLE`。反例：`tests/unit/test_extension_capability_gating.py`。
- **失效证据不携带正文**：新增 `SearchResult`；只有 `CURRENT` 结果带正文，`STALE`/`DELETED` 只返回元数据（来源、locator、旧哈希、状态）并写入 reconciliation 事件；`ContextProvider.retrieve` 只返回 `CURRENT` 证据，其余一律丢弃，没有可靠证据时返回空。反例：`test_stale_and_deleted_results_never_expose_their_body`、真实 Worker 的删除后 `retrieve` 为空。

### 14.4c 第三轮完整修复（2026-09-17）

- **首次 Schema 与 deadline**：execute/transaction/migrate 都在 search path 前有界创建 namespace；动态核心关系表不缓存；SDK deadline 进入 RPC 参数，数据库取消映射 `DATA_TIMEOUT`。
- **构建 owner 与无副作用 CAS**：同一版本只允许一个 `built_by` owner，分块、激活、清理全链校验 owner；CAS 不满足时候选保持 BUILDING，活动 READY 版本及分块不变。
- **来源与事件身份**：移动后旧路径可获得碰撞后备 source id；`knowledge_sources.generation` 每次成功激活/移动单调递增并进入事件幂等键，A→B→A→B 每次真实转换都产生事件，删除/重建通过 tombstone 延续 generation。
- **有界大文件管线**：超长单行与大 PDF 页拆成可复算 locator 片段；embedding 每批最多 512 条，数据库 transaction 同时按条数和估算 UTF-8 字节限批，任何受支持大小的单个文件不会仅因批次上限失败。
- **向量与 Ollama 协议**：向量查询精确绑定活动版本 embedding identity；Content-Length 截断/流读取不完整统一为 `EMBEDDING_PROTOCOL_ERROR`，配置阶段只接受合法 loopback HTTP URL。
- 反例：`test_execute_before_migration_creates_and_stays_in_its_namespace`、`test_late_core_relation_and_statement_timeout_fail_closed`、`test_same_version_cannot_be_reclaimed_or_cleaned_by_another_run`、`test_renamed_path_can_be_reused_by_a_new_source`、`test_long_single_line_within_limit_builds_and_remains_locatable`、`test_changed_embedding_identity_never_queries_old_vector_space`、`test_repeated_hash_transition_is_not_permanently_deduplicated`、`test_truncated_content_length_is_a_typed_protocol_error`。

### 14.4d 第四轮完整独立审计修复（2026-09-18）

- **配置与输入边界**：配置值拒绝 NaN/Infinity；Schema 校验递归检查未支持关键字，不能藏在嵌套结构中；`safe_read_bytes` 在真实读取后再次校验长度并重新解析路径，防止预检查后内容增长或链接目标变化。
- **有界抽取**：PDF 单个 Flate 流和全部页面解码后的聚合字节都有独立硬上限，压缩炸弹不能绕过源文件大小限制。
- **来源身份与并发**：跨媒体类型重命名（如 `.txt`→`.md`）必须因 extractor/media type 变化重建；陈旧并发 move 不能重复推进 generation 或写事件。
- **数据适配器活性与隔离**：首次向量类型 Schema 发现复用当前事务连接，单连接池不会自锁；namespace advisory lock 超时转 `DATA_TIMEOUT`；迁移不能以 `SET LOCAL search_path` 把未限定对象写入核心 Schema。
- 主要反例：`test_read_limit_is_checked_after_actual_read`、`test_pdf_total_decoded_page_bytes_are_bounded`、`test_cross_media_rename_rebuilds_with_the_new_extractor`、`test_concurrent_stale_move_is_side_effect_free`、`test_vector_schema_discovery_does_not_require_a_second_connection`、`test_namespace_lock_timeout_is_typed`、`test_migration_cannot_override_search_path`。

### 14.4e 第五轮修复后自审计（2026-09-18）

- **配置存储再审计**：直接存储路径也执行严格 JSON、文件大小与非有限数值检查；同扩展并发保存串行且临时文件唯一；Supervisor 对已有非空配置按当前安装版本 Schema 再验证后才启动。
- **版本租约与 CAS 再审计**：激活/移动/删除绑定完整来源快照；删除本身消耗 generation；BUILDING 增加 `heartbeat_at`，构建批次续租，清理只回收真正过期 owner。
- **事件一致性再审计**：`EventSource.poll` 与 reconciliation 共用同一差异规则；rename 只发 MOVED；re-add 从 tombstone generation 延续；旧路径复用使用碰撞后备 identity；提前到达的 modified 事件以观察哈希与 reconcile 去重。
- **RPC/数据库预算再审计**：向量 transaction 按实际 JSON 字节流式分批；参数嵌套深度 ≤32；所有非有限数值拒绝；execute/transaction 聚合结果 ≤512 KiB。
- **迁移与抽取再审计**：迁移文件只读取一次，校验的同一字节随后执行；安全语句拆分后逐条应用白名单；PDF 聚合解压上限覆盖多页累计。
- 最终证据：`./scripts/test.ps1` 为 `597 passed, 81 skipped`，Ruff/Mypy（166 files）通过；`./scripts/test-postgres.ps1` 为 `81 passed`（PostgreSQL 17.11 + pgvector，含真实 Worker）；`pip check`、`git diff --check` 通过。扩展迁移 SHA-256 为 `0f8758f97cce7206ac0b5cd4aa159f1b7b43de79cf1fa77a811a67f8aecd759d`，核心 `0001`–`0005` 未改。

### 14.5 授权根与路径安全

- 根目录来自通用配置；每个根必须存在且是目录，多个根不得互相包含（避免重复索引），最多 16 个。
- 读取前解析真实路径：拒绝绝对路径、Windows 盘符、`..`、根外符号链接/junction、大小写绕过；遍历时不跟随链接/junction，隐藏目录默认跳过（`include_hidden` 可显式开启）。
- 源文件只读：索引过程读取文件内容与元数据，但绝不写入、重命名、修改时间戳或权限；系统生成内容只写入扩展 Schema 与受管扩展目录。

### 14.6 有意未实现（不得描述为 F05 已交付）

- 宿主在 enable/upgrade 阶段自动执行 `MigrationProvider`：F05 由扩展在工作调用中通过通用数据能力触发迁移；`migration.list` 仍用于安装契约校验。
- OS 级文件监听（Windows `ReadDirectoryChangesW`/watchdog 属 F09）：`EventSource.poll` 是轮询式增量检测，正确性由 reconciliation 保证。
- 未创建 HNSW/IVFFlat 向量索引：pgvector 无法为无维度约束列建索引，当前为精确检索；固定单一本地模型后可另加维度特定索引。
- 真实远程 embedding 与语义向量质量验收、PWA Schema 表单渲染、永久 purge（保持 501）、smail/ehall/Web Push（F06+）。

## 15. F06 实施后的契约补充（contract v1.6，IN_PROGRESS，等待独立验收）

F06 交付 `nju.smail` 扩展与它需要的通用宿主邮件能力：只读 IMAP 同步、版本化草稿、受 Tool Gateway + R2 审批 + SideEffect Outbox 约束的 SMTP 单封发送与 Sent 只读对账。核心与通用宿主模块没有出现 `nju.smail`/NJU 分支、业务路由或业务表；`0001`–`0005` 未改动；扩展业务数据全部位于 `ext_nju_2e_smail` Schema。六轮独立审计提出的 21 个 P1 与 18 个 P2（凭据重定向、分块游标跳过、SMTP 取消后线程继续、台账先发送后记录、Message-ID 覆盖正文、草稿指针非原子、对账未绑定、崩溃遗留 EXECUTING、连接阶段取消、账户换绑 TOCTOU、凭据解析期间换绑、租约过期误判、伪造发送结果、DNS 无界、草稿取消清理、注册表弱类型/重复 ID、指纹未绑定 thread_id、心跳 owner 不一致、复核后换绑、删除重建复用 generation、非终态投影报错、Admin PUT 陈旧 fingerprint、文档残留旧工具、跨进程 TOCTOU、跨进程 monitor/心跳 fail-open、真实 sender 未保持 ACCOUNT_CHANGED 语义、心跳挂起绕过本地租约、锁等待期间不复核组合 guard、静态检查未通过、注册表异步写阻塞事件循环、本地 guard 未绑定数据库领取时刻、取消注册表写入后后台线程仍提交等）均已修复并补反例。第四、五轮按用户要求未由实现者执行测试；第六轮及补充修复由实现者运行静态验收命令（`./scripts/test.ps1` 742 passed、103 skipped，Ruff/Mypy 192 files 通过；未运行 PostgreSQL），证据见 `docs/NEXT_STEPS.md` F06 章节。

### 15.1 新增公共宿主端口与类型

- `core/mail/ports.py`：`MailAccountRecord`（非秘密账户元数据 + `SecretHandle` id + 独立 `read_enabled`/`send_enabled` + `tls_mode`，提供 canonical `fingerprint()`）、`MailAccountNotFoundError`、`MailAccountRegistry`（宿主所有的账户真相源）、`MailAccountBinding`（宿主内部值对象，扩展不可构造）、`MailCapabilities`/`MailFolder`/`MailFetchedMessage`/`MailFetchResult`、`MailEnvelope`/`MailDeliveryRequest`、`MailRecipientResult`/`MailDeliveryReceipt`/`MailReconciliationResult`、`MailPolicy`、`MailDeliveryRecord`（含 `owner_id`/`lease_expires_at`）；`MailDeliveryStatus` 是显式状态机 `PREPARED -> EXECUTING -> SUCCEEDED|PARTIAL|FAILED|UNKNOWN`（`terminal` 属性）。`MailDeliveryLedger` 定义 `prepare`、`begin_execution(local_action_id, envelope_digest, owner_id, lease_seconds)`（CAS PREPARED→EXECUTING 并领取有界租约；第二个调用者拿到活跃 EXECUTING 时不得烧毁首个调用）、`heartbeat(local_action_id, owner_id, lease_seconds)`（发送期间续租；返回 False 表示 owner 失去该行，发送器必须中止）、`finalize`、`reconcile(local_action_id, account_id, message_id, status, ...)`（账户与 Message-ID 属于 CAS 守卫）、`recover_stale_executions(active_owners=...)`（仅把租约过期且 owner 不在活跃本机代次中的 EXECUTING 原子转为 UNKNOWN；宿主启动与对账前都会安全扫描）、`get`、`close`；冲突与非法转换分别抛 `MailLedgerConflictError`/`MailLedgerStateError`。类型与供应商/服务器无关。
- `infrastructure/mail/registry.py`：`FileMailAccountRegistry`（严格 JSON、≤32 账户、**手工文件重复 id 拒绝**、flush+fsync+原子替换；`read_enabled`/`send_enabled` 必须是真实 JSON boolean，字符串/数字/null 一律拒绝；每次管理端写入对既有账户递增 `generation`，并持久化 **revision tombstone**，删除后重建不会复用旧 generation/指纹）与 `InMemoryMailAccountRegistry`；`verify(lease)` 在 generation 或 fingerprint 变化、账户删除后返回 False。`dispatch_guard(lease)` 返回活体、fail-closed 的 `MailAccountDispatchGuard`：每次 `valid`/`reason` 都实时读注册表（无轮询缓存），读/解析失败即视为不可确认（失效，`reason="ACCOUNT_CHANGED"`）；`begin_critical_section` 获取与写者相同的锁并复读绑定（文件注册表使用跨进程 `ExclusiveFileLock`，路径 `<registry>.lock`；内存注册表使用专用进程内锁），持锁直到 DATA 提交结束，因此“复核后换绑”在跨进程下也不再是 check-then-act。写者（`upsert`/`replace_all`/`delete`）的完整 read-modify-write 在受控工作线程内执行并保持同一跨进程锁覆盖整个操作；等待线程使用项目既有取消安全模式 `run_blocking`（取消时继续 shield 等待、消费线程终态后才重抛 `CancelledError`，重复取消也不中断回收），因此 Admin 事件循环既不会被文件锁等待或文件 I/O 阻塞，也不会在调用方已观察到取消后仍有后台换绑/凭据句柄/generation 写入落地。这是宿主账户的唯一写入口；扩展配置只能提交 `account_id`。
- `core/extensions/artifact_access.py` + 实现：`host.artifact.put/read/delete`，按扩展版本持久化所有权，跨扩展访问 fail closed。
- `infrastructure/mail/imap_client.py`：`TlsImapReadSession` 只使用 `CAPABILITY`、`LOGIN`/`AUTHENTICATE PLAIN`（从 probe 选择）、`LIST`、`EXAMINE`/`SELECT (readonly)`、`UID SEARCH`、`UID FETCH BODY.PEEK`；证书与主机名验证来自注入 `SSLContext`（默认系统信任库）；单一 deadline 覆盖整次操作，超时/取消关闭 socket；错误类型化且不回显服务器文本或密码。
- `infrastructure/mail/smtp_client.py`：`TlsSmtpSender` 显式 `MAIL FROM`/`RCPT TO`/`DATA` 阶段机；DATA 前失败 `FAILED`，payload 写入/等待最终响应期间断线 `UNKNOWN`，显式非 2xx `FAILED`，250 时逐收件人 `SUCCEEDED`/`PARTIAL`。DNS 解析在启动发送线程之前由事件循环在同一个总 deadline 内完成：超时或取消时调用方绝不继续发送，但 `loop.getaddrinfo` 使用默认执行器，卡住的系统解析线程无法被 Python 强制终止，可能比调用更晚结束（这是有意如实记录的限制，不是“零线程残留”保证）。随后 `_TrackedSmtp` 在 connect/TLS 握手之前注册 socket；`abort_requested`（来自超时/取消）与发送租约/账户派发 `guard` 在连接后、认证前/后、DATA 前均被检查：超时或取消会关闭 socket 并等待线程退出后才返回/重抛（重复取消也会先回收再抛出），连接/TLS 阶段取消同样可达；线程已完成时其真实 receipt 优先于超时分类，`done` 永不被归类为 `FAILED`。DATA 临界区：提交前调用 `guard.begin_critical_section()`，失败时按 `guard.reason` 抛类型化 `MailError(ACCOUNT_CHANGED)` 或按租约丢失返回 FAILED；`docmd("DATA")`、payload 发送与最终回复全程持锁，`finally` 释放。因此未开始 DATA 的换绑会中止发送，已经开始 DATA 的换绑必须等待提交完成；发送器保留 guard 失效原因，账户变更以 `MAIL_ACCOUNT_CHANGED` 抛出，租约丢失仍返回 FAILED/UNKNOWN 回执。
- `infrastructure/mail/broker.py`：`ConfiguredMailTransportBroker` 只接受宿主注册表的 `account_id`，解析记录、解析 `SecretHandle`，并在凭据 `await` 返回后、创建任何 socket 之前用注册表 generation/fingerprint **原子复核**账户（`MAIL_ACCOUNT_CHANGED`）。此后每次派发还会通过 `accounts.dispatch_guard(record)` 创建活体 `MailAccountDispatchGuard`：SMTP 在建连接、认证前/后与 DATA 前实时检查组合 guard（`CompositeMailGuard` 汇聚租约 guard 与账户 guard），DATA 提交在账户临界区内完成。组合 guard 先取得账户锁，再用单一总 monotonic deadline 取得其余临界区，并在全部锁内复核所有 guard，因此等待账户锁期间失效的租约既不会进入 DATA、也不会泄漏账户锁；账户/端点/开关变更（含另一进程的写入）要么在 DATA 前以 `MAIL_ACCOUNT_CHANGED` 中止，要么必须等待已经开始的提交完成。SMTP 构造经类型化 `SmtpSenderFactory`/`SmtpSenderPort` 端口注入，生产默认 `TlsSmtpSender`。凭据后端失败（含 F09 前 fail-closed）返回 `MAIL_CREDENTIAL_UNAVAILABLE` 且 `needs_user_action=true`。
- `infrastructure/mail/host.py`：只读 `host.mail.account/probe/folders/fetch/delivery_status/reconcile_sent`。`delivery_status` 只按 `account_id + local_action_id` 返回宿主账本的权威状态/逐收件人结果（扩展据此投影，不能提交状态）。`account` 只返回 `{account_id,address,display_name,read_enabled,send_enabled,fingerprint}`，不含端点或句柄；所有方法只接受 `account_id` 并从注册表解析。`reconcile_sent` 先做一次 owner 感知的租约恢复扫描，再读取宿主账本并严格校验 `record.account_id == account_id`、`record.message_id == message_id` 且 `record.status == UNKNOWN`，不合法的动作抛 `MAIL_ACTION_UNKNOWN`/`MAIL_ACTION_BINDING_MISMATCH` 或返回 `UNAVAILABLE(MAIL_ACTION_NOT_UNKNOWN)` 且不查询邮箱；唯一命中后必须由 ledger CAS 真正把 `UNKNOWN` 提升为 `SUCCEEDED`（`server_code=SENT_RECONCILED`）才返回 `MATCHED`，CAS 失败或返回其它终态一律 `UNAVAILABLE`。
- `infrastructure/mail/executor.py`：`MailSendExecutor` 只执行声明 `mail.send` 能力的工具；只接受 `account_id` + `account_fingerprint`，与宿主注册表 fingerprint 精确比对；物化当前草稿后校验 MIME 哈希、解析收件人/主题/Message-ID 与附件哈希。账户在物化前、物化 await 返回后、以及建立 SMTP 连接前各复核一次（fingerprint 与 `send_enabled`），期间禁用/换绑/删除账户会在 SMTP 前确定失败且不写入 EXECUTING。随后在 `_begin` 之前生成唯一 owner 代次，并把**同一个值**用于账本领取、本机活跃登记、心跳与注销（否则心跳无法续租自己领取的行）→ `prepare` → `begin_execution`（含 owner 与有界租约；建立连接前必须成功落 EXECUTING）→ 启动心跳任务续租并在丢失租约时使发送 guard 失效 → `broker.send(account_id, request, guard=...)` → `finalize` CAS。心跳任务持有的 `_LeaseGuard` 以单调 deadline 为权威，且 deadline **锚定数据库领取/续租调用时刻**（`claim_started` 在 `_begin` 前取得、`heartbeat` 续租锚定调用起点）：`valid` 实时按 deadline 计算，`renew` 只在未过期时推进，`invalidate`/`renew` 由线程锁串行化；每次 `ledger.heartbeat` 调用由 `asyncio.timeout_at` 以剩余租约为界，因此心跳挂起（永久不返回）或心跳间隔大于租约都会让 guard 在 deadline 到期即失效（`reason="EXECUTION_LEASE_LOST"`）。若领取后的复核工作已耗尽租期，执行器在创建 guard 后立即检测到无效并抛 `OutcomeUnknownError`（绝不开 SMTP，`EXECUTING` 行留给恢复/对账）；测试可用构造参数 `lease_seconds`/`heartbeat_interval_seconds` 注入有界租约。发送结束（含取消）会回收心跳任务并注销本机 owner 代次。终结账本失败时向 Gateway 抛 `OutcomeUnknownError`，绝不返回成功；已终态动作幂等重放返回与工具输出 Schema 相同形状的结果，绝不二次派发；崩溃遗留的 `EXECUTING` 由宿主启动恢复扫描转为 `UNKNOWN` 后仍可由 Sent 对账收敛。
- `core/extensions/descriptors.py` 与 Manifest 工具级 `capabilities`：`ManifestTool.capabilities` 映射为领域 `ToolDescriptor.required_capabilities`；`infrastructure/tools/capability_router.py` 的 `CapabilityRoutingExecutor` 按该声明把 `mail.send` 路由到宿主执行器，其余调用路由到拥有者 Worker；`bootstrap.py` 用真实组合根构造 `Container.tool_registry`/`tool_gateway`，public/admin lifespan 在启动时按持久 lifecycle 调用 `refresh_tool_registry()`。
- 迁移 `0006_f06_mail_transport.sql`：通用宿主传输台账 `mail_delivery_actions(local_action_id PK, account_id, message_id, status, envelope_digest, mime_sha256, recipient_results jsonb, server_code, diagnostic_code, created_at, updated_at)`，状态约束为上述六态，另含消息索引与未决状态部分索引；不含正文、凭据或扩展 ID。SHA-256 为 `dbc5001bdd16981f2a17f36abe4ef3fcdd36c63c3461477f1a61e1ecb6f32a01`（第二轮审计修复增加 `owner_id`/`lease_expires_at` 与租约索引）。

### 15.2 SDK 新增接口（向后兼容）

- `personal_assistant_sdk`：`MailAccountInfo`（无端点、无句柄）、`MailboxCapabilities`、`MailFolderInfo`、`FetchedMail`、`FetchedMailBatch`、`HostMailClient`、`HostArtifactClient`；`RuntimeContext` 新增 `host_mail`/`host_artifact`。
- 全双工宿主方法：`host.mail.account`、`host.mail.probe`、`host.mail.folders`、`host.mail.fetch`、`host.mail.reconcile_sent`、`host.artifact.put/read/delete`。发送（SMTP）**没有**宿主方法。
- 能力门：`ProcessRuntimeSupervisor` 只在 Manifest 声明且能力可用时注册 handler；`required` 能力不可用时 enable 失败并落 `REQUIRED_CAPABILITY_UNAVAILABLE`。

### 15.3 nju.smail Manifest 槽位与风险

- 槽位：`EventSource: smail.poll_inbox`、`ContextProvider: smail.thread_history`、`ToolProvider: smail.search`(READ)/`smail.prepare_reply`(INTERNAL_WRITE)/`smail.sync`(INTERNAL_WRITE)/`smail.send`(EXTERNAL_WRITE)/`smail.send_status`(INTERNAL_WRITE，只读宿主状态并做 CAS 投影)/`smail.reconcile_send`(INTERNAL_WRITE)、`WorkflowProvider: smail.reply_flow`、`ScheduleProvider: smail.poll_every_5m`（300 秒、`coalesce`）、`FormSchemaProvider: smail.account_settings`、`MigrationProvider: smail.mail_schema`。`smail.record_send_result` 已删除，扩展无法伪造发送结果。
- `smail.send` 额外声明工具级 `capabilities = ["mail.send"]`，因此生产 Gateway 会把它路由到宿主执行器；`smail.prepare_reply` 永不发送，风险等级不因本任务改变。

### 15.4 IMAP 同步、游标与去重语义

- 只读保证：保留 `EXAMINE`/`SELECT (readonly)` 与 `BODY.PEEK`，客户端不存在 `STORE`/`EXPUNGE`/`COPY`/`MOVE`/`APPEND`/`IDLE`；协议测试断言命令日志无变更命令、服务器 flags/文件夹不变。
- 主去重：位置表主键 `(account_id, folder_name, uidvalidity, uid)`；逻辑身份 `dedupe_key` 同时绑定 `Message-ID` 与 `canonical_content_hash`（缺失 Message-ID 时仅内容哈希）。复用/伪造 Message-ID 但正文不同的邮件是新逻辑消息，不会丢失正文；重复扫描、Worker 重启与 UIDVALIDITY 重置仍收敛为一条消息、一个事件。
- 分块游标：每批按 ≤25 条分块提交，事务只把游标推进到**当前分块的最大 UID**；后一块失败时下一轮从上一块末尾继续，绝不跳过未提交邮件。消息、位置、线程、联系人、附件、事件与游标在同一宿主事务提交。
- 退避与用户动作：认证/凭据类错误进入 `NEEDS_USER_ACTION`（后续轮询跳过探测）；瞬时错误指数退避 60→3600 秒；`force` 才允许显式重试。
- 邮件正文是不可信数据：`ContextProvider` 证据标记 `sensitivity=PERSONAL`、`trust=EXTERNAL_MESSAGE`；正文不能改变工具/计划/策略或触发发送。

### 15.5 草稿版本、审批绑定与发送结果

- `mail_draft_versions` 绑定 `account_fingerprint`、`from_address`、**`thread_id`**、`revision_request_id`、To/Cc/Bcc、Subject、Body、附件清单（文件名、媒体类型、SHA-256、大小）与 `canonical_digest`。草稿行确保语句是 `ON CONFLICT DO NOTHING`（stale CAS 不触碰 `updated_at`），版本插入与当前指针 CAS 在**同一条 data-modifying CTE** 中完成（`FOR UPDATE` 锁草稿行）：CAS 不匹配或请求已存在时 SQL 本身零修改，不可能留下引用已删除 Artifact 的孤立版本，指针不会回退；`(draft_id, revision_request_id)` 唯一；同一请求键换到另一线程即使正文相同也必须冲突。
- 幂等与再次编辑：`prepare_reply` 用 `InvocationContext.idempotency_key` 作为 `revision_request_id`；同键同内容重放返回原版本（不产生新 Artifact/动作 ID/Message-ID），同键异内容抛 `SMAL_IDEMPOTENCY_CONFLICT`。新的编辑请求（新请求 ID 或显式 draft_id）即使正文完全相同或 A→B→A，也产生新版本、新 `local_action_id`、新 Message-ID 与新 MIME 制品；数据库失败或取消会用抗重复取消的 shielded 清理删除刚创建的 MIME Artifact（无孤儿制品）。发送审批绑定账户 fingerprint，宿主注册表端点/端口/句柄/开关任一变化都会使旧审批失效。
- 审批与发送：`smail.send` 参数包含 account_id/fingerprint、draft_id/version、canonical 摘要、local_action_id、Message-ID、From/To/Cc/Bcc、Subject、MIME 哈希与附件哈希；执行器在 SMTP 前逐项校验，编辑草稿后旧版本无法匹配当前版本（`SMAL_DRAFT_CHANGED`）。
- 幂等与结果：Outbox `(tool_id, idempotency_key)` 与台账 `local_action_id` 双重保证最多一次派发；`SUCCEEDED`/`PARTIAL`/`FAILED`/`UNKNOWN` 与逐收件人 `ACCEPTED/REJECTED/UNKNOWN` 持久化；`PARTIAL` 绝不上报整体成功；`UNKNOWN` 不自动重试，只由 `smail.reconcile_send` 只读 Sent 对账或用户裁决收敛，命中时宿主台账 CAS 提升为 `SUCCEEDED`。**扩展没有可写入发送结果的公开工具**：`smail.send_status` 只能读取 `host.mail.delivery_status` 并把宿主状态投影到本地 `mail_send_actions`（SQL CAS 仅允许 `PREPARED/UNKNOWN → 宿主终态`，第一个终态不可覆盖）；宿主返回 `PREPARED/EXECUTING` 时只回报宿主状态、不做终态投影；伪造或并发终态写入不可能成功。
- 初始真实发送仅允许 `PA_MAIL_TEST_RECIPIENTS` 中登记的受控地址。

### 15.6 配置、注册表与管理接口

- 新增 `PA_MAIL_SEND_ENABLED`（默认 false）、`PA_MAIL_TEST_RECIPIENTS`（发送开启时必填、规范化/去重/≤64）、`PA_MAIL_MAX_MESSAGE_BYTES`（65536–26214400）。所有构造路径规范化并校验。
- 宿主账户注册经 Local Admin（仅回环 8001）：`GET/PUT /admin/v1/mail/accounts`，仅保存非秘密元数据与 `SecretHandle` id；扩展 Manifest 的 `config_schema` 只接受 `{account_id, display_name?}`，宿主拒绝任何扩展提交的端点/句柄字段。
- 客户端专用密码只存在于宿主凭据后端（F09 前生产 fail closed）；配置、数据库、日志、RPC、异常、测试 fixture 与 Git 均不含密码。扩展 Worker 不建立 IMAP/SMTP 连接；venv/Worker 仍只是依赖与崩溃隔离。

### 15.7 有意未实现与剩余风险

- 真实 smail 只读/发送 E2E 未执行：缺少用户账号、SecretHandle 与受控测试地址；当前证据为协议级模拟、真实 TLS/socket、真实 PostgreSQL 与真实 Worker/组合根测试。
- Windows Credential Manager 与其余生产凭据后端属 F09；当前生产凭据不可用时 fail closed。
- Sent 文件夹名称按供应商配置（默认 `Sent`）；真实服务器 capability probe 结果需在真实只读验收中确认。
- 收信附件转发仅支持受控 Artifact 引用；`host.mail.account` 只暴露非秘密元数据，普通生产收件人发送需用户显式登记。
