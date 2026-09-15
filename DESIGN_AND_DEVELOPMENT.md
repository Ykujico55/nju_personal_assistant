# 会成长的个人助手：设计与开发文档

> 状态：设计基线已确认（2026-09-15）  
> 产品输入：`Lab1 personal assistant.md`  
> 后端主语言与框架：Python + FastAPI  
> 目标形态：本地优先、单用户、可长期日用的 Beta

## 1. 文档目的与规范用语

本文给出项目从零开始的实现级设计，包括架构边界、目录结构、核心接口、数据模型、状态机、扩展协议、外部系统适配、移动端接入、部署运维、开发步骤和验收标准。产品描述中的命令式文字在本文中只被解释为产品需求，不代表当前立即访问邮箱、操作 ehall 或执行任何外部动作。

本文使用以下规范用语：

- **必须（MUST）**：缺失即不能通过验收。
- **应该（SHOULD）**：除非有记录在案的架构决策（ADR），否则必须采用。
- **可以（MAY）**：可选能力，不影响当前版本验收。
- **失败红线**：一旦触发，本阶段验收立即失败，不得带病进入下一阶段。
- **禁区**：本阶段或首版明确不允许实现、调用或承诺的能力。

## 2. 已确认的产品范围

### 2.1 必须交付的能力

1. **个人资料库**：监测用户授权目录的文件变化，增量索引；检索结果必须定位到原文件、标题/页码/行号及内容版本。
2. **smail 邮箱**：持续、只读地同步新邮件，关联往来和个人资料，生成可编辑草稿；仅发送用户确认的最终版本；重复扫描和进程重启不得造成重复处理或盲目重发。
3. **ehall 办事**：在用户可见、受监督的浏览器中查询本人可用事项，至少完成一种低风险事务的材料准备和表单填写；提交前展示字段差异、附件和后果。
4. **Android 手机联动**：通过 PWA 发起任务、查看状态、编辑草稿、处理确认并接收最小化通知；任务不依赖某个桌面对话窗口持续开启。
5. **能力组合与成长**：业务能力共享最小必要上下文；交付两条可重复使用的完整流程；将成功流程与用户纠正保存为可读、可改、经审核、可回滚的规则或工作流。
6. **扩展基架**：新增业务功能时，只实现扩展接口、提供 Manifest 并注册，不得要求修改 Agent 循环、上下文管理、工具网关或扩展管理器的核心代码。

### 2.2 两条强制端到端验收流程

- **流程 A：综合办事流**  
  邮件通知 → 资料检索 → 材料缺口识别 → 手机补充材料 → ehall 表单准备与填写 → Android 审阅并确认最终提交前预览 →（仅在合法、低风险且确有需要时，再独立确认真实提交）→ 邮件草稿或结果归档。
- **流程 B：邮件回复流**  
  邮件同步 → 历史往来与个人资料检索 → 草稿生成 → 手机编辑 → 精确载荷确认 → 向受控地址真实发送 → 结果归档。

### 2.3 明确非目标

- 多用户 SaaS、组织级权限体系和公有云托管不是首版目标。
- 已安装的本地或第三方扩展均被视为可信代码；首版不隔离恶意扩展，也不宣称 `venv`、子进程或 JSON-RPC 是安全沙箱。
- 首版不允许扩展携带任意前端 JavaScript 或向核心 FastAPI 动态注册任意路由；扩展表单由核心 PWA 根据 JSON Schema 渲染。
- 首版不实现退课、撤销、支付、选课变更、法律声明或其他高后果 ehall 操作，即使用户确认也拒绝执行。
- 不绕过验证码、动态码、扫码登录、二次认证或校园系统安全控制；不逆向并重放 ehall 私有接口。
- 不承诺跨 SMTP、网页系统和进程故障的严格 `exactly-once`；目标是可审计的 `at-least-once` 调度与副作用的 effectively-once 防重。
- 笔记本关机或休眠期间不提供云端代执行；恢复后仅按任务类型补偿。

## 3. 架构原则与系统不变量

### 3.1 核心原则

1. **核心稳定、业务外置**：核心只负责编排、上下文、工具治理、审批、持久化和扩展生命周期；个人资料、邮箱、ehall、通知和组合流程均为扩展。
2. **模型只提议，系统做决定**：模型输出是非可信提案。所有工具参数、权限、风险、预算、审批和状态转移由确定性代码校验。
3. **先持久化，后产生副作用**：外部发送或提交前，任务状态、载荷哈希、审批证据和待执行记录必须已经持久化；审计不可用时 fail closed。
4. **来源优先于摘要**：模型摘要和记忆不能覆盖原始来源。来源改变、删除或哈希不匹配时，相关索引和摘要必须标记失效。
5. **默认最小披露**：上下文只按任务需要组合；敏感内容发往远程模型前必须让用户查看接收方、用途和字段范围。
6. **可恢复而非假装成功**：每个长任务有检查点、租约和明确状态；无法判断外部动作结果时进入 `UNKNOWN`，不得自动重试或伪造成功。
7. **删除能力不破坏历史**：禁用或卸载扩展后，历史事件、审计、产物和数据所有权仍可解释；代码删除与数据清除分开确认。

### 3.2 不变量

- 任何工具调用都必须具有 `task_id`、`run_id`、`tool_id`、`extension_id/version`、调用者、风险级别和幂等键。
- 邮件发送和允许的 ehall 提交只能由核心副作用网关执行。
- 用户编辑审批对象的任一字段后，旧审批立即失效。
- 审批只能原子消费一次，默认 5 分钟过期。
- `UNKNOWN` 状态只能通过对账或人工裁决离开，不能由普通重试器推进。
- 密码、客户端专用密码、访问令牌、Cookie 和私钥不得进入模型上下文、业务数据库、日志、Git、备份或扩展 RPC 返回值。
- 外部内容中的“忽略规则”“调用工具”“代用户确认”等文字始终作为数据处理，不能提升为系统指令。
- 用户配置的普通任务预算可以为空；不可关闭的安全熔断始终生效。

## 4. 系统上下文与部署拓扑

```text
Android PWA
    │ HTTPS / Cloudflare Access（唯一正式用户 origin）
    ▼
Cloudflare Tunnel
    │ cloudflared 出站隧道
    ▼
FastAPI API  ───── SSE / Web Push ─────► Android PWA
127.0.0.1
    │
    ├── Agent Worker ─── ModelRouter ─── Remote / Local Model
    ├── Scheduler / Durable Job Worker
    ├── Extension Supervisor ── JSON-RPC ── Extension Workers
    ├── Tool & Side-effect Gateway ─────── External Systems
    └── PostgreSQL + pgvector（Docker Compose）

Tailscale ──► health-only Sidecar 127.0.0.1:8010 ──► 脱敏健康聚合
              （网络上无法到达主 API 127.0.0.1:8000）
本机控制台 ─► 启停、密钥轮换、灾难恢复
```

### 4.1 两种正式运行配置

| 配置 | 功能支持 | 可用性承诺 | 离线行为 |
|---|---|---|---|
| 常开主机 | 全部能力 | 服务自启动、禁止自动休眠，按日用 Beta 运行 | 异常重启后从检查点恢复 |
| 日常笔记本 | 全部能力 | 仅在线且服务运行时可用 | PWA 显示离线；开机后按 `skip/coalesce/catch_up` 策略补偿 |

两种配置功能等价，但不能声称笔记本关机时仍满足五分钟邮件同步或实时通知。

### 4.2 进程职责

| 进程/组件 | 唯一职责 | 不得承担的职责 |
|---|---|---|
| FastAPI API | HTTP API、Access 身份解析、CSRF/Origin 校验、SSE、PWA 静态资源 | 长时间 Agent 循环、直接执行外部副作用 |
| Agent Worker | 领取任务、恢复检查点、运行有熔断的 Agent 状态机 | 直接读取原始凭据、绕过工具网关 |
| Scheduler | 持久计划、生成到期任务、停机补偿 | 直接发送邮件或点击提交 |
| Tool Gateway | 工具注册、Schema 校验、能力检查、风险分类、审批与幂等 | 自行做开放式规划 |
| Extension Supervisor | 创建 venv、启动/停止 Worker、JSON-RPC、健康检查、排空和切换版本 | 把 `venv` 宣称为恶意代码隔离 |
| Side-effect Broker | 邮件发送、受允许的表单提交、UNKNOWN 对账 | 在结果不明时自动重复执行 |
| PostgreSQL | 真相状态、任务、审批、审计、全文与向量索引 | 保存原始凭据 |

## 5. 建议项目框架结构

```text
personal-assistant/
├── pyproject.toml
├── dependency.lock
├── README.md
├── .env.example
├── .gitignore
├── compose.yaml                     # PostgreSQL + pgvector
├── src/personal_assistant/
│   ├── app.py                       # FastAPI 组装，不放业务逻辑
│   ├── admin_app.py                 # 独立回环管理 API，不经 Tunnel
│   ├── health_app.py                # health-only 最小 ASGI 应用
│   ├── bootstrap.py                 # 依赖注入与启动检查
│   ├── cli/
│   │   ├── main.py
│   │   ├── extensions.py
│   │   ├── recovery.py
│   │   └── kill_switch.py
│   ├── desktop_companion/
│   │   ├── main.py
│   │   ├── session.py
│   │   └── headed_browser.py
│   ├── api/
│   │   ├── middleware/
│   │   │   ├── cloudflare_access.py
│   │   │   ├── csrf_origin.py
│   │   │   ├── idempotency.py
│   │   │   └── request_id.py
│   │   └── v1/
│   │       ├── tasks.py
│   │       ├── approvals.py
│   │       ├── drafts.py
│   │       ├── extensions.py
│   │       ├── knowledge.py
│   │       ├── settings.py
│   │       └── events.py
│   ├── core/
│   │   ├── agent/
│   │   │   ├── engine.py
│   │   │   ├── state_machine.py
│   │   │   ├── checkpoint.py
│   │   │   ├── progress_detector.py
│   │   │   └── safety_fuse.py
│   │   ├── context/
│   │   │   ├── manager.py
│   │   │   ├── composer.py
│   │   │   ├── provenance.py
│   │   │   ├── redaction.py
│   │   │   └── retention.py
│   │   ├── tools/
│   │   │   ├── registry.py
│   │   │   ├── gateway.py
│   │   │   ├── policy.py
│   │   │   ├── schemas.py
│   │   │   └── result_sanitizer.py
│   │   ├── approvals/
│   │   │   ├── service.py
│   │   │   ├── canonicalize.py
│   │   │   └── state_machine.py
│   │   ├── extensions/
│   │   │   ├── manager.py
│   │   │   ├── registry.py
│   │   │   ├── manifest.py
│   │   │   ├── protocol.py
│   │   │   ├── rpc.py
│   │   │   ├── runtime.py
│   │   │   └── lifecycle.py
│   │   ├── models/
│   │   │   ├── provider.py
│   │   │   ├── router.py
│   │   │   └── disclosure_policy.py
│   │   ├── jobs/
│   │   │   ├── queue.py
│   │   │   ├── scheduler.py
│   │   │   ├── lease.py
│   │   │   └── outbox.py
│   │   ├── secrets/
│   │   │   ├── store.py
│   │   │   └── handles.py
│   │   ├── platform/
│   │   │   └── ports.py
│   │   └── audit/
│   │       ├── writer.py
│   │       └── redaction.py
│   ├── domain/                      # 纯领域对象与状态枚举
│   ├── infrastructure/
│   │   ├── database/
│   │   ├── filesystem/
│   │   ├── mail/
│   │   ├── browser/
│   │   ├── notifications/
│   │   ├── platform/
│   │   │   ├── windows/
│   │   │   └── portable_test_doubles/
│   │   └── observability/
│   └── workers/
│       ├── agent_worker.py
│       ├── scheduler_worker.py
│       ├── extension_supervisor.py
│       └── health_sidecar.py
├── extension_sdk/
│   ├── src/personal_assistant_sdk/
│   │   ├── manifest.py
│   │   ├── protocols.py
│   │   ├── rpc.py
│   │   ├── schemas.py
│   │   └── testing.py
│   └── templates/basic_extension/
├── extensions/                     # 与核心隔离的普通扩展包
│   ├── personal_knowledge/
│   │   ├── extension.toml
│   │   ├── pyproject.toml
│   │   ├── schemas/
│   │   ├── migrations/
│   │   ├── src/
│   │   └── tests/
│   ├── smail/
│   ├── ehall/
│   ├── mobile_notifications/
│   └── personal_workflows/
├── web/pwa/                         # 统一 PWA；不加载扩展任意 JS
├── migrations/
├── config/
│   ├── policies.yaml
│   ├── retention.yaml
│   └── workflows/
├── tests/
│   ├── unit/
│   ├── contract/
│   ├── integration/
│   ├── e2e/
│   ├── security/
│   └── fixtures/
├── scripts/
├── docs/
└── var/                             # 运行数据；必须被 Git 忽略
    ├── artifacts/
    ├── extensions/
    ├── backups/
    └── debug/
```

核心包依赖方向必须是 `api、infrastructure → core → domain`。顶层业务 `extensions/` 只能依赖公开 `extension_sdk` 和自身依赖，不能导入核心内部模块；`domain`、`core` 和 SDK 不得反向依赖某个具体业务扩展。

