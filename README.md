# Personal Assistant Framework

这是一个本地优先、扩展驱动的个人助理基架。FastAPI 只负责控制面；Agent、上下文、工具策略、审批与扩展生命周期都通过稳定接口组合。具体业务能力必须放在 `extensions/`，不能在核心里添加扩展 ID 分支。

完整设计见 [DESIGN_AND_DEVELOPMENT.md](./DESIGN_AND_DEVELOPMENT.md)，简要接力列表见 [TODO.md](./TODO.md)，后续实现顺序见 [docs/NEXT_STEPS.md](./docs/NEXT_STEPS.md)，稳定接口与持久化契约见 [docs/CONTRACTS_AND_INTERFACES.md](./docs/CONTRACTS_AND_INTERFACES.md)。

## 当前可用范围

- 可启动的用户 API、本机管理 API、独立 health-only API。
- 持久 Agent 状态机、工具风险门、精确审批、上下文与模型披露接口的基础实现。
- 数据库队列与 PostgreSQL Schema；开发环境提供内存适配器。
- 扩展 Manifest、Registry、生命周期/RPC 契约和 `example_echo` 示例扩展。
- CLI、单元/契约测试以及面向后续模型的任务清单。
- 响应式 PWA 壳和 `assistantctl extension scaffold` 扩展生成器。

真实 smail、ehall、Cloudflare、Tailscale、Web Push 和 Windows Credential Manager **尚未实现**；它们必须作为适配器或业务扩展完成，不能用假成功替代。

开发模式创建的任务会可靠进入内存队列，但 Agent Worker 仍有意锁定，因此会停在 `QUEUED`。这是基架状态，不是可用的完整助理。

## 本地启动（Windows）

```powershell
./scripts/bootstrap.ps1
Copy-Item .env.example .env
./scripts/run-public.ps1
```

`bootstrap.ps1` 会优先使用 Codex 随附的 Python 3.12，也允许通过 `-PythonExecutable` 指定解释器。本工作区已经创建并验证了 `.venv-win`。

开发模式默认使用内存存储，因此无需数据库即可体验 API。启动 PostgreSQL：

```powershell
docker compose up -d postgres
```

生产环境必须设置 `PA_ENVIRONMENT=production`、`PA_STORAGE_BACKEND=postgres`，并完成 PostgreSQL 适配器；启动检查会拒绝生产环境使用内存存储。

另外两个安全边界必须单独启动：

```powershell
.venv-win\Scripts\python.exe -m uvicorn personal_assistant.admin_app:app --host 127.0.0.1 --port 8001
.venv-win\Scripts\python.exe -m uvicorn personal_assistant.health_app:app --host 127.0.0.1 --port 8010
```

Tailscale 只能映射 `8010`。Cloudflare Tunnel 只能映射用户 API `8000`，不得映射 `8001`。

## 验证

```powershell
./scripts/test.ps1
```

若尚未安装 Web 依赖，可先运行不依赖第三方包的核心测试：

```powershell
$env:PYTHONPATH="src;extension_sdk/src;extensions/example_echo/src"
.venv-win\Scripts\python.exe -m unittest discover -s tests/unit -p "test_*.py"
```

生成一个不修改核心的新扩展骨架：

```powershell
.venv-win\Scripts\assistantctl.exe extension scaffold demo.weather extensions\demo_weather
.venv-win\Scripts\assistantctl.exe doctor
```

## 三条不可破坏的不变量

1. 所有工具调用都必须经过统一 Tool Gateway；R3 永远拒绝，R2 只执行用户确认过的精确 payload。
2. 结果为 `UNKNOWN` 的外部副作用不得自动重试，必须进入对账状态。
3. 新业务通过 SDK 注册扩展槽；不得修改核心路由、核心表或写 `if extension_id == ...`。
