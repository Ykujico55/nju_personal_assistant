# F00 已实现基线

本文件描述“现在确实能做什么”，避免后续模型把设计目标误报成已完成。规范仍以 `DESIGN_AND_DEVELOPMENT.md` 为准。

## 可运行入口

| 入口 | 默认地址 | 已实现 | 有意锁定 |
|---|---|---|---|
| 用户 API/PWA | `127.0.0.1:8000`、`/ui/` | 创建/查看/补充/取消任务，审批查看/确认/拒绝，扩展只读状态，SSE，PWA 壳 | Cloudflare Access JWT 未实现时远程模式统一 503；任务 Worker 尚不消费队列 |
| Local Admin API | `127.0.0.1:8001` | 扩展发现、状态、纯数据 Manifest/哈希检查 | install/enable/disable/upgrade/rollback/uninstall/purge 返回明确 501，等待 F02 |
| Health-only | `127.0.0.1:8010/healthz` | 只返回 `{"status":"ok"}` | 无 docs、任务、审批、扩展或管理路由 |
| CLI | `assistantctl` | `doctor`、扩展 list/status/inspect/scaffold | 变更命令调用 Admin API，绝不直接改数据库 |

开发模式使用内存仓储。`PA_ENVIRONMENT=production` 会拒绝内存；`PA_STORAGE_BACKEND=postgres` 时使用 F01 的真实 PostgreSQL 适配器，启动生命周期执行连接检查与迁移，数据库不可达或迁移失败会 fail closed，不回退内存（F01 DONE；证据见 `docs/NEXT_STEPS.md`）。

## 稳定核心边界

- `domain/`：风险、状态和不可变领域对象；无框架依赖。
- `core/agent/`：Plan/Act/Observe、CAS checkpoint、进展检测、3/5/8/24h 紧急熔断。
- `core/context/`：分层合成、来源、敏感度、过期过滤、外部文本数据边界。
- `core/tools/`：Schema、不可变 Registry、能力/工作流策略、唯一 Tool Gateway、UNKNOWN 语义。
- `core/approvals/`：规范化 action、5 分钟 TTL、精确绑定、nonce、单次消费和修改失效。
- `core/extensions/`：静态 Manifest、不可变槽位 Registry、安装确认屏障、生命周期和宿主 JSON-RPC 客户端。
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
5. F02 完成前只能 discover/inspect；不得写临时 import 绕过安装确认屏障。

公开 SDK 位于 `extension_sdk/src/personal_assistant_sdk`。业务扩展不得导入 `personal_assistant.core` 或 `personal_assistant.infrastructure`。示例 `extensions/example_echo` 已通过内存 dispatcher 和真实 stdio 子进程两种测试。

## 当前测试所覆盖的不变量

- R3 永远不到达 executor。
- R2 先产生精确审批；payload 漂移烧毁审批；同一审批不可重复消费。
- 外部结果 UNKNOWN 进入对账态且不可自动重试。
- 远程个人/敏感数据必须有匹配 provider、用途、字段哈希和期限的许可；SECRET 对本地模型也禁止。
- 上下文排除 SECRET/STALE，且恶意闭合标签不能逃逸不可信数据边界。
- 作业幂等键绑定 payload；lease 独立续租；离线重放有明确 skip/coalesce/catch-up。
- 扩展确认之前 installer/verifier 调用次数为零；启用持久化失败会撤销已发布能力。
- health-only 端口没有其他路由；API 命令要求幂等键；缓存与安全响应头存在。
- 新扩展骨架可被静态 Manifest parser 接受，无需改核心。

## 接力纪律

领取 `NEXT_STEPS.md` 中最靠前的一个 TODO。先给现有端口写生产适配器，除非验收证明公共契约确实缺字段，否则不要改核心接口。任何真实外部集成都要保留模拟契约测试，但最终验收必须明确区分模拟与受控真实测试。

每次交付都运行：

```powershell
./scripts/test.ps1
```

然后在 `NEXT_STEPS.md` 写入命令、测试数和真实限制。不得用空实现、跳过测试或内存回退声称生产功能完成。