后端、Agent、调度器、扩展 SDK、扩展 Worker、Desktop Companion、迁移和自动化测试均以 Python 为主；FastAPI 是 HTTP 控制面。PWA 只使用浏览器所必需的 HTML/CSS/TypeScript 与 Service Worker，不承载 Agent 编排或业务规则，因此不改变“Python + FastAPI 为主技术栈”的约束。

### 5.1 跨平台端口

首版只对 Windows 完整部署验收，但核心代码保持平台无关。`core.platform.ports` 至少定义 `SecretStorePort`、`ServiceManagerPort`、`DesktopInteractionPort`、`ProcessSupervisorPort`、`FileWatcherPort` 和 `PathPolicyPort`；Windows Credential Manager、服务自启动、交互桌面和进程管理只能出现在 `infrastructure.platform.windows` 适配器中。

核心、SDK 和业务扩展不得直接导入 Win32 API。CI 至少在 Windows 运行完整测试，并在另一平台使用 portable test doubles 运行核心、SDK 与扩展契约测试。增加 Linux/macOS 正式部署时只替换平台适配器，不改 Agent、上下文、工具和扩展协议。

## 6. 扩展系统设计

### 6.1 扩展的定义

扩展是一个独立 Python 包，包含 Manifest、锁定依赖、一个 Worker 入口以及零个或多个扩展槽实现。安装扩展不得修改核心源码、核心数据库表定义或 PWA 路由。

首版扩展槽：

| 槽 | 用途 | 示例 |
|---|---|---|
| `ToolProvider` | 提供可由 Agent 提议调用的工具 | 检索资料、准备邮件草稿 |
| `ContextProvider` | 按查询提供带来源的上下文 | 邮件线程、个人资料片段 |
| `EventSource` | 产生规范化领域事件 | `mail.received`、`file.changed` |
| `WorkflowProvider` | 提供确定性流程骨架和允许的动态节点 | 邮件回复流、办事流 |
| `ScheduleProvider` | 声明持久计划及停机补偿策略 | 每五分钟同步邮箱 |
| `NotificationProvider` | 发送不含敏感正文的通知 | Web Push |
| `MigrationProvider` | 管理扩展自有数据 Schema | smail 游标升级 |
| `FormSchemaProvider` | 提供由核心 PWA 渲染的 JSON Schema | 账户配置、事务字段预览 |

### 6.2 Manifest 最小契约

扩展根目录必须包含 `extension.toml`；TOML 可以由 Python 标准库解析，减少安装器自身的依赖面。示例：

```toml
manifest_version = "1"
id = "nju.smail"
name = "NJU smail"
version = "0.1.0"
core_api = ">=1,<2"
python = ">=3.12,<3.14"
entrypoint = "nju_smail.worker:main"
dependency_lock = "requirements.lock"
config_schema = "schemas/config.json"
state_schema_version = 1
healthcheck = "system.health"

event_sources = ["smail.poll_inbox"]
context_providers = ["smail.thread_history"]
workflows = ["smail.reply_flow"]
schedules = ["smail.poll_every_5m"]
forms = ["smail.account_settings"]

[[tools]]
id = "smail.search"
risk = "READ"
input_schema = "schemas/search-input.json"
output_schema = "schemas/search-output.json"

[[tools]]
id = "smail.prepare_reply"
risk = "INTERNAL_WRITE"
input_schema = "schemas/reply-input.json"
output_schema = "schemas/draft-output.json"

[capabilities]
required = ["mail.read", "artifact.read"]
optional = ["model.remote.sensitive"]
```

Manifest 中的权限是安装提示、路由和审计契约，不是操作系统安全边界。安装第三方扩展即代表用户信任其代码；文档和界面必须明确显示这一点。

### 6.3 Python SDK 协议

```python
from typing import Protocol, Sequence

class Extension(Protocol):
    async def initialize(self, runtime: "RuntimeContext") -> "ExtensionInfo": ...
    async def health(self) -> "HealthReport": ...
    async def drain(self, deadline: float) -> "DrainReport": ...
    async def shutdown(self) -> None: ...

class ToolProvider(Protocol):
    def tools(self) -> Sequence["ToolDescriptor"]: ...
    async def invoke(
        self,
        tool_id: str,
        arguments: dict,
        context: "InvocationContext",
    ) -> "ToolResult": ...

class ContextProvider(Protocol):
    async def retrieve(self, query: "ContextQuery") -> Sequence["Evidence"]: ...

class WorkflowProvider(Protocol):
    def workflows(self) -> Sequence["WorkflowDefinition"]: ...

class EventSource(Protocol):
    def event_sources(self) -> Sequence["EventSourceDescriptor"]: ...
    async def poll(self, request: "PollRequest") -> "PollResult": ...

class ScheduleProvider(Protocol):
    def schedules(self) -> Sequence["ScheduleDefinition"]: ...

class NotificationProvider(Protocol):
    async def deliver(self, request: "NotificationRequest") -> "DeliveryReceipt": ...

class MigrationProvider(Protocol):
    def migrations(self) -> Sequence["MigrationDescriptor"]: ...

class FormSchemaProvider(Protocol):
    def forms(self) -> Sequence["FormSchemaDescriptor"]: ...
```

上述代码是协议形状，不是要求把扩展 import 进核心进程。实际调用通过版本化 JSON-RPC 完成。

`PollResult` 必须包含事件列表、来源去重键和下一游标；核心成功持久化事件后才能提交游标。`ScheduleDefinition` 必须声明时区及 `skip/coalesce/catch_up`。通知返回可对账回执且不得自行扩大内容等级。迁移只能操作扩展自己的 `ext_*` Schema。表单只提供 JSON Schema、UI Schema 和字段敏感等级，不包含可执行 JavaScript。

### 6.4 JSON-RPC 边界

请求必须包含：

```json
{
  "jsonrpc": "2.0",
  "id": "call_01...",
  "method": "tool.invoke",
  "params": {
    "protocol_version": "1",
    "extension_id": "nju.smail",
    "extension_version": "0.1.0",
    "task_id": "task_01...",
    "run_id": "run_01...",
    "tool_id": "smail.search",
    "arguments": {},
    "deadline": "2026-09-15T12:00:00Z",
    "idempotency_key": "...",
    "context_handles": [],
    "artifact_handles": []
  }
}
```

约束：

- RPC 只传 JSON、`ArtifactHandle`、`ContextHandle` 和短期能力句柄，不传 Python 对象、数据库连接或宿主绝对路径。
- 大正文和附件经受管 Artifact Store 传递，RPC 只传引用和内容哈希。
- 每个调用必须有 deadline、取消语义、最大输出尺寸和结构化错误码。
- 扩展不得返回原始异常堆栈给用户；Supervisor 保存脱敏诊断并映射成 `RETRYABLE`、`PERMANENT`、`NEEDS_USER_ACTION` 或 `UNKNOWN`。

### 6.5 扩展生命周期

```text
DISCOVERED → STAGED ───────────────→ INSTALLED_DISABLED
               └── user reject/fail → REJECTED

INSTALLED_DISABLED ── enable ──→ STARTING ── healthy ──→ ENABLED
                                     └── failure ──────→ QUARANTINED
QUARANTINED ── repair/acknowledge ─────────────────────→ INSTALLED_DISABLED

ENABLED ── disable ──→ DRAINING ── drained ──→ DISABLED
DISABLED ── re-enable ────────────────────────→ STARTING
DISABLED ── uninstall ─→ UNINSTALLING ───────→ UNINSTALLED

ENABLED/DISABLED ── upgrade ─→ UPGRADING ── success ─→ STARTING(new)
                                      └── failure ────→ ROLLED_BACK(old)
```

安装流程：

1. 用户选择本地目录、Wheel 或 Git 精确 revision；系统只把制品复制/下载到不可执行的暂存区，并计算来源与内容哈希。
2. 仅以数据方式静态解析 Manifest，校验核心 API 范围、Python 范围、依赖锁及重复扩展 ID；不得 import、build、安装依赖或启动代码。
3. 在执行任何第三方构建/安装脚本前，向用户展示来源、版本、哈希、扩展槽、工具风险、声明能力以及“安装即信任代码”的警告，并取得显式确认。
4. 确认后才在暂存区创建独立 venv、构建/安装锁定依赖；禁止后台从任意 URL 自动安装或自动升级。
5. 启动临时 Worker，完成握手、健康检查和扩展契约测试。
6. 原子写入 `INSTALLED_DISABLED`；只有用户启用后才注册能力。用户拒绝或任一步失败时，仅删除暂存制品和未启用 venv，不执行扩展代码。

启用、禁用和卸载：

- 启用时构造不可变 Registry Snapshot；所有槽位要么一次性生效，要么全部不生效。
- 禁用先停止派发新任务，再进入 `DRAINING`；到期未结束的调用被取消并保存检查点。
- 禁用后撤销工具、事件源和计划任务注册，但历史调用仍以扩展 ID/版本可追溯。
- 卸载必须先禁用并检查依赖；默认删除代码和 venv，保留扩展业务数据及 tombstone。
- 永久清除数据是独立操作，必须预览影响、二次确认并留下不含正文的审计记录。
- 升级采用新 venv 并行安装、迁移前备份、契约测试、排空旧版本、原子切换；失败自动回滚。

### 6.6 “接入新功能无需改核心”的验收方式

提供一个仓库外测试扩展 `example.weather_stub`，仅通过 SDK 实现一个工具、一个上下文源、一个计划任务和一个 Schema 表单。验收必须证明：

1. 不修改 `src/personal_assistant/core`、核心 API 或核心数据库迁移即可安装。
2. 启用后 Agent 能发现其工具，PWA 能显示配置表单，Scheduler 能创建任务。
3. 禁用后不再产生新调用；卸载后代码消失、历史记录可读、业务数据默认保留。
4. 装回相同或兼容版本后可以重新关联保留的数据。

若必须在核心中新增 `if extension_id == ...`、业务路由或业务表，本项验收失败。

## 7. 主 Agent 循环

### 7.1 任务与运行状态

`Task` 表示用户目标，`TaskRun` 表示一次可恢复的执行。任务状态如下：

| 状态 | 含义 | 允许的下一状态 |
|---|---|---|
| `CREATED` | 请求已校验但未入队 | `QUEUED`、`CANCELLED` |
| `QUEUED` | 等待 Worker 租约 | `RUNNING`、`CANCELLED` |
| `RUNNING` | Agent 正在主动执行 | 等待态、暂停态、终态 |
| `WAITING_USER` | 等待补充资料或选择 | `QUEUED`、`CANCELLED` |
| `WAITING_APPROVAL` | 等待精确副作用确认 | `QUEUED`、`WAITING_USER`、`CANCELLED` |
| `WAITING_RECONCILIATION` | 外部动作结果不明，只允许只读核对或人工裁决 | `QUEUED`、`WAITING_USER`、`CANCELLED` |
| `PAUSED_SAFETY` | 安全熔断触发 | 用户检查后创建新的运行片段或取消 |
| `PAUSED_EXTENSION` | 所需扩展禁用、故障或升级 | 恢复扩展后 `QUEUED`，或取消 |
| `SUCCEEDED` | 目标和必要归档已完成 | 无 |
| `FAILED` | 已知失败，且无安全自动恢复路径 | 人工创建重试运行 |
| `CANCELLED` | 用户或管理员取消 | 无 |

每次状态转移必须通过数据库版本号做 compare-and-swap，并同时写入领域事件。进程内状态不得作为真相源。

### 7.2 循环算法

```python
async def run_task(task_id: UUID) -> None:
    lease = await queue.acquire(task_id)
    run = await repository.resume_or_create_run(task_id, lease)

    # keepalive 独立续租，不能等长模型/工具调用结束才 heartbeat。
    async with lease.keepalive(), lease.release_on_exit():
        while run.state == RUNNING:
            await safety_fuse.check(run)
            snapshot = await checkpoint_store.load(run)
            context = await context_manager.compose(snapshot)

            proposal = await model_router.propose(
                objective=run.objective,
                context=context,
                allowed_tools=tool_registry.snapshot(run),
            )
            decision = await deterministic_validator.validate(proposal, run)

            if decision.needs_user_input:
                await checkpoint_and_transition(run, WAITING_USER, decision.question)
                return
            if decision.is_complete:
                await finalize_and_transition(run, SUCCEEDED)
                return

            outcome = await tool_gateway.invoke(decision.tool_call, run)

            if outcome.kind == "APPROVAL_REQUIRED":
                await checkpoint_and_transition(run, WAITING_APPROVAL, outcome.approval_id)
                return
            if outcome.kind == "USER_ACTION_REQUIRED":
                await checkpoint_and_transition(run, WAITING_USER, outcome.message)
                return
            if outcome.kind == "EXTENSION_UNAVAILABLE":
                await checkpoint_and_transition(run, PAUSED_EXTENSION, outcome.extension_id)
                return
            if outcome.kind == "OUTCOME_UNKNOWN":
                await checkpoint_and_transition(
                    run, WAITING_RECONCILIATION, outcome.reference_id
                )
                return
            if outcome.kind == "SAFETY_PAUSE":
                await checkpoint_and_transition(run, PAUSED_SAFETY, outcome.reference_id)
                return

            await observation_store.append(run, outcome.result)
            await progress_detector.record(run, decision, outcome.result)
            await checkpoint_store.save(run)
```

