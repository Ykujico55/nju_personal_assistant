# F00 已实现基线

本文件描述“现在确实能做什么”，避免后续模型把设计目标误报成已完成。规范仍以 `DESIGN_AND_DEVELOPMENT.md` 为准。

## 可运行入口

| 入口 | 默认地址 | 已实现 | 有意锁定 |
|---|---|---|---|
| 用户 API/PWA | `127.0.0.1:8000`、`/ui/` | 创建/查看/补充/取消任务，审批查看/确认/拒绝，扩展只读状态，SSE，PWA 壳 | Cloudflare Access JWT 未实现时远程模式统一 503；任务 Worker 尚不消费队列；无任何扩展管理路由 |
| Local Admin API | `127.0.0.1:8001` | 扩展发现/状态、纯数据 inspect、install/upgrade（精确预览确认）、enable/disable/rollback/uninstall、operation 轮询、启动恢复 Worker | `purge-data` 明确 501；必须单进程运行 |
| Health-only | `127.0.0.1:8010/healthz` | 只返回 `{"status":"ok"}` | 无 docs、任务、审批、扩展或管理路由 |
| CLI | `assistantctl` | `doctor`、list/status/inspect/scaffold、install/upgrade（交互确认或 `--yes`）、enable/disable/rollback/uninstall（轮询 operation） | 只调用 Admin API，绝不直接改数据库；`purge` 服务端 501 |

开发模式使用内存仓储，但 Extension Supervisor 在 dev 与 production 都使用真实暂存目录、每版本 venv 和 Worker 子进程（`PA_EXTENSION_ROOT` 下 `staging/`、`installed/`）。`PA_ENVIRONMENT=production` 会拒绝内存；`PA_STORAGE_BACKEND=postgres` 时使用 F01 的真实 PostgreSQL 适配器，启动生命周期执行连接检查与迁移，数据库不可达或迁移失败会 fail closed，不回退内存（F01 DONE；F02 证据见 `docs/NEXT_STEPS.md`）。

## 稳定核心边界

- `domain/`：风险、状态和不可变领域对象；无框架依赖。
- `core/agent/`：Plan/Act/Observe、CAS checkpoint、进展检测、3/5/8/24h 紧急熔断。
- `core/context/`：分层合成、来源、敏感度、过期过滤、外部文本数据边界。
- `core/tools/`：Schema、不可变 Registry、能力/工作流策略、唯一 Tool Gateway、UNKNOWN 语义。
- `core/approvals/`：规范化 action、5 分钟 TTL、精确绑定、nonce、单次消费和修改失效。
- `core/extensions/`：静态 Manifest、不可变槽位 Registry、安装确认屏障、生命周期和宿主 JSON-RPC 客户端；F02 新增 `ExtensionSupervisorService`、持久 operation 模型与恢复状态机。
- `infrastructure/extensions/`：受控暂存、每版本 venv/payload 安装、真实 Worker 进程监督与契约验证、保留版本目录与 `ext_*` 数据保留适配器（venv/Worker 只是依赖与崩溃隔离，不是恶意代码沙箱）。
- `core/jobs/`：at-least-once 队列端口、独立 lease keepalive、离线调度策略和副作用 outbox 契约。
- `core/models/`：本地/远程 provider、字段分类、精确披露许可和显式本地回退。
- `core/platform|secrets|artifacts|audit/`：操作系统、凭据、受管制品与审计端口。

生产适配器只能放在 `infrastructure/`；禁止把 SQL、Win32、SMTP、Playwright 或模型 SDK 导入上述核心模块。

## 扩展开发最短路径

1. 运行：

   ```powershell
   .venv-win\Scripts\assistantctl.exe extension scaffold demo.weather extensions\demo_weather
   ```

2. 只在新目录实现一个或多个 SDK 槽，更新 `extension.toml`、JSON Schema 和锁文件。
3. 添加扩展自己的单元测试及 SDK/RPC 契约测试。
4. 运行 `scripts/test.ps1`。`tests/contract/test_extension_independence.py` 会阻止示例业务反向侵入核心。
5. 通过本机 Admin API 安装/启用：`assistantctl extension inspect <path>` 查看预览，再 `assistantctl extension install <path>` 显式确认；CLI 只轮询 operation，不得写临时 import 绕过安装确认屏障。

公开 SDK 位于 `extension_sdk/src/personal_assistant_sdk`。业务扩展不得导入 `personal_assistant.core` 或 `personal_assistant.infrastructure`。示例 `extensions/example_echo` 已通过内存 dispatcher 和真实 stdio 子进程两种测试。

## 当前测试所覆盖的不变量

- R3 永远不到达 executor。
- R2 先产生精确审批；payload 漂移烧毁审批；同一审批不可重复消费。
- 外部结果 UNKNOWN 进入对账态且不可自动重试。
- 远程个人/敏感数据必须有匹配 provider、用途、字段哈希和期限的许可；SECRET 对本地模型也禁止。
- 上下文排除 SECRET/STALE，且恶意闭合标签不能逃逸不可信数据边界。
- 作业幂等键绑定 payload；lease 独立续租；离线重放有明确 skip/coalesce/catch-up。
- 扩展确认之前 installer/verifier 调用次数为零；真实 Worker 集成以 import 日志证明确认前 0 次执行。
- 确认后 staged artifact 变化会使旧确认失效；URL/Git 来源、路径穿越、符号链接、缺 lockfile 与未钉版本/无哈希 lock 行全部安全失败。
- Worker 子进程只继承最小系统环境；数据库 URL、Token、Cookie 不会出现在子进程环境中。
- handshake 的 id/version/protocol/slots/schema hash 任一不匹配都不会进入 Registry；启用前还会比较完整工具描述符（风险/输入输出 Schema）与全部槽位能力。
- RPC 超时/畸形帧/超限行/写入管道关闭/异常退出会停止 Worker 并抛类型化错误，绝不静默重试。
- 同一扩展同时只有一个生命周期操作；相同 Idempotency-Key 重放同一命令返回原 operation，不同输入冲突。
- disable 的排空受 deadline 约束并校验 DrainReport；升级/安装失败会补偿旧版本或清理未登记代码目录。
- enable 只发布完整 Registry Snapshot；disable 先撤销再在期限内排空；uninstall 删除代码并保留 `ext_*` 数据与 tombstone。
- 升级失败时旧版本保持 ENABLED 且可调用；成功升级原子切换并保留可回滚版本；回滚校验数据 Schema 兼容性。
- PostgreSQL 重启后恢复生命周期、operation 状态并收敛瞬时态。
- health-only 端口没有其他路由；公共 API 无扩展管理路由；Admin API 只监听回环；API 命令要求幂等键；缓存与安全响应头存在。
- 新扩展骨架可被静态 Manifest parser 接受，无需改核心。

## 接力纪律

领取 `NEXT_STEPS.md` 中最靠前的一个 TODO。先给现有端口写生产适配器，除非验收证明公共契约确实缺字段，否则不要改核心接口。任何真实外部集成都要保留模拟契约测试，但最终验收必须明确区分模拟与受控真实测试。

每次交付都运行：

```powershell
./scripts/test.ps1
```

然后在 `NEXT_STEPS.md` 写入命令、测试数和真实限制。不得用空实现、跳过测试或内存回退声称生产功能完成。

