# 项目接力 TODO

状态只使用 `DONE / NEXT / WAITING / BLOCKED`。每个 Agent 一次只推进一个编号；完成后写入可复现的命令与结果，不得顺手开始下一项。

## 已完成

- [x] **F00 — 基础框架（DONE）**
  - Python 3.12 + FastAPI 三入口、PWA 壳、Agent Plan/Act/Observe、上下文管理、Tool Gateway、精确审批、扩展 Registry/Lifecycle/RPC 契约、扩展 SDK、队列/租约/Outbox 端口、内存参考适配器与示例扩展已经建立。
  - 证据：`scripts/test.ps1` 为 42 passed，Ruff/Mypy 通过；`pip check` 无冲突；wheel 含核心、SDK、PWA、配置和迁移。
  - 有意锁定：生产 PostgreSQL、真实 Agent Worker、扩展 Supervisor、Cloudflare JWT、真实模型和业务扩展尚未实现；不得把当前 `QUEUED` 状态描述成任务已经执行。

## 接下来几步

- [ ] **F01 — PostgreSQL 持久化内核（NEXT，本次唯一允许推进）**
  - 实现迁移运行器，以及 Task、Run、Checkpoint、Observation、Approval、Job、Event、Audit、Side-effect Outbox、Extension lifecycle state 的 PostgreSQL 适配器。
  - 接入 composition root；生产模式不得回退内存。详细契约见 `docs/CONTRACTS_AND_INTERFACES.md`。
- [ ] **F02 — Extension Supervisor（WAITING）**
  - 接通暂存、明确确认、独立 venv、Worker 进程、健康检查、排空、升级与回滚。
- [ ] **F03 — Cloudflare Access 验证（WAITING）**
  - 校验 JWT 签名、issuer、audience、expiry 与代理边界；当前远程模式继续 fail closed。
- [ ] **F04 — 模型适配器与敏感披露许可（WAITING）**
  - 接入真实本地/远程模型，持久化精确披露许可；SECRET 在所有模型路径继续硬阻断。

F05–F10 和两条最终 E2E 的完整范围见 `docs/NEXT_STEPS.md`。F01 未完成并通过真实 PostgreSQL 验收前，不得开始 F02。

## 每次交接必须留下

1. 本文件中的状态变更，只能把实际完成项标为 `DONE`。
2. `docs/NEXT_STEPS.md` 中对应任务的命令、测试数量、真实限制和剩余风险。
3. 目标测试、`scripts/test.ps1`、Ruff、Mypy 的原始结果摘要。
4. 新迁移、公共接口或行为变化的说明；既有迁移不得重写。