模型不能直接修改任务状态、生成有效审批、读取 Secret Store、执行 RPC 或决定自身权限。`deterministic_validator` 必须拒绝未知工具、Schema 不合法参数、超出工作流允许集合的动作以及由外部内容诱导的权限提升。

`ToolGateway` 返回类型化 outcome，而不是只抛通用异常。长模型和工具调用期间由独立 keepalive 续租；退出时只释放当前 owner 仍持有的租约。`OUTCOME_UNKNOWN` 在动作状态机中保存事实后暂停，不能作为普通观察继续规划。

### 7.3 预算与不可关闭的熔断

普通任务默认不设置步数、Token、费用和总工具调用数上限；用户可在全局、工作流或单任务层设置更严格的预算。无论普通预算如何设置，下列安全熔断不可关闭：

- 每个工具必须声明超时；普通工具默认 60 秒，浏览器等待类工具可以放宽，但单次不得超过 15 分钟。
- 同一规范化工具调用在没有状态或证据变化时最多重复 3 次。
- 连续 5 次错误或连续 8 轮无可证明进展后进入 `PAUSED_SAFETY`。
- 单个运行片段主动执行最长 24 小时；等待用户、审批或计划时间不计入主动执行。
- 用户取消、全局 Kill Switch、凭据撤销和扩展排空信号必须在当前工具返回或超时后生效。
- 邮件发送和 ehall 提交从不由通用重试器重试。

用户选择“继续”时必须看到暂停原因、最近调用和预计后果；继续操作创建新的运行片段并保留原审计链，不得清除历史来规避循环检测。

### 7.4 进展判定

以下至少一项变化才算有进展：

- 任务状态或工作流节点改变；
- 获得新的、有来源且与目标相关的证据；
- 产生新版本产物、草稿或材料清单；
- 解决一个已记录的缺口或用户问题；
- 外部只读状态发生经哈希验证的变化。

只改变自然语言措辞、重复读取相同内容、重复调用相同参数或生成相同哈希结果均不算进展。

## 8. 上下文管理

### 8.1 上下文分层

| 层 | 内容 | 权威性 | 生命周期 |
|---|---|---|---|
| Policy | 系统安全策略、禁止动作、审批规则 | 最高，不可由模型覆盖 | 随版本 |
| Workflow | 当前流程定义、节点约束、验收目标 | 高 | 任务/版本 |
| Approved Rules | 用户审核并启用的纠正规则 | 高，受作用域限制 | 直到撤销 |
| Task Working Set | 当前目标、状态、用户回答、产物引用 | 中 | 任务期间及归档 |
| Retrieved Evidence | 文件、邮件、页面的原文片段与来源 | 取决于来源 | 随来源版本 |
| Episodic Summary | 历史任务摘要和经验候选 | 低，不得覆盖来源 | 可压缩/删除 |
| Tool Observation | 工具返回、错误、DOM 或调试信息 | 最低且不可信 | 默认短期 |

`ContextManager` 负责检索候选，`ContextComposer` 负责按模型上下文窗、最小披露和任务目的组装。任何外部文本都必须带数据边界标记，不能拼接进 system/developer 指令区域。

### 8.2 来源模型

每个 `Evidence` 至少包含：

- `source_uri`：文件 URI、邮箱账户/文件夹/UID 或 ehall 页面标识；
- `locator`：标题、段落、行号、页码、邮件 Message-ID 或字段路径；
- `content_hash` 与 `source_version`；
- `observed_at`、`valid_from`、可选 `valid_until`；
- `producer_extension_id/version`；
- `sensitivity`：`PUBLIC`、`PERSONAL`、`SENSITIVE`、`SECRET`；
- `trust`：用户原文、官方页面、外部来信、模型生成或推断；
- `derived_from`：摘要、分块和规则的完整上游引用。

源文件内容或哈希变化后，旧分块、向量和摘要先标记 `STALE`，重建成功后再原子切换；删除源文件后不得继续把缓存内容表述为现行事实。

### 8.3 远程模型披露

`DisclosurePolicy` 在每次远程调用前计算数据类别：

1. `PUBLIC` 可以按任务需要发送。
2. `PERSONAL` 可以在已存在、未过期且作用域匹配的用户许可下发送。
3. `SENSITIVE` 必须展示模型提供方、用途、字段摘要和脱敏预览，由用户单次允许或授予有限期限/作用域的许可。
4. `SECRET` 始终拒绝，不提供“仍然发送”按钮。

拒绝远程披露时，路由器尝试本地模型；若本地模型能力不足，任务进入 `WAITING_USER` 并解释缺失能力，不得悄悄扩大披露范围。

### 8.4 纠正和规则成长

规则状态为：

```text
CANDIDATE → USER_REVIEWED → TESTED → ACTIVE → RETIRED
```

- Agent 只能生成候选，不能自动激活。
- 规则必须声明作用域，如 `mail.deadline_language`、某联系人或某工作流；默认不允许全局作用域。
- 测试至少包含触发样例、反例和一次历史回放。
- 激活规则保存为可读 YAML/Markdown，并由 Git 记录版本；敏感原文只能替换为脱敏 fixture。
- 回滚后新任务使用旧版本；进行中任务继续使用其启动时记录的规则快照，除非用户显式重启任务。

### 8.5 保留策略

| 数据 | 默认保留 |
|---|---|
| 临时 DOM、截图、浏览器 trace、调试输出 | 7 天；默认不开启高敏调试 |
| 模型请求摘要、工具调用过程、脱敏诊断 | 30 天 |
| 结构化审批与副作用审计 | 1 年 |
| 原始个人资料 | 由用户源目录控制 |
| 正式草稿、材料包、回执 | 由用户显式删除或归档策略控制 |
| 凭据、Cookie、令牌 | 不进入上述存储或备份；在专用凭据/会话存储中单独过期 |

## 9. 工具、能力与审批管理

### 9.1 工具描述符

每个工具必须注册不可变 `ToolDescriptor`：

```python
class ToolDescriptor(BaseModel):
    id: str
    version: str
    extension_id: str
    input_schema: dict
    output_schema: dict
    risk: Literal["READ", "INTERNAL_WRITE", "EXTERNAL_WRITE", "PROHIBITED"]
    required_capabilities: set[str]
    timeout_seconds: int
    retry_policy: Literal["NONE", "IDEMPOTENT_ONLY"]
    data_classes_in: set[str]
    data_classes_out: set[str]
```

风险含义；文中也分别简称为 R0–R3：

- R0 / `READ`：纯计算或不改变外部及用户可见状态的读取，可在权限与速率限制内自动执行。
- R1 / `INTERNAL_WRITE`：只写入助手内部、可撤销的草稿、索引或任务状态，执行后审计。
- R2 / `EXTERNAL_WRITE`：邮件发送、可能触发服务器保存的表单填值或允许的最终提交；必须走 prepare/approve/commit。
- R3 / `PROHIBITED`：首版不可执行，不能通过修改 Manifest 或用户点击临时放行。

### 9.2 工具网关检查顺序

1. Registry Snapshot 中存在完全匹配的工具 ID 和版本。
2. 参数通过 JSON Schema，且不存在未知字段或超限正文/附件。
3. 当前工作流允许该工具，调用扩展处于 `ENABLED`。
4. 调用者具备声明能力；敏感上下文披露符合许可。
5. 速率、并发、普通预算和安全熔断允许执行。
6. 风险策略允许；`PROHIBITED` 立即拒绝。
7. `EXTERNAL_WRITE` 必须匹配未过期、未消费的精确审批。
8. 写入调用意图和审计后才交给执行器。
9. 结果按 Schema 校验、大小限制和不可信内容规则清洗后写入观察记录。

扩展契约要求外部副作用全部经过网关。由于扩展被视为可信且首版无 OS 沙箱，系统只能对守约扩展提供这一保证；恶意或被入侵的已安装扩展可能绕过网关，属于已接受的安全边界之外。

### 9.3 审批状态机

```text
DRAFT
  │ prepare + canonicalize
  ▼
PREPARED ──► WAITING_APPROVAL ──► APPROVED ──► EXECUTING
                   │                 │              ├──► SUCCEEDED
                   ├──► EXPIRED      ├──► EXPIRED   ├──► FAILED
                   └──► CANCELLED    └──► CANCELLED └──► UNKNOWN
```

审批快照必须包含：动作类型、目标账户、收件人或 ehall 应用、主题/正文或完整字段、附件名称与哈希、任务、工具、扩展 ID/版本、规范化载荷哈希、创建时间、5 分钟过期时间和随机 nonce。

用户通过 Cloudflare Access 会话打开核心审批页并点击确认。系统不使用独立应用登录或 Passkey；因此审批能防止误操作、重放和确认后载荷漂移，但不能抵御被盗的 Cloudflare/浏览器会话。该剩余风险是经确认的产品边界，不得在安全说明中隐去。

服务器必须验证 Cloudflare Access JWT 的签名、issuer、audience 和有效期，并对状态修改请求执行 CSRF token 与严格 Origin 校验；不能只信任客户端可伪造的身份 Header。

审批消费与 `EXECUTING` 转移必须在同一数据库事务中完成。任何字段、附件、目标、扩展版本或页面表单版本改变后，都必须重新 prepare 和审批。

### 9.4 外部动作结果不明

执行状态必须区分：

- `SUCCEEDED`：获得可验证的服务器接受响应或唯一回执。
- `FAILED`：确定没有产生目标副作用，且错误已分类。
- `UNKNOWN`：请求可能已经到达外部系统，但客户端未得到可验证结果。

`UNKNOWN` 的处理方式只能是：查询 Sent/流程跟踪进行对账，或让用户人工裁决。自动重发、重新点击或把超时映射为 `FAILED` 均为失败红线。

### 9.5 凭据边界

- 原始凭据保存在 Windows 操作系统凭据库；数据库只保存不可逆引用 ID 和元数据。
- Agent、模型、PWA、任务 payload、JSON-RPC 和普通扩展配置不得出现原始凭据。
- 邮件和浏览器副作用由核心代理执行；扩展获得短期 `SecretHandle`/`SessionHandle` 或调用面向业务的代理方法。
- 句柄必须绑定扩展、工具、账户、允许操作和有效期，不能序列化进长期上下文。
- ehall 浏览器认证状态如确需短期复用，应本机加密、短 TTL、可撤销且不进入 Git/备份。
- 因无 OS 沙箱，上述限制是核心 API 和守约扩展边界，不能阻止恶意扩展自行读取当前用户可访问的系统资源。

## 10. 模型能力管理

`ModelProvider` 隔离厂商 SDK：

```python
class ModelProvider(Protocol):
    async def capabilities(self) -> "ModelCapabilities": ...
    async def generate(self, request: "ModelRequest") -> "ModelResponse": ...
    async def embed(self, request: "EmbeddingRequest") -> "EmbeddingResponse": ...
```

`ModelRouter` 根据结构化输出、工具规划、上下文长度、隐私等级、可用性和成本选择提供方。首版至少包含一个真实远程适配器和一个真实本地适配器；不得让业务扩展直接依赖厂商 SDK。

每次模型调用记录提供方、模型标识、参数摘要、规则/上下文快照 ID、Token/费用（若可得）、披露许可 ID 和结果哈希。不得用模型名判断安全性；数据披露由独立策略决定。

## 11. 持久化与任务队列

### 11.1 存储基线

PostgreSQL + pgvector 是唯一正式真相源；首版不引入 Redis。它承载：

- 任务、运行、检查点、计划、租约和死信；
- 扩展安装、版本、注册槽、配置引用和生命周期；
- 上下文元数据、文档版本、全文索引、分块与向量；
- 草稿、审批、动作尝试、outbox、审计和保留策略；
- 邮箱同步游标、邮件元数据和 ehall 事务元数据。

原始用户文件仍留在授权目录；系统生成的附件、材料包和回执放在 content-addressed Artifact Store。数据库和 RPC 均只保存 artifact ID、哈希、媒体类型、大小和访问策略。

### 11.2 核心表与约束

| 表组 | 关键表 | 必要约束 |
|---|---|---|
| 任务 | `tasks`, `task_runs`, `checkpoints`, `observations` | 状态版本、运行片段、不可变观察哈希 |
| 队列 | `jobs`, `job_attempts`, `schedules`, `dead_letters` | 幂等键、租约到期、计划名义时间唯一键 |
| 扩展 | `extensions`, `extension_versions`, `extension_slots`, `extension_data` | 扩展 ID/版本唯一；`extension_data` 只存简单元数据/墓碑，复杂数据位于独立 `ext_*` Schema |
| 知识 | `documents`, `document_versions`, `chunks`, `embeddings` | URI+版本唯一、来源哈希、embedding 模型/维度 |
| 上下文 | `context_items`, `rule_versions`, `model_consents` | 来源链、敏感度、作用域和有效期 |
| 副作用 | `drafts`, `approvals`, `action_attempts`, `outbox` | 载荷版本、nonce 唯一、审批一次性消费 |
| 邮件 | `mail_accounts`, `mail_folders`, `mail_messages`, `mail_sync_cursors` | 账户+文件夹+UIDVALIDITY+UID 唯一 |
| ehall | `ehall_apps`, `ehall_transactions`, `form_snapshots` | 页面/表单版本、字段哈希、回执唯一 |
| 运维 | `audit_events`, `artifacts`, `retention_runs` | 追加式审计、内容寻址、删除证明 |

