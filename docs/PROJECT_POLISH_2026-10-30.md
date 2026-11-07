# 项目细节完善清单（2026-10-30）

本轮是实现与使用路径梳理，不是全项目独立验收。用户要求参考iCode打磨TUI，同时检查助手主体搁置功能、设计遗漏与小bug并逐项修补；已核对最新README、TODO、契约、TUI/API/CLI/Admin界面、扩展与真实环境使用记录。一次推进一个编号，本地修补不自动启动原实例、解冻、安装扩展或执行真实外部动作。

## 当前修补顺序

| 编号 | 状态 | 缺口与处理范围 |
|---|---|---|
| TUI.POLISH.1 | DONE（本地） | 紧凑状态栏/对话布局、Markdown、命令、多行编辑、帮助；刷新保留选中行/阅读位置；设置失败不阻断其他读取；并发配置冲突后显式重新加载；Worker状态分辨 |
| CORE.FIX.1 | DONE（本地） | 有效每分钟coalesce调度积压两天后扫描溢出，Worker在领取无关任务前反复退出 |
| CORE.FIX.2 | DONE（本地；PG待验） | Agent恢复规划只发用户消息，遗漏助手追问，使用户短回答失去上下文 |
| MAINT.1 | DONE（本地） | CLI安装/升级未接住离线异常；轮询断连应保留操作ID与只读查询指引，不能重发安装 |
| MAINT.2 | DONE（本地） | Admin按钮成功后恢复、升级按预览mode路由、配置保存丢回执后先GET核对；固定未确认请求 |
| MAINT.3 | 合并入MAINT.2 | Admin预览升级材料后仍调用install；需按预览mode固定目标与回执类型，并区分明确拒绝与结果未知 |
| MAINT.4 | DONE | README当前状态混入历史限制；配置PUT错误同键重试说明；开发工作区备份排除项需明确 |

前述功能缺陷通过隔离Textual/Node/CLI或真实Runtime配合合成输入复现；文档问题通过接口与实现对照确认。后续每项先补失败回归再最小修复。详细命令与最终结果在下方逐项登记。

## 后续功能与真实依赖

以下是尚需完善的范围，不能当作本轮已复现的小缺陷直接宣告修好：

| 范围 | 当前限制 | 下一步 |
|---|---|---|
| CORE.RECONCILE.1（下一编号） | 宿主邮件/浏览器能对账，但任务层未接入可信对账事实来解除WAITING_RECONCILIATION | 独立设计绑定原动作的显式恢复命令，禁止普通继续/清标记/重新派发旧动作 |
| FILES.1 | 知识目录和简历目录只读；developer.files.write仅能写候选工作区 | 独立最小文件扩展：显式root、相对路径、hash/CAS、防链接，删除另确认 |
| BACKUP.WORKSPACE.1 | 现有数据库/制品备份不包含开发工作区SQLite、候选和发布源码 | 先补排除项与停用后独立保存说明；正式恢复需清除历史执行授权，不能直接恢复为可运行 |
| 实例启动与队列 | TUI不启动/解冻后台；原队列和冻结状态需要核对 | TUI.2用选定持久实例做小型真实联调，先只读核对队列 |
| 对话体验 | 无流式输出、无跨重启恢复的TUI聊天会话 | 独立编号设计会话持久化与增量显示，不扩大当前UI修补 |
| 模型用量 | 全实例最多5000条成功调用审计，无逐任务归因或供应商账单核对 | 有需要时增加稳定调用归因，不能凭估算生成费用 |
| 正式审批 | TUI展示任务/工具，完整R2审批继续使用既有PWA | 后续若加入TUI审批，必须复用同一权威预览，单独验收 |
| 扩展配置 | 现有PUT配置接口没有幂等回执；未知结果禁止重发 | 后续独立补配置CAS/回执，保留当前只读核对策略 |
| 扩展数据清除 | CLI暴露purge，宿主明确返回未实现 | 单独实现精确删除预览与确认，不能混成一般卸载 |
| 简历投递 | 插件及接线本地已实现，原实例安装和BOSS实站兼容未验证 | 按安装/手测文档精确确认材料，先单个真实目标 |
| 编码插件 | 计算器候选已生成和测试，候选本身尚未安装 | 检查材料哈希后由本人确认安装，再做READ调用 |
| 邮件与ehall | 已有实现和不同范围历史证据，当前版本完整真实链路尚未签收 | 收件/发给本人/提交前事务按手册小样本验证，不能把模拟当真实通过 |
| F08.6阶段门禁 | 真机补证已过，独立全量仍有F07创建超时记录 | 保留BLOCKED，专项定位后补独立门禁，不重复手机验收 |

