# 对话预算与知识库索引显示修复

日期：2026-11-09。状态：目标验证通过，personal.knowledge 0.1.8已正式升级并受控重启加载。

## 证据与原因

- 原实例只读审计发现一次 `MODEL_RESPONSE_TOO_LARGE / ANSWER`；此前累计模型HTTP流保护为1 MiB，包含思考片段与协议元数据，不能把它等同于最终回答长度。
- 截图对应时段有一个 `FAILED / v2`，路由模型完成后即结束、没有进入回答。旧记录没有保留具体校验原因，不能断言这一次由超时或某个确切字段导致。
- 定向反例确认概览提供者不需要搜索词，但路由解析要求非空query，二者会冲突。现只在已验证提供者合法、RETRIEVE的query空白时使用本轮用户原问题；不新增工具、权限、动作，也不截短查询。其他非法字段继续拒绝。
- 文件项此前硬编码 `UNKNOWN`，即使整体绑定已READY也不投影活动索引记录。本轮只读确认实际索引元数据存在。

## 实现

1. 默认输出token继续为None，不发送人为max_tokens；不裁剪已接受的回答。累计模型传输和TUI SSE资源保护均调至16 MiB，超限显式失败，不冒充完整回答。模型服务自身的上限仍无法由客户端取消。
2. 默认远端单次模型预算300秒；整轮对话660秒（路由、检索、回答）；TUI总预算及HTTP read为690秒。连接/写入/连接池预算15/30/15秒，普通请求预算不变。显式模型配置仍优先，思考模式未改。
3. 确定的路由校验失败和回答不一致保留FAILED，额外保存固定原因/阶段/关联号。API→TUI均验证白名单，不保存原模型JSON、异常原文或个人正文。重放只读取原回执；查询自身超时不会写成原请求的失败原因。
4. personal.knowledge 0.1.8按安全扫描出的精确文件读取活动索引元数据，展示INDEXED、STALE、NOT_INDEXED、UNSUPPORTED或UNKNOWN。每批最多100项/128KiB身份参数，结果合并而不截断清单。绑定状态前后复核；数据库不可用等情况仍如实UNKNOWN。
5. `content_verification=NOT_CHECKED`明确概览没有重读正文核验哈希。INDEXED表示活动版本与源哈希、当前大小/时间等元数据匹配；实际正文检索继续原有内容核验，不能把目录元数据当正文证据。

## 目标验证

只运行受影响专项，遵循本人避免完整套件过度验证的要求；各组有交叉，不合计。

- 概览空查询、FAILED诊断/API重放及TUI传递新增3项先失败；状态查询超时污染原诊断新增1项先失败，修复后通过。
- 模型wire超过旧1MiB、TUI超过旧2MiB、默认模型时限3项先失败；TUI内层read=75反例先失败，随后修复。
- 索引显示新增8项先失败；Unicode参数帧超过512KiB反例先失败，分批后通过。

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/api/test_workbench_conversation.py tests/integration/test_conversation_diagnostics_wire.py tests/integration/test_conversation_model_failures.py tests/unit/test_conversation_routing.py tests/unit/test_conversation_diagnostics.py tests/unit/test_tui_error_diagnostics.py tests/contract/test_f05_contract_consistency.py -o addopts= -q --tb=short -p no:cacheprovider
```

结果：114 passed、2个既有依赖警告。日志：`var/verification/conversation-limits-index-2026-11-09/conversation-final.log`。

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/unit/test_model_streaming.py tests/unit/test_model_adapters.py tests/unit/test_settings.py tests/unit/test_output_unbounded.py tests/unit/test_tui_stream.py -o addopts= -q --tb=short -p no:cacheprovider
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/unit/test_tui_stream.py tests/unit/test_tui_service.py tests/unit/test_output_unbounded.py -o addopts= -q --tb=short -p no:cacheprovider
```

分别131 passed/2 warnings；随后修复read预算再验证46 passed/2 warnings。Ruff及7份宿主源文件Mypy通过；知识库3份源码Ruff/Mypy通过。

知识库目标命令：

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/unit/test_knowledge_workspace_index.py tests/unit/test_knowledge_binding.py tests/unit/test_knowledge_workspace.py tests/unit/test_knowledge_instance_workspace.py tests/unit/test_knowledge_discovery_planner.py tests/integration/test_conversation_workspace.py -q -o addopts= -p no:cacheprovider
```

46 passed/2 warnings；精确临时PG节点未执行：Docker只读查询20秒超时，进程已退出，未建容器/卷。补充原库只读事务验证新SELECT和SQL白名单：3条活动索引元数据匹配、未命中来源排除；不读取个人正文、不写原库、不执行候选Worker。证据 `sql-readonly.json`；不能把它等同于完整PG/Worker套件。

## 候选与运行边界

personal.knowledge 0.1.8 材料：`sha256:2b7dcd084abdb9078d623292e66c1706037eeb6dda31888f9d73b76169ebbfc0`。
必需能力仍仅extension.data.sql，无新增依赖或迁移；本人已明确允许升级并重启。
部署结果以 `var/deployment/conversation-limits-index-2026-11-09/result.json` 为准。
本轮不调用真实模型、不恢复旧任务、不改变审批/UNKNOWN或风险分级，不提交或推送。

WORKSPACE.1仍待终端人工检查，不据此关闭F08.6/F07/F08。推荐后继仍是完成现有终端核对后再推进FILES.BACKUP.1。


## 正式部署结果

本人明确批准上述精确材料后，确认没有新近仍未完成的对话及READY/LEASED作业，持有重启协调锁，按boot ID正常停止原宿主。正式inspect核对同一摘要，经InstallCoordinator/LifecycleManager升级成功、安装payload摘要相同。临时Worker实际读取目录索引元数据后已退出，再启动原profile与原Admin模式，健康与工作台就绪后按freeze ticket恢复原领取策略。

- personal.knowledge 0.1.8 / ENABLED；workspace APPLIED；Worker在线；frozen=false（保持原状态）。
- 目录共121项：INDEXED 117、NOT_INDEXED 2、UNSUPPORTED 2；全部明确NOT_CHECKED正文哈希。
- 原15任务及作业状态不变；知识来源117、版本117、分块7408及来源哈希/活动版本摘要不变。
- 应用配置、profile、其他扩展版本/状态不变。未调用真实模型、未恢复旧任务、未编辑个人文件。
- 新宿主模型默认预算300秒。TUI客户端需本人退出并重新启动以加载690秒读取预算及新诊断显示。

一次性部署脚本与证据：`var/deployment/conversation-limits-index-2026-11-09/deploy.py`、`operation.json`、`index-projection.json`、`result.json`；不要直接重复执行已经完成的升级脚本。临时PG专项未跑的限制仍保留；实际授权安装Worker核验不代替完整PostgreSQL/Worker套件。

## 改动文件范围

宿主：`core/conversation.py`、`core/conversation_diagnostics.py`、`api/workbench_conversation.py`、`cli/tui/app.py`、`cli/tui/service.py`、`settings.py`、`infrastructure/models/base.py`。
知识插件：`workspace.py`、`worker.py`、`store.py`、三份版本声明及README；对应知识、路由、流、预算、TUI和契约目标测试及core-suite登记。协议/设计/TUI指南与当前任务交接同步更新。没有迁移历史修改或新增权限。