数据库迁移由核心和扩展自有迁移分开管理。扩展不得直接写核心表，只能使用 SDK 仓储接口或自身命名空间数据；核心升级先备份、再迁移、再健康检查，失败必须停止启动并提供回滚说明。

### 11.3 PostgreSQL 持久队列

- Worker 使用 `FOR UPDATE SKIP LOCKED` 领取到期任务，并写入带 owner、deadline 和 heartbeat 的租约。
- Worker 崩溃后，reaper 回收过期租约；任务按策略重新排队或进入死信。
- 队列只承诺 `at-least-once`；所有可重试内部操作必须幂等。
- 任务 payload 只存 ID/引用，不复制秘密、大正文或附件。
- 状态变更和新任务/事件通过 transactional outbox 在同一事务写入，reconciler 修复发布中断。
- 计划任务以 `(schedule_id, nominal_run_at)` 唯一，避免恢复后重复生成。

停机补偿策略：

| 任务类型 | 恢复策略 |
|---|---|
| 邮箱轮询 | `coalesce`：立即做一次最新增量检查 |
| 文件索引 | `coalesce`：扫描当前差异，不重放每次文件事件 |
| 定期备份 | `skip + latest`：跳过过期次数，立即做一次最新备份 |
| 一次性内部任务 | `catch_up`：从检查点恢复 |
| 邮件发送/ehall 提交 | 永不由 Scheduler 或通用队列自动重放 |

### 11.4 检索索引

首版使用 PostgreSQL 全文检索和 pgvector 精确余弦检索，再做确定性的融合排序。只有基准测试证明精确扫描不满足指标时才引入近似索引；向量行必须记录 embedding provider、model、dimension、归一化方式、源内容哈希和生成时间，以支持旁路重建和原子切换。

## 12. FastAPI 与移动端 API

所有正式接口位于 `/api/v1`，通过 OpenAPI 固化契约。命令型请求必须携带 `Idempotency-Key`；资源更新使用版本字段或 `If-Match` 防止手机和 Worker 覆盖彼此修改。

扩展代码安装、升级、卸载、永久清除和核心配置变更属于本机管理员操作：管理 API 必须监听独立回环端口或本机 IPC，且不能出现在 `cloudflared` 或 Tailscale 路由中。远程 PWA 可以查看扩展状态；代码级变更只能在本机管理界面或 CLI 发起。启用/禁用是否开放给远程 PWA 由部署策略控制，默认也仅限本机。

| 方法与路径 | 用途 | 风险/约束 |
|---|---|---|
| `POST /tasks` | 创建任务 | 幂等；只创建，不在请求线程运行 Agent |
| `GET /tasks/{id}` | 查看状态、缺口、产物 | 按敏感字段过滤 |
| `POST /tasks/{id}/messages` | 补充信息或回答问题 | 乐观并发控制 |
| `POST /tasks/{id}/cancel` | 取消任务 | 写审计；不能回滚已发生副作用 |
| `GET /events` | SSE 状态流 | 支持 `Last-Event-ID` 断线续传 |
| `GET/PUT /drafts/{id}` | 查看/编辑草稿 | 编辑产生新版本并使旧审批失效 |
| `POST /actions/{id}/prepare` | 生成副作用预览 | 不执行外部动作 |
| `POST /approvals/{id}/approve` | 单次确认 | Access 身份、CSRF、Origin、5 分钟 TTL |
| `POST /approvals/{id}/reject` | 拒绝确认 | 原因可选，立即终止该动作 |
| `GET /extensions` | 查看扩展及健康状态 | 不返回秘密配置 |
| `POST /extensions/install` | 暂存扩展 | 本机管理员操作、来源与哈希审计 |
| `POST /extensions/{id}/enable` | 启用并注册槽位 | 原子 Registry Snapshot |
| `POST /extensions/{id}/disable` | 排空并禁用 | 展示受影响任务 |
| `POST /extensions/{id}/upgrade` | 旁路安装、测试并切换版本 | 必须有升级前恢复点；异步 operation |
| `POST /extensions/{id}/rollback` | 回退到已保留的兼容版本 | 校验数据迁移兼容性；异步 operation |
| `DELETE /extensions/{id}` | 删除代码 | 默认保留业务数据 |
| `POST /extensions/{id}/purge-data` | 永久清除数据 | 独立影响预览与二次确认 |
| `GET /extension-operations/{operation_id}` | 查询安装/启停/升级/卸载进度 | 返回持久状态与安全诊断，不返回秘密 |
| `GET /healthz` | 本机/编排健康 | 不含敏感信息 |
| `GET http://127.0.0.1:8010/healthz` | 独立 health-only Sidecar；不属于 `/api/v1` | Tailscale 只映射此端口；不提供管理、任务或审批数据 |

本机 CLI 必须覆盖同一状态机：`assistantctl extension install|status|enable|disable|upgrade|rollback|uninstall|purge`。CLI 不直接修改数据库，而是调用 Local Admin API 并轮询 operation ID，保证 CLI 与管理界面不会形成两套实现。

统一错误结构：

```json
{
  "error": {
    "code": "APPROVAL_PAYLOAD_CHANGED",
    "message": "草稿已修改，请重新确认。",
    "request_id": "req_...",
    "retryable": false,
    "details": {}
  }
}
```

错误响应不得包含堆栈、SQL、凭据、Cookie、完整邮件正文或页面快照。

PWA 页面至少包括任务列表/详情、对话与缺口、草稿编辑器、审批预览、资料来源、扩展状态、模型披露许可、设备/通知设置和健康状态；本机管理界面另提供扩展安装与生命周期操作。Service Worker 只缓存版本化静态资源；审批页、邮件正文、表单数据和 API 响应必须使用 `no-store`。

## 13. 具体业务扩展设计

所有项目自带业务能力也必须经过与第三方扩展相同的 Manifest、SDK、RPC、注册和生命周期流程，不能走“内置后门”。

### 13.1 `personal_knowledge`：个人资料扩展

#### 能力注册

- `EventSource: knowledge.file_changes`
- `ContextProvider: knowledge.retrieve`
- `ToolProvider: knowledge.search`
- `ToolProvider: knowledge.reindex`
- `ScheduleProvider: knowledge.reconcile`
- `FormSchemaProvider: knowledge.roots`

#### 数据流

```text
授权目录 → 文件事件 + 周期性全量对账 → 内容哈希
       → 格式抽取 → 结构化定位 → 分块
       → PostgreSQL FTS + pgvector
       → 混合检索 → Evidence + 原文引用
```

实现要求：

- 用户显式配置一个或多个允许根目录；所有路径在读取前解析并确认仍位于根目录内，拒绝路径穿越和越界符号链接。
- 文件监听只用于降低延迟，周期性 reconciliation 才是正确性保障；事件丢失不能导致永久漏索引。
- 原文件是权威来源且默认只读。系统生成内容只写入受管 Artifact 目录。
- 抽取器返回统一的 `ExtractedDocument`，包含文本、标题层级、页码/段落/行号映射、媒体类型和抽取器版本。
- 同一来源的新版本在独立批次构建索引；完成并校验后才切换 `active_version`，避免查询到半套索引。
- 答案引用必须在展示前重新核对来源哈希；无法核对时标记“来源已变化”，不能伪造稳定引用。
- 删除源文件时保留最小审计 tombstone，但删除正文、分块和向量；任何由其派生的摘要进入 `STALE`。

首版至少支持 Markdown、纯文本和 PDF；其他格式通过新的 Extractor/Context 扩展增加，不修改知识扩展的索引管线。

### 13.2 `smail`：南京大学学生邮箱扩展