## 界面参考

参考[iCode官方首页](https://github.com/openJiuwen-ai/iCode)及其[首页/对话截图](https://github.com/openJiuwen-ai/iCode/tree/main/screenshots)：紧凑上下文、集中输入、键盘命令与清晰对话区。只借鉴交互和布局，不复制其源代码、不安装其依赖，也不引入跳过审批的模式。

## 修补证据

### TUI.POLISH.1

改动限cli/tui/app.py、panels.py、test_tui_polish.py与文档。原UI回归18 passed；新打磨回归在补入Markdown真实Rich渲染对比检查后10 passed。最后一次仅重跑受影响的新文件，不重复无变化的18项。Ruff及两源文件Mypy通过。

~~~powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/unit/test_tui_polish.py tests/unit/test_tui_app.py -q -p no:cacheprovider -W error::RuntimeWarning
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/unit/test_tui_polish.py -q -p no:cacheprovider -W error::RuntimeWarning
~~~

首次联合命令为27 passed；最后增加1项对比回归后新文件10 passed。合成110×36首页/对话与80×24窄屏预览在var/verification/tui-polish下，用隔离Headless Edge离线渲染并复核。未连接宿主或模型；截图中的任务与用量均为合成展示。因是局部UI改变，没有运行全套或PostgreSQL门禁。

### CORE.FIX.1

仅scheduling.py与test_scheduler_backlog.py。先红2 failed/4 passed，分别复现长积压和无关Worker任务阻断；修改后目标30 passed：

~~~powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/unit/test_scheduler_backlog.py tests/unit/test_scheduler.py tests/unit/test_workflows_delivery.py tests/unit/test_runtime_delivery.py -q -p no:cacheprovider
~~~

Ruff、Mypy2文件及差异检查通过。额外45组纯计算新旧语义对照通过。730天高频积压三策略的匹配调用均≤20，不随遗漏次数增长；保留skip的60秒边界、catch_up最近N、DST和DOM/DOW语义。不修改Worker或外部动作链路，不吞异常；一年内无触发的显式错误与损坏记录隔离仍是后续范围。未重复全套/PG，不操作原实例。

### CORE.FIX.2

runtime.py与planner.py将真实模型NEEDS_USER的消息ID写入既有检查点，消息与标记同UoW；恢复仅把用户与标记命中的助手消息按角色提供为不可信上下文。旧无标记快照保持原用户字符串数组；宿主披露/错误提示不进入对话，不漂移原精确披露。原R2/UNKNOWN分支和默认写入顺序未改。

先红3 failed/2 passed，修复后本地21 passed。新增5项涵盖真实StructuredPlanner+合成provider、跨runtime重建、伪assistant排除、旧格式、工作流问句不标记、同scope写入及远程披露再次确认；与原Runtime/R2回归联合执行。Ruff4文件、Mypy2源文件和差异检查通过，另5组标记边界只读检查通过。

两项PostgreSQL持久/回滚回归已写入test_runtime_conversation_postgres.py，但专用PA_TEST_DATABASE_URL未配置，Docker info短探针8秒超时后结束，所以本轮2 skipped，不能视为真实PG通过。未启动原服务或调用真实模型，未跑完整套件。

~~~powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/unit/test_runtime_conversation_context.py tests/unit/test_runtime_delivery.py tests/unit/test_runtime_remediation_delivery.py tests/integration/test_runtime_conversation_postgres.py -q -o addopts='' -p no:cacheprovider
~~~

### MAINT.1

cli/main.py与test_cli_extension_flow.py。先红8 failed/2 passed，修复后13 passed：

~~~powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/unit/test_cli_extension_flow.py tests/unit/test_tui_cli.py -q -p no:cacheprovider
~~~

Ruff、Mypy2文件和差异检查通过。离线不再抛未处理异常；已接受操作的轮询网络/HTTP/超时诊断保留operation_id和恢复参数。新增单次只读查询：

~~~powershell
assistantctl --admin-url http://127.0.0.1:8001 extension operation <operation_id>
~~~

PENDING/RUNNING只表示原操作仍未结束；FAILED退出码1。POST丢回执且没有ID时明确COMMAND_RESULT_UNCONFIRMED，先查扩展状态，不自动重发、不凭空声称能够恢复原操作ID。局部CLI修复，未跑全套/PG或原实例。

### MAINT.2（含MAINT.3）

仅web/admin/app.js、index.html和test_admin_recovery_browser.py。6组真实Headless Edge反例先6 failed，修复后与两份Admin API联合14 passed、2个既有依赖弃用警告：

~~~powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/integration/test_admin_recovery_browser.py tests/api/test_admin_operations_delivery.py tests/api/test_admin_extension_config.py -q -o addopts='' -p no:cacheprovider
~~~

最后复核补入明确拒绝与未知结果的区分，新组先1 failed后通过；最终只复跑浏览器文件7 passed，不重复未改的8项API回归。新增fixture只用实际静态HTML/JS和拦截的合成HTTP，全部其他网络拒绝，未连接原后台。Ruff、JS语法和差异检查通过。

已确认回执释放本次请求并恢复按钮；动态列表刷新仍保留未知命令原键与校验器。安装/升级绑定原预览mode/目标/材料；新inspect清旧预览，未知期间不能重新取计划。仅4xx且明确的确认/计划拒绝码解除本次安装重试，其他失败或坏回执继续保留原键。

配置PUT无服务端命令回执或CAS：未知结果只GET原扩展，当前值相符也不冒称原命令执行次数；后来编辑的文本保留。不同值需本人选择采用服务端或保留本机重新准备，均不自动PUT。页内刷新不丢请求；整页离开原生提醒，未实现跨重启恢复，不把配置/nonce写入浏览器存储。未跑全套、未操作原实例。

### MAINT.4

已校准README当前能力表与历史记录、TUI使用指南、两个新插件安装说明、插件开发README、真实手测手册及本地运行手册。配置PUT没有CAS或去重回执，文档不再指示盲目同键重试；备份明确排除开发工作区、外部资料原件和独立CLI SQLite，离线保存也不意味着恢复旧执行授权。修正“Worker未实现”“Windows凭据后端未实现”“插件尚未安装/手机通知未验”等历史语句的当前指向，保留历史证据。

文档按已读实现逐项对照；8份说明文件读取、控制字符与新增本地链接目标核对通过，全工作区git diff --check通过。没有为文字修正重复跑产品套件。

## 本轮文件范围与交付限制

产品实现：
- src/personal_assistant/cli/tui/app.py、panels.py
- src/personal_assistant/core/scheduling.py
- src/personal_assistant/core/agent/runtime.py、planner.py
- src/personal_assistant/cli/main.py
- web/admin/app.js、index.html

目标回归：
- tests/unit/test_tui_polish.py
- tests/unit/test_scheduler_backlog.py
- tests/unit/test_runtime_conversation_context.py
- tests/integration/test_runtime_conversation_postgres.py
- tests/unit/test_cli_extension_flow.py
- tests/integration/test_admin_recovery_browser.py

文档：README.md、TODO.md、docs/NEXT_STEPS.md、docs/CONTRACTS_AND_INTERFACES.md、本清单、docs/TUI_WORKBENCH.md、docs/RUNBOOK_LOCAL_BETA.md、docs/MANUAL_REAL_ENV_TEST_GUIDE_2026-10-30.md、extensions/career_apply/INSTALL.md、extensions/plugin_developer/INSTALL.md及README.md。

以上是本次打磨范围；工作区还存在之前TUI.1、招聘和编码插件的未提交改动，本报告不把所有git差异冒认为本轮新写。按用户避免完整套件过度验证的要求，各编号仅运行改变路径和此前相关故障的回归；不同测试集合有重叠，不累加为唯一用例总数。新增PG回归尚待专用测试库，不宣称真实持久化回滚已复验。本轮不是阶段/发布验收，未跑全量或完整PG/Worker、未调用真实模型、未部署/重启/解冻原实例、未安装扩展或提交推送。

下一编号CORE.RECONCILE.1先复现并设计可信对账恢复；通用目录写文件、开发工作区安全恢复、流式/持久会话等属后续功能，不以本轮小修替代。F08.6继续BLOCKED。
