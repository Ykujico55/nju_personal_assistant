# Personal Assistant Framework

这是一个本地优先、扩展驱动的个人助理基架。FastAPI 只负责控制面；Agent、上下文、工具策略、审批与扩展生命周期都通过稳定接口组合。具体业务能力必须放在 `extensions/`，不能在核心里添加扩展 ID 分支。

完整设计见 [DESIGN_AND_DEVELOPMENT.md](./DESIGN_AND_DEVELOPMENT.md)，简要接力列表见 [TODO.md](./TODO.md)，后续实现顺序见 [docs/NEXT_STEPS.md](./docs/NEXT_STEPS.md)，稳定接口与持久化契约见 [docs/CONTRACTS_AND_INTERFACES.md](./docs/CONTRACTS_AND_INTERFACES.md)。

## 当前可用范围

- 可启动的用户 API、本机管理 API、独立 health-only API。
- 持久 Agent 状态机、工具风险门、精确审批、上下文与模型披露接口的基础实现。
- 数据库队列与 PostgreSQL Schema；开发环境提供内存适配器。
- 扩展 Manifest、Registry、生命周期/RPC 契约和 `example_echo` 示例扩展。
- Extension Supervisor：受控暂存与静态检查、精确确认屏障、每版本独立 venv、真实 Worker 子进程、原子 Registry Snapshot、enable/disable/upgrade/rollback/uninstall 与启动恢复（venv/Worker 只做依赖与崩溃隔离，不是恶意代码沙箱）。
- Cloudflare Access 边界：公共 API 在 `PA_TRUST_CLOUDFLARE_ACCESS=true` 时验证 Access JWT（`RS256`、`kid`、issuer、audience、expiry）后建立身份，JWKS 有界缓存与受控轮换，失败 fail closed（F03 DONE，三轮独立验收通过）。
- 模型适配器与披露许可（F04 DONE）：真实远端 OpenAI 兼容适配器与本地 Ollama 适配器（协议驱动 HTTP，无厂商 SDK，错误类型化、无静默重试、禁重定向、有界响应、总 deadline），持久化 `model_disclosure_consents` 许可（精确 provider/用途/字段摘要与不可复用接收端指纹绑定、到期/撤销、幂等重放）与 public API 的 preview/confirm/revoke 接口；SECRET 在所有模型路径硬阻断。
- 个人知识扩展（F05 DONE，独立审计与修复后通过）：`extensions/personal_knowledge` 注册 `knowledge.file_changes`、`knowledge.retrieve`、`knowledge.search`(READ)、`knowledge.reindex`(INTERNAL_WRITE)、`knowledge.reconcile`、`knowledge.roots` 与 `knowledge.index_schema`，实现授权目录扫描、Markdown/TXT/PDF 抽取与稳定 locator、owner/心跳租约绑定的 SHA-256 版本化分块与严格来源快照 CAS、扩展自有 `ext_*` Schema、embedding identity 隔离的 PostgreSQL FTS + pgvector 混合（RRF）检索、可验证引用与删除传播。扩展不接触数据库凭据：语句经通用宿主数据能力（`host.data.execute/transaction/migrate`）在扩展 Schema 内执行；配置经通用 `GET/PUT /admin/v1/extensions/{extension_id}/config` 通道注入并在启动时按当前版本 Schema 复核。默认 `embedding.provider=none` 时显式降级为仅 FTS。
- CLI、单元/契约测试以及面向后续模型的任务清单。
- 响应式 PWA 壳和 `assistantctl extension scaffold` 扩展生成器。

扩展安装与生命周期只经本机 Admin API（`127.0.0.1:8001`），例如：

```powershell
.venv-win\Scripts\assistantctl.exe extension inspect extensions\example_echo
.venv-win\Scripts\assistantctl.exe extension install extensions\example_echo
```

扩展的非秘密配置经同一 Admin API 的通用通道设置（例如个人知识扩展的授权目录），保存后在下一次 enable/recover 时注入 Worker：

```powershell
$body = '{"config":{"roots":[{"path":"C:/Users/me/Documents/notes","key":"notes"}],"embedding":{"provider":"none"}}}'
Invoke-RestMethod -Method Put -Uri http://127.0.0.1:8001/admin/v1/extensions/personal.knowledge/config `
  -Headers @{ "Idempotency-Key" = "knowledge-config-1" } -ContentType "application/json" -Body $body
```

个人知识扩展的索引数据位于 PostgreSQL `ext_personal_2e_knowledge` Schema，不写入核心表；源目录始终只读。

Cloudflare Access JWT 验证已实现并通过三轮独立验收（F03 DONE），但 Tunnel/Tailscale 部署编排、真实 smail、ehall、Web Push 和 Windows Credential Manager **尚未实现**；它们必须作为适配器或业务扩展完成，不能用假成功替代。Windows Credential Manager（F09）完成前，生产环境的远程模型凭据后端是 fail-closed 占位（`UnavailableSecretStore`），不会静默使用内存明文；协议行为只能通过注入 HTTP transport 测试，尚未完成真实厂商端到端验证。

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

远程访问必须设置 `PA_TRUST_CLOUDFLARE_ACCESS=true`，并提供 `PA_CF_ACCESS_TEAM_DOMAIN`、`PA_CF_ACCESS_AUD` 和 `PA_PUBLIC_ORIGIN`。team domain 只接受 `<team>`、`<team>.cloudflareaccess.com` 或 `https://<team>.cloudflareaccess.com`；签名密钥只从 `https://<team>.cloudflareaccess.com/cdn-cgi/access/certs` 获取。配置缺失或非法时启动失败，不会降级为无认证模式。

模型端点必须显式配置，半配置会拒绝启动：

- 远端：`PA_MODEL_REMOTE_BASE_URL`（必须 https）+ `PA_MODEL_REMOTE_MODEL` + `PA_MODEL_REMOTE_SECRET_HANDLE`（仅句柄 ID，不是密钥原文）。
- 本地：`PA_MODEL_LOCAL_BASE_URL`（必须 loopback）+ `PA_MODEL_LOCAL_MODEL`。
- 显式本地回退：`PA_MODEL_LOCAL_FALLBACK_PROVIDER_ID`，必须等于已配置的本地 provider ID。

敏感/个人字段发往远端模型前必须存在持久的、精确绑定 provider、用途和字段摘要的披露许可；`SECRET` 在本地、远端和回退路径全部拒绝。生产环境当前没有 Windows Credential Manager（F09），远程凭据解析会 fail closed。

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
