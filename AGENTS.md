# Continuation Rules for Coding Agents

本文件面向后续实现模型。开始任何任务前，先阅读 `README.md`、`TODO.md`、`docs/CONTRACTS_AND_INTERFACES.md`、`DESIGN_AND_DEVELOPMENT.md` 中对应章节及 `docs/NEXT_STEPS.md` 中唯一一个待办项。一次只完成一个编号任务；当前只允许推进 `TODO.md` 标为 `NEXT` 的任务。

## 架构边界

- 依赖方向只能是 `api/infrastructure/workers -> core -> domain`。
- `extensions/*` 只能依赖公开 `personal_assistant_sdk` 与扩展自身依赖，不能导入 `personal_assistant.core`。
- 新业务功能只能新增扩展并注册标准槽，禁止在核心出现具体扩展 ID、业务专用路由或业务专用核心表。
- 外部副作用只能经过 Tool Gateway；禁止从 API 路由、Agent、扩展管理器或模型适配器直接发送邮件/提交表单。
- 凭据只通过 `SecretHandle` 和宿主代理能力使用。日志、RPC、模型输入、数据库和测试 fixture 中不得出现真实凭据。
- `venv + worker process` 只隔离依赖和崩溃；所有扩展被视为用户信任代码，不得把它描述成恶意代码沙箱。

## 状态与安全

- 风险只有 `R0/READ`、`R1/INTERNAL_WRITE`、`R2/EXTERNAL_WRITE`、`R3/PROHIBITED` 四档。
- R3 永久阻断；R2 必须使用一次性、5 分钟、绑定目标/payload 哈希/扩展版本/nonce 的审批。
- payload、目标、附件哈希或扩展版本任一变化，旧审批立即无效。
- 外部动作结果不明时记录 `UNKNOWN`，绝不自动重试。
- 队列是 at-least-once；副作用靠幂等键、回执与对账实现 effectively-once。
- 不得删除或弱化 3 次相同无进展调用、5 次连续错误、8 轮无进展、24 小时活动运行的紧急熔断器。

## 每个任务的操作顺序

1. 读相关协议、已有测试和相邻实现；不要猜接口。
2. 先写或更新失败测试，再做最小实现。
3. 只编辑待办项授权的文件；发现需要跨边界改动时停止并记录原因。
4. 运行目标测试，再运行全部测试；不得用跳过/删除断言换取绿色。
5. 更新 `docs/NEXT_STEPS.md` 的任务状态和证据。不要写“已完成”而没有可复现命令。
6. 报告：改动文件、通过的命令、未实现项、风险和推荐的下一编号任务。

## 禁止的捷径

- 不要用内存实现宣称生产持久化完成。
- 不要用模拟 SMTP/浏览器结果宣称真实集成完成。
- 不要吞掉异常后返回成功。
- 不要把 TODO 藏在宽泛异常处理、空函数或永远通过的测试中。
- 不要自动安装/升级扩展；确认必须早于任何第三方 build、install、import 或 execute。
- 不要让 PWA 加载扩展提供的 JavaScript；扩展 UI 只能提供 JSON Schema/UI Schema。
- 不要更改既有风险等级、审批 canonicalization 或迁移历史来迁就新功能。

若任务无法满足验收条件，请保留失败测试并把状态标为 `BLOCKED`，说明缺少的真实依赖或用户授权。