南京大学公开配置说明给出的学生邮箱地址为 `学号@smail.nju.edu.cn`，推荐客户端使用 IMAP/SMTP；当前公开服务器配置为 IMAP SSL `imap.exmail.qq.com:993`、SMTP SSL `smtp.exmail.qq.com:465`，登录名为完整邮箱地址。客户端应使用用户在网页端生成的客户端专用密码，而不是复用网页登录密码。实现前仍必须进行只读 capability probe，不硬编码 IDLE、认证机制或发送限额。参见[南京大学学生邮箱客户端配置办法](https://itsc.nju.edu.cn/1a/8f/c21586a334479/page.htm)和[南大邮箱系列问答](https://itsc.nju.edu.cn/96/2e/c21475a497198/page.htm)。

#### 能力注册

- `EventSource: smail.poll_inbox`
- `ContextProvider: smail.thread_history`
- `ToolProvider: smail.search`
- `ToolProvider: smail.prepare_reply`
- `WorkflowProvider: smail.reply_flow`
- `ScheduleProvider: smail.poll_every_5m`

SMTP 发送不由扩展直接持有密码并建立任意连接。扩展向核心 `MailTransportBroker` 提交规范化 `MailEnvelope`；Broker 使用 OS 凭据库中的账户句柄建立 TLS 连接并执行已审批动作。正常扩展接口永不返回客户端专用密码。

#### 绑定与同步

1. PWA 创建非秘密账户元数据；客户端专用密码经本机设置流程写入 Windows 凭据库。
2. Broker 使用 TLS 默认信任库验证证书和主机名，完成只读 capability probe。
3. 默认每五分钟轮询，也允许用户手动触发；选择邮箱文件夹时必须使用只读模式，不自动标记已读、移动或删除邮件。
4. 同步游标至少由账户、文件夹、`UIDVALIDITY` 和 UID 构成，辅以 `Message-ID` 和规范化内容哈希。
5. 邮件事件与新游标在核心数据库事务中持久化后，才认为该批同步完成。
6. Worker 重启、同一批重复返回或停机后补查，只能生成一个规范化 `mail.received` 事件。

#### 草稿与发送

- 草稿是版本化 Artifact；收件人、抄送/密送、主题、正文、附件或任一附件哈希变化都会产生新版本。
- 发送确认必须展示上述完整字段和附件清单，审批只绑定一个草稿版本。
- 每次发送生成稳定的本地动作 ID 和邮件 `Message-ID`，但不能把 `Message-ID` 当成 SMTP 提供的强幂等保证。
- SMTP 接受部分收件人时必须记录部分成功/拒收明细，不能把整体简单标记为成功。
- 在提交邮件数据后断网或超时且无法确定服务器状态时，动作进入 `UNKNOWN`；先只读核对 Sent 文件夹，再由用户裁决，禁止盲目重发。
- 真实验收只向用户控制的测试地址发送；批量发送、自动转发全部邮件和无人确认发送均为禁区。

### 13.3 `ehall`：受监督办事扩展

南京大学网上办事大厅是统一身份认证后的动态 Web 入口；公开的能力开放平台对接有校内单位、项目和网络等准入要求，个人项目不能默认获得官方 API。因此首版按受监督网页适配设计，不宣称得到 ehall 官方自动化授权。参见[南京大学网上办事大厅指南](https://guide.nju.edu.cn/faq/33/07/c44791a537351/pagem.htm)和[南京大学能力开放平台](https://itsc.nju.edu.cn/nlkfpt/listm.htm)。

#### 组件拆分

- 核心 `BrowserSessionBroker` 持有临时浏览器会话和加密后的短期会话句柄，不向扩展返回 Cookie。
- `ehall` 扩展只提供应用发现、字段映射、材料清单、风险分类和事务工作流。
- 具体事务通过 `EhallTransactionAdapter` 扩展接口实现；新增事务不得修改主 Agent 或 BrowserSessionBroker。
- Windows 后台服务不能承担需要用户看见并完成扫码/验证码的交互。系统必须提供在登录用户桌面会话中运行的 Python Desktop Companion；需要认证时任务进入 `WAITING_USER`，由 Companion 打开 headed 浏览器。

#### 首次事务选择

1. 用户从 PWA 发起“发现可用事项”。
2. Desktop Companion 打开可见浏览器，用户自行完成账号密码、扫码、验证码和动态验证。
3. 首次探测只允许导航和读取本人可见应用，禁止提交、保存或调用私有 XHR。
4. 系统展示应用名称、页面路径和初步风险；用户选择一种低风险事项。
5. 开发者据此实现事务适配器、字段 Schema、页面版本探测和测试 fixture。

#### 表单与提交规则

- 操作前必须探测“填写、下一步、暂存”是否会触发服务器写入；不确定时一律按 `EXTERNAL_WRITE`。
- 每个字段记录旧值、新值、来源 Evidence、置信度和验证结果；附件记录名称、大小和 SHA-256。
- 填写后停在最终提交前，向用户展示完整差异、缺失材料、风险和提交后果。
- 普通低风险提交仍需要独立审批；邮件与 ehall 不能共用一个审批。
- 退课、撤销、支付、选课变更和法律声明类动作硬编码为 `PROHIBITED`，Manifest 和用户点击均不能降级。
- 页面新增字段、按钮语义变化、出现费用/承诺/撤销等高风险文字、选择器失配或页面版本未知时立即停止。
- 提交后必须取得回执或流程编号；结果不明时只查询流程跟踪，禁止再次点击。
- 禁止自动填写统一认证秘密、识别或破解验证码、截取二维码、绕过二次认证、隐藏浏览器、逆向或重放私有接口。

### 13.4 `mobile_notifications`：PWA 与通知扩展

PWA 通过固定的 `assistant.<用户域名>` 访问，Cloudflare Tunnel + Access 是唯一正式用户入口；FastAPI 只监听 `127.0.0.1`。Cloudflare TLS 在边缘终止，业务内容进入其信任边界，这是经确认的产品取舍。Tunnel 的出站连接模型见[Cloudflare Tunnel 官方文档](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/)。

- 前台状态更新使用 SSE，并以持久事件 ID 支持断线续传。
- Android 后台提醒使用标准 Web Push；通知只包含“有待处理事项”、风险级别和非敏感任务 ID。
- 点击通知只能打开 PWA 对应页面，不能直接批准、发送或提交。
- PWA 不提供独立账户、密码、Passkey 或应用 MFA；核心验证 Cloudflare Access JWT，并实施 CSRF 与 Origin 防护。
- Service Worker 不缓存邮件正文、表单、审批、模型上下文或敏感 API 响应。
- Cloudflare 不可用时，Tailscale 只暴露无敏感信息的只读健康检查；重启、密钥轮换和恢复操作只能在本机控制台完成。

### 13.5 `personal_workflows`：组合流程扩展

工作流采用声明式有向图，只引用能力 ID、输入/输出 Schema、暂停点、补偿规则和风险要求，不直接访问 IMAP、SMTP、浏览器、数据库或文件系统。

每个运行固定 Registry Snapshot、规则版本和模型策略。扩展升级不会改变已开始任务；旧版本不可用时进入 `PAUSED_EXTENSION`，不能静默切换到语义不同的新版本。

工作流 A 和 B 必须分别拥有自己的 fixture、状态机回放和端到端验收报告。综合流程必须在 ehall 最终预览生成后回到 Android 明确确认“预览正确并停在提交前”；若满足真实提交条件，提交本身还需新的动作审批。ehall 提交与邮件发送是两个独立动作，分别 prepare、展示和确认。

## 14. 安全设计与威胁边界

### 14.1 数据分类

| 级别 | 示例 | 处理规则 |
|---|---|---|
| D0 公开 | 公共帮助、公开校园说明 | 可正常处理 |
| D1 内部元数据 | 任务 ID、运行状态、风险级别 | 可用于最小化通知 |
| D2 个人敏感 | 邮件正文、个人资料、表单字段、附件 | 远程模型外发需许可；展示和存储最小化 |
| D3 秘密 | 密码、Token、Cookie、私钥、恢复密钥 | 不进入模型、Git、普通数据库、日志或备份 |

模型披露许可与外部副作用审批必须分离：允许模型读取邮件不等于允许发送；批准发送也不等于允许把内容交给另一模型。

### 14.2 威胁与控制

| 威胁 | 控制 | 明确剩余风险 |
|---|---|---|
| 互联网未授权访问 | Cloudflare Access allowlist；验证 JWT；FastAPI 回环监听 | Cloudflare/身份账户失陷 |
| 代理身份头伪造 | 只信任通过密码学验证的 Access JWT | 本地主机失陷后无保证 |
| Access 会话被盗 | Secure/HttpOnly/SameSite Cookie、CSRF、Origin、短时单次审批 | 无应用二次认证，攻击者仍可能创建并批准动作 |
| Prompt Injection | 指令/数据分层、Schema 提案、确定性策略、能力白名单 | 模型仍可能生成错误建议，但正常路径不能越权 |
| XSS/恶意邮件 HTML | 默认纯文本、严格清洗、CSP、不允许扩展注入 JS | 浏览器或清洗器漏洞 |
| 重复发送/提交 | 审批原子消费、动作状态机、幂等记录、`UNKNOWN` 禁止重试 | 外部系统与本地 DB 无分布式原子性 |
| 凭据泄漏 | OS 凭据库、核心代理、结构化日志白名单、备份排除 | 恶意同权限扩展仍可能读取宿主资源 |
| 恶意第三方扩展 | 安装时展示来源/哈希/权限；禁止无人值守升级 | 安装即信任；无恶意代码隔离保证 |
| 源变化后的陈旧引用 | 内容哈希、版本、展示前复核、派生项失效 | 回答后来源再次被外部修改 |
| 备份泄漏/不可恢复 | 加密离机副本、秘密排除、恢复演练 | 用户遗失备份密钥 |

### 14.3 Prompt Injection 强制规则

- 邮件、附件、网页 DOM、个人文件、模型输出和工具错误都是不可信数据。
- 外部文本不能安装/启用扩展、授予权限、批准规则、放宽模型披露、改变风险级别或生成有效审批。
- 模型只能输出类型化 `ActionProposal`；核心用当前 Registry Snapshot、工作流和策略重新判断。
- 标准扩展不得暴露任意 Shell、任意 URL 请求、原始 SQL 或浏览器自由点击工具。
- ehall 浏览器仅允许官方 origin、已选事务和动作白名单；页面文字不能改变 `PROHIBITED` 集合。
- RAG 片段必须放入明确的数据边界并保留来源，不能原样拼入高优先级提示。

### 14.4 审计

审计事件至少记录 UTC 时间、correlation ID、Access subject/本机操作者、task/run/action、扩展 ID/版本/制品哈希、工具、参数哈希、数据类别、模型及披露许可、策略版本、审批 ID、结果和外部回执摘要。

审计使用字段白名单，禁止记录 D3 数据或默认保存完整 D2 正文。事件追加写入并保存前一事件哈希，以发现普通意外修改；不能宣称哈希链可抵抗取得本机同等权限的攻击者。

### 14.5 本地总停机开关

本机管理员必须可以在不启动 Agent 的情况下执行：

1. 冻结 Side-effect Broker；
2. 暂停 Scheduler 和新任务领取；
3. 进入扩展排空，超时后终止 Worker；
4. 关闭 Cloudflare Tunnel；
5. 查看不含秘密的最近动作和 UNKNOWN 清单。

Kill Switch 不删除数据、不把 `EXECUTING` 改成 `FAILED`，也不自动撤回已经发生的外部动作。

## 15. 部署、可观测性与恢复

### 15.1 Windows-first 部署单元

- Python API、Agent Worker、Scheduler、Extension Supervisor 和 Desktop Companion 原生运行在 Windows。
- PostgreSQL + pgvector 由 Docker Compose 管理；数据库只绑定本机，不开放到 LAN 或公网。
- `cloudflared` 作为自启动服务连接固定域名；FastAPI 只绑定回环地址。
- 用户 API、Local Admin API 和 health-only Sidecar 使用三个独立回环监听端口；`cloudflared` 只路由用户 API，Tailscale 只路由 health-only 端口，Local Admin 端口不进入任一隧道。
- 常开配置将后台进程注册为开机自启动服务；笔记本配置允许手动启动，但两者使用同一配置 Schema 和数据格式。
- headed ehall 浏览器只能在登录用户的 Desktop Companion 中运行；后台服务不能伪装成交互会话。
- 核心定期原子写出只含总体状态、版本和更新时间的 D1 健康快照；health-only Sidecar 只读取该受限快照，不加载业务路由、数据库仓储或管理依赖。Tailscale ACL 只允许指定设备访问该独立端口；所有其他路径返回 404，且不得映射用户 API 或核心管理 API。

### 15.2 配置分层

配置优先级为：不可变安全默认 → 项目配置 → 主机配置 → 用户设置 → 任务级收紧。低层配置只能收紧 `PROHIBITED`、秘密处理和紧急熔断，不能放宽。

正式配置中只保存 Secret Reference；`.env.example` 只能列字段名和假值，`.env` 不是正式凭据方案。启动时执行配置 Schema、数据库版本、扩展兼容、凭据引用、Artifact 路径和 Cloudflare audience 检查，失败则拒绝启动相关能力。

### 15.3 健康与可观测性

健康分为：

- **liveness**：进程事件循环仍能响应；
- **readiness**：数据库、Registry Snapshot 和必要迁移可用；
- **capability health**：具体扩展、模型、邮箱或浏览器连接状态；
- **side-effect readiness**：审计、审批和 Broker 均可用，才允许外部写入。

结构化日志、指标和 trace 默认只保存在本机并按数据分类脱敏，不启用第三方遥测。至少监测队列延迟、租约回收、任务状态、熔断、扩展重启、模型用量、待审批数量、UNKNOWN、邮箱同步延迟、索引新鲜度、数据库空间和备份年龄。

日志或观测系统不可用时，只读操作可按策略降级；审批审计或副作用审计不可用时，所有外部写入 fail closed。

### 15.4 备份策略

- 每 6 小时生成一个一致恢复点：数据库备份、上次恢复点后新增/改变的受管 Artifact、扩展 Manifest/版本清单和非秘密运行配置共同进入加密恢复包；迁移、扩展数据升级和核心升级前额外生成恢复点。
- 每个 6 小时恢复点都必须在同一周期复制到不同设备或加密云端；离机复制失败时健康状态降级并告警，不能继续宣称满足 6 小时灾难 RPO。
- 每日合并一个完整恢复包，并保留 7 个日备份、4 个周备份、6 个按月备份。
- 为恢复任务、审批、邮件历史和正式产物所必需的 D2 数据必须进入加密恢复包；D3 密码、Token、Cookie、私钥、恢复密钥和可重建临时调试输出不得进入。
- Git 仓库单独管理，只含代码、Manifest、流程、规则和脱敏 fixture，不能代替业务数据恢复包。
- 用户授权目录中的原始个人文件由用户持有，默认不复制到助手恢复包；必须在运维文档中为这些目录配置单独备份。RPO/RTO 指标只覆盖助手管理的数据库和 Artifact，不能把丢失的原文件从索引中“恢复”出来。
- 向量索引可以重建，但恢复包必须包含源引用、内容哈希、抽取版本和 embedding 模型标识。
- 每月在隔离目录执行一次自动恢复演练；正式目标为 RPO ≤ 6 小时、RTO ≤ 2 小时。
- 凭据不备份，恢复后按运行手册重新绑定邮箱、Cloudflare 和 ehall 会话。

### 15.5 升级与回滚

核心升级顺序：冻结新任务 → 排空 Worker → 创建备份 → 验证迁移计划 → 升级代码 → 迁移 → 健康检查 → 恢复调度。失败时保持副作用 Broker 冻结并回滚代码/数据库；没有可用备份时禁止执行破坏性迁移。

第三方扩展永不无人值守升级。扩展采用旁路 venv 安装和健康检查，旧版本排空后原子切换；迁移不可逆且没有已验证恢复点时拒绝升级。

## 16. 分阶段开发路线与门禁

以下阶段必须顺序通过。每个阶段的“能力边界”规定本阶段允许系统做到哪里；“失败红线”一旦触发必须修复后重验；“禁区”不是待优化项，而是本阶段明确不允许触碰的操作。

### 阶段 0：需求基线、仓库与安全测试环境

#### 实施步骤

1. 初始化 Git，建立主分支保护、提交规范、`.gitignore` 和秘密扫描。
2. 创建 `pyproject.toml` 与锁文件，固定 Python 支持范围和可重复安装方式。
3. 配置格式化、静态检查、类型检查、单元测试和 CI；任何真实凭据不得出现在 CI。
4. 为每项产品要求分配稳定 ID，建立需求—设计—测试追踪表。
5. 写入 ADR：本地单用户、Windows-first、Cloudflare 主入口、Access-only、PostgreSQL/pgvector、可信扩展、无普通默认预算等已确认取舍。
6. 建立威胁模型和 D0–D3 数据分类。
7. 创建脱敏个人资料、模拟 IMAP/SMTP、模拟 ehall 页面、远程/本地模型 Stub 和两条流程 fixture。

#### 能力边界

只允许文档、Schema、测试夹具和模拟器。系统不得连接真实邮箱、真实 ehall、远程模型或外部通知服务。

#### 验收条件

- 每条产品要求均映射到一个或多个设计章节和测试 ID。
- 新环境可从锁文件完成离线或受控依赖安装；测试命令有唯一入口。
- Git、fixture、CI 变量和测试日志的 D3 扫描结果为零。
- 两条强制流程均可在模拟器上描述完整输入、状态和期望输出。

#### 失败红线

- 外部副作用、扩展信任边界、Access-only 剩余风险或数据保留存在互斥解释。
- 测试依赖真实个人数据或真实账户才能运行。
- 未锁定依赖、无法重复创建开发环境。

#### 禁区

- 不得使用真实邮箱密码、ehall Cookie、Access Token 或未脱敏邮件作为 fixture。
- 不得为了“先跑起来”跳过 Git 和需求追踪。
- 不得在此阶段访问或修改任何真实外部系统。

### 阶段 1：核心骨架、数据库与持久任务内核

#### 实施步骤

1. 创建第 5 节目录结构和依赖方向检查。
2. 建立 FastAPI 工厂、配置加载、请求 ID、统一错误、liveness/readiness。
3. 用迁移工具建立任务、运行、检查点、Job、Schedule、Event、Outbox、Audit 基础表。
4. 实现 PostgreSQL 事务边界、乐观锁、`SKIP LOCKED` 领取、租约、心跳、回收和死信。
5. 实现 `skip/coalesce/catch_up` 计划补偿及名义运行时间去重。
6. 建立 content-addressed Artifact Store，先支持本地文件系统后端。
7. 实现结构化日志白名单和不可关闭的基础熔断计数器。

#### 能力边界

只能执行确定性的内部测试任务和调度；不接入模型、业务扩展、邮件、浏览器或外部副作用。

#### 验收条件

- API、Agent Worker、Scheduler 可分别启动；停止任一进程不破坏数据库状态。
- 两个测试 Worker 并发领取时，同一租约只能有一个有效 owner。
- 在领取前、检查点前后和 outbox 发布前后强杀进程，任务均能恢复到确定状态。
- 停机跨过多个计划点后，三种补偿策略结果与定义一致。
- 数据库迁移能在空库和上一版本快照上成功运行；失败时保持旧版本可用。
- 日志中不存在 payload 全文、凭据字段或未过滤异常堆栈。
- 核心和 SDK 使用 portable test doubles 在非 Windows CI 上通过；静态依赖检查证明 Win32 导入只存在于 Windows 平台适配器。

#### 失败红线

- 恢复依赖进程内内存，或重启后丢失任务。
- lease 过期会把已开始的外部动作重新排队。
- 任务状态和 outbox 事件可能出现一方提交、一方丢失且无 reconciler。
- 审计写入失败却允许未来外部写入路径继续。

#### 禁区

- 不得使用内存队列作为真相源。
- 不得引入 Redis 作为关键状态存储。
- 不得在 HTTP 请求中同步运行长任务。
- 不得注册任何真实发送或提交任务。

### 阶段 2：扩展 SDK、Supervisor 与生命周期

#### 实施步骤

1. 定义 `extension.toml` Schema、语义版本兼容和制品哈希规则。
2. 发布独立、只含协议模型的 `personal_assistant_sdk` 包。
3. 实现 JSON-RPC 帧、握手、deadline、取消、错误映射和 Artifact Handle。
4. 实现每扩展独立 venv、Worker 启停、健康检查和崩溃退避。
5. 实现安装、启用、Registry Snapshot、排空、禁用、旁路升级、回滚、卸载和独立数据清除。
6. 实现扩展自有 PostgreSQL Schema 和受限迁移入口。
7. 创建仓库外示例扩展与自动契约测试套件。
8. 建立管理 CLI 和持久化管理 API；所有生命周期操作返回 operation ID。

#### 能力边界

扩展安装后被视为可信；`venv + Worker` 只提供依赖和故障隔离。本阶段只使用无外部副作用的示例扩展；PWA 仅渲染 Schema 表单，不执行扩展任意 JS。

#### 验收条件

- 示例扩展只靠 SDK、Manifest 和安装命令接入，核心源码零修改。
- Manifest 与握手中的扩展 ID、版本、能力和 Schema Hash 不一致时拒绝启用。
- 扩展依赖冲突和进程崩溃不带崩 FastAPI 或 Agent Worker。
- 启用时能力原子出现；失败时不产生半注册状态。
- 禁用先停止新派发，再排空在途调用；超时调用有明确 outcome。
- 升级失败自动恢复旧版本和旧 Registry Snapshot。
- 卸载删除代码及 venv，历史审计可读，业务数据默认保留；重新安装兼容版本可重连数据。

#### 失败红线

- 新增一个合规业务能力仍需修改 Agent 循环、核心路由或核心数据库迁移。
- FastAPI/Agent 直接 import 扩展包。
- 禁用或卸载留下活动计划、可调用工具或孤儿 Worker。
- 把权限 Manifest、venv 或普通子进程宣传为恶意代码安全边界。

#### 禁区

- 不得无人值守安装任意 URL 或自动升级第三方扩展。
- 不得允许扩展修改核心或其他扩展数据库 Schema。
- 不得把原始秘密放入扩展配置或 RPC。
- 不得支持运行中 Python 真热卸载；必须排空并重启 Worker。

### 阶段 3：模型适配、Agent 循环与上下文

#### 实施步骤

1. 实现 `ModelProvider`、一个真实远程适配器、一个真实本地适配器和模型 Stub。
2. 实现持久化 Agent 状态机、结构化 `ActionProposal` 和确定性 Validator。
3. 实现 ContextItem、来源链、检索、压缩、失效和引用渲染。
4. 实现 D0–D3 分类、远程模型披露预览、单次/限时许可和本地模型降级。
5. 实现 progress detector 与“3 次同参无进展、5 次连续错误、8 轮无进展、24 小时主动运行”安全熔断。
6. 实现候选规则状态机，但暂不允许激活到真实业务流程。
7. 创建邮件、网页、附件、个人文件和工具错误中的 Prompt Injection 测试语料。

#### 能力边界

Agent 只能调用模拟的 R0/`READ` 工具；不能发送邮件、填写真实表单、安装扩展或修改外部系统。

#### 验收条件

- 任一 Agent 轮次完成检查点后强杀 Worker，重启能继续且不重复已确认内部步骤。
- 未知工具、错误 Schema、工作流外动作和模型伪造审批均在执行前被拒绝。
- D2 内容无有效许可时不会进入远程模型请求；D3 内容不存在外发路径。
- 拒绝远程披露后能够切到本地模型；能力不足时进入 `WAITING_USER`，不静默外发。
- 外部内容中的越权指令不能改变 Policy、Registry、规则状态或可用工具。
- 3 次同参无进展、5 次连续错误、8 轮无进展和 24 小时主动运行均产生正确暂停事件。

#### 失败红线

- 模型可以直接调用 OS、RPC、数据库或外部 API。
- 模型自述“已确认”“有进展”即可通过确定性检查。
- 摘要丢失上游引用，或来源改变后旧摘要仍被当作当前事实。
- 敏感数据在没有匹配许可时发送到远程模型。

#### 禁区

- 不得让模型批准规则、模型披露、扩展安装或自身权限。
- 不得把未经裁剪的 HTML、DOM、整封邮件或工具输出拼入高优先级指令。
- 不得提供任意 Shell、任意 URL、原始 SQL 或自由浏览器点击工具。

### 阶段 4：工具网关、审批、凭据与副作用模拟器

#### 实施步骤

1. 实现 Registry Snapshot、ToolDescriptor、Schema 校验和 R0–R3/四级风险策略。
2. 实现 Secret Store、Secret/Session Handle 与核心邮件/浏览器代理的模拟版本。
3. 实现 prepare/approve/commit、规范化载荷、SHA-256、附件哈希、nonce、五分钟 TTL 和单次消费。
4. 实现版本/ETag 并发控制；任何编辑自动作废相关审批。
5. 实现动作状态机、transactional outbox、幂等键、`UNKNOWN` 和人工对账入口。
6. 实现本机 Kill Switch 和 side-effect readiness 健康检查。
7. 在调用前、请求写出后、收到响应前、成功响应后但落库前四个位置加入故障注入测试。

#### 能力边界

所有外部系统均为模拟器；可以验证副作用协议，但不得连接真实 SMTP 或 ehall。

#### 验收条件

- 一百个并发审批请求最多一个成功消费 nonce。
- 收件人、正文、附件、字段、目标、扩展版本或策略改变后旧审批全部失效。
- `PROHIBITED` 工具即使有伪造或历史审批也无法进入执行器。
- 四个故障点产生可证明的 `FAILED` 或 `UNKNOWN`，没有自动重试。
- Access subject、动作哈希、审批和结果形成完整审计链，审计中不含秘密或默认完整正文。
- Kill Switch 冻结新副作用但不改写 `EXECUTING/UNKNOWN` 的事实状态。

#### 失败红线

- 未经有效审批的外部调用能够到达模拟器。
- 双击、重放、过期审批或并发竞争造成重复执行。
- `UNKNOWN` 被映射成普通失败并进入自动重试。
- 原始凭据出现在模型、数据库业务表、日志、Git、备份或 RPC。

#### 禁区

- 不得使用任务级、会话级或“批准后续所有操作”的笼统审批。
- 不得让业务扩展直接修改审批或动作记录。
- 不得在审计不可用时降级执行外部写入。

### 阶段 5：个人资料扩展

#### 实施步骤

1. 实现授权根目录配置、路径规范化、事件监听和周期性 reconciliation。
2. 实现 Markdown、纯文本、PDF 抽取及稳定定位映射。
3. 实现内容哈希、版本化分块、FTS、pgvector 和融合排序。
4. 实现旁路重建、原子版本切换、移动/删除传播和派生摘要失效。
5. 实现可验证引用渲染、受管 Artifact 输出和知识扩展契约测试。
6. 建立黄金查询集，覆盖精确事实、跨文件关联、同名冲突、陈旧来源和删除来源。

#### 能力边界

只读取用户授权根目录；原件只读。生成内容进入受管目录。此阶段检索结果可以供模拟任务使用，不接入真实邮件或 ehall。

#### 验收条件

- 新增、修改、移动、删除和漏失文件事件均能通过 reconciliation 得到正确索引。
- 每个答案证据可定位到文件、标题/页码/行号和内容哈希。
- 展示引用前发现哈希变化时，不返回伪稳定引用并触发重建。
- 删除源文件后正文、分块、向量和派生摘要按策略失效。
- 黄金查询集 `Recall@5 ≥ 0.90`，且引用正确率为 100%；未找到证据时明确回答未知。
- 索引重建不修改源文件，查询不会看到半完成版本。

#### 失败红线

- 回答无法定位原文，或引用坐标与显示片段不一致。
- 来源已变化/删除，系统仍把旧片段标为当前事实。
- 读取授权根之外的路径或跟随越界符号链接。

#### 禁区

- 不得把个人资料、索引正文或向量提交 Git。
- 不得静默修改、移动或删除用户原文件。
- 不得用模型生成内容代替缺失的来源证据。

### 阶段 6：smail 只读同步、关联与草稿

#### 实施步骤

1. 实现核心 `MailTransportBroker` 的 IMAP TLS 只读操作和 Windows 凭据库绑定。
2. 实现 smail 扩展的 capability probe、文件夹发现、UID 游标、五分钟轮询和手动同步。
3. 实现 `UIDVALIDITY + UID` 主去重以及 `Message-ID + 内容哈希` 辅助去重。
4. 将邮件、线程、联系人和附件元数据转成带来源及敏感等级的 ContextItem/Artifact。
5. 实现历史往来检索、个人资料关联、草稿生成和版本化编辑。
6. 实现停机后合并补查、凭据失效退避和 `NEEDS_USER_ACTION`。

#### 能力边界

允许连接真实 smail，但只执行只读 IMAP 和内部草稿写入。SMTP Broker 尚未开放发送；不自动标记已读、移动、删除、归档或转发邮件。

#### 验收条件

- 首次绑定能探测服务器能力；不能确认的能力采用安全降级而非硬编码。
- 同一批邮件连续同步两次、Worker 重启一次、停机后补查一次，仍只产生一个事件和一个草稿候选。
- 读取前后服务端已读、移动、删除和文件夹状态不变。
- 邮件线程和附件来源可回到邮箱账户、文件夹、UID/Message-ID 与内容哈希。
- 撤销客户端专用密码后进入 `NEEDS_USER_ACTION`，不会高频重试或锁定账户。
- Prompt Injection 邮件不能改变策略、注册能力、激活规则或触发发送。

#### 失败红线

- 重复同步产生重复任务、重复草稿副作用或丢失游标。
- 只读阶段改变邮箱状态。
- 邮箱客户端专用密码进入数据库、模型、日志、RPC、Git 或备份。
- 服务限流或认证失败时无限重试。

#### 禁区

- 不得启用自动发送、批量转发、自动删除或移动邮件。
- 不得假设服务器支持 IMAP IDLE 或某一认证机制。
- 不得把邮件正文中的指令视为用户授权。

### 阶段 7：受控 SMTP 发送

#### 实施步骤

1. 启用核心 SMTP Broker，读取和发送权限分别配置。
2. 定义规范化 `MailEnvelope`、附件 Artifact、稳定本地动作 ID 和 Message-ID。
3. 将草稿版本接入 prepare/approve/commit 状态机。
4. 实现服务器响应、部分收件人拒收、连接中断和 Sent 文件夹只读对账。
5. 将真实发送限制为用户登记的受控测试地址，完成后再允许普通收件人逐次确认。
6. 实现发送结果归档和手机端 UNKNOWN 处置页面。

#### 能力边界

可以向受控测试地址真实发送单封邮件；不支持批量营销、规则驱动自动发送或无人值守发送。普通生产收件人必须在 Beta 验收后由用户显式开启。

#### 验收条件

- 没有有效审批时，SMTP 连接/发送代码路径不可达。
- 手机编辑任一字符、收件人或附件后，旧审批立即失效。
- 审批快照与实际发送的收件人、主题、正文和附件字节哈希完全一致。
- 并发确认、重复请求和 Worker 重启不会导致本地二次派发。
- 部分拒收被记录为部分结果，而不是整体成功。
- 在 SMTP DATA 前、期间和之后注入断网，系统正确区分未执行、失败和 UNKNOWN；UNKNOWN 不自动重发。

#### 失败红线

- 未确认、使用旧确认或确认内容与实际发送内容不一致。
- 对 UNKNOWN 自动重试或把超时断言为“未发送”。
- 只要一个收件人成功就隐藏其他收件人失败。
- 发送触发后审计记录缺失。

#### 禁区

- 不得以“用户此前同意过”为由复用审批。
- 不得自动补发关机期间错过的发送任务。
- 不得为测试向非受控第三方地址发送。

### 阶段 8：受监督 ehall 适配器

#### 实施步骤

1. 实现 Desktop Companion 与核心间的本地短期会话，以及 headed BrowserSessionBroker。
2. 建立 ehall 官方 origin 白名单、导航/读取动作集和登录暂停点。
3. 由用户在可见浏览器中完成统一认证、扫码、验证码或动态验证。
4. 首次只读发现本人可用应用，展示后由用户选择一种低风险事务。
5. 为该事务实现独立 Adapter、页面版本探测、字段 Schema、材料清单和风险分类。
6. 实现旧值/新值/来源/附件差异、提交前预览和页面漂移 fail closed。
7. 先在模拟页面完成全流程，再在真实系统完成提交前填写；确有合法低风险需求时，另行验收一次真实提交。

#### 能力边界

标准验收止于真实页面的最终提交前。普通低风险提交必须满足确有办理需要、页面版本已知、风险策略允许和新鲜审批。R3 禁止动作永远不可执行。

#### 验收条件

- 未由用户亲自在官方页面完成认证时，扩展不能进入业务页面。
- 扫码、验证码和动态码只能由用户完成，系统不识别、保存、破解或绕过。
- 首次发现阶段只导航和读取；任何可能自动保存的操作均先按外部写入阻止。
- 目标事务能生成完整材料清单、字段差异、附件哈希和后果说明，并可靠停在提交前。
- 页面元素缺失、新增未知字段、业务名称/版本变化或出现高风险文字时立即暂停。
- 若执行允许的真实提交，审批绑定 origin、应用、表单版本和完整载荷；提交后保存唯一回执。
- 提交响应丢失时只查询流程跟踪，绝不自动再次点击。

#### 失败红线

- 执行退课、撤销、支付、选课变更或法律声明动作。
- 逆向/重放私有 XHR，或把其包装为“官方 API”。
- 自动输入统一认证密码、绕过验证码或隐藏用户应看见的浏览器操作。
- 没有字段差异和后果预览即提交，或 UNKNOWN 后重复提交。

#### 禁区

- 不得为了验收制造不需要的真实校园事务。
- 不得把浏览器 Cookie、认证状态、敏感 trace 或截图提交 Git。
- 不得让通用 Agent 根据按钮文字自由点击；每个写动作必须来自事务 Adapter 的允许集合。

### 阶段 9：Android PWA、Cloudflare 主入口与通知

#### 实施步骤

1. 实现任务、状态、对话/缺口、草稿、审批、来源、扩展和设置页面。
2. 实现 SSE、断线续传、乐观并发和离线状态；Service Worker 只缓存静态资源。
3. 配置用户固定域名、Cloudflare Tunnel 和 Access allowlist；实现 Access JWT 严格验证。
4. 实现 CSRF token、Origin 校验、CSP、点击劫持防护和安全 Cookie。
5. 实现 Android Web Push、订阅撤销和最小化锁屏内容。
6. 启动独立 health-only Sidecar 端口，配置 Tailscale 只映射该端口；所有恢复性变更只留在本机 CLI。
7. 在蜂窝网络、家庭/校园 Wi-Fi、网络切换、Cloudflare 中断和主机离线情况下验收。

#### 能力边界

Cloudflare Access 是唯一远程身份层；不实现应用账户、Passkey 或应用内 MFA。Tailscale 只提供不含敏感信息的健康状态，不能管理任务、扩展、审批或凭据。

#### 验收条件

- Android 可经固定域名创建任务、查看进度、编辑草稿和完成单次审批。
- 无 JWT、签名错误、issuer/audience 不匹配、过期 JWT 和伪造普通身份头均被拒绝。
- CSRF、跨 Origin 请求、恶意邮件 HTML 和点击劫持测试不能改变状态。
- PWA 的并发编辑通过版本冲突提示处理，不静默覆盖 Worker 或另一设备修改。
- 推送只显示存在事项、风险等级和非敏感任务 ID，点击不能直接执行副作用。
- 浏览器缓存和 Service Worker 存储中没有邮件正文、表单、审批或模型敏感响应。
- 主机离线时界面明确显示离线，不把请求表示为已排队或完成。
- Tailscale 端点只能返回最小健康码且无法执行任何变更。
- 从 Tailscale 对用户 API、管理 API、任务、审批和任意非 `/healthz` 路径做黑盒访问时，必须全部不可达或返回 404。

#### 失败红线

- FastAPI 监听公网/LAN 地址或通过路由器端口映射暴露。
- 仅信任可伪造 Header 而不验证 Access JWT。
- 敏感正文出现在锁屏通知或缓存。
- Tailscale 健康入口可访问核心管理或审批 API。

#### 禁区

- 不得使用公开 Funnel 代替 Cloudflare Access。
- 不得宣称 Access-only 可以抵御有效浏览器会话被盗。
- 不得在通知按钮中直接批准、发送或提交。
- 不得让扩展向 PWA 注入任意 JS 或自定义核心路由。

### 阶段 10：组合工作流与可审核成长

#### 实施步骤

1. 用声明式工作流实现流程 A 和流程 B，明确节点输入/输出、能力契约、暂停点和补偿策略。
2. 将邮件、资料、ehall 和通知事件映射为规范化领域事件。
3. 实现材料缺口提问、手机回答、草稿/表单版本传播和独立审批。
4. 实现候选规则生成、作用域编辑、正反例测试、用户启用、Git 提交和回滚。
5. 实现成功流程版本化；每次运行固定工作流、规则、扩展和模型策略快照。
6. 对两条流程执行模拟、故障注入和受控真实验收。

#### 能力边界

组合层只能编排注册能力，不能直接读取秘密、访问 IMAP/SMTP、操纵浏览器、写数据库业务表或改变风险策略。规则只能由用户审核后激活。

#### 验收条件

- 流程 A 完成：邮件通知 → 资料证据 → 材料缺口 → 手机补充 → 真实 ehall 提交前填写 → Android 确认最终预览 → 结果归档；若实际提交，则另建审批。
- 流程 B 完成：邮件 → 历史/资料检索 → 草稿 → Android 编辑 → 精确确认 → 受控 SMTP 真实发送 → 归档。
- ehall 与邮件分别生成审批，不能用一次点击授权两个副作用。
- 中途重启任一 Worker，流程从检查点继续且不重复已完成副作用。
- 用户纠正形成候选规则；经审核和测试后能在新任务中生效，并能通过 Git 回滚。
- 运行期间禁用所需扩展会暂停任务；重新启用兼容版本后可安全继续。

#### 失败红线

- 为组合流程绕过工具网关、审批、模型披露或来源验证。
- 自动激活一次性纠正，或将局部纠正无审核地提升为全局规则。
- 运行中静默切换扩展、规则或工作流版本。
- 一项审批触发多个不同外部动作。

#### 禁区

- 不得把两条业务流程硬编码进主 Agent 循环。
- 不得因“跨能力”而共享与任务无关的完整邮箱或个人资料。
- 不得将成功流程保存为包含真实凭据或敏感原文的 Git 文件。

### 阶段 11：运维硬化、恢复与 Beta 发布

#### 实施步骤

1. 为常开主机与笔记本配置编写安装、启动、停止、升级和卸载脚本。
2. 配置 Windows 自启动、Docker 数据卷、`cloudflared`、日志轮换、保留清理和磁盘预警。
3. 实现每 6 小时的数据库与增量 Artifact 加密恢复点及离机复制、每日完整合并，以及 7 日/4 周/6 月保留。
4. 编写并演练数据库恢复、凭据重绑、UNKNOWN 对账、扩展回滚、Cloudflare 故障和磁盘耗尽 Runbook。
5. 完成 24 小时连续运行、主机休眠/恢复、断网、外部限流和进程崩溃演练。
6. 关闭 P0/P1 缺陷，生成需求追踪、测试、威胁模型和恢复演练报告。

#### 能力边界

发布目标仍是本地单用户 Beta，不扩展为多用户服务。常开与笔记本模式功能相同，但笔记本离线期间无持续可用性承诺。

#### 验收条件

- 在隔离的新环境从恢复包完成恢复，数据损失不超过 6 小时，服务在 2 小时内恢复。
- 恢复包不含 D3 数据；恢复后明确要求重新绑定凭据和会话。
- 离线恢复后邮箱/索引合并补查，备份补做一次，发送/提交不补放。
- 核心和扩展升级前有恢复点；失败演练能回到旧代码、旧 Registry 和一致数据。
- 连续运行期间没有丢任务、重复副作用、无限错误循环或未处理的租约泄漏。
- 运维人员可以用本机 Kill Switch 冻结副作用、暂停调度并查看 UNKNOWN 清单。
- 没有未关闭的 P0/P1 缺陷；已知剩余风险均在发布说明中出现。

#### 失败红线

- 备份从未实际恢复验证，或无法满足 RPO/RTO。
- 备份包含密码、Token、Cookie、私钥或未批准的完整敏感 trace。
- 服务重启、主机恢复或升级造成邮件/ehall 动作自动重放。
- 迁移破坏扩展数据且没有回滚路径。
- 安全说明隐去 Access-only、Cloudflare 边缘或可信扩展的剩余风险。

#### 禁区

- 不得无人值守升级第三方扩展、模型适配器或破坏性数据库 Schema。
- 不得在没有已验证恢复点时执行不可逆迁移。
- 不得把 Beta 描述为多用户生产系统或恶意扩展安全平台。

## 17. 测试策略与质量门禁

### 17.1 测试层级

1. **单元与性质测试**：状态转移、规范化哈希、Schema、风险策略、路径边界、索引失效和调度补偿。
2. **扩展契约测试**：Manifest、握手、JSON-RPC、超时、取消、错误 outcome、迁移及生命周期。
3. **组件集成测试**：PostgreSQL、Artifact Store、模拟 IMAP/SMTP、模拟 ehall 和模型 Stub。
4. **恢复与故障注入测试**：进程 kill、租约过期、断网、数据库短暂不可用、响应丢失和磁盘不足。
5. **安全测试**：Prompt Injection、CSRF、XSS、JWT 伪造、路径穿越、审批重放和秘密扫描。
6. **受控真实测试**：真实 smail 只读同步、测试地址 SMTP、真实 ehall 提交前填写、Android 远程访问。
7. **运维测试**：备份恢复、升级回滚、扩展排空、休眠恢复和保留清理。
8. **端到端验收**：流程 A、流程 B 及一次跨会话纠正规则复用。

模型输出具有非确定性，因此测试不得依赖一字不差的自然语言。验收应验证结构化状态、必需事实、来源引用、允许/拒绝的工具、审批载荷和副作用结果。

### 17.2 关键测试矩阵

| 测试域 | 必测场景 | 通过条件 |
|---|---|---|
| Agent 状态机 | 每种状态重启、非法转移、并发领取 | 无非法转移；只有一个有效租约；可恢复 |
| 安全熔断 | 同参调用、连续错误、无进展、24 小时边界 | 精确进入 `PAUSED_SAFETY` 并通知 |
| 扩展契约 | ID/版本/Hash 不符、RPC 超时、崩溃 | 拒绝或隔离故障；核心继续可用 |
| 扩展生命周期 | 安装、启用、排空、升级、回滚、卸载、重装 | 核心零业务修改；无孤儿注册；数据语义正确 |
| 工具网关 | 未知工具、错误 Schema、禁用扩展、越权能力 | 全部在执行前拒绝 |
| 审批 | 双击、重放、过期、编辑、扩展升级、策略变化 | 至多执行一次；旧审批全部失效 |
| UNKNOWN | 外部调用四个崩溃点 | 不自动重试；仅进入核对/人工处置 |
| Prompt Injection | 邮件标题/正文/附件、网页隐藏文本、个人文件、错误消息 | 不改变 Policy/Registry/审批/规则状态 |
| 模型披露 | D2 无许可、许可过期、作用域不符、D3 | D2 被暂停；D3 始终拒绝 |
| 凭据 | Git、日志、DB 导出、模型记录、RPC、备份扫描 | D3 零命中 |
| 资料索引 | 新增、修改、移动、删除、漏文件事件、重建中查询 | 当前版本一致；引用可核验；旧项失效 |
| 邮箱同步 | 重复批次、UID 变化、重启、离线补查 | 一封邮件一个事件，不改变服务端状态 |
| 邮件发送 | 编辑后确认、附件变化、部分拒收、网络中断 | 只发确认版本；结果分类准确；无盲目重发 |
| ehall 策略 | R3 文案、未知字段、页面漂移、验证码 | 硬拒绝或暂停；不绕过认证 |
| ehall 提交 | 提交前预览、双击、响应丢失 | 精确确认；取得回执或 UNKNOWN；不重复 |
| Android PWA | 蜂窝/Wi-Fi、后台恢复、并发编辑、主机离线 | 状态真实；冲突可见；敏感数据不缓存 |
| Cloudflare | 无 JWT、错签名、错 issuer/audience、过期、伪造 Header | 全部拒绝 |
| 调度恢复 | 离线跨过多次计划时间 | 按类别合并/跳过/恢复；不补放副作用 |
| 备份 | 全新隔离环境恢复 | RPO/RTO 达标；凭据需重绑；数据一致 |
| 流程 A | 邮件→资料→缺口→ehall 填写→Android 最终预览确认→归档 | 所有证据、暂停点和独立审批可追溯；实际提交必须另行审批 |
| 流程 B | 邮件→资料→草稿→手机→SMTP→归档 | 测试地址真实收到与审批一致的唯一邮件 |

### 17.3 参考负载与性能门槛

参考环境应记录 CPU、内存、磁盘、Windows 版本、Python/数据库版本和数据规模。首版验收数据集建议至少包含 10,000 份文档或 100,000 个分块、50,000 封邮件元数据和 10 个安装扩展。

| 指标 | 首版门槛 |
|---|---|
| 本地任务/状态查询 API | P95 ≤ 500 ms，不含外部系统和模型时间 |
| SSE 持久事件可见延迟 | 正常连接下 P95 ≤ 2 s |
| 混合知识检索 | 参考负载 P95 ≤ 2 s；黄金集 Recall@5 ≥ 0.90 |
| 引用正确率 | 100%，错误定位视为功能失败而非质量波动 |
| 邮箱同步 | 在线时每 5 分钟轮询，计划触发偏差 ≤ 1 分钟 |
| Worker 异常恢复 | 租约到期后 2 分钟内恢复或进入明确暂停/UNKNOWN |
| 重复副作用 | 全部压力与故障测试中为 0 |
| 扩展禁用 | 立即停止新派发；在配置的 drain deadline 内完成/暂停在途任务 |
| 备份恢复 | RPO ≤ 6 小时，RTO ≤ 2 小时 |

若参考硬件无法满足性能门槛，应先记录基线并优化查询、分块和批处理；不得以关闭来源验证、审计或安全检查换取性能。

### 17.4 覆盖率和发布缺陷等级

- 核心状态机、工具策略、审批、幂等、秘密和扩展生命周期分支覆盖率不得低于 90%。
- 其他后端核心模块行覆盖率不得低于 80%。
- 外部适配器以契约、故障注入和受控真实测试为主，不能用高行覆盖率代替真实边界验证。
- P0：数据/凭据泄漏、未经审批副作用、重复发送/提交、不可恢复损坏；发布阻断。
- P1：任务丢失、审批状态错误、R3 策略可绕过、恢复失败；发布阻断。
- P2：有安全降级路径的功能故障；必须记录责任人和修复版本。
- P3：不影响正确性或安全的体验问题；可以带已知问题发布。

## 18. 需求追踪矩阵

| ID | 产品要求 | 主要设计 | 主要阶段/验收证据 |
|---|---|---|---|
| FR-01 | 资料变化后更新索引 | 8.2、11.4、13.1 | 阶段 5 文件变更/对账测试 |
| FR-02 | 回答定位原文 | Evidence、哈希、引用坐标 | 阶段 5 引用正确率 100% |
| FR-03 | 持续关联 smail | EventSource、五分钟轮询、线程 Context | 阶段 6 真实只读同步 |
| FR-04 | 可编辑回复草稿 | 版本化 Artifact、PWA 编辑 | 阶段 6/9 草稿并发测试 |
| FR-05 | 只发送确认最终版 | prepare/approve/commit | 阶段 7 受控真实发送 |
| FR-06 | 不重复处理/发送 | UID 游标、幂等、UNKNOWN | 阶段 6/7 重复与故障测试 |
| FR-07 | 查询 ehall 信息 | 受监督 BrowserSessionBroker | 阶段 8 只读应用发现 |
| FR-08 | 至少一种事务材料与填写 | TransactionAdapter | 阶段 8 真实提交前填写 |
| FR-09 | 提交前展示字段与后果 | 表单 diff、附件哈希、审批 | 阶段 8 预览测试 |
| FR-10 | 高风险操作不自治 | `PROHIBITED` 硬策略 | 阶段 4/8 R3 绕过测试 |
| FR-11 | 手机发起/查看/编辑/确认 | Android PWA、SSE、Web Push | 阶段 9 蜂窝/Wi-Fi 实测 |
| FR-12 | 不依赖桌面对话窗口 | PostgreSQL 队列、Worker、检查点 | 阶段 1/3 重启恢复 |
| FR-13 | 能力共享必要上下文 | ContextManager、Handle、Workflow | 阶段 10 两条组合流程 |
| FR-14 | 可复用完整流程 | WorkflowProvider | 阶段 10 流程 A/B |
| FR-15 | 成功与纠正跨会话复用 | 候选规则、测试、Git、回滚 | 阶段 10 新会话回放 |
| FR-16 | 实现与规则由 Git 管理 | 仓库、ADR、规则版本 | 阶段 0/10 Git 审计 |
| EXT-01 | 新功能只注册扩展 | SDK、Manifest、Registry | 阶段 2 仓库外示例扩展 |
| EXT-02 | 便捷删除或接入扩展 | 生命周期、drain、数据分离 | 阶段 2 卸载/重装/回滚 |

## 19. 运行手册最小集合

Beta 发布前必须存在下列可执行 Runbook，每份均包含触发条件、只读诊断、操作步骤、预期结果、失败升级和审计要求。

### 19.1 `UNKNOWN` 邮件/ehall 动作

1. 冻结该动作的所有自动处理和旧审批。
2. 查看本地时间线、载荷哈希、连接阶段和已有服务器响应。
3. 只读查询 Sent 文件夹或 ehall 流程跟踪。
4. 找到匹配回执则人工标记成功并关联证据；确认未发生时创建全新动作和审批。
5. 无法判断则保留 UNKNOWN，并让用户在外部系统人工核对；不得猜测。

### 19.2 丢失手机或 Access 会话疑似泄漏

1. 在 Cloudflare/身份提供方撤销会话和设备授权。
2. 关闭 Tunnel 或用本机 Kill Switch 冻结副作用。
3. 撤销 Web Push subscription，检查过去一年的审批和最近动作。
4. 轮换相关账户凭据；对可疑 UNKNOWN 只读核对。
5. 恢复入口后重新建立 Access 策略。由于没有应用 Passkey，不能只在应用内“退出登录”就视为处置完成。

### 19.3 扩展故障、禁用和卸载

1. 停止向扩展派发新任务，进入 `DRAINING`。
2. 列出在途工具、计划和依赖它的工作流。
3. 等待 drain deadline；对外部动作不能强行标失败。
4. 生成新 Registry Snapshot，停止 Worker 并撤销句柄。
5. 卸载仅删除代码/venv；清除数据需要独立影响预览和确认。

### 19.4 数据库恢复

1. 冻结 Tunnel、Scheduler 和 Side-effect Broker。
2. 在隔离路径校验恢复包、版本清单和备份哈希。
3. 恢复 PostgreSQL、Artifact 和扩展清单，运行只读一致性检查。
4. 把恢复点时处于 `EXECUTING` 的动作统一置为待核对状态，而不是重新执行。
5. 重新绑定凭据，启动只读能力，完成 UNKNOWN 核对后再开放副作用。

### 19.5 ehall 页面漂移

1. 自动隔离对应 TransactionAdapter 并暂停相关任务。
2. 保存脱敏、短期的页面版本证据；不得默认上传完整 DOM/截图。
3. 在模拟 fixture 中更新选择器、字段和风险规则。
4. 契约与受控真实提交前测试通过后，发布新扩展版本；进行中任务不得静默切换。

## 20. 风险登记与演进边界

| 风险 | 影响 | 当前处理 | 后续演进触发条件 |
|---|---|---|---|
| 恶意/被入侵扩展 | 可读取宿主数据或绕过正常接口 | 安装即信任，展示来源/哈希，禁止自动升级 | 需要接纳未知发布者时实现 OCI/WASM Runtime 和 OS 强隔离 |
| Access 会话被盗 | 可冒用用户创建及批准动作 | 短时单次审批、CSRF/Origin；风险明确接受 | 出现共享设备或实际事件时加入 Passkey 交易确认 |
| Cloudflare 可见传输内容 | 第三方信任与隐私风险 | 最小披露、固定域名；风险明确接受 | 要求服务商不可见正文时设计应用层端到端加密或改用私网入口 |
| ehall 页面/政策变化 | 适配器停机或误操作 | 页面版本探测、受监督、fail closed | 获得校内正式 API 资格时优先迁移官方接口 |
| SMTP 结果模糊 | 重复发送风险 | UNKNOWN、Sent 核对、人工裁决 | 服务端提供正式幂等发送 API 时替换 SMTP |
| 本地主机离线 | 无同步、执行和通知 | 两种正式配置、离线真实显示、补偿策略 | 需要离线仍提醒时引入最小云调度/通知中继 |
| 本地模型能力不足 | 拒绝敏感外发后任务停顿 | 明确暂停并询问用户 | 有可验证本地模型后扩展路由和评测 |
| Windows 交互会话限制 | ehall headed 浏览器不能作为后台服务显示 | Desktop Companion | 转为受支持的远程交互浏览器前不得无人值守 |
| PostgreSQL/Docker 运维负担 | 安装和恢复复杂 | Compose、自动检查、Runbook | 单文件部署成为首要目标时重新评估 SQLite，不直接双写迁移 |

未来新增能力时优先新增扩展槽实现；只有下列变化才允许修改核心协议：出现无法表达的新通用能力类型、现有安全不变量需要加强、或协议主版本演进。不得因为某个业务扩展开发方便而向核心加入专用条件分支。

## 21. Definition of Done

项目只有在以下条件全部满足后，才能标记为“可长期日用的单用户 Beta”：

- 需求、设计、代码、测试和验收证据具有双向追踪关系。
- 一个仓库外示例业务能力只需实现 SDK、提供 Manifest 并注册；核心 Agent、上下文、工具网关、核心 API 和核心迁移零修改。
- 主 Agent、审批、工具策略、幂等和扩展生命周期满足覆盖率门槛。
- 扩展契约、API、数据库迁移、规则和审计事件有明确版本兼容规则。
- 全部失败红线测试通过，无未关闭 P0/P1 缺陷。
- 故障注入没有产生重复发送、重复提交或 UNKNOWN 自动重试。
- Git、日志、数据库普通表、模型请求、RPC 和备份的 D3 扫描均为零命中。
- 个人资料检索稳定返回可验证原文引用，黄金集指标达标。
- smail 重复同步不重复处理，受控 SMTP 实际收到与审批快照一致的唯一邮件。
- 真实 ehall 完成低风险事务发现、材料准备和提交前表单填写；不为验收虚构事务。
- Android 经 Cloudflare 固定域名完成发起任务、查看进度、编辑草稿和精确确认。
- 两条强制组合流程均完成并留下完整来源、状态和审批证据。
- 用户纠正可以审核、测试、跨会话复用并通过 Git 回滚。
- 扩展安装、启用、排空、禁用、升级、回滚、卸载、数据保留和重装均通过验收。
- 完成一次满足 RPO/RTO 的实际恢复演练。
- 用户指南、扩展开发指南、威胁模型、数据保留说明、UNKNOWN 处置和运维 Runbook 齐全。

## 22. 关键决策记录摘要

| ADR | 决策 |
|---|---|
| ADR-001 | 本地优先、单用户、长期日用 Beta |
| ADR-002 | Python + FastAPI 模块化单体，长任务运行在独立 Worker |
| ADR-003 | Windows 原生应用进程；PostgreSQL + pgvector 使用 Docker Compose |
| ADR-004 | Cloudflare Tunnel + Access 和固定域名为主入口；Tailscale 仅只读健康 |
| ADR-005 | Android PWA；锁屏只显示最小元数据 |
| ADR-006 | Access-only，无应用账户、Passkey 或交易二次认证；接受会话盗用剩余风险 |
| ADR-007 | PostgreSQL 同时承载真相状态、索引和持久任务队列；首版无 Redis |
| ADR-008 | Agent 普通预算默认空；不可关闭的高位安全熔断保留 |
| ADR-009 | 扩展安装后视为可信；独立 venv/Worker 不是安全沙箱 |
| ADR-010 | 新业务通过版本化扩展槽、Manifest 和 JSON-RPC 接入，核心无专用分支 |
| ADR-011 | 统一 PWA 只渲染扩展 Schema，不执行扩展任意 JS/路由 |
| ADR-012 | 外部副作用采用精确、五分钟、一次性审批；UNKNOWN 不自动重试 |
| ADR-013 | 原文件是个人资料权威来源；数据库保存版本、引用、全文和向量索引 |
| ADR-014 | 远程/本地模型统一适配；敏感数据按用户许可外发，D3 永不外发 |
| ADR-015 | ehall 使用受监督可见浏览器；首版永久禁止高后果事务 |
| ADR-016 | 代码、Manifest、流程、规则和脱敏 fixture 入 Git；个人数据不入 Git |

## 23. 参考资料

- [原始实验产品描述](./Lab1%20personal%20assistant.md)
- [南京大学学生邮箱客户端配置办法](https://itsc.nju.edu.cn/1a/8f/c21586a334479/page.htm)
- [南大邮箱系列问答](https://itsc.nju.edu.cn/96/2e/c21475a497198/page.htm)
- [南京大学电子邮件管理办法](https://itsc.nju.edu.cn/17/b5/c21586a333749/pagem.htm)
- [南京大学网上办事大厅指南](https://guide.nju.edu.cn/faq/33/07/c44791a537351/pagem.htm)
- [南京大学能力开放平台](https://itsc.nju.edu.cn/nlkfpt/listm.htm)
- [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/)
- [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve)
- [Tailscale Access Control Grants](https://tailscale.com/docs/features/access-control/grants)
- [pgvector](https://github.com/pgvector/pgvector)
- [PostgreSQL Backup and Restore](https://www.postgresql.org/docs/current/backup-dump.html)
- [Python `imaplib`](https://docs.python.org/3/library/imaplib.html)
- [Python `smtplib`](https://docs.python.org/3/library/smtplib.html)
- [Playwright Authentication](https://playwright.dev/python/docs/auth)
- [W3C Push API](https://www.w3.org/TR/push-api/)
