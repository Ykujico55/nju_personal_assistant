# 项目接力 TODO

## 2026-11-08 当前唯一待办：WORKSPACE.1（NEXT，已授权，尚未实施）

本人已明确要求实现统一实例工作区：由同一份实例级目录授权供文件读写和知识库索引共同使用，更换工作区后两者同步更新。随后要求先提交并推送现有成果，因此本次提交不包含该功能的代码实现。FILES.BACKUP.1顺延为WAITING，待统一目录配置完成后推进。

当前必须明确：FILES.1虽已验收安装，但personal.files与personal.knowledge仍各自保存roots；安装时仅一次复制为相同目录，不会动态跟随。仅在聊天里提供任意目录不会自动新增授权；知识库目录变更也不会自动撤销旧文件目录的读写权限。下一项将统一配置来源与应用状态，保持旧计划版本/配置绑定、UNKNOWN和旧任务工具快照保护。

FILES.1 DONE；CORE.RECONCILE.1 WAITING正式验收/部署；F08.6 BLOCKED、F07/F08 IN_PROGRESS不变。以下日期段落为历史状态，本节排期优先。

## 历史：2026-11-07 FILES.1（DONE，已验收并安装）；当时后继为FILES.BACKUP.1

本人要求验收和安装，并明确授权整个知识库目录读写及动态子目录。已完成独立插件 `personal.files@0.1.0` 安装与ENABLED持久登记：沿用已应用的知识库根 `personal`，配置read_write供整个实例共享，允许受支持文本新建/修改和逐层创建目录；删除仍限本机本人确认。无新核心业务分支、迁移或第三方运行依赖。

本轮先发现并修复Windows 8.3路径别名P1，原样独立复验3 passed，新增mkdir独立组合4 passed；完整文件插件专项72 passed，关键回归643 passed/6 warnings，Ruff、Mypy宿主290/插件7文件及依赖检查通过。按本人避免过度验证要求未重复完整本地/完整PostgreSQL-Worker套件。原失败记录保留，不将通过数量混合相加。

安装材料 `sha256:f32fdf9804bac410a3dc4f2bae7c87d6865eedd34755b7d9a1d4311353717829`，正式inspect/confirm/install/enable、真实Worker及配置应用核验通过，安装载荷摘要相同。原15任务、知识库40版本/747分块、其他扩展及控制状态不变；未改知识库现有文件、索引或原任务工具快照。原助手本来停止且manual冻结，本轮保持；只启动并回收了安装验证Worker，不启动全宿主、不迁移、不调用模型或外部动作。

完整证据与可复现命令见 `docs/FILES1_ACCEPTANCE_AND_INSTALL_2026-11-07.md`（从本目录阅读时为 `FILES1_ACCEPTANCE_AND_INSTALL_2026-11-07.md`），日志在 `var/verification/files1-accept-2026-11-07/`。后台正常启动后从新对话/新任务使用文件工具；冻结需由本人按日常操作解除，旧任务不自动继续。

下一唯一编号 **FILES.BACKUP.1**：工作区备份，需覆盖授权资料根与文件计划SQLite，并验证隔离恢复；本轮只登记下一项，没有开工。**CORE.RECONCILE.1仍WAITING正式验收/部署；F08.6 BLOCKED、F07/F08 IN_PROGRESS不变。** 未提交或推送。

以下内容保留历史状态，当前结论以上节为准。

## 历史：2026-11-07 FILES.1本地实现交接（当时待验收/安装）

按本人明确排期，先结束 CORE.RECONCILE.1 的本地实现收尾，再开发通用本地文件扩展。**CORE.RECONCILE.1 为 WAITING（本地实现完成，等待正式验收/部署）**；本轮只读复核未发现新的具体实现阻断，没有把复核称为独立验收，也没有操作原实例。此状态调整不代表完整阶段门禁或真实外部对账已通过。

可复用的既有证据：Runtime/API/TUI/能力 23 项与宿主证据 14 项，共 **37 passed**；`var/verification/core-reconcile-2026-11-07/final/postgres-directed.log` 为 **5 passed，82.93 秒**；随后测试维护的 `var/verification/test-suite-trim-2026-11-07/core-final.log` 为 **643 passed、6 warnings，97.20 秒**，Ruff 和 Mypy 290 文件通过。它们是不同范围的既有运行，不相加、不冒称本轮重跑；旧完整门禁失败日志保留。正式验收及受控部署仍须分别记录。

FILES.1 已完成 `extensions/personal_files` 独立插件及对应测试/文档和测试入口，不向核心加入文件业务分支：

- `personal.files@0.1.0` 通过实例级 roots 配置为所有对话/任务提供共享授权目录；列举、读取、状态查询为 READ，持久差异预览和写入为 INTERNAL_WRITE。
- 首版限已有父目录内、最多 256 KiB 的 UTF-8 文本新建/替换；使用持久计划、SHA-256 版本复核和同键回执，冲突不覆盖。状态 SQLite 在全部授权目录外。
- 删除仅由本人在本机交互终端核对精确计划后确认；Agent 工具不暴露执行删除。配置应用复用既有实例同步，旧任务的工具快照与权限不静默变化。
- 原实例尚未安装此插件；安装前仍须正式静态审查和本人确认。首版不含 Office/PDF/图片编辑、任意 shell、目录删除或工作区备份。

本轮实际验证：新插件专项 **56 passed（20.41秒，无skip）**；关键回归 **643 passed、6 warnings（137.30秒）**，Ruff通过，宿主Mypy290文件、插件Mypy7文件通过。未运行完整本地/完整PostgreSQL套件；没有部署、操作原实例、真实模型或外部动作。日志在 `var/verification/files1-2026-11-07/`。可复现命令、材料摘要和边界见 `docs/FILES1_LOCAL_FILES_2026-11-07.md`（NEXT同目录下为 `FILES1_LOCAL_FILES_2026-11-07.md`）。**F08.6 BLOCKED、F07/F08 IN_PROGRESS 不变**。当前交接 FILES.1 审查与安装材料，原计划后继为工作区备份，不在本轮擅自启动。

以下章节是按时间保留的历史记录；其中“当前”“NEXT”、测试数量及实例状态仅描述当时，当前排期以本节为准。

## 历史：2026-11-07 测试精简（本地维护完成）

按本人新要求整理测试，不推进新产品功能。删除17项文档历史状态/源码拼写等低价值测试；保留架构、迁移、安全、审批、UNKNOWN和真实故障反例。修正RPC deadline计时/进程管道收尾、知识库旧版本硬编码，重命名同名PG测试避免收集冲突。

日常 `scripts/test.ps1` 改为643项关键回归；`-Suite full` 显式运行主项目及两个独立插件，`-Suite integration` / `-Suite plugins` / `-TestPath` 用于专项。完整收集主项目2157项、两个插件119+73项；PostgreSQL清单补至18文件/160项。没有通过skip隐藏失败。

实际验证：关键回归 **643 passed、6 warnings，97.20秒**；Ruff通过，Mypy **290文件**通过。剩余契约90项通过，RPC14项通过且受控错误注入能被拒绝；完整及PG仅收集，不算执行通过。首次关键集642 passed/1 failed为过期版本断言，已修正；日志保留于 `var/verification/test-suite-trim-2026-11-07/`。命令、删除映射及维护原则见 `docs/TESTING.md`（本目录阅读时为 `TESTING.md`）。

此前RPC测试阻断已修好，但本轮是测试维护，未重复完整门禁，不自动宣称CORE.RECONCILE.1整项验收或部署完成；当时唯一NEXT为CORE.RECONCILE.1 / BLOCKED，F08.6 BLOCKED、F07/F08 IN_PROGRESS不变。下一步回到CORE.RECONCILE.1验收评估，必要时仅跑受影响专项，不再为每次局部修正循环全量。邮件取消慢用例保留并记录待查，不以删测试掩盖潜在问题。未重启、解冻、操作原库、真实发送、提交或推送。

## 历史：2026-11-07 CORE.RECONCILE.1（当时 BLOCKED，本地实现与 CDP 修复已完成）

本轮已实现“宿主权威对账事实 → 显式恢复任务”：API/TUI核对入口、完整原动作绑定、独立持久证据、CAS与同键回执、工作流只推进一次。任务先转WAITING_USER，残留旧作业不能自动推进，必须另行明确继续；原审批/Outbox UNKNOWN历史保留，不重发、不复用旧批准。

目标证据：对账Runtime/API/TUI/能力 **23 passed**、host resolver **14 passed**、隔离PostgreSQL **5 passed**。已获授权修复的模型配置常量依赖方向 **13 passed**、配置运行状态读写互斥 **38 passed**。本次继续修复CDP测试夹具：8项反例先7 failed/1 passed，修复后与PID探针合计 **10 passed**；原浏览器失败节点及握手即就绪 **2 passed**；真实强制RPC/CDP重号被拒绝并回收子进程。改动仅测试helper及新增回归，未改生产浏览器。Ruff/Mypy/差异检查通过。

本轮仅运行一次完整串行本地门禁，结果 **1070 passed、160 skipped、1 failed、8 warnings，1466.31秒（24分26秒）**。此前CDP与配置同步失败节点均已通过。新失败为 `tests/unit/test_extension_rpc_robustness.py::RpcRobustnessTests::test_rpc_call_timeout_includes_lock_wait`：elapsed0.813秒超过0.7秒断言，并有Windows子进程管道析构警告。单例复跑 **1 passed，1.86秒**；不计作完整通过。

只读确认：RPC在取锁前建立单一deadline，取锁后使用剩余预算；本测试却将超时后最多0.25秒进程等待及管道清理也计入0.7秒。断言失败会跳过first任务和client收尾；日志不足以确定额外耗时分配或全部管道警告的唯一来源。失败测试、rpc.py与async_utils.py均与HEAD相同，本轮未修改。

日志：`var/verification/core-reconcile-2026-11-07/full-after-cdp.log`、`rpc-lock-deadline-recheck.log`；CDP目标日志和准确命令见[本轮实现报告](CORE_RECONCILIATION_2026-11-07.md)（从TODO阅读时位于docs目录）。保留所有历史失败，不合并多次中止运行冒称全绿。

该历史轮次的唯一NEXT为CORE.RECONCILE.1，因完整门禁未关闭而保持BLOCKED。当时建议修正RPC测试的deadline触发与资源回收分离验证及失败finally收尾；后续修复与当前排期见本文顶部。FILES.1在该历史轮次尚未开始。未部署、重启、解冻、真实发送、操作原库、提交或推送；F08.6 BLOCKED、F07/F08 IN_PROGRESS不变。

## 历史交接状态（2026-11-07）

六项日用修复及知识正文读取已部署。本次 **CORE.KNOWLEDGE.INDEX.1 DONE**：按本人精确授权升级到 **personal.knowledge 0.1.6 / ENABLED** 并受控重启；原实例索引已READY，40份活动版本/747分块保留，真实关键词检索返回带正文及定位的CURRENT结果。Worker在线、配置已应用、恢复原先未冻结策略，15个原任务状态/版本保持。该索引部署记录之后已推进CORE.RECONCILE.1，最新状态以上方本轮记录为准。F08.6 BLOCKED、F07/F08 IN_PROGRESS不变；下方较早的部署状态、测试结果和“下一编号”属于历史记录，以本段及本次部署证据为准。

## CORE.KNOWLEDGE.INDEX.1（DONE，已授权部署并核验原索引）— 索引状态编码与可用性修复

现场只读证据：`var/verification/knowledge-index-2026-11-07/live-metadata.json`。原库仍有40个READY版本/747分块，`index.binding.v1` 的 `jsonb_typeof(value)` 为 string，任务 `task_34559d20831e40268f8999e812ae9d47` 已 WAITING_USER，无待领取/执行Agent作业。根因是插件预序列化JSON叠加宿主JSON codec，状态被读成缺失；full复用既有版本仍报告modified40/built0，空文档又阻止整库就绪。并非40份内容全部构建失败，不需要给空文件填假内容。

候选 **personal.knowledge 0.1.6**：

- `store.py` 用对象参数保存元数据，仅兼容解开一次旧JSON字符串；CAS支持旧/新格式且保留完整scan_id比较，绑定摘要/schema/PENDING检查不变。
- `index.py`/`models.py`/`reindex-output.json` 正确报告复用为unchanged，新增明确空文本跳过诊断和实际读回的index_state/index_reason。只跳过完整读取且UTF-8解码为空白的TXT/Markdown；原正文变空先CAS删除旧索引，删除竞争失败仍待索引。PDF无字/解析/读取错误仍阻断。
- `workspace.py` 文件项提供编码后的source_uri，`read-input.json` 参数说明通过标准工具注册进入Planner。概览URI仍不能当作文件读取资源，不添加宿主业务分支。
- 三个插件版本声明及F05契约断言同步0.1.6；无新迁移、能力或第三方运行依赖。不改变审批、原任务工具快照、文件权限或无进展熔断。

可复现验证（Desktop项目根目录，均使用 `.\.venv-win\Scripts\python.exe -Xutf8 -B`）：

1. `-m pytest tests/unit/test_knowledge_index_metadata.py tests/unit/test_knowledge_index.py tests/unit/test_knowledge_binding.py tests/unit/test_knowledge_output_contract.py -q -o addopts='' -p no:cacheprovider --tb=short` → **74 passed**；新增反例先13 failed/5 passed（仅工具输出，未另存红测日志），最终新增文件19 passed（与74重叠）。日志 `var/verification/knowledge-index-2026-11-07/index-target.log`、`index-final.log`。
2. `-m pytest tests/unit/test_knowledge_discovery_planner.py tests/unit/test_knowledge_workspace.py tests/unit/test_knowledge_documents.py -q -o addopts='' -p no:cacheprovider --tb=short` → **54 passed**；四条新增先4 failed。日志同目录 `discovery.log` / `discovery-red.log`。正式Manifest→注册→冻结快照→Planner请求验证说明可见；合成模型不当作真实模型理解证据。
3. `-m pytest tests/contract/test_f05_contract_consistency.py tests/unit/test_knowledge_output_contract.py -q -o addopts='' -p no:cacheprovider --tb=short` → **27 passed**，与第1项output有重叠；日志同目录 `contract.log`。
4. `var/verification/knowledge-meta-codec-2026-11-07/run-postgres.py` → **5 passed**，真实临时PG通过宿主codec/SQL broker验证对象写入、旧值/PENDING/根隔离、新旧CAS、迟到扫描，以及健康MD+空MD→READY→中文全文检索→CURRENT来源/行号且合成源文件不变。对应测试 `tests/integration/test_knowledge_meta_postgres.py`；日志 `postgres.log`、资源清理 `postgres-resources.json`。首轮夹具先初始化broker后建vector类型导致4通过/1失败，调整夹具顺序后5通过；初始日志保留。该组启动时产品已修，不声称其原版红测。
5. 改动路径Ruff、插件store/index/models三源码及workspace、PG测试Mypy通过，`git diff --check`通过；同行只读检查未发现本轮新增阻断。目标验证覆盖此局部插件修复，按本人要求未重复全量或完整PostgreSQL/Worker套件。

精确材料：`sha256:bcf642e3efa642cfa8070ce7926ee7f002b9dbe776f16b8a20f8b5f52f5d9474`，静态清单 `var/verification/knowledge-index-2026-11-07/candidate.json`。必需能力仍仅 `extension.data.sql`，没有第三方运行依赖。临时数据库剩余0，自建容器已删除并确认；本轮未修改原文件/原库、重建原目录、恢复旧任务、调用真实模型、安装/重启原实例、提交或推送。

### 2026-11-07 已授权部署及原目录索引核验

本人明确回复“允许升级、重启并核验索引”。本轮单独基线及owner/revision冻结票据保存在 `var/verification/knowledge-index-deploy-2026-11-07/`，未复用旧部署票据。原始基线未冻结、15任务、Agent待领取/执行作业0；40来源/40活动版本/747分块。

- 加密备份 `20261107T090940_b32cdceff09b4dd4a9d5fb1c62e210aa.pa-backup`，SHA-256 `4d26955fc2bc895a7a4816af19b70aaf05e6f315c41aec823d06e137f159582e`；verify实际通过43表/0 artifact，凭据仍须安全句柄。证据 `backup-create.json` / `backup-verified.json`。
- 正式inspect确认与批准材料一致，升级作业 `extop_b21b37432f3f4482a4124791ebf4084d` 为SUCCEEDED。之后执行一次 `python -Xutf8 -B -m personal_assistant.host_entry restart --profile var/deployment/ext-dev2-2026-10-30/host.json`；新boot `5cff93bde2e8457f9198fc698d48fad9` / guardian26364，ready=true、resumed=false，继续保留部署冻结。
- `verify.py before-resume` 确认升级未改变原目录配置、profile、索引及15个任务，也未改变其他扩展状态；Admin/Agent两runtime加载相同配置摘要/代际，0.1.6 ENABLED及精确hash匹配。
- 本轮独占防重跑helper `index-check.py --profile var/deployment/ext-dev2-2026-10-30/host.json`，仅连接原库及启动已授权0.1.6的helper自有Worker，经ToolGateway执行一次full reindex与一次“数据结构”search；无全局startup/recover，不创建任务或调用模型，不写原文件。Gateway保留风险、能力和schema校验，R1/R0在冻结下允许；不使用直接SQL补READY。该helper的Gateway审计为既有进程内实现，落盘证据是本次脱敏执行记录，不宣称新增持久Gateway审计。
- `index-result.json`：扫描42、unchanged40、skipped_empty2、added/modified/deleted/versions_built/events均0、errors0；`index_state=READY / INDEX_READY`。关键词返回2条、unknown=false、均CURRENT且有非空正文/来源/locator；未记录或输出正文、文件路径。vector_mode=disabled，这是实际全文检索证据，不冒称语义向量或模型回答已验收。
- 原库绑定实际为JSONB object、schema1、READY，完整性与目录绑定检查由真实索引链路通过。索引仍40活动版本/747分块；helper已关闭。`verify.py restore-running` 仅以本轮票据CAS恢复原先未冻结策略，随后 `verify.py verified` 全部核对通过，frozen=false、worker_online=true、mail_send_enabled=false、browser_submit_enabled=false，无旧任务恢复或新作业。
- `old-processes-checked.json` 记录27个旧进程按PID/创建时间均已退出；`native-listeners.json` 核对8000/8001/8010全部只监听127.0.0.1，PID与本boot就绪记录一致。helper结束后配置应用记录只剩两个正常Admin/Agent runtime。

可复查命令（只读，不重复reindex）：`.\.venv-win\Scripts\python.exe -Xutf8 -B var/verification/knowledge-index-deploy-2026-11-07/verify.py verified`。部署脚本的baseline、restore-running及index-check是本轮单次材料，不可当下一次部署模板原样执行。当前辅助脚本AST/Ruff通过；本轮复用上一轮目标与临时PG测试结果，按本人要求没有再次跑全套或完整PostgreSQL/Worker，不把旧测试数量算作本轮新执行。文档差异检查通过；未提交或推送。

本项已DONE，推荐下一编号CORE.RECONCILE.1，尚未开工。用户可在TUI按F5刷新后新开对话，使用“在知识库中检索数据结构，给出来源和定位”；旧任务仍保留原工具快照，不静默升级权限或自动继续。

## CORE.KNOWLEDGE.CONTENT.1（DONE，已授权部署）— 对话读取授权文件正文

本人反馈只能看目录元数据后，只读现场确认原实例配置已应用，但索引绑定仍缺失；原0.1.4也没有单文件正文读取槽。补齐0.1.5的 `knowledge.read` READ工具与 `knowledge.document` ContextProvider，独立于索引，复用同一实例roots与TXT/Markdown/有限PDF抽取器。按精确知识URI或唯一文件名读取，不自动建任务或重建索引。读前后验证文件身份与路径链，歧义/未找到/不支持/待索引/超过完整传输预算均返回明确状态，失败不泄露部分正文。关键词搜索待索引不再表示普通无命中。

宿主仅增加通用Manifest槽描述（可选、旧记录兼容、持久化JSON往返），根据描述路由概览/关键词/单文件。无SDK/RPC/迁移变化，无业务ID或路径分支。两层通用上下文转发不再strip精确资源字符串，原非空和长度校验保留；避免含前导空格的合法文件名被改成另一个文件。

测试与范围（全部以项目根目录 `.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest <以下文件> -q -o addopts='' -p no:cacheprovider --tb=short` 运行）：

- `tests/unit/test_knowledge_documents.py tests/unit/test_knowledge_workspace.py tests/unit/test_knowledge_paths.py tests/unit/test_knowledge_binding.py tests/unit/test_knowledge_output_contract.py tests/contract/test_f05_contract_consistency.py`：108 passed。包含真实临时Windows junction/父junction、忽略目录大小写、来源变化、同名、空探针及真实SDK codec超限；不依赖管理员符号链接权限的测试替换了原skip，最终无skip。三项同行检查缺口均补反例后通过。
- `tests/unit/test_context_provider_descriptions.py tests/unit/test_conversation_routing.py tests/unit/test_extension_context_conversation.py tests/unit/test_extension_staging.py tests/unit/test_extension_registry.py`：56 passed；新增描述契约先6 failed/11 passed。日志 `var/verification/knowledge-content-2026-11-07/descriptions.log`。
- 精确资源两层trim反例先1 failed，修复后 `tests/unit/test_extension_context_conversation.py tests/unit/test_context_provider_descriptions.py`：37 passed。日志同目录 `resource.log`；与上一组重叠，不能相加。
- `tests/integration/test_conversation_workspace.py tests/api/test_workbench_conversation.py`：17 passed、2个既有依赖弃用警告，日志同目录 `conversation.log`。新增正文组合先3 failed；精确前导空格文件先1 failed（误读resume.txt而非空格文件），最终通过。联测走实际API→上下文服务→Supervisor→知识Worker→SDK序列化→合成流式模型输入，验证超过原默认长度的正文完整、单文件来源、不可信边界、未索引状态、同键不重读以及任务/作业为0。没有把合成模型路由结果当作真实模型理解能力证据。

最终格式收尾仅复跑新增documents与F05契约58 passed，日志var/verification/knowledge-documents-2026-11-07/plugin-final.log。相关Ruff、宿主5源码及插件2源码Mypy、差异检查通过；与前述集合重叠，计数不相加。插件静态材料为 `sha256:f05c5a87c5b3bbb472faaab1bf21a48e452f4f00134341f737a9f0e0f207af5c`，无运行依赖或迁移增量；部署前固定的8项宿主文件摘要与实际重启使用材料一致。

按本人要求未运行完整项目套件、完整PostgreSQL/Worker或真实模型。候选验证阶段未操作原实例；随后本人批准后已按下项完成原实例升级和重启，未读取个人正文或重建原索引。F08.6 BLOCKED、F07/F08 IN_PROGRESS不变。

## CORE.KNOWLEDGE.APPLY.1（DONE，已授权原实例升级）

本人明确批准0.1.5与精确材料摘要后，正式inspect再次匹配，升级操作 `extop_ba3352918e5e4037b5bed9a3c8597e66` 返回SUCCEEDED。必需能力仍仅extension.data.sql，无运行依赖或迁移增量。后台先正常restart加载新Manifest字段解析，再upgrade，最后统一restart加载0.1.5；两次均保持本轮独立部署冻结。未使用旧冻结票据或无条件resume，核验后CAS恢复原先未冻结策略。

升级前保存新基线，并生成加密备份 `20261107T060527_b7477022715b4b3799d0c02a4a8c70e0.pa-backup`，SHA-256 `4bc50bc60ab7bd221f344780a22017d54c38d480cd8844e02d12541aaa5c3717`；verify为43表/0登记制品。原profile未改，临时backup-profile只合并已配置的Windows凭据句柄。旧后台枚举新Manifest字段时列表500，基线改用只读持久元数据；重启后列表正常200，新增读取槽和描述已发布/持久化。

最终 `personal.knowledge@0.1.5 / ENABLED`，Guardian41252、boot `3f92cf7dbb7d4c64852c068c22c39d19`，Admin/Agent等组件就绪，Worker在线、未冻结。两次旧进程树27/25个均按PID和创建时间确认退出；Windows原生TCP核对8000/8001/8010都只监听127.0.0.1且属于对应ready进程。原profile/配置、40来源/40版本/747分块、13个任务ID-status-version及其他扩展状态全部不变；两份活跃运行配置均APPLIED。没有强制启用既有隔离插件，没有恢复旧暂停任务。

脱敏证据位于 `var/verification/knowledge-deploy-2026-11-07/`。最终verified.json的ok=true；candidate与8项宿主文件摘要均匹配。复验命令（未来用户更新任务或索引后基线可能合理变化，并非永久健康门禁）：
~~~powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B var/verification/knowledge-deploy-2026-11-07/verify.py verified
.\.venv-win\Scripts\python.exe -Xutf8 -B var/verification/knowledge-deploy-2026-11-07/native-listeners.py
~~~
本轮部署没有读取个人文件正文、调用模型、重建原索引、提交或推送。现有索引绑定仍待就绪，embedding.provider=none；指定文件直读不依赖它，关键词RAG需后续明确重建索引，向量模型也尚未配置。本轮不因RAG建议擅自执行扫描/模型处理。下一唯一NEXT回到CORE.RECONCILE.1；F08.6及其他真实验收状态不变。

## CORE.OUTPUT.1（DONE，已授权升级并重启）— 默认完整输出

2026-11-07：按本人要求取消目录概览50文件/10000字符、上下文12000字符、模型1024输出token及API/TUI回答8000字符默认限制；长历史不静默裁切，运行/安全边界保留。追加修复长回答完整SSE终帧因网络分块触发64KiB阈值的问题，累计2MiB保护不变。目标结果与准确命令见docs/NEXT_STEPS.md本项（没有运行完整套件/真实模型/原库）。知识插件0.1.3材料sha256:f4f63f572a590b99b42c5345a16d91fac4c1aec678dd069d11db9bf7cf3932a1，已获本人确认并正式升级重启，当前0.1.3/ENABLED、Worker在线、未冻结；40来源/747分块/13原任务及目录配置保留。启动隔离再次出现并已通过正式enable恢复，CORE.STARTUP.1恢复为NEXT；F08.6状态不变。

## CORE.CONVERSATION.2（DONE，已授权升级并重启）— 已配置工作区概览与明确模型失败收尾

2026-11-06 根据本人截图修复。只读现场确认 personal.knowledge 0.1.1 ENABLED，保存的一条授权根目录存在；最新对话记录为 STREAMING v2，模型审计只有该轮路由完成，没有回答完成。旧代码未保存具体失败类型，不能据此认定是限额、网络或内容过滤；不要求本人重新填写已保存路径。

候选实现：
- 知识插件0.1.2增加 knowledge.workspace，基于实例roots返回有界目录元数据清单和配置/可访问/截断状态；不读正文或自动索引，清单不冒充已索引资料。正文 knowledge.retrieve 保留。通用宿主按Manifest校验并传递同扩展多provider选择；单provider旧filters形状兼容，不添加核心业务ID分支。
- 明确模型length/content_filter、HTTP拒绝及调用前凭据错误，保存固定FAILED错误码，同键回放不重新调用模型；真正断流/超时/缺尾帧/存储失败仍UNKNOWN。增加本机“放弃未完成回复”：仅STREAMING v2可CAS为FAILED，原键/审计保留，片段不入history；DISPATCHING/READY_TASK/外部动作不适用，迟到成功不能覆盖。
- 更新工作台/契约/README/设计说明。模型适配器的固定终止类型通过共享解析清洗边界重新抛出，先清除原响应，不传供应商正文或秘密。

已执行目标验证（Desktop项目根目录，用.\.venv-win\Scripts\python.exe -Xutf8 -B；pytest统一 -q -o addopts='' -p no:cacheprovider --tb=short）：
1. tests/unit/test_extension_context_conversation.py + tests/unit/test_conversation_routing.py：23 passed；新增provider选择反例最初7 failed、10 passed。之后为兼容旧单provider调整filters形状，再跑 context_conversation + extension_supervision_service：62 passed。两组重叠，数字不相加。
2. test_knowledge_workspace.py + test_knowledge_paths.py + test_knowledge_output_contract.py：37 passed；目录概览反例先8 failed、1 passed。tests/contract/test_f05_contract_consistency.py：20 passed。
3. tests/integration/test_conversation_workspace.py：2 passed、2个既有依赖警告。实际API→上下文服务→Supervisor→知识worker→SDK JSON往返→合成流式模型，配置与两个文件名进上下文、sources非空、任务/作业均0，同键不重查目录；双provider一次RPC，空正文检索仍保留概览。不是跨进程Worker/数据库验收。
4. 明确模型失败/回复放弃/原流式/模型适配器最终113 passed、2个既有弃用警告，日志var/verification/conversation-check-2026-11-06/model-failure-discard.log。命令如下：

~~~powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/integration/test_conversation_model_failures.py tests/unit/test_tui_conversation_failures.py tests/unit/test_model_streaming.py tests/api/test_workbench_conversation.py tests/unit/test_tui_stream.py tests/unit/test_tui_conversation.py tests/unit/test_model_adapters.py -q -o addopts='' -p no:cacheprovider --tb=short
~~~

先失败后修复：明确终止反例7 failed/4 passed；放弃回复入口7 failed/11 passed；补充路由length/filter后2 failed/65 passed，定位共享base解析清洗抹掉新类型。最小修复清洗边界后，上述最终113项全过。没有删断言、调高生产超时或重新发原请求。本轮相关Ruff、Mypy（宿主3+6源码、插件3源码分组）及差异检查通过；活动目标测试运行器0。

真实DeepSeek只使用公开合成目录清单：文件清单问题与“路径已写入配置”的追问均选workspace；回答57个非空delta，包含两个正确文件名并正常结束。三次请求报告1767 tokens；未发真实目录/正文/任务/邮箱，未创建原实例任务。可复现脚本与脱敏结果：var/verification/conversation-check-2026-11-06/workspace-model-check.py及同名.json；原错误分类反例在stream-failure-probe.json。该脚本会产生三次模型调用费用，不自动重复。

插件静态候选：personal.knowledge@0.1.2，材料SHA-256 a7a4f5e6510a50d0b622e7753a3cd84d5adb7cad3d1363f48f0115ce5aacc290；必需能力仍仅extension.data.sql，无第三方运行依赖，旧迁移/state_schema_version不变。候选阶段未操作原实例；随后本人确认后已按以下部署记录应用，目录和索引保留。

按本人避免完整套件过度验证要求，没有重跑全量、完整PostgreSQL或真实Worker；本地结果不等于原实例已加载修复。未清空原库/幂等记录、未恢复旧任务、未提交推送。下一编号仍为CORE.RECONCILE.1（WAITING），F08.6 BLOCKED、F07/F08 IN_PROGRESS不变。


### 2026-11-06 已授权原实例升级与重启

本人确认精确sha256:a7a4f5e6510a50d0b622e7753a3cd84d5adb7cad3d1363f48f0115ce5aacc290后，正式inspect再次匹配，同键upgrade操作extop_57e3149f5bdc4003b4316084ba522cf5返回SUCCEEDED。未重建材料/改版本/新增迁移。升级前只读保存配置摘要、索引摘要及9个任务状态；确认READY/LEASED Agent作业0后临时freeze。

通过host_entry stop正常停止原Guardian与四组件，确认旧PID全部退出、8000/8001/8010释放，再以serve --profile var/deployment/ext-dev2-2026-10-30/host.json --admin隐藏启动。初次重启后知识插件变为QUARANTINED，未记recover失败操作；保持冻结并通过正式enable同一已授权0.1.2重新健康检查，操作extop_e72559381b8f4454b6999fbb5dc333dc为SUCCEEDED。现ENABLED，两个ContextProvider可见；新conversation/discard路由已加载。该启动隔离现象需要另项复现，不能把这次升级说成完全无异常；双进程同时recover的STARTING状态竞争只是与源码相符的推断，尚未证实根因。其他ehall/smail隔离时间早于本次重启，未改变其状态。

最终核对：配置摘要相同；4来源/4活动版本/20分块及来源版本摘要相同；9个原任务ID/status/version全部相同，未恢复旧暂停任务；无待领取/执行中的Agent作业。随后恢复原先未冻结状态，Worker在线、model_configured=true、health正常，mail_send_enabled/browser_submit_enabled仍false。原TUI进程需本人Ctrl+Q后重新打开以加载新按钮；没有强制关闭其窗口或丢弃未完成回复。

脱敏部署记录：var/verification/conversation-deploy-2026-11-06/{baseline,operation,reenable,stopped,verified}.json。可复验当前与当次基线元数据：
~~~powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B var/verification/conversation-deploy-2026-11-06/verify.py
~~~
该只读脚本在未来用户创建任务或更新索引后可能因基线变化而失败，不能当作永久健康门禁。停止后的首次进程核对因psutil未安装失败，未安装依赖；改用Windows原生Get-Process/Get-NetTCPConnection确认全部退出。最终verified.json的ok=true。本轮仅应用上轮已验证材料及现场部署核对，没有重复全量/目标测试、没有发起模型调用/正文检索、提交或推送。F08.6仍BLOCKED，不据此宣布其他阶段通过。

### 2026-11-07 本人授权的日用可靠性修复顺序

按本人新指令依次完成六项，每项完成目标回归后再切换唯一NEXT；前两项通过后继续后四项，不中途要求重复确认既有开发范围。不增加真机测试步骤，不擅自修改原实例扩展状态或安装新材料。

1. CORE.STARTUP.1（DONE，已授权部署）：验证并修复多宿主恢复竞争；保留真正失败/崩溃隔离、正确操作回执与诊断。
2. CORE.CONFIG.1（DONE，已授权部署）：保存配置与运行配置明确区分；统一应用，Admin/Agent/Worker同步代际，目录切换后旧索引失效/隔离；已保存/待生效/已生效/待索引状态。
3. CORE.TOOLSET.1（DONE，已授权部署）：旧任务固定工具快照的明确提示，以及携带原目标创建新任务；不改旧权限、审批、未决动作。
4. CORE.DIAGNOSTIC.1（DONE，已授权部署）：脱敏错误分类与关联编号，模型不得把工具不可用说成目录未配置。
5. CORE.HOST.1（DONE，已授权部署）：重复serve明确已运行，受支持restart正常停止/等待/启动/就绪，保留或明确冻结策略。
6. CORE.COMPAT.1（DONE，已授权部署）：TUI启动核对后端接口能力与版本，提前提示不兼容；整理对话/任务投影，避免将原始JSON当用户目标展示。

本人已要求避免完整套件过度验证：每项运行改动链路及相关故障回归，涉及跨进程持久同步补隔离数据库目标验证；保留失败证据及真实依赖边界。全部六项通过后才做本批次完整交付汇总，不把本地通过写成原实例已加载。

## CORE.COMPAT.1（DONE，已授权部署）— 接口能力协商与可读任务历史

新增只读 capabilities 协议。TUI 启动、远程动作开始及远程确认返回后检查协议与必需能力；旧后台、畸形声明和网络不可达分别提示。没有清除 pending、原键、输入或已确认历史，不降级或自动重放请求。本机参数读取独立于后台；已有未确认命令时仍沿用原串行操作限制。

任务列表投影原目标，详情按历史用户/历史助手显示完整参考对话；只识别两种严格宿主格式，任意 JSON 或异常包装原样显示。格式不是来源证明，持久原文、创建载荷、权限/审批判断不使用展示投影。80×24 终端保留列表可读高度，压缩任务按钮行并去掉重复内边距，所有按钮保持可见。

可复现命令（项目根目录，pytest 均使用 `.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest <文件> -q -o addopts='' -p no:cacheprovider --tb=short`）：
- `tests/api/test_workbench_capabilities.py tests/unit/test_tui_capabilities.py`：21 passed（先20 failed/1 passed），日志 `var/verification/config-check-2026-11-07/compat-api-service.log`。
- `tests/unit/test_task_presentation.py`：32 passed（先32 failed），精确包装/长历史/重复键/尾部内容边界均保留。
- `tests/unit/test_tui_compatibility.py tests/unit/test_tui_task_toolset.py tests/unit/test_tui_task_history.py tests/unit/test_tui_configuration_apply.py tests/unit/test_tui_app.py`：最终33 passed，日志 `compat-confirmation.log`。确认期间更换后台的3个反例先全部失败；补确认返回后的协商后通过。包含原请求保留、设置、工具快照和原文新建回归。
- `tests/unit/test_tui_list_layout.py`：最终12 passed，日志 `compat-layout.log`。新增窗口缩放回归先1 failed/1 passed；保持原列表高度和业务断言，修正窄屏按钮横向/纵向越界。
- TUI 子系统前一次合并运行116 passed/1 failed（窄屏移除按钮不可达），日志 `compat-tui-final.log`；其失败及之后涉及的路径由上述两组定向复跑覆盖，未再次跑整套，不写成一轮全绿。更早的两次合并运行因测试等待挂起被中断，`compat-tui-regression.log`、`compat-tui-core.log` 不是通过证据。
- 测试等待辅助函数改为有界等待；额外吞取消反例先失败，修复后确认超时明确失败且不遗留 waiter。没有放大生产超时或弱化业务断言。
- 本项12目标文件 Ruff、4源码 Mypy 和差异检查通过。各组有重叠，计数不相加。

六项均已完成本地实现与目标验证。按本人避免完整套件过度验证要求，本批次没有重跑全项目或完整 PostgreSQL/Worker 门禁；恢复竞争已补真实临时 PostgreSQL、配置同步和主机重启已补隔离真实进程测试，证据见对应编号。没有新增手机步骤，未调用真实模型、访问原库或原服务、安装/升级扩展、提交或推送。本地通过不代表原实例已加载；personal.knowledge 0.1.4 仍为源码候选，既有 F08.6 BLOCKED、F07/F08 IN_PROGRESS 不变。

## CORE.APPLY.1（DONE，已授权部署）— 本批次原实例应用

2026-11-07 本人明确要求部署六项修复后执行。personal.knowledge 0.1.4 静态材料及正式 inspect 均为 `sha256:2c246d13b7b45c503db0aa6b1442b9e34da2bb7a2173caabc0557adb1172bc27`，能力仍仅 extension.data.sql，无新增第三方运行依赖或核心迁移。操作 `extop_81745f1c101d47caae01bec9aba38fbb` 唯一提交、轮询至 SUCCEEDED。

部署前记录1根目录配置、40来源/40版本/747分块、13任务及其他扩展状态摘要，Agent READY/LEASED作业为0，原实例未冻结。临时冻结后创建加密恢复点 `20261107T043406_39e494fb073f4abca74c60072887b5a2.pa-backup`，验证43表/0登记制品可读取；没有恢复或覆盖原库。保存271个宿主/SDK/网页及启动文件摘要和本次候选源码归档；这不是旧运行进程内存代码的回退镜像。

先正常停止旧Guardian10060，确认其25个进程已退出、守护锁释放及8000/8001/8010关闭，再使用原profile和.venv-win、Admin模式隐藏启动新版。首轮新后台已正常恢复原启用扩展，没有再次错误隔离。正式升级知识插件后，使用新版restart正常停止前一23进程并启动新Guardian2784（launcher35632），boot `a85d2c2aa41347008902c3bb7d93905d`，Admin模式保留。该restart在冻结状态执行，回执ready=true/resumed=false；完成只读核验且部署冻结票据未变、Agent作业仍0后，CAS恢复部署前未冻结策略，不恢复旧任务。

最终核验：配置/profile/索引/13任务摘要不变，其他扩展状态不变；0.1.4 ENABLED，配置APPLIED且Admin/Agent两个活跃副本摘要一致，四组件就绪，Worker在线，DeepSeek配置与凭据存在性正确，TUI真实客户端协商协议1通过，271代码摘要不变。Windows原生TCP表核对8000/8001/8010只绑定127.0.0.1，并与本次ready记录的实际PID相符。PowerShell监听查询曾返回空，未把该结果记为通过；采用原生表补证。首个就绪探针误将ready布尔值当作映射导致AttributeError，修正核对方式后逐组件通过，不属于服务故障。

旧知识索引缺少 `index.binding.v1` 已只读确认，因此0.1.4在首次完整reindex前显示PENDING；没有清空索引、自动重扫目录或手写READY。nju.ehall与nju.smail原有QUARANTINED保留，未强制启用。mail_send_enabled和browser_submit_enabled仍false，没有发送邮件、模型试调用、浏览器提交或原任务恢复；没有新增手机步骤、提交或推送。F08.6 BLOCKED、F07/F08 IN_PROGRESS不变。

证据目录：`var/verification/daily-deploy-2026-11-07/`（Git忽略），包括baseline、精确preview/原请求、operation-latest、restart.log、before-resume、verified、tui-readiness及native-listeners。`verified.json`最终ok=true/frozen=false；`restore-running.json`记录的是CAS恢复前快照，当前状态以verified为准。

只读复验命令（未来本人变更任务、目录或索引后，基线比较可能合理失败）：
~~~powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B var/verification/daily-deploy-2026-11-07/verify.py verified
.\.venv-win\Scripts\python.exe -Xutf8 -B var/verification/daily-deploy-2026-11-07/native-listeners.py
~~~

本轮复用上一轮已通过的目标验证，按本人避免完整套件过度验证要求未重复全套/完整PostgreSQL-Worker门禁；本次新增证据是原实例正式升级、正常重启及只读部署核验，不将其称为全系统业务验收。下一编号回到CORE.RECONCILE.1，本轮不继续编码。

## CORE.HOST.1（DONE，已授权部署）— 可靠重启与运行归属

新增 `host_entry restart --profile ...` 与 `assistant-host.ps1 -Action restart`：串行重启锁、跨进程冻结票据 CAS、原 Admin 模式保留，先正常停止并等待旧守护器释放锁，再隐藏启动和确认新 boot/受监督 PID 归属与全部组件就绪。原冻结保持；原未冻结仅在本次票据仍有效时恢复领取。旧版缺 boot 的守护器明确返回 HOST_RESTART_UNSUPPORTED，需一次 stop/serve 加载新版；不按旧 PID 强杀。

真实 Windows 验证发现并修正两个问题：venv启动器PID与实际Python PID不同，使用本次boot及直接父子归属验证；普通状态读取会与原子替换发生WinError5共享冲突，守护器级已确定性复现退出，现仅对5/32/33短暂共享冲突作2秒有界重试，永久失败仍抛出且清理临时文件。

验证命令统一使用 `.venv-win/Scripts/python.exe -Xutf8 -B -m pytest <文件> -q -o addopts='' -p no:cacheprovider --tb=short`：
- `tests/unit/test_host_restart.py tests/unit/test_host_diagnostics.py`：最终23 passed；先7个重启反例失败，父子PID及真实文件竞争反例也先红后绿。
- 相邻host_delivery/scheduler集合此前36 passed（与上项重叠，不相加）。
- `tests/integration/test_host_restart_process.py`：2 passed（49.25秒）；增加1.3秒状态只读占用后，仅重跑未冻结分支1 passed（24.06秒，重叠不相加）。覆盖重复serve不改变冻结、两种冻结策略、新旧真实进程归属/就绪及全部自建PID和端口回收。
- Ruff8目标文件、Mypy6源码、PowerShell语法和差异检查通过；新进程测试Ruff/Mypy另过。

仅独立临时内存主机和合成组件/记录；未调用模型、访问原库、操作原实例或安装扩展，未跑完整套件。下一编号CORE.COMPAT.1。

## CORE.DIAGNOSTIC.1（DONE，已授权部署）— 固定失败码与关联编号

重复启动、端口占用、配置格式、数据库/模型凭据、扩展恢复、组件启动/退出均提供固定宿主码+diag编号；重复serve及无child预检失败不冻结原运行状态。child只保存code/id/component/pid，拒绝旧PID和污染字段，stderr仍不落盘。Agent将模型/工具/配置失败分类，保留原状态和UNKNOWN、不自动重试；checkpoint/execution/API/TUI只投影固定说明及合法关联编号。明确工具缺失不代表用户目录没填。HTTP关联编号限制为host格式；TUI不显示供应商原始错误message。

目标证据：
- `python -m pytest tests/unit/test_host_diagnostics.py tests/unit/test_host_delivery.py ...`19 passed；首6红，最后新诊断文件9 passed。Ruff4文件/Mypy3源码通过。
- `python -m pytest tests/unit/test_runtime_failure_diagnostics.py tests/unit/test_runtime_delivery.py tests/unit/test_runtime_queue_lifecycle.py tests/unit/test_runtime_remediation_delivery.py tests/unit/test_runtime_tool_failure_observations.py tests/unit/test_planner_validation_diagnostics.py tests/unit/test_conversation_routing.py ...`54 passed，覆盖11类合成异常、原UNKNOWN/取消/熔断；Ruff/Mypy4源码通过。
- `python -m pytest tests/api/test_workbench_failure_diagnostics.py tests/api/test_workbench_api.py -k trace ...`7 passed/11 deselected/2 warnings。安全哨兵不进响应，检查点不变；Ruff/Mypy1源码通过。
- `python -m pytest tests/unit/test_tui_error_diagnostics.py tests/api/test_request_diagnostic_id.py tests/unit/test_tui_service.py tests/unit/test_tui_stream.py ...`25 passed；追加Agent投影后与test_tui_task_diagnostics合并7 passed，2既有警告；先4红+1投影红。Ruff/Mypy3源码通过。

以上`...`均为`-q -o addopts='' -p no:cacheprovider --tb=short`。未跑全量、原服务/库、模型或真机；没有安装部署。下一编号CORE.HOST.1。

## CORE.TOOLSET.1（DONE，已授权部署）— 固定工具快照与携目标新建

trace与execution提供只读toolset投影，区分UNBOUND与空工具快照，明确新增工具/原工具版本或grants变化；历史未存extension_id的项目标null，不伪造证据。TUI显示原因，提供“携原目标新建”；确认前后检查未决/活动状态和目标版本，只创建独立任务，不复制旧checkpoint、审批、Outbox或权限。原任务保持不变。

验证：API先17失败反例，最终`python -m pytest tests/api/test_workbench_toolset.py tests/api/test_workbench_api.py -q -o addopts='' -p no:cacheprovider --tb=short`得30 passed/2 warnings；日志`var/verification/config-check-2026-11-07/toolset-api.log`。TUI先2红，`tests/unit/test_tui_task_toolset.py tests/unit/test_tui_task_diagnostics.py`初4过；追加确认期间转未决后新文件最终3 passed（toolset-tui.log），原诊断2过。中间测试启动挂起的精确进程已回收，改为日志直写后正常完成，未用删断言或生产超时绕过。Ruff与API两源码Mypy通过。未跑全量、未创建原实例任务或提交部署。下一编号CORE.DIAGNOSTIC.1。

## CORE.CONFIG.1（DONE，已授权部署）— 保存、应用与知识索引绑定

非秘密保存稿与已应用快照分离，apply绑定摘要/扩展版本；活跃进程持有OS锁并报告加载摘要，全部确认才APPLIED。Worker轮询及派发前同步，在途R2不取消/重放，跨配置READ结果拒绝发布。异进程禁用/隔离/升级时不复活缓存旧版本；失败代际不自动重试，可修正配置或重启后台。TUI新增应用按钮及独立状态，配置校验422与已卸载详情404不阻断其他面板。知识插件0.1.4候选将索引绑定真实根/扫描选项，换根及遗留索引先PENDING，完整扫描才READY。

可复现目标命令（均加`-q -o addopts='' -p no:cacheprovider --tb=short`）：
- `python -m pytest tests/unit/test_extension_config_application.py tests/unit/test_extension_config.py`及既有能力目标：实施者36 passed（含11新应用/生命周期边界）；先写5红、READ跨代际红及3个禁用/升级反例。
- `python -m pytest tests/unit/test_knowledge_binding.py tests/unit/test_knowledge_index.py tests/unit/test_knowledge_workspace.py tests/unit/test_knowledge_output_contract.py`：67 passed；最初绑定6反例全红。日志`var/verification/config-check-2026-11-07/knowledge.log`。
- `python -m pytest tests/integration/test_configuration_process_sync.py`：1 passed，真实双宿主进程+合成Worker验证save不生效、apply两端确认、退出释放lease；无真实扩展安装或数据。
- `python -m pytest tests/api/test_admin_extension_config.py tests/unit/test_tui_service.py tests/unit/test_tui_configuration_apply.py`初轮20 passed；后补schema变更422、详情404隔离，API+TUI13 passed。TUI既有相邻19 passed/1旧文案断言失败，保留不重启提示后该单例1 passed。
- 目标Ruff与Mypy（Admin/TUI3源码、插件12源码）通过；JS语法通过。没有跑完整套件、真实模型、真机或原实例，避免用户禁止的过度验证；没有build/install扩展或提交推送。

下一编号CORE.TOOLSET.1；原实例仍需最后准备精确候选并获授权后应用，不能把本地通过视为已部署。

## CORE.STARTUP.1（DONE，已授权部署）— 重启后的扩展恢复隔离排查

基于本轮升级重启时实际观察到的personal.knowledge隔离，先用独立定向反例复现Admin与Agent同时恢复的STARTING竞争；确认根因后做最小修复，保留真正崩溃隔离和幂等恢复。不能把猜测写成已验证原因，不自动重启/恢复其他业务扩展或旧任务。CORE.RECONCILE.1继续WAITING。本轮未开始该项编码。


2026-11-07 本地修复证据：确定性并发反例先3 failed/1 passed，确认双进程恢复将活跃STARTING误隔离，以及interrupt_running误伤正在启停的操作。恢复、安装/启停/升级命令在持久生命周期协调锁下串行，PostgreSQL采用不占应用池的专用会话锁连接；进程崩溃自动释放，原真正STARTING残留和健康失败继续QUARANTINED。未认领请求取消不晚执行，已持久请求断连继续按原操作收尾，恢复取消关闭已启动Worker。

复验：监督器及新增并发单元55 passed；`PA_TEST_DATABASE_URL=<独立临时库> .venv-win/Scripts/python.exe -m pytest tests/integration/test_extension_recovery_concurrency.py -q -o addopts='' -p no:cacheprovider` 得6 passed，覆盖两个数据库池、活跃disable、真正残留、取消等待、pool_max_size=1及真实子进程终止后的锁释放。测试先捕获单连接池死锁，修为专用连接后通过；连接清理比较启动基线（原迁移连接已存在），不削弱回收断言。Ruff/Mypy目标源码通过。未跑全量：按用户要求只测改动路径和并发/数据库故障边界；未操作原库原服务、安装扩展、部署、提交或推送。下一编号CORE.CONFIG.1。

## CORE.CONVERSATION.1（DONE，本地及合成模型验证；原实例待重启）— 实例工作环境与对话自动任务

2026-11-06 已按本人要求完成：复用实例级 roots/索引；automatic 对话按意图直接回答、调用选定的已启用 ContextProvider 只读取证，或将原用户目标保存为真实持久任务。聊天留在当前页显示来源/警告及任务创建回执，侧栏刷新最新状态。保留显式 Agent 任务按钮、manual 纯问答、最终审批、UNKNOWN 与原熔断。没有新增业务专用表/ID或数据库迁移。

改动：新增 core/conversation.py、core/context/extension_provider.py、api/workbench_conversation.py；修改 extensions/supervision.py 的通用检索包装、api/workbench.py 路由、cli/tui/{app,service,panels}.py；新增五份目标测试、调整TUI测试夹具；README/TODO/NEXT/CONTRACTS/DESIGN/TUI指南同步。知识扩展代码和版本本轮未改，现有0.1.1可复用；不把工作环境解释成全磁盘授权。

本轮可复现目标命令（Desktop 项目根目录，Python统一使用 .\.venv-win\Scripts\python.exe -Xutf8 -B）：

~~~powershell
python -m pytest tests/api/test_workbench_conversation.py -q -o addopts='' -p no:cacheprovider
# 最终9 passed；最初新增7项全失败（接口尚不存在），另11项旧测试通过。
python -m pytest tests/api/test_workbench_conversation.py tests/unit/test_conversation_routing.py tests/api/test_workbench_stream.py -q -o addopts='' -p no:cacheprovider
# 中间快照22 passed，包含当时7项新API+4项路由/总时限+11项原流；随后API补2项单独全过。
python -m pytest tests/unit/test_extension_context_conversation.py tests/unit/test_context_manager.py tests/unit/test_extension_supervision_service.py -q -o addopts='' -p no:cacheprovider
# 实施时56 passed；随后新增并行有界检索一项，只复跑新文件11 passed。
python -m pytest tests/unit/test_tui_conversation.py tests/unit/test_tui_stream.py -q -o addopts='' -p no:cacheprovider
# 21 passed；新TUI8项最初全失败。
python -m pytest tests/unit/test_tui_app.py tests/unit/test_tui_service.py tests/unit/test_tui_polish.py -q -o addopts='' -p no:cacheprovider
# 34 passed。
python -m pytest tests/integration/test_conversation_dispatch.py -q -o addopts='' -p no:cacheprovider
# 2 passed。真实API→Runtime→Gateway，合成模型/执行器：READ调用一次后SUCCEEDED；R2停WAITING_APPROVAL且执行器零调用。
~~~

上述集合有重叠，数字不相加。最后UI/整合联合复跑时9 passed、1 failed：断言仍要求旧“尚未完成”文案，现改为严格断言创建时状态/实时状态区别，并补SUCCEEDED刷新验证；该用例单独复跑1 passed。最后8源码联合Mypy、所有本轮源码/测试Ruff及git diff --check通过。原创建回执重放不会新建任务/模型调用；READY_TASK写回执失败可恢复原任务；取消/80秒总deadline回收读取协程，原命令不会重调模型。并行检索过滤SECRET/失效/源版本变化、保留来源定位，资料仅作不可信字段。

按本人避免过度完整套件的要求，没有重跑全量、完整PostgreSQL/真实Worker套件；本轮也未运行临时PostgreSQL验证、真实模型分类/手机验收。生产持久路径复用既有RecordStore/TaskService同事务，不能把内存回归写成新增数据库验收。旧中文短词召回/文件清单枚举边界保留，模型意图识别仍需真实体验；详见TUI指南。本轮没有安装/升级扩展、变更原库/profile、重启/解冻原实例、恢复旧任务、提交或推送。后台与TUI重启后本轮源码才生效。下一编号回到CORE.RECONCILE.1；F08.6 BLOCKED、F07/F08 IN_PROGRESS不变。


### 2026-11-06 本人要求的补充测试

本轮只跑本功能定向验证，没有全量重跑或原实例重启。测试中实际发现并修正两点：

1. 原心跳测试在80ms测试预算内还要求先收到10ms心跳，宿主繁忙时先超时而失败。拆为“已启动的源产生心跳”和“总时限到期回收”两个独立用例，保留关闭断言；生产80秒预算未改变。首轮后端为25 passed、1 failed；不计全绿。
2. 真实DeepSeek验证的三个意图请求均返回ModelProviderRejectedError，资料回答流正常。分流提示词虽给了对象样例却没有JSON字样，与已启用JSON模式不兼容。新增真实适配器+MockTransport协议反例先失败，再只为routing_request补上明确JSON对象说明。依据[DeepSeek JSON模式说明](https://api-docs.deepseek.com/guides/json_mode/)。不关闭JSON校验、不放宽输出字段、不改审批。

最终分组结果（无重复相加，共49个不同相关用例）：
- API/分流/预算/真实Runtime-Gateway整合：17 passed、2个既有依赖警告。
- 通用上下文11项在首轮组合中全部通过，源代码未变，没有重复运行。
- TUI新对话与原流式：21 passed。
- 修改路径Ruff、Mypy与git diff --check通过，已结束的测试进程已核对回收。

本轮命令：
~~~powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/api/test_workbench_conversation.py tests/unit/test_conversation_routing.py tests/integration/test_conversation_dispatch.py -q -o addopts='' -p no:cacheprovider --tb=short
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/unit/test_tui_conversation.py tests/unit/test_tui_stream.py -q -o addopts='' -p no:cacheprovider --tb=short
~~~

另用原有DeepSeek安全凭据做公开合成内容验证：修复后三项分别正确识别ANSWER、RETRIEVE、TASK；测试资料BLUE-42的真实回答收到58个非空增量且与终态一致。成功请求报告用量共1567 tokens（3个路由请求1302 + 已通过的流265）；失败请求无用量回执，不推算其费用。没有向模型发送真实知识库/邮箱/任务历史，没有为这次模型验证创建原实例任务。已通过的流式请求未重复。

日志及可复现临时脚本保存在Git忽略的var/verification/conversation-check-2026-11-06/：backend.log保留初次失败，backend-fixed.log、tui.log为最终定向结果；json-before.log保留JSON反例；live-model.json保留初次真实请求失败与流通过，live-model-fixed.json为修复后三项通过，live-model.py --routes-only可重新进行三次有费用的合成检查，不自动再次调用。脚本只使用SecretHandle/WCM，不保存密钥。

本轮改动仅core/conversation.py、tests/unit/test_conversation_routing.py和此状态记录。未运行PostgreSQL/完整Worker套件，未安装扩展、访问个人文件、变更原库/profile、重启/解冻、提交或推送；不宣称原实例已经加载新功能。下一项仍为CORE.RECONCILE.1，F08.6 BLOCKED不变。

## CORE.KNOWLEDGE.1（DONE，本地验证及已授权部署；真实查询待本人）— 命中结果契约与失败观察

2026-11-06 用户补图后只读确认：后台未冻结；4个来源均有活动索引。“我的测试短语”在索引内全文/字面匹配均为0；“数据结构”全文命中3个分块、字面匹配4个分块。新用户输入已进入新运行片段，原计数正常重置，随后真实工具连续失败5次、同参无进展3次触发熔断。没有关闭或提高任何熔断阈值。

根因与修复：personal.knowledge 0.1.0将不适用的page/line_start/line_end输出为null，违反既有integer结果Schema；空结果正常，有命中时经Gateway被拒。0.1.1只省略不适用字段，保留定位、来源及CURRENT/STALE/DELETED正文保护，不放宽Schema、不改旧迁移或重建索引。通用Agent此前只保存outcome.result，使FAILED变成null；现在失败观察只包含固定7字段：kind/tool_id/tool_version/error_code/retryable/same_call_as_previous/message，不转发原异常、参数或资料正文。成功结果仍原形，UNKNOWN提前暂停，进展计算仍使用原始outcome。Planner区分失败和成功空结果，避免无条件重复调用。

修改范围：core/agent/{runtime,planner}.py；extensions/personal_knowledge的worker.py、__init__.py、extension.toml、pyproject.toml、README；新增test_runtime_tool_failure_observations.py、test_knowledge_output_contract.py，同步契约版本断言；TODO/NEXT/契约/TUI说明。知识库版本升为0.1.1，必需能力仍仅extension.data.sql，无第三方运行依赖。

目标验证（Desktop项目根目录）：

~~~powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/unit/test_knowledge_output_contract.py tests/unit/test_knowledge_index.py tests/unit/test_knowledge_extractors.py tests/contract/test_f05_contract_consistency.py tests/unit/test_tool_gateway.py -q -o addopts='' -p no:cacheprovider
# 92 passed；新Gateway契约反例最初6 failed、1 passed，修复后全绿。
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/unit/test_runtime_tool_failure_observations.py tests/unit/test_planner_validation_diagnostics.py tests/unit/test_runtime_delivery.py tests/unit/test_runtime_remediation_delivery.py tests/unit/test_runtime_crosscheck_delivery.py tests/unit/test_runtime_queue_lifecycle.py tests/unit/test_runtime_conversation_context.py tests/unit/test_safety_fuse.py -q -o addopts='' -p no:cacheprovider
# 48 passed；新失败观察最初5 failed、2 passed，修复后7项全绿。
~~~

Ruff（本轮源码/测试7文件）、Mypy（worker/runtime/planner三源码）、git diff --check通过。合成中文只读SQL确认连续句子内短词可能被simple FTS漏召回；该已有检索局限另行保留，本轮不改词法，不把vector_mode=disabled当作模型配置故障。按本人避免过度完整套件的要求，未重跑全量/PostgreSQL/真实Worker测试套件；没有调用真实模型或运行用户的知识库查询，不将本地合成验证当作真实查询成功。

本人明确授权精确材料升级并重启后，静态inspect再次核对如下摘要，再走正式upgrade：
- 版本personal.knowledge 0.1.1，替换0.1.0。
- 材料SHA-256：4d83a4b6cdee5b52a27d9dd778e0bd255832226526732e4688c758d88cb37234。
- operation extop_3ccc5079d0f3479485bd5dc1d9ce82bb：SUCCEEDED；重启后ENABLED。
- 授权目录配置规范化摘要前后一致，重启后4个来源/4个活动索引保留。
- 正式stop后原守护器及四组件已退出，再隐藏启动新守护器；核实Agent待领取作业0后恢复原未冻结状态。Worker在线、health正常。
- 旧任务task_e7d7f89f45f74560abb9fa9a2d67dc28保持PAUSED_SAFETY，未恢复或重放。原任务绑定0.1.0，应新建查询任务使用0.1.1；不需再次reindex。

现场脱敏记录位于var/verification/core-knowledge-2026-11-06/upgrade.json（Git忽略）。首次inspect因缺少Idempotency-Key被400拒绝，未发升级；补齐头后唯一升级成功。一次只读状态查询误用tasks.state列，修正为status后验证完成；不将失败探针当作通过。没有提交或推送。下一编号回到CORE.RECONCILE.1；中文短词召回与fake SQL语义偏差另记后续局部修复。F08.6 BLOCKED、F07/F08 IN_PROGRESS不变。

## CORE.PLAN.1（DONE，本地验证；原任务待本人重启后继续）— 知识库任务提案诊断与排队提示

2026-11-06 只读确认原任务的 personal.knowledge 已启用、授权目录存在、reindex/search 均在该运行工具快照；没有工具派发/观察记录。旧代码统一吞成通用校验提示，无法恢复历史失败的具体分类，不将其倒推为某种模型参数错误。当前冻结文件早于本次 guardian 启动22秒；未发现当前备份/磁盘故障反复冻结的证据。

本轮最小实现：严格提案校验新增固定错误码（JSON、字段、工具/版本、参数、决策、说明），区分宿主准备/资源/工具/工作流校验；安全诊断进入 Checkpoint/Execution，trace 和 TUI 按白名单重建，不返回原模型输出、参数或异常消息。提示词采用合法 JSON 示例，要求每次只选择下一步并等待观察；不自动重试。排队时显示“等待领取（上次阶段：waiting）”。正常后续提案清理旧诊断，派发后 R2 校验异常仍保留 WAITING_RECONCILIATION，不允许普通继续重放。

改动文件：core/agent/{decisions,planner,runtime}.py、api/workbench.py、cli/tui/app.py、tests/unit/test_planner_validation_diagnostics.py、tests/unit/test_tui_task_diagnostics.py、tests/api/test_workbench_api.py，以及本节、TODO、契约和TUI说明。没有更改扩展、数据库结构、审批规则或熔断阈值。

可复现本轮目标验证（Desktop 项目根目录）：

~~~powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/api/test_workbench_api.py tests/unit/test_tui_task_diagnostics.py tests/unit/test_tui_polish.py tests/unit/test_tui_list_layout.py -q -o addopts='' -p no:cacheprovider
# 35 passed、2 个既有依赖弃用警告。
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/unit/test_planner_validation_diagnostics.py tests/api/test_workbench_api.py::WorkbenchApiTests::test_trace_projects_only_allowlisted_validation_diagnostic tests/unit/test_tui_task_diagnostics.py -q -o addopts='' -p no:cacheprovider
# 最后新增诊断/安全投影/TUI组合11 passed、2 warnings；与上项重叠，不累加。
~~~

先失败后修复：模型诊断最初5项失败；API/TUI3项最初均失败，修复后通过。扩大运行器回归期间一个新增workflow测试夹具误写字段，修正为既有type契约后8项新测试通过；不把之前失败轮记为全绿。五个改动源码文件Mypy（3+2分组）、八个源码/测试文件Ruff通过。

未操作原任务/原库，不重启、解冻、安装或执行插件；原任务尚未索引或搜索。尝试发起一次只用公开合成内容的DeepSeek协议检查（45秒、最多512输出token），但执行返回丢失，无法确认请求是否到达模型或验证结果，不计通过、未重复请求。按本人避免过度完整套件要求，仅验证受影响路径和安全回归，未重跑全量/PostgreSQL/Worker，不宣称真实知识库任务完成。下一编号回到CORE.RECONCILE.1；F08.6 BLOCKED、F07/F08 IN_PROGRESS不变。

## UX.QUEUE.1（DONE，本地验证）— 任务队列管理与列表面板稳定性

2026-11-06 用户反馈无法完成的任务退出不及时、TUI缺少移除/排序入口，以及任务/扩展列表面板过窄且滚动抖动；随后明确不是滚动条本身。按本轮请求优先修复任务取消/失败收尾、持久队列排序和TUI列表布局与刷新。保留等待输入/审批/UNKNOWN的不同语义，不清空原库、不删除审计和回执、不重启或解冻原实例。范围为任务/队列服务及对应持久适配器、本机工作台API/TUI和目标回归；先失败反例，按用户要求避免重复完整套件。CORE.RECONCILE.1顺延，F08.6仍BLOCKED。

本轮已完成：整个任务/扩展列表面板扩大并按终端伸缩，增量刷新保留高亮和滚动位置；任务页提供上移/下移/置顶/移除，键盘需Enter选定目标。排序影响真实领取顺序并持久化，不改计划时间；移除取消待执行工作并隐藏列表项，保留全部历史/审批/回执。UNKNOWN拒绝移除，迟到未知结果重新可见。模型规划/材料预览中取消会收尾原协程，缺失Task/Run作业失败不再击倒Worker。等待补充/审批不占执行器，保留原熔断。

实际验证：后端/API/客户端/取消/UNKNOWN/熔断联合68 passed、2 warnings；原公共API6 passed、2 warnings；最终TUI列表与队列交互10 passed；真实临时PostgreSQL7 passed；此前TUI打磨/流式23 passed。Ruff、Mypy（本轮10个源文件，分组）及git diff --check通过。第一轮联合有1项新测试超时，已用固定时钟和逆序Job ID重现其错误领取假设，改为确认首任务已进入规划后再创建次任务；原2秒取消上限未变，最终联合68项全过。不将此前失败轮算通过。

范围：core/agent/runtime、core/jobs/{queue,management}、core/tasks/{service,management}、memory/database job_queue、api/workbench、cli/tui/{app,service}，新增五份目标测试和本轮说明。没有迁移变更；原库/服务/profile未操作，未解冻、安装扩展、调用真实模型、提交或推送。临时PG容器已删除并按精确名称复查为0。按用户避免过度完整套件要求，仅运行受影响路径和真实PG事务/领取验证，未重跑项目完整套件或完整Worker/真实业务验收。需要本人重启后台与TUI后验证实际使用；F08.6 BLOCKED、F07/F08 IN_PROGRESS不变。下一编号回到CORE.RECONCILE.1，不在本轮继续实现。

## UX.TRIAL.1（DONE，本地实现与配置）— 直接对话、真实流式回复与未安装扩展引导

2026-11-06：根据本轮真测反馈，普通发送/带历史建任务移除额外确认；当前原profile已获本人明确授权设置automatic（包括授权目录/邮箱资料的模型处理），备份为同目录host.before-direct-chat.json，其他值保持。最终R2审批与SECRET阻断保留。原后台未重启、未解冻，未调用真实模型。nju.ehall真实失败原因是仅DISCOVERED、尚未安装；本人最后选择稍后手动安装，本轮没有build/install/import/enable该扩展。

改动范围：core/models/provider/router/automatic，OpenAI兼容stream传输，Settings/host配置，Planner/runtime，api/workbench与system，TUI app/service；扩展supervision/operations和Admin错误映射；对应测试与本节/README/契约/TUI说明。没有新增迁移，没有更改外部审批canonicalization/风险等级，没有提交/推送。

本轮验证（各集合有重叠，不相加）：

~~~powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/api/test_admin_extensions.py tests/unit/test_extension_supervision_service.py tests/unit/test_extension_operations_model.py -q -o addopts='' -p no:cacheprovider
# 61 passed、2既有warnings；新未安装反例先2 failed、1 passed。
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/unit/test_model_streaming.py tests/unit/test_model_router_f04.py tests/unit/test_model_adapters.py -q -o addopts='' -p no:cacheprovider
# 95 passed；新增流式最初10 failed；后续仅新文件14 passed。
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/api/test_workbench_stream.py -q -o addopts='' -p no:cacheprovider
# 11 passed、2既有warnings；初轮与原API/runtime/配置联合66 passed，新增最初6 failed。
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/unit/test_tui_stream.py tests/unit/test_tui_app.py tests/unit/test_tui_service.py tests/unit/test_tui_polish.py -q -o addopts='' -p no:cacheprovider
# 初轮45 passed；追加两项后只复跑新文件13 passed，不把两批重叠数累加。
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/integration/test_workbench_streaming_live.py -q -o addopts='' -p no:cacheprovider
# 1 passed：真实随机回环HTTP，首delta到达时上游尚未完成，随后审计/回执完成，同键上游仅一次；端口与服务已回收。
~~~

受影响源码Ruff/Mypy通过；验证覆盖auto策略下真实Planner/Router/Runtime/Gateway仍WAITING_APPROVAL且外部执行器0次、SECRET拒绝、取消收尾、同键查询和Token只计一次。按本人避免过度完整套件要求，本轮仅运行受影响路径/安全回归与隔离HTTP联测，未重跑全量或PostgreSQL/Worker；不把本地通过当成真实DeepSeek/手机验收。TUI /ask真实流已实现，Agent规划/手机回复/Ollama增量仍不在本轮范围。F08.6 BLOCKED及F07/F08 IN_PROGRESS保持，下一编号回到CORE.RECONCILE.1。

## 历史：CORE.RECONCILE.1（当时 BLOCKED）— 外部动作对账到任务恢复的闭环

实现及目标验证已完成：原动作绑定的宿主证明、显式恢复接口与TUI入口、同事务回执/CAS、旧作业门闩和工作流单次推进均已落地。原UNKNOWN及审批不可复用，不接受客户端伪造结果。目标37项与隔离PG5项通过；原CLI模型配置跨层依赖已按授权修复，架构/模型配置13项通过；补跑完整门禁的配置同步文件竞争已获授权修复，目标38项通过；CDP夹具也已修复（目标10项、真实浏览器2项通过）；本轮完整门禁1070 passed/160 skipped/1 failed，停于既有RPC耗时断言，单例复跑通过但不能算整套通过。状态、精确命令、日志及后续最小修复方案见本文顶部本轮记录与docs/CORE_RECONCILIATION_2026-11-07.md。本轮未部署或推进其他编号。


## MAINT.4（DONE，文档校准）— 当前能力与操作文档校准

MAINT.2及合并的MAINT.3已完成：真实Admin浏览器最初6 failed，原联合14 passed；新增明确拒绝组后浏览器7 passed，静态检查通过，未重复未变的API8例。当前仅修文档：README历史状态、配置PUT不得盲目同键重试、开发工作区及本地文件不在现有备份包范围。产品API与原实例不变。


## MAINT.2（DONE，本地验证）— Admin 操作与配置回执恢复

MAINT.1已完成，CLI/TUI启动目标13 passed；已知operation_id可用extension operation只读查询。现在合并原MAINT.3到本项，仅修web/admin前端和对应目标测试：成功后按钮恢复、预览mode绑定安装/升级路由、丢回执配置禁止盲目重发并先GET核对。页内刷新保留原请求，整页离开原生提醒，不持久化配置正文；不改后台API/CAS/审批，不操作原实例。


## MAINT.1（DONE，本地验证）— CLI 管理断连与操作查询

CORE.FIX.2本地21 passed；PostgreSQL两个用例因未配置专用测试库且Docker短探针超时而未运行，不记通过。原R2/UNKNOWN写入顺序未变。当前仅修CLI安装/升级/轮询的离线异常和只读operation查询，保留未知结果、不重发安装；范围cli/main.py、对应测试与文档。


## CORE.FIX.2（DONE，本地；PG补证待运行）— 保留 Agent 的实际追问上下文

CORE.FIX.1已完成：定时任务长积压三策略有界推进，目标30 passed，45组独立纯计算对照通过；具体命令见项目细节清单。当前只推进真实模型NEEDS_USER追问与用户短回答的上下文修复；仅core/agent、相关回归及文档。用既有检查点标记真实对话，排除宿主披露/状态提示，保持旧快照和精确披露兼容；不修改审批、Outbox、数据库结构或原运行实例。


## CORE.FIX.1（DONE，本地验证）— 定时任务积压不得阻断 Worker

2026-10-30 用户补充范围包含助手主体的搁置功能、设计遗漏及小bug。本项修复已隔离复现的有效每分钟coalesce调度停机两天后触发扫描上限、导致无关队列任务始终无法处理的问题。范围限调度计算/Worker必要接线、对应回归和文档；先失败回归再最小修复，不改审批/风险/Outbox，不运行原实例或外部动作。下一项CORE.FIX.2处理模型遗漏助手追问上下文；其他MAINT小项随后逐项推进。



## TUI.POLISH.1（DONE，本地验证）— iCode 风格打磨与 TUI 细节修补

2026-10-30 用户明确要求参考openJiuwen-ai/iCode调整粗糙TUI，并梳理全项目小缺口依次修补。本项先打磨TUI布局、输入/命令/帮助、Markdown显示、焦点与刷新/离线状态等已核实小缺口；授权cli/tui、对应目标测试和文档，不改Gateway/审批/Outbox协议，不自动部署或解冻。全项目只读梳理同时进行，发现的其他小项登记为后续MAINT编号；完成本项后再顺序推进，每次只保留一个NEXT。按用户要求避免重复完整套件，只做必要定向验证；已有F08.6/F07验收状态保留。

## 2026-10-30 TUI.1（DONE，本地实现）— 电脑端助手工作台

用户明确要求简约TUI：日常对话、任务发布与继续、扩展手工管理、助手参数配置，并允许参考成熟开源项目。本次开发范围为CLI/TUI、本机工作台API复用接线、可选Textual依赖、相关测试和文档；不改Gateway风险/审批/UNKNOWN协议，不自动启动或解冻原实例、不安装业务扩展。EXT.DEV.2待计算器安装确认；F08.6仍BLOCKED。按用户“避免完整套件过度验证”，只运行新路径与安全相邻定向回归，报告具体范围。


本轮已完成问答/对话转Agent任务、工具状态、Token统计、扩展手工管理与模型参数设置。联合定向 **95 passed、2个依赖弃用警告**；Ruff、Mypy12源文件、pip check、CLI/PowerShell解析与差异检查通过。按本人要求未跑全量/PG或真实模型，不启动/解冻原实例；F08.6仍BLOCKED。使用与可复现命令见[TUI指南](docs/TUI_WORKBENCH.md)及[实现报告](docs/TUI_IMPLEMENTATION_2026-10-30.md)。

## TUI.2（TODO）— 运行实例小型联调

核对原实例配置、冻结与待处理队列后，连接本机Admin验证真实模型问答和简单READ工具任务；真实启动/解冻沿用用户明确授权范围，不自动执行旧队列。本轮尚未推进此项。EXT.DEV.2等待计算器候选安装确认，其他既有BLOCKED状态保留。

## 2026-10-30 EXT.DEV.1（DONE，本地开发）— 受监督插件开发Agent

独立assistant.plugin_developer@0.1.0已实现9工具：工作区/CAS/diff、静态检查、本人授权的固定验证、失败后修复、精确快照及本机确认安装/升级/启用/回滚桥。Agent不获取Admin确认nonce，无宿主核心修改。最终新扩展定向 **73 passed / 65.85秒**，Ruff、Mypy6源文件、Manifest只读解析、文档命令语法及差异检查通过。真实本地开发循环及stdio Worker已验证，实际安装CLI边界使用替身，无原实例操作/真实模型/发送/部署/提交推送。按用户要求未重复全宿主或PG套件。证据和命令见[交付报告](extensions/plugin_developer/DELIVERY.md)。

## 2026-10-30 EXT.DEV.2（WAITING，原实例安装/真实试写已完成，候选安装待确认）

用户已授权安装开发插件并用系统DeepSeek密钥试写计算器。原持久实例已安装启用assistant.plugin_developer@0.1.0（9工具），环境DEEPSEEK_API_KEY经Windows凭据库引用接入deepseek-flash。真实模型经原TaskRuntime/Gateway/Worker写出sample.calculator候选，经错误反馈后完成，最终14项本地测试通过并生成待安装快照。无模型替身、实施者未代写计算器，不宣称无人干预开发成功。计算器尚未安装，下一步仍在本编号确认精确材料并验证实际工具调用。原四任务未运行；109个已终结试写任务的队列作业待后续收尾；后台恢复冻结，临时Admin关闭，未留常驻Web/Agent。无全量/PG套件、宿主源码修改或提交推送。报告及命令见[真实模型试写记录](docs/EXT_DEV2_REAL_MODEL_TRIAL_2026-10-30.md)。F08.6仍BLOCKED，F07/F08整体IN_PROGRESS。

## 2026-10-30 EXT.CAREER.1（IN_PROGRESS，真实安装/实站待验）— 独立简历投递插件与通用接线

用户新增授权优先开发BOSS直聘、其次本地邮件名单；最新“完成插件并接入接线”覆盖此前仅规划/零宿主修改选择。本轮业务限独立`extensions/career_apply`，宿主只增加业务无关的R2只读预览、审批展示及执行丢回执UNKNOWN接线；不增加招聘专用核心表或路由。本地实现与定向验证已收尾：插件93 passed、JS33 passed、新宿主组合18 passed、相邻回归37 passed。按用户要求未完成/重复全量或完整PostgreSQL门禁；不安装到原运行实例、解冻或真实投递。当前仍IN_PROGRESS：0.2.0已补resume_roots授权目录简历列举/import_file导入，无需上传控件；本次仅插件文件适配，不改宿主/知识库。目录与BOSS本地适配41 passed，Worker/邮件兼容27 passed（重叠不相加），Ruff/Mypy11源文件通过。原实例未安装，BOSS实站兼容未验证，不能描述成一键可实投。见[交付总结](extensions/career_apply/DELIVERY.md)、[安装说明](extensions/career_apply/INSTALL.md)和[真实环境手测](docs/MANUAL_REAL_ENV_TEST_GUIDE_2026-10-30.md)。F08.6原独立门禁问题仍BLOCKED，F07/F08整体状态不因本插件改变。

## 2026-10-30 F08.6：独立全量未全绿，单例复跑通过（BLOCKED）

独立核验四份隔离测试 **37 passed**，140条原断言、79项交接摘要和wheel内容一致。随后独立完整本地为 **1407 passed、137 skipped、1 failed、6 warnings**（exit 1）：F07落地路径反例在创建Companion会话时超时，尚未执行到路径断言；原冻结文件未变。按本人要求仅重跑该用例，**1 passed / 20.95秒**，未改源码、断言或超时，不覆盖原全量失败。独立PostgreSQL/Worker尚未启动，实施者此前137 passed仍仅为实施证据。F08.6继续 **BLOCKED**，手机补证结论不变，无需新增手机操作；下一步仅定位F07会话创建超时及补齐独立门禁。详情见[报告独立复验补记](docs/F086_TEST_ISOLATION_GATE_REPORT_2026-10-30.md)。本次仅准备本地提交，不推送或部署。

## 2026-10-30 F08.6：测试目录隔离及完整门禁通过，等待最终独立复验（BLOCKED）

用户已独立认可真机补证，无需新增手机动作。本轮仅修正ntfy及同类API/Runtime共四份测试的临时运行目录，140条原断言调用AST不变；定向37 passed/2 warnings。完整本地1408 passed/137 skipped/6 warnings、Ruff/Mypy263通过；完整隔离PostgreSQL/真实Worker137 passed。三份JS、依赖、差异及wheel核验通过；340项产品材料和冻结文件未变，测试容器/卷/运行器归零。F08.6仍 **BLOCKED**，只等待最终独立复验，F07/F08整体 **IN_PROGRESS**；未操作原库、解冻或重启助手、真实调用或提交推送。报告：[本轮测试隔离与门禁报告](docs/F086_TEST_ISOLATION_GATE_REPORT_2026-10-30.md)。

## 2026-10-27 F08.6：关键真机缺口补齐，独立复验待办（BLOCKED）

真实手机的保存丢回执/同键重试已通过；受控暂停独立主站Tunnel90秒期间手机助手断连、私有health正常，恢复后本人下拉刷新恢复。19组回环/私有health全200，同一Tunnel已恢复、宿主仍冻结，Admin关闭、通知未动。最终手机v16/四静态缓存/空本地存储及任务/草稿/审批三项200/no-store通过；原库草稿仍10、命令未重复、无外部动作。完整范围、摘要、历史门禁及余项见[当前真机交接报告](docs/F08_6_REAL_DEVICE_REPORT_2026-10-27.md)。F08.6仍 **BLOCKED**，等待独立复验；重复批准额外分支、自动恢复与未覆盖场景不记通过，F07/F08整体 **IN_PROGRESS**。本轮无产品代码变更、迁移、提交或推送，未重复全量/PG门禁。

## 2026-10-27 F08.6：真机保存丢回执及同键重试通过（BLOCKED）

在本人手机Chrome实际v16、固定公开自检草稿上，经精确CDP Fetch响应阶段故障注入，原库先提交版本10，再中断唯一PUT 200回执；手机先显示结果未确认/原基准9和同请求重试按钮，随后事件重读提示服务器10冲突。本人显式采用服务器草稿，再手动重试原请求，确认版本10；只读复核版本、更新时间、该任务10条命令及版本10唯一命令均未变化。没有自动重试、清幂等键、读认证头/正文或新增外部动作；拦截已关闭、网络恢复、转发清理。此前限速保存正常到9，作为无效丢回执尝试保留，不计通过。详见[真机回执记录](docs/F086_MOBILE_APPROVAL_PLAN_2026-10-25.md)。无产品变更，未重复完整本地/PG套件。F08.6仍 **BLOCKED**；当前继续独立Cloudflare中断，手机已反馈私有health正常，尚未暂停Tunnel；重复批准额外证据及其余未覆盖范围仍待验。

## 2026-10-27 F08.6：手机实际v16及当前敏感缓存核验通过（BLOCKED）

官方ADB已授权设备上，通过正式浏览器Target目录读取当前Chrome助手/ui/页：实际app.js与Worker源码SHA匹配部署材料、页面由该Worker控制、CACHE常量v16。唯一CacheStorage恰四个静态路径；LocalStorage/SessionStorage键名及IndexedDB数据库名均空。没有读取Cookie/业务正文/存储值，没有刷新/更新Worker/清数据；自建转发全部移除。旧Android目标超时未计通过，成功快照范围及摘要见[手机版本/缓存记录](docs/F086_MOBILE_APPROVAL_PLAN_2026-10-25.md)。无产品代码变化，不重复完整套件。F08.6仍 **BLOCKED**，下一步真实保存丢回执/同键重试；已请求手机准备公开测试输入、反馈基准版本，尚未限速或保存，独立Cloudflare中断等仍待验。

## 2026-10-26 F08.6：手机首次审批通过，合成自检收尾及禁发冻结恢复（BLOCKED）

本人确认真实手机材料相符并批准，仓储当时APPROVED版本1、未消费；重复批准检查前已到期，未做该检查，不记409通过。cleanup为CLEANED/EXPIRED版本2，保留原批准信息/审计/草稿/MIME，合成账户及凭据移除，原配置JSON恢复；jobs4/Outbox0/邮件账本0。私有restored曾因全机查询超时停止，本轮修复持续监视，离线20 passed/Ruff通过，保留旧失败journal，以独立run-label恢复禁发冻结主站/原Tunnel。20项HTTP及完整身份/监听核验通过、Admin关闭，独立ntfy正常。无产品源码变化，未重复完整本地或PG/Worker；F08.6仍 **BLOCKED**，下一步补手机实际版本/敏感缓存、丢回执与独立Cloudflare中断等证据。见[完整收尾](docs/F086_MOBILE_APPROVAL_PLAN_2026-10-25.md)，下方保留历史记录。

## 2026-10-25 F08.6：获准邮件升级及合成审批准备完成，等待手机（BLOCKED）

本人已批准精确自检方案，正常升级nju.smail0.2.0明确SUCCEEDED，原配置在升级中未变。独立Admin窗口已关闭；真Worker生成唯一合成.invalid邮件草稿并核验MIME，冻结主站及原Tunnel已恢复，独立ntfy保留。六项HTTP边界通过，既有4个jobs不变、Outbox/邮件账本及夹具发送动作均0。尚未创建审批，等待手机就绪后才开始5分钟TTL；完毕须取消/过期并恢复禁发配置。无产品源码改动，未重复完整套件；F08.6仍 **BLOCKED**。见[执行结果](docs/F086_MOBILE_APPROVAL_PLAN_2026-10-25.md)，下方为历史记录。

## 2026-10-25 F08.6：手机新版访问及真实任务通知点击通过（BLOCKED）

本人确认普通刷新后任务列表/连接正常。当前UI未渲染冻结字段，宿主另证FROZEN_RUNNING/freeze=true，未解除冻结。正式ntfy适配器仅向既有第二自检任务发一条R1最小提醒，accepted1/unknown0；本人确认点击后进入固定助手域名并显示该任务详情，此通知点击分支通过。手机脚本/缓存版本仍待USB核验（本人取消Chrome发现后，官方ADB再查仍0设备）；已准备[正式审批自检候选](docs/F086_MOBILE_APPROVAL_PLAN_2026-10-25.md)，限合成.invalid账户、真Worker/MIME及一次批准，需邮件扩展0.2.0升级和临时冻结配置授权，尚未实施。F08.6仍 **BLOCKED**，F07/F08整体 **IN_PROGRESS**。未执行任务、调用模型、发邮件或提交；详见[手机证据第11节](docs/F08_6_NTFY_NATIVE_PREPARATION_2026-10-25.md)，下方保留历史记录。

## 2026-10-25 F08.6：原库0012及新版冻结启动通过主机核验，手机待反馈（BLOCKED）

本人明确批准后，已按固定计划应用唯一0012；原库12迁移/哈希匹配，23张保护表全部行未变，3个READY作业未领取。新版Public/Health/Agent以冻结模式运行，Admin关闭，现有两份0.1.0扩展正常恢复；原主站连接器已恢复，独立通知服务未改。19项回环/同节点私有HTTP检查通过；公网UI/tasks无认证302到团队Access，两个连接器ready200，私有Serve仍tailnet only→8010。未调用模型、同步邮箱、发通知或提交。无产品代码变更，未重复完整门禁；此前同材料完整门禁按原范围保留。手机普通刷新/连接已请求本人反馈，实际版本、通知点击和其余既有真机证据尚待，F08.6仍 **BLOCKED**。见[切换执行结果](docs/F086_ORIGINAL_CUTOVER_PLAN_2026-10-25.md)，下方保留历史记录。

## 2026-10-25 F08.6：原库0012与新版冻结启动材料就绪，等待授权（BLOCKED）

隔离恢复已通过后，已绑定263运行文件、原库11迁移、现有example.echo/nju.smail 0.1.0精确payload及Manifest、备份/恢复证据、原主站连接器；候选check及只读DB预检通过，缺/错批准摘要2项负向探针均在变更前拒绝。离线wheel265条目，236源码/7PWA/12迁移逐字节一致，未安装；当前产品仍与同日完整门禁相同，仅已验收材料范围内旧快照修复。拟应用唯一0012并启动冻结guardian（Public/Health/Agent、无Admin）、恢复两份既有扩展Worker及原主站连接器；不自动resume、不启用发信/浏览器提交。因前次授权只含隔离恢复，实际原库迁移及启动等待本人确认；目前均未执行。F08.6仍 **BLOCKED**，见[具体切换方案](docs/F086_ORIGINAL_CUTOVER_PLAN_2026-10-25.md)，下方保留历史证据。

## 2026-10-25 F08.6：真实加密备份隔离恢复通过，原库切换待准备（BLOCKED）

本人批准精确静态材料后，已在全新临时PostgreSQL目标完成原11迁移、邮件扩展Schema-only预建及真实加密备份恢复：34张表全部行与冻结预期一致，5项序列通过，扩展DISABLED。最终exit0；两次配置/就绪阶段失败保留，全部临时容器/凭据/目录已清理，原备份哈希不变。原库再只读确认11迁移匹配、0012待执行，原助手未启动。此操作无产品改动，未重复全量；此前最小产品修复完整门禁按原范围保留。F08.6仍 **BLOCKED**，下一步准备原库0012及新版冻结启动材料，另行确认后执行。见[隔离恢复结果](docs/F086_ISOLATED_RESTORE_PLAN_2026-10-25.md)；下方保留历史记录。

## 2026-10-25 F08.6：原库加密备份已验证，隔离恢复等待静态SQL批准（BLOCKED）

本人已生成备份密钥；已核验Windows凭据库句柄、配置未激活候选，创建并认证读取一个原库恢复点（34张表、11份匹配迁移、0制品）。备份中的13张邮件扩展表需先在隔离空目标建立匹配结构，安装nju.smail@0.1.0及SQL摘要已与认证备份记录绑定；已准备精确材料，按AGENTS的扩展执行前确认规则等待本人批准。未执行扩展SQL、创建恢复目标或实际恢复，原库未迁移/改写，新版未启动。F08.6仍 **BLOCKED**，F07/F08整体 **IN_PROGRESS**。见[隔离恢复计划](docs/F086_ISOLATED_RESTORE_PLAN_2026-10-25.md)与通知入口报告第9节；下方保留历史门禁和准备记录。

## 2026-10-25 F08.6：旧快照最小修复通过本地门禁，继续备份准备（BLOCKED）

本人明确允许最小修复F09恢复模块后，已仅在快照原本含runtime_records时执行原hosts过滤，保留原表集合及其余恢复保护；新增三单元与两真实PostgreSQL回归。先红：单元1 failed/9 passed，临时PostgreSQL1 failed/2 passed；后绿：单元及原反例 **11 passed**，相同临时PostgreSQL文件 **3 passed**。本轮完整本地 **1408 passed、137 skipped、7 warnings**，Ruff/Mypy263通过，exit0；随后完整临时PostgreSQL/真实Worker **137 passed**，exit0；门禁运行器和测试标签容器均为0。原库只读复查仍11份迁移/哈希一致，待0012。下一步由本人本机配置备份恢复密钥，再建立加密恢复点及隔离恢复。原迁移未改、原库未写、新版未启动；F08.6仍 **BLOCKED**。报告：[旧快照兼容修复](docs/F086_LEGACY_RECOVERY_FIX_2026-10-25.md)。下方保留发现时与通知分支的历史证据。

## 2026-10-25 F08.6：通知撤销恢复通过，切换前旧快照兼容问题待确认（BLOCKED）

通知撤销/替换后手机收件已通过。原Docker容器及原卷、回环5432已只读核验；原库0001–0011哈希全部匹配，唯一待迁移0012。已准备未激活的持久profile候选、切换计划及逐字节匹配的11份迁移副本。准备隔离恢复时发现 `freeze_snapshot()` 会向缺runtime_records的旧快照无条件补入该表项；纯合成反例 **1 failed** 已保留，按代码路径会触发11迁移目标缺表拒绝，尚未真实恢复验证。此兼容问题在F09恢复模块，遵守AGENTS跨边界确认规则，尚未改产品代码。原库未写、助手未启动、新迁移未执行；F08.6仍 **BLOCKED**，F07/F08整体 **IN_PROGRESS**，此前本地验收结论按原范围保留。下一步待确认最小兼容修复，再继续加密备份/隔离恢复及入口验收。详情见通知入口报告第8节。

## 2026-10-25 F08.6：通知设备凭据撤销与手机恢复通过（BLOCKED）

本人仅替换手机Authorization后，宿主单发最小自检 `f086-reader-restored-20261025` / R1，relay accepted=1、unknown=0；本人反馈“已收到”。结合旧凭据公网401/手机连接错误、断开旧流及新凭据公网200，此次真实通知撤销与恢复分支通过。此前锁屏最小提醒样本已通过。F08.6整体仍 **BLOCKED**：原助手新版持久运行/通知自动接线、真实任务通知点击、其他既有草稿/正式审批/缓存/网络证据待补齐；F07/F08整体 **IN_PROGRESS**。未迁移原库、启动原助手或提交/推送。详见通知入口报告第7节，下方为此前记录。

## 2026-10-25 F08.6：旧通知凭据撤销通过，手机恢复待验（BLOCKED）

锁屏最小提醒样本已通过后，本轮按本人准备确认执行独立通知凭据轮换。真实隔离 ntfy 演练 **7项通过**；正式一次性撤销后，旧只读token公网GET返回 **401**、替代只读token返回 **200**，并强制停止已核验的独立ntfy进程以断开旧长流，再按原配置启动。本人随后反馈手机订阅“连接错误”，与撤销一致。替代凭据只在Windows凭据库；当前等待本人仅更新手机Authorization后单发恢复提醒，未以服务端200代替真机恢复证据。publisher、Cloudflare应用/凭据/Tunnel、原助手profile及数据库不变，未发测试消息。F08.6仍 **BLOCKED**，F07/F08整体 **IN_PROGRESS**。详见通知入口报告第7节；下方是此前证据。

## 2026-10-25 F08.6：独立通知公网入口通过，手机待验（BLOCKED）

本人已建 `assistant-notify` 应用，截图确认Service Auth仅允许 `assistant-notify-phone`，提供新AUD；本机已绑定并验证。新named Tunnel与主站分离，通知DNS无覆盖新增，连接器仅代理回环8090并强制通知AUD/JWT校验。本轮本地ingress **3项通过**，真实公网双层鉴权/读写隔离/主站隔离/最小消息链路 **14项通过**（无浏览器Cookie，未跟随重定向）；这是主机到公网受限测试读取者，不是手机投递。原助手profile/原库/主站Tunnel均未改，核心任务还未自动接入此通道。官方无Firebase Android APK来源已核对，本人反馈已安装打开；本机私密配置窗check模式通过；本人修正一位Cloudflare标头输入后订阅成功，首次最小自检真机应用内收到。首次锁屏未收到保留为失败记录；本人核对ntfy应用专属后台/电池/通知项后，再锁屏60秒的新编号提醒已确认出现系统通知，完整显示的测试载荷仅固定标题/kind/R1/合成ID。此样本锁屏接收及最小内容通过，长时稳定性、撤销恢复、点击和原助手通知接线仍待验证。**F08.6仍BLOCKED，F07/F08整体IN_PROGRESS**；DELIVERY.2本地、F06.P2/最小F09.1 DONE。手机订阅/前后台锁屏/点击/撤销、助手运行版本与其余真实证据仍待补齐。本轮未跑全量、PostgreSQL/Worker，未提交或推送。详见 [通知入口报告](docs/F08_6_NTFY_NATIVE_PREPARATION_2026-10-25.md) 第6节；下方是此前准备记录。

## 2026-10-25 F08.6：原生 ntfy 本机启用，外网/手机待配合（BLOCKED）

从已推送 main `38b192a`继续。用户本轮明确允许启用独立通知通道后，官方ntfy2.33.1已仅在回环8090运行；独立认证库、两个非管理员单topic只写/只读账户，助手侧令牌副本仅用Windows凭据句柄保存（30天），ntfy独立认证库按受限安全材料管理。本轮ntfy定向 **13 passed**；真实二进制隔离检查 **9项通过**，独立新进程使用真实Windows句柄的本机检查 **7项通过**；隔离进程和认证库已清理，授权8090保留。未启用候选先失败再通过，正式启用后保留原防覆盖断言。独立通知域名/Access/Service Auth草案已准备，控制台自动读取仍超时，本人已创建并隐藏录入通知专用Cloudflare凭据，已验证句柄可解析，已登记独立通知Tunnel，待新通知应用及AUD；尚未改DNS路由、启动外网连接器或手机。原助手8000/8010当时无监听，本轮不启动新版、迁移原库或改原profile；后续版本切换需备份/恢复与迁移清单。**F08.6仍BLOCKED，F07/F08整体IN_PROGRESS；DELIVERY.2本地、F06.P2/最小F09.1 DONE**。当前不是F08.6整体验收，本轮未跑全量/PostgreSQL/Worker或手机，未提交/推送。精确命令、结果与边界见 [本机启用报告](docs/F08_6_NTFY_NATIVE_PREPARATION_2026-10-25.md)。下方保留历史记录。

## 2026-10-20 当前结论：DELIVERY.2 本地验收 DONE；F08.6 BLOCKED

用户已独立复验通过：相关160 passed，原两项连续串行5轮/每轮2 passed，真实总deadline到期回收及清理失效反例通过，Ruff/Mypy/差异及118项摘要匹配，活动测试运行器0。用户核对实施者完整日志1405 passed/135 skipped/6 warnings；该数字不是用户本轮重跑。本轮只记录验收结论和项目现状，不新增运行测试，不操作原库/原服务，不提交/推送/部署。**DELIVERY.2 的本地阻断关闭并标DONE**；**F08.6仍BLOCKED，F07/F08整体IN_PROGRESS**，F06.P2/最小F09.1 DONE。下方BLOCKED/待复验段落为当时历史。

当前项目是本地优先的单用户助手：手机/电脑PWA只与助手通信，助手统一连接模型、知识资料、邮件和桌面浏览器。持久任务执行/工作流、模型披露、审批/Outbox、Windows主机、加密备份恢复/升级已有本地实现；DeepSeek仅有公开连接自检证据，邮件代写代发和完整业务流程仍需真实验收。新批次未部署到原手机入口；开发18100模板为内存，正式持久使用需PostgreSQL、独立Worker、凭据句柄及新迁移，不能把仓库新版当作线上已运行。

当前后续编号为 **F08.6（BLOCKED）**：核对实际运行版本、真实手机丢回执重试、正式单次审批及敏感缓存、独立Cloudflare中断、无Google环境ntfy投递/撤销/锁屏/点击。之后补真实模型任务和受控邮件“资料→草稿→手机核对→精确审批→发送→归档”，以及冷启动/长期运行/生产备份恢复/升级演练。F07正式真实事务Adapter、完整目标/材料/后果权威绑定、Gateway/Outbox实站链路仍延期；没有确有需要事务时不制造申请或最终提交。详情见[本地运行手册](docs/RUNBOOK_LOCAL_BETA.md)和[最新验收记录](docs/F07_SESSION_CREATION_TEST_TIMING_2026-10-20.md)。

## 2026-10-20 DELIVERY.2 测试时序局部修复（BLOCKED，待独立复验）

用户独立确认资源回收修复及额外10项反例通过，但统一150ms测试预算导致正常根撤销/主动取消提前超时，独立完整本地1402 passed/135 skipped/2 failed，故整体验收未通过。本轮只修改 `tests/integration/test_companion_failed_creation_f07.py` 与交接文档：正常请求5秒、故意创建响应超时只在driver进入后注入、创建任务提前结束立即取异常、finally收尾；正常TTL与唯一idle TTL用例隔离，全部原回收断言保留。先红 **3 failed**，单文件 **8 passed**，原两项连续串行 **5轮各2 passed**，相关合并 **160 passed、2 warnings**；本轮完整本地 **1405 passed、135 skipped、6 warnings**（21:46），Ruff/Mypy263通过、exit0，修复文件8项在全量中全部通过。活动测试运行器0、测试标签容器0，查询成功。Ruff/Mypy测试1文件及源码269文件、JS/依赖/差异检查通过，118项材料仅该测试一项摘要改变。精确命令、日志和最终门禁见[本轮报告](docs/F07_SESSION_CREATION_TEST_TIMING_2026-10-20.md)。DELIVERY.2及F08.6保持 **BLOCKED**，F07/F08 **IN_PROGRESS**；生产代码、30秒超时、旧迁移、原库与原服务不改。本轮不重新运行PostgreSQL/Worker，因为只改测试且未涉及数据库；此前135 passed属于上一轮实施证据，不计本轮。未提交、推送或部署。下方为历史实现/门禁记录。

## 2026-10-19 DELIVERY.2 相邻 F07.P2 创建失败清理（BLOCKED，待独立复验）

用户已独立确认原三项 DELIVERY.2 修复通过；新增阻断为创建超时后迟到会话无法撤销、TTL 无访问时不回收。本轮仅修复此清理缺口：已知 ID 的宿主根撤销、先撤销后启动竞态、独立 TTL 及抗重复取消收尾；不增加命令超时。先写失败反例后实现，合并定向 **159 passed、2 warnings**；真实本机浏览器取消回收 **1 passed**。本轮串行完整本地 **1404 passed、135 skipped、8 warnings**，Ruff/Mypy263通过；随后完整临时 PostgreSQL/真实 Worker **135 passed**，两门禁exit0。另独立Ruff/Mypy269、三份JS、依赖/差异检查及wheel265条目/10web/12迁移/118项摘要通过；活动测试运行器0、测试标签容器0且查询成功，见[本轮报告](docs/F07_SESSION_CREATION_CLEANUP_2026-10-19.md)。DELIVERY.2 保持 **BLOCKED** 待独立复验；F08.6 真实缺口仍 **BLOCKED**，F07/F08 **IN_PROGRESS**，F06.P2/最小 F09.1 **DONE**。不操作原库/服务，不调用真实模型、邮箱或 ehall，不提交、推送或部署。下节为原三项修复的历史门禁，不能替代本轮结果。

## 2026-10-19 DELIVERY.2 修复与复验（BLOCKED）

独立验收确认 1 项 P1、2 项 P2：实际 EXTERNAL_WRITE 未决动作被普通继续绕过、已绑定工作流切换后错误成功收尾、恢复 EXECUTING 审批时丢失 UNKNOWN 与审核来源。本轮仅修复上述缺陷并补本地完整门禁；未决结果不自动重放，旧授权不可重用。DELIVERY.2 保持 **BLOCKED**，等待修复后的独立复验；F08.6 继续 **BLOCKED**，F07/F08 整体 **IN_PROGRESS**，F06.P2/最小 F09.1 **DONE**。原库、运行服务、真实模型、邮件及站点不操作，不提交/推送/部署。本轮先失败后修复：相关回归 **38 passed、2 warnings**；完整临时 PostgreSQL／真实 Worker **135 passed**。完整本地 **1383 passed、135 skipped、1 failed、6 warnings**，失败为 F07 创建 Companion 会话超时；隔离复跑 **1 passed**，不等于全量全绿或根因已解决。Ruff、Mypy269、三份 JS、依赖与差异检查通过；离线 wheel265条目/10web/12迁移逐字节核验，当前112项摘要。收尾活动 pytest/测试运行器0、测试标签容器0，查询成功；原库/服务未动。精确命令、日志、文件及剩余风险见 [本轮修复报告](docs/DELIVERY_2_REMEDIATION_2026-10-19.md)。历史数字不算本轮运行。


2026-10-13 用户明确调整排期：授权一次性推进全部后续项目开发，范围不限 F08.6；外部邮件/通知/站点 API 配置、真实环境联调与验收留待后续；随后用户另行授权本批次提前接入真实模型，并已完成 DeepSeek 单次 PUBLIC 自检，本地自动化验证继续执行。本授权取代此前唯一 NEXT/不得推进其他 F09 的排期限制；按依赖顺序完成 Windows 运行适配、持久任务执行与调度、组合工作流/可审核规则、管理与手机入口、备份恢复及交付手册。审批、凭据、可信扩展确认、UNKNOWN 不自动重放及 health-only 边界仍适用。F07/F08 真实验收状态不冒改 DONE；开发实现和独立验收分别记录。当前交付包 **BLOCKED（独立验收发现阻断，修复后待复验）**，见 [集中开发报告](docs/DEVELOPMENT_DELIVERY_2026-10-13.md)。

2026-10-10 F08.6 ntfy 最小接线（本轮）：本人已明确授权无 Google 通知通道的主机接线和本地测试，要求停在手机安装或外网入口配置之前。新增默认关闭的本机 ntfy 适配器、秘密句柄配置、两种存储组合根及关闭资源接线；消息只含任务 ID/风险/提醒类型，点击只进入助手，结果不明不自动重试。先失败后实现，定向57 passed、2 warnings，全项目Ruff/Mypy234通过，依赖/JS/差异检查通过；本轮全量1312 passed、130 skipped、2 failed（F07 Companion测试启动超时与F06 TLS线程状态断言），隔离复跑1 passed、1 failed，F06断言仍失败。完整PostgreSQL/Worker门禁未继续，原因及精确命令见报告第5节；不引用旧轮数字代替，也不称全量全绿。真实 ntfy/手机客户端尚未接入，原服务未重启或部署；F08.6仍 **BLOCKED**，F07/F08整体 **IN_PROGRESS**，F06.P2/最小F09.1 **DONE**，其他F09不启动。本轮实现详情、精确命令和剩余证据见 [主机接线报告](docs/F08_6_NTFY_LOCAL_REPORT_2026-10-10.md)。下方诊断轮未改源码的说明属于历史范围。

2026-10-10 F08.6 浏览器注册定位（此前诊断轮，后台数据为10-09只读快照）：用户恢复推进；手机点击开启通知后反馈“通知权限未授予”。现有页面在requestPermission非granted时返回，本次操作尚未进入浏览器订阅和订阅POST；不据此判断是denied还是default，也不归因于GMS或服务器。本人确认Chrome应用通知已允许，但助手单站显示“不允许”；随后页面提示“订阅结果未确认，可手动重试同一浏览器订阅”。2026-10-09 23:42（北京时间）只读复核：VAPID宿主可用、原库read_only=on，订阅0/订阅命令0，public无JWT401、health200、Tunnel ready200。该提示覆盖多个失败阶段，尚未定位浏览器subscribe或API失败；本人保留页面重试一次后，提示短暂“正在订阅”又转为“订阅结果未确认”；未记为成功。已请求一次有Network观察的定位，仅读POST是否出现及状态码，不展开正文/Cookie；定位前Chrome显示USB Pending authentication。实际用原官方ADB devices -l只投影数量/状态，exit0、1台device；未输出序列号或读取手机内容。等待本人让Chrome重读设备，保留订阅未确认的原页面；2026-10-10本人Network反馈没有POST；全局异常断点先停在草稿不存在的读取错误，换通知专用catch断点后得到AbortError: Registration failed - push service error。实际ADB包路径只读查询以android和Chrome为对照均exit0/present=true，com.google.android.gms为exit1/present=false且无输出；手机缺少Chrome Android注册所需GMS组件，是明确环境依赖缺口，与该注册错误吻合。通用SERVICE_ERROR本身不证明唯一底层成因；未读取手机用户内容/安装软件/修改权限或发出通知。已停止同配置重试；本人随后明确永久不使用 Google Play，不再以更换为具备 Google 服务的手机为前置条件；本人已明确允许 F08.6 最小 ntfy 接线和本地测试，并要求停在手机安装或外网入口配置之前；F08.6 仍 BLOCKED，其他 F09 不启动；请求移除通知断点并F8恢复原页面。开始时 public/health/Tunnel 均未监听；第一次恢复 public exit3，类型 ConnectionRefusedError，原库连接也被拒绝，Docker Linux engine pipe 不存在。执行 docker desktop start --detach 得到 already running，随后 status为running、原lab1-postgres-1为healthy；按原授权恢复可跟踪的三个运行会话。当前public8000无JWT的Push配置401、health8010/healthz 200、Tunnel ready200、匿名公网/ui/302至既有Access团队；原库只读订阅0、自检任务匹配1，VAPID句柄可用、发信/提交关闭。Tailscale现Running且自身在线、在线peer0，已保存Serve仍只代理health8010、AllowFunnel=false，未修改其配置。本轮无pytest/全量/PostgreSQL测试，前一轮13 passed不是本轮重跑结果；没有发送通知、改源码、提交或推送。手机订阅未确认，F08.6继续BLOCKED、F07/F08整体IN_PROGRESS，其他F09不启动。

2026-10-09 F08.6 本机准备续接（历史证据）：按用户“开始下一步”回到 F08.6；随后用户要求停在需要其配合之前。本轮用真实 Windows Credential Manager 生成并持久化 VAPID 私钥，忽略目录仅新增公钥/句柄/精确 FCM origin，原库临时 DSN 保留。生产组合根在另一宿主进程成功解析同一密钥；现恢复 public 127.0.0.1:8000、独立 health 127.0.0.1:8010 与既有 Tunnel，Admin 未启动，发信/ehall 提交关闭，未投递通知。实际定向 13 passed、2 warnings；本轮未重跑全量或 PostgreSQL/Worker 套件，产品源码未新增修改，恢复基线使用此前已独立通过的同一实现。手机订阅与真实 Push 尚未开始；Tailscale 当前 NoState、私有入口本轮未恢复。F08.6 保持 BLOCKED、F07/F08 整体 IN_PROGRESS，F06.P2 与最小 F09.1 DONE，其他 F09 不启动。详见 docs/F08_6_PUSH_PREPARATION_2026-10-09.md；本轮未提交或推送。

2026-10-09 独立验收通过（最新状态）：F06.P2 DONE，原TLS取消、STARTTLS缺能力/454/EHLO拒绝时连接收尾均通过；最小授权范围F09.1 DONE，Windows凭据后端阶段阻断解除。用户独立实际结果：邮件89 passed，额外10项通过；全量1301 passed/130 skipped/9 warnings；PostgreSQL/真实Worker130 passed/1 warning；Ruff、Mypy232、依赖、差异、8项摘要及wheel通过，记录的25个测试进程均退出。实施者本轮仅只读核对日志摘要及三份代码/测试摘要一致并更新文档，没有重新运行这些测试。F08.6仍BLOCKED，是唯一后续验收项：实际手机v15、真机丢回执重试、正式审批及敏感缓存、独立Cloudflare中断和真实Push仍待补证；F07/F08整体IN_PROGRESS，其他F09功能不启动。原临时DSN未迁移，真实Push未配置；本轮未修改源码、提交、推送、部署或重启服务。下方IN_PROGRESS/BLOCKED及旧门禁数字为历史快照，以本段及报告最新验收节为准。

2026-10-09 F06.P2局部补修交接（历史证据）：用户独立确认原TLS取消/交接窗口已修好，但STARTTLS缺能力或454拒绝升级仍遗留TCP连接，暂不能关闭。已补缺能力/454/EHLO拒绝的实际TCP反例，先3 failed，_connect统一关闭准备失败连接并原样抛出后3 passed；相邻回归**89 passed，134.30秒**，Ruff/Mypy1/依赖通过。F06.P2保持IN_PROGRESS待独立复验，F09.1/F08.6保持BLOCKED，F07/F08整体IN_PROGRESS。本轮仅局部回收修复，未重跑全量或PostgreSQL/Worker；下方1298/130为补修前历史门禁。未提交/推送/部署或连接真实邮箱；当前命令和摘要见修复报告第8/9节。

2026-10-09 F06.P2第一轮修复交接（历史证据）：按用户要求，当前实现快照9891102已先提交并推送origin/main，然后才修SMTP。隐式TLS与STARTTLS均先创建/登记实际SSLSocket再显式握手，失败/取消关闭实际连接；新增取消<0.75秒、socket超时与宿主总时限分支、peer关闭/零发送及正常STARTTLS精确MIME反例。先红4 failed/3 passed；首次修复复跑6 passed/1 failed（1.5秒计时超限，隔离1 passed；未放宽断言）；最终邮件定向86 passed。完整scripts/test.ps1 **1298 passed/130 skipped/11 warnings**，Ruff/Mypy232通过；scripts/test-postgres.ps1 **130 passed/1 warning**。F06.P2实现和实施者完整验证通过，保持IN_PROGRESS等待独立复验；F09.1/F08.6继续BLOCKED，F07/F08整体IN_PROGRESS。F06修改未再次提交/推送，未部署、重启服务或连接真实邮箱/ehall；详见docs/F06_TLS_CANCELLATION_REPAIR_2026-10-09.md。下方“未授权/未修复”均为历史快照。

2026-10-09 已执行授权（历史记录）：先写提交说明并推送现有实现，再修F06。现有快照已提交9891102并成功快进推送origin/main。推送后唯一NEXT为 **F06.P2 SMTP TLS握手取消修复**，本轮进入IN_PROGRESS；范围仅smtp_client.py、既有TLS协议测试及必要测试服务夹具和状态/契约/修复报告。先失败反例，再最小修复，串行完整门禁与PostgreSQL/Worker复验；不重做凭据后端，不部署，不连接真实邮箱。F09.1/F08.6继续BLOCKED，F07/F08整体IN_PROGRESS；下方“待授权”为此前历史状态。

2026-10-09 修复前独立验收（历史证据）：F09.1 凭据功能通过，整阶段仍 BLOCKED。用户实际运行定向42 passed（含真实Windows跨进程）、RPC14 passed（本次未复现，不表示根治）、全量1292 passed/130 skipped/1 failed（邮件TLS超时；单例复跑通过不等于全绿）、PostgreSQL/真实Worker130 passed；Ruff/Mypy232文件、依赖/差异与15项交接摘要通过。新阻断为既有F06 SMTP P2：TLS socket在握手完成后才登记，取消250ms后连接和线程仍在，2秒socket超时后才回收，默认30秒可能拖延。修复拟只涉及smtp_client.py及相关TLS协议测试，先补取消耗时、底层socket超时和宿主总时限独立反例，保留关闭/回收/零发送断言。AGENTS第3步及用户本轮要求另行授权；尚未授权，不把F06修复设为NEXT或修改产品。F08.6继续BLOCKED，F07/F08整体IN_PROGRESS；其他F09不启动。本轮实施者只核对代码并记录用户证据，未重跑上述测试，未部署/重启/提交/推送。

2026-10-08 排期例外（历史记录，当前已转F06.P2）：用户明确授权提前推进最小 F09.1「Windows 安全凭据后端」，范围仅 Credential Manager、凭据句柄及必要宿主/CLI 接线；不启动服务守护、自动发信、自动填表或其他 F09 功能。F09.1 是本轮唯一授权 NEXT，代码就绪、定向验证通过，但必需RPC回归门禁未过，收尾状态 BLOCKED；完成并复验后回到 F08.6。F08.6 继续 BLOCKED，F07/F08 整体 IN_PROGRESS。下方“不推进 F09.1／唯一当前 F08.6”是该授权前的历史记录。


状态只使用 `DONE / NEXT / IN_PROGRESS / WAITING / BLOCKED`。每个 Agent 一次只推进一个编号；完成后写入可复现的命令与结果，不得顺手开始下一项。

2026-10-08 P2 独立复验通过并关闭：用户复跑报告第10节定向集合 30 passed、2 warnings，另有原问题、坏回执/渲染失败同键重试、迟到回执保留未处理冲突的组合反例 3 passed；Ruff、两份 JS 语法、差异检查及 17 项摘要吻合。用户确认测试浏览器已回收、原服务未受影响，本轮未发现新的局部阻断。旧回执仅确认原命令，显式保留的本机输入仍须再次保存。F08.6 整体继续唯一当前 BLOCKED，F07/F08 整体 IN_PROGRESS；尚缺手机 v15、真机丢回执重试、正式审批及缓存、独立 Cloudflare 中断、真实 Push。下一步仍补 F08.6 证据，不推进 F09.1。本轮实施者只记录审计反馈，不新增代码/测试或改动服务，不提交/推送。

2026-10-08 独立复验后 P2 修复：旧草稿回执只确认原命令，不覆盖已采用的新版本或显式保留的本机输入；原幂等键保留至完整回执验证和渲染成功。390px 三个反例先 3 failed、修复后 3 passed，草稿 API/浏览器及静态缓存相关回归 30 passed、2 依赖警告，Ruff/Node/差异检查通过。Service Worker 磁盘版本 v15，仍只缓存四条静态路径，手机交付未验证。报告已补修复证据并更新 17 项摘要；本修复已通过独立复验并关闭（见最新记录），F08.6 继续唯一当前 BLOCKED、F07/F08 整体 IN_PROGRESS，不推进 F09.1；未运行全量/完整 PostgreSQL/Worker，未重启或改配原服务，未提交/推送。

2026-10-08 报告交接：[F08.6 实现与验收交接报告](docs/F08_6_IMPLEMENTATION_REPORT_2026-10-08.md) 已汇总当前工作区、实际测试、部署快照、真机证据与未验收项。用户决定先交独立审计，不在本轮推进 F09.1；F08.6 仍为唯一当前 BLOCKED，F07/F08 整体 IN_PROGRESS。本轮仅报告/状态文档及只读复核，不重跑测试、不提交或推送。

2026-10-08 下一实际依赖：原库只读盘点确认现有审批记录为0、nju.smail部署版0.1.0（源码0.2.0）、两个VAPID配置均未启用，发送/提交开关仍关闭。生产SecretStore默认拒绝操作，真实Push依赖F09。已提请用户授权先推进最小F09.1凭据后端；答复前F08.6仍唯一当前BLOCKED，不把提议当作NEXT或开始跨阶段代码。该轮无新增测试结果；实际只读命令见NEXT_STEPS。

2026-10-08 收缩真机操作：本轮修复固定 `/ui/` 文件缺少 HTTP 缓存策略的问题，响应统一 no-store，Service Worker 仍只缓存四个静态资源。先失败反例后最小实现，相关 API/390px 浏览器 29 passed、2依赖警告，目标 Ruff/Mypy/Node 与差异检查通过。沿用现有配置重新加载公共助手后，无认证脚本/API 均401/no-store，独立health与Tunnel仍正常；未清理Cloudflare现存缓存或证明手机已升级v14。本人普通刷新后确认列表和连接正常，仅记正常使用恢复，不等同于v14交付验收。本轮停止追加手机实验。此前任务/消息/草稿/PUT409证据保留，真实丢回执与正式审批/Push等仍未完成；F08.6继续唯一当前 BLOCKED，F07/F08整体 IN_PROGRESS。实际命令及关停过程见 NEXT_STEPS。

2026-09-30 排期例外：用户明确允许 F07 尚未 DONE 时先实施不依赖其剩余真实站点验收的 F08 子任务。F08.1–F08.5 已分别通过约定范围的独立验收并标为 `DONE`；F08.6「安全与真机验收」是唯一当前子任务，仍为 `BLOCKED`。用户截图及确认表明固定主机名应用仅有一条单邮箱 Allow 策略；用户授权的临时本机 DSN 已保存于指定 Git 忽略、仅本人/SYSTEM 可读配置，原库口令已轮换。发布前门禁通过后，用户明确授权原助手/health/Tunnel 启动、原库迁移及私有 health Serve；又明确批准将核对过的唯一占位 A192.0.2.0 替换为指定 Tunnel CNAME。当前原库 11 份迁移校验和一致，助手与独立 health 仅监听回环 8000/8010，Tunnel ready、仅代理8000；用于已授权草稿夹具的临时回环Admin现已关闭；无认证本机/边缘黑盒通过。Windows 与 Android Tailscale 节点在线，私有 Serve 已只代理8010且 `AllowFunnel=false`，同节点 HTTPS 八项黑盒通过。应用仅选 One-time PIN 的截图已确认，Android首次登录/SSE和私有health三项已有本人反馈；随后确认Chrome标识桌面入口无地址栏启动、空列表/事件连接、Wi-Fi切到移动数据后恢复，以及后台或锁屏约一分钟后自动恢复正常。恢复联网自动连接和断网明确提示也已确认；手机自检任务与消息刷新恢复、电脑页面新增任务/消息同步已有本人反馈，原库只读聚合也核实了先前自检任务/首条消息各一条。任务/消息使用后的手机CacheStorage四固定静态路径由截图支持，唯一节点及名称 `assistant-static-v12` 由本人文本反馈支持；手机任务GET的200、`Cache-Control: no-store`、`Cf-Cache-Status: DYNAMIC` 与Disable cache未勾选由Network截图支持。此确认限于上述CacheStorage及任务GET响应头，HTTP缓存底层、LocalStorage/IndexedDB、Schema草稿冲突/缓存及审批、其余跨设备边界和真实Push仍待验收，正式 Secret Reference 方案仍待 F09。随后经本人对精确材料授权，example.echo@0.1.0已安装启用，公共页面出现example.echo_settings；手机来源由未确定变为用户输入，初次草稿版本2及刷新恢复已有本人反馈；后续真实手机截图确认保留本机未保存值并显示服务器3，显式保留后保存版本4及刷新恢复获本人确认和原库只读核对。手机草稿GET的200/no-store已有截图；更新后截图确认唯一缓存为v13、旧v12节点消失，仍仅四静态路径。手机PUT的409/412、其余缓存与审批仍待核验。F07 与 F08 整体保持 `IN_PROGRESS`；不得把 F07 试填预览变成可批准的权威预览，不启动 F07 真实提交或阶段 10 自动路由。

2026-10-07 主机断网场景已获本人按实际范围认可：电脑断网时手机提示“事件连接中断”，恢复电脑网络后任务列表自动恢复，顶部提示需手动刷新才恢复。该顶部提示滞留作为已接受的体验限制保留，不记作SSE或顶部提示自动恢复已通过；本轮不改代码。F08.6仍BLOCKED，手机PUT409/412、结果不明/同键重试、正式审批及其他敏感场景缓存、独立Cloudflare中断和真实Push仍待验收；F07/F08整体仍IN_PROGRESS。详细人工步骤与证据见NEXT_STEPS.md。

2026-10-08 已补真实手机PUT409及显式采用服务器草稿：本人确认旧基准5的PUT返回409、未保存值保留且服务器面板6；选择采用服务器草稿后变为版本6/电脑自检值并消除冲突。原库只读复核仍6/6（版本/成功保存回执），没有额外保存。本轮只准备手机丢回执/同键重试，尚未执行；正式审批、其他敏感场景缓存、独立Cloudflare中断和真实Push仍待补证，F08.6仍唯一当前BLOCKED、F07/F08整体IN_PROGRESS。未改代码或运行测试集合；完整命令和范围见NEXT_STEPS.md。

2026-10-08 第二次手机Offline仅出现草稿GET Failed to fetch；原库版本/回执为8/8，恢复后手机基准8、无重试/冲突，未证明原PUT回执丢失或同键重放。已按先失败反例后最小实现修复暂时草稿读取失败清空表单的问题，保留输入/原请求并继续拒绝不支持Schema，静态缓存升v14。定向21 passed、2 warnings；强化在途反例后草稿文件9 passed（集合重复不加总）。手机v14复验及真实丢回执重试仍待执行，F08.6保持唯一当前BLOCKED，F07/F08整体IN_PROGRESS；未跑全套/完整PostgreSQL/Worker，未提交推送。

2026-10-08 手机刷新后仍报告v13，v14更新与局部修复的真机复验尚未通过。只读确认当前解释器静态目录/磁盘文件为v14，手机Update后的Status/错误尚待反馈；不推断故障原因、不清站点数据或改变认证/缓存配置。F08.6仍唯一当前BLOCKED，后续阶段不启动。

## 已完成

- [x] **F00 — 基础框架（DONE）**
  - Python 3.12 + FastAPI 三入口、PWA 壳、Agent Plan/Act/Observe、上下文管理、Tool Gateway、精确审批、扩展 Registry/Lifecycle/RPC 契约、扩展 SDK、队列/租约/Outbox 端口、内存参考适配器与示例扩展已经建立。
  - 证据：`scripts/test.ps1` 为 42 passed，Ruff/Mypy 通过；`pip check` 无冲突；wheel 含核心、SDK、PWA、配置和迁移。
  - 有意锁定：生产 PostgreSQL、真实 Agent Worker、扩展 Supervisor、Cloudflare JWT、真实模型和业务扩展尚未实现；不得把当前 `QUEUED` 状态描述成任务已经执行。
- [x] **F01 — PostgreSQL 持久化内核（DONE）**
  - 已交付迁移运行器、`0002_f01_persistence.sql`、Task/Run/Checkpoint/Observation/Approval/Job/Event/Audit/Side-effect Outbox/Extension lifecycle 的真实 PostgreSQL 适配器，以及生产 composition root 和启动/关闭生命周期；生产数据库不可达或迁移失败时 fail closed。
  - 三轮验收发现的 9 项缺陷均已修复并由反例覆盖，包括审批过期/绑定/终态原子性、带数据 0001 升级、Run/Checkpoint 事务、checkpoint 序列、Extension 回填和内存 Outbox 单次审批语义。
  - 最终复验：`./scripts/test-postgres.ps1` 为 24 passed；`./scripts/test.ps1` 为 44 passed、24 skipped；Ruff、Mypy、`pip check`、`git diff --check` 通过。迁移 `0002` SHA-256 为 `5fdc67aaff07dbcd486fc62774ad5745cbef871332433dfb8535790a0a18a580`。
  - 已知边界：外部动作完成到 `finalize` 之间崩溃会保留 `EXECUTING/PREPARED` 未决态，后续必须通过 reconciliation 处理，禁止自动重试。

- [x] **F02 — Extension Supervisor（DONE）**
  - 已交付：受控暂存与静态检查（本地目录/zip/whl，拒绝 URL 与未固定 lock；路径穿越、符号链接、越界 Manifest 引用安全失败）、一次性确认屏障（重新校验 staged hash，确认后 artifact 变化即失效）、每扩展版本独立 venv 与真实 Worker 子进程、JSON-RPC framing/超时/超限加固、handshake 精确校验后才发布原子 Registry Snapshot、enable/disable（有界排空）/upgrade（旁路安装+原子切换+失败保持旧版）/rollback（数据 Schema 兼容校验）/uninstall（保留 `ext_*` 数据与 tombstone）、operation 状态与安全诊断持久化（PostgreSQL `extension_operations`，无需新迁移）、启动恢复（ENABLED 重启 Worker，瞬时态收敛，RUNNING operation 标记 `SUPERVISOR_RESTART`）、Local Admin API 管理端点与 CLI（inspect→确认→operation 轮询）。
  - 第二轮复验修复：新增迁移 `0003_f02_operations.sql`（幂等键/命令指纹 + 部分唯一索引，SHA-256 `28cbda227918bfcd80366208b59713eb0dbb0b0cbdaa582cb5e55a91ce2e1e03`）；Worker 最小环境白名单；暂存/哈希/安装统一忽略集合；lockfile 精确版本+哈希与 `--require-hashes`；升级基线重查与全链路补偿；安装最终持久化失败回滚代码目录；拒绝计划语义与重新安装；有界 drain 与 `DrainReport` 校验；契约验证比较完整工具描述符/风险/Schema/槽位；恢复失败先停 Worker；RPC 写入/取消类型化；`data_namespace` 单射；诊断码允许列表强制；公共扩展列表读持久 store；`RUNNING` 转换失败原子落 `FAILED`。
  - 第三轮复验修复：新增迁移 `0004_f02_operation_request_scope.sql`（request_scope + 部分唯一索引，SHA-256 `6b6bb9f5cea83b443c9ca345f7a9d134f6ebca69969f01e23557ecff706af6fc`）；取消统一“先清理后重抛”（shielded cleanup + 子进程回收）；drain 单调 deadline 覆盖等锁+写入+读取并严格校验 DrainReport；写入期取消破坏 RPC 流；安装取消清理版本目录/staging/plan 并收敛 REJECTED；升级自禁用旧版起全链路补偿（含 UPGRADING 保存与 DRAIN_TIMEOUT）；install/upgrade 重放先查持久 request_scope，跨重启返回原 operation；`interrupt_running` 强制诊断码白名单。
  - 第四轮复验修复：回滚先解析候选再禁用当前版本，失败时补偿保持当前版本 ENABLED 且可调用；`run_blocking` 取消时等待复制线程结束，删除后的版本目录不会被线程重建；`call()` 的 deadline 覆盖锁等待；契约验证 finally shield 关闭 Worker；新增文档-实现一致性测试。
  - 第五轮复验修复：回滚候选的启动/健康/发布/持久化全部纳入 `LifecycleManager.rollback` 同一补偿边界（Supervisor 删除额外 `enable()`），候选失败时撤销发布、停止 Worker 并重新启用原版本（四类反例）；`run_blocking` 首次取消优先，线程在取消后抛异常不覆盖取消信号（支持重复取消）。
  - 第六轮验收材料修复：取消反例直接取消单个 `manager.rollback()` 任务（不再用 `service.stop_all()`），测试 Runtime 维护活动 Worker 映射且未启动时 `invoke_tool` 失败；另增 `stop_all` 全停后 `recover()` 按持久 ENABLED 记录重启的测试；文档数字同步。
  - 证据：`./scripts/test.ps1` 153 passed、28 skipped；`./scripts/test-postgres.ps1`（F01+F02）28 passed；F02 相关集合 110 collected（无数据库时 106 passed + 4 skipped；cancel_cleanup 11、supervision_service 34；含真实 `python -m venv` + 真实 Worker 子进程集成、契约漂移、崩溃/超时/框架错误注入、取消资源回收、失败回滚保持当前版本、升级失败矩阵、并发启动、PostgreSQL 重启恢复与跨重启幂等重放）；测试后无残留 python/worker 子进程；Ruff、Mypy、`pip check`、`git diff --check` 通过；wheel 含 Supervisor/SDK/PWA/config/四份迁移。
  - 有意未实现：永久 purge（API 明确 501，需独立影响预览与二次确认）、扩展自有 `ext_*` migration 执行入口、扩展配置表单注入、Worker 崩溃后的自动重启退避（当前崩溃即 QUARANTINED，重启恢复只在宿主启动时）、远程 URL/Git 制品（默认拒绝）、跨进程操作互斥与 `NotificationProvider` 启用前行为验证。
  - 已知风险：宿主被强杀时子进程可能成为孤儿（Windows 需要 F09 的 Job Object 管护）；待确认的 staged 计划只存在于 Admin 进程内存，重启后按 `REJECTED` 收敛并清理暂存目录；带哈希锁的第三方依赖需要可达包索引/缓存。

## 接下来几步

- [x] **F03 — Cloudflare Access 验证（DONE — 三轮独立验收通过）**
  - 已交付：`core/auth/ports.py` 的传输无关身份端口；`infrastructure/auth` 的 Cloudflare Access JWT verifier（含总 deadline、禁重定向、取消安全刷新；未知 `kid` 成功刷新确认不存在为 401、节流窗口内未检查为可重试 503）与有界 JWKS 缓存/轮换；public middleware 的载体提取（含跨原始 Cookie 头重复检测）与稳定 401/503；`PA_CF_ACCESS_TEAM_DOMAIN`/`PA_CF_ACCESS_AUD`/`PA_PUBLIC_ORIGIN` 在所有 Settings 构造路径写回规范化值并校验；可注入 verifier；运行时依赖 `PyJWT`/`cryptography`/`httpx`。
  - 第一轮独立验收的 6 项缺陷（手工 Settings 绕过、无总 deadline/未禁重定向、取消污染节流、重复 Cookie 头、API→infrastructure 反向依赖、关闭顺序）已全部修复并补反例测试。
  - 第二轮独立验收的 2 项缺陷（节流窗口把合法密钥轮换误报为不可重试 401；接口归属/规范化文档过期）已修复：未检查的未知 `kid` 现在返回可重试 503，成功刷新确认不存在才 401；`Settings` 所有构造路径写回 canonical 值再校验。
  - 证据：F03 目标集合 `124 passed`；`./scripts/test.ps1` `269 passed、28 skipped`；`./scripts/test-postgres.ps1` `28 passed`（F01+F02 无回归）；Ruff、Mypy（138 files）、`pip check`、`git diff --check` 通过；wheel 含 `core/auth`、`infrastructure/auth` 与四份迁移。详见 `docs/NEXT_STEPS.md`。
  - 最终独立复验：F03 目标集合 `124 passed`；全量 `269 passed、28 skipped`；PostgreSQL `28 passed`；手工轮换复现、wheel 内容与四份迁移 checksum 均通过复核。
  - 未交付（有意，均属于 F03 范围之外）：自定义 Access 域名、应用内登录/MFA、Tunnel/Tailscale 编排；F04 披露许可已由 F04 单独实现（见下）。
- [x] **F04 — 模型适配器与敏感披露许可（DONE — 独立验收通过）**
  - 已实现：`core/models/disclosure.py` 的持久披露许可端口/服务（`DisclosureConsentService`/`DisclosureAuthorizer`/`canonical_field_digest`）、`core/models/errors.py` 类型化 provider 错误、`ModelRouter` 接入持久许可查询与显式本地回退；`infrastructure/models/` 的真实远端 OpenAI 兼容适配器与本地 Ollama 适配器（协议驱动 HTTP、SecretHandle 宿主解析 fail closed、loopback 强制、禁重定向、有界响应、总 deadline、取消安全、不重试）；`PostgresDisclosureConsentStore` + 内存测试替身；迁移 `0005_f04_model_disclosure.sql`（`model_disclosure_consents`/`model_disclosure_commands`，SHA-256 `012532834b281040d0031b48744ec7298e3c9b960bb247f51fd22c050f7534f0`）；`PA_MODEL_*` 配置在直接构造/`from_env`/`replace` 全部规范化并校验；public API 的 `preview/confirm/revoke` 最小接口；组合根接入真实适配器与 fail-closed 凭据占位。
  - 证据：两轮自我审查 16 项与第三至七轮验收修复后，F04 目标集合 `161 passed`（含 `test_model_disclosure.py`、`test_model_router_f04.py`、`test_model_adapters.py`、`test_bootstrap_f04.py`、`test_disclosures.py`、`test_f04_contract_consistency.py`、`test_settings.py`、`test_model_router.py`）；`./scripts/test.ps1` `409 passed, 37 skipped`；`./scripts/test-postgres.ps1` `37 passed`（F01 24 + F02 4 + F04 9）；Ruff、Mypy（150 files）、`pip check`、`git diff --check` 通过；wheel 含 `core/models/disclosure.py`、`infrastructure/models/*`、`PostgresDisclosureConsentStore` 与五份迁移。原始结果、逐项反例与残余风险见 `docs/NEXT_STEPS.md` 与契约 13.7。最终独立验收通过（七轮修复后）：F04 目标集合 `161 passed`；全量 `409 passed、37 skipped`；PostgreSQL `37 passed`；Ruff、Mypy、`pip check`、`git diff --check` 通过；`0001`–`0005` checksum 与报告一致，旧迁移未改动。
  - 仍未实现：Windows Credential Manager 真实凭据后端（F09，当前为 fail-closed 占位）、真实厂商端到端（无真实凭据，只有 HTTP transport 协议测试）、Embedding/结构化输出、PWA 披露界面与模型调用编排（F08/后续）。未 commit，未 push。
- [x] **F05 — 个人知识扩展（DONE — 完整独立审计与修复后通过）**：已交付 `extensions/personal_knowledge`（`knowledge.file_changes`/`knowledge.retrieve`/`knowledge.search` READ/`knowledge.reindex` INTERNAL_WRITE/`knowledge.reconcile`/`knowledge.roots`/`knowledge.index_schema`）、通用宿主能力（全双工 RPC、`host.data.execute/transaction/migrate` 数据代理、`ExtensionConfigStore` 配置通道与 Admin `GET/PUT .../config`）、授权根路径安全、Markdown/TXT/PDF 抽取与稳定 locator、SHA-256 版本化分块与来源快照 CAS、扩展自有 `ext_*` Schema 迁移、PostgreSQL FTS + pgvector RRF 混合检索、引用复核与删除传播、embedding 端口（默认显式降级为仅 FTS）。
  - 第二轮独立验收修复（6×P1 + 2×P2）已完成并在最终复验中通过：数据代理三路径统一核心表拦截、授权根即时边界与删除传播、首次失败重试、取消/孤立候选清理与激活 CAS、embedding 身份纳入增量差异（含维度探测）、Ollama 无代理总 deadline、capability 授权门、STALE/DELETED 不返回正文。
  - 第三轮完整修复（8 项）已完成：首次数据调用先串行创建专属 Schema 且动态刷新核心关系禁表；SDK deadline 传入宿主并把 PostgreSQL statement timeout 类型化为 `DATA_TIMEOUT`；同版本构建采用 `built_by` 全链路 owner + 无副作用 CAS；移动后旧路径可建立新 source；向量检索严格绑定活动版本 embedding identity；长单行/大 PDF 页可精确分块且 embedding/数据库写入有界批处理；Ollama 截断响应类型化；事件以单调 generation 区分重复 A→B→A→B 转换。
  - 最终独立审计与自审计修复：配置严格 JSON/Schema/并发原子写入与启动复核；读取和 PDF 解压双层字节上限；来源激活/移动/删除使用完整快照 CAS 且 generation 严格单调；长构建 heartbeat 租约；轮询事件与 reconciliation 共用转换语义；数据参数深度、向量帧和聚合结果有界；迁移单次校验读取、逐语句白名单与 search_path 越权阻断。每项均有反例测试。
  - 证据：`./scripts/test.ps1` → 597 passed、81 skipped，Ruff/Mypy（166 files）通过；`./scripts/test-postgres.ps1` → 81 passed（PostgreSQL 17.11 + pgvector，含真实 Worker）；`pip check`/`git diff --check` 干净。扩展迁移 `0001_knowledge_index.sql` SHA-256 为 `0f8758f97cce7206ac0b5cd4aa159f1b7b43de79cf1fa77a811a67f8aecd759d`；`0001`–`0005` 核心迁移未改。详见 `docs/NEXT_STEPS.md`。
  - 未实现（有意）：宿主自动执行 MigrationProvider（当前由扩展经数据能力触发）、OS 级文件监听（F09）、向量 ANN 索引（无维度约束列）、真实远程 embedding、PWA 表单渲染（F08）、永久 purge（501）。

- [x] **F06 — smail 扩展（DONE — 六轮独立审计及补充复验通过）**：已交付 `extensions/nju_smail`（`smail.poll_inbox`/`smail.thread_history`/`smail.search` READ/`smail.prepare_reply` INTERNAL_WRITE/`smail.sync`/`smail.send` EXTERNAL_WRITE/`smail.send_status`/`smail.reconcile_send`/`smail.reply_flow`/`smail.poll_every_5m`/`smail.account_settings`/扩展自有迁移）与通用宿主邮件能力：`core/mail` 端口（`MailTransportBroker`/`MailAccountBinding`/`MailReadSession`/`MailEnvelope`/`MailRecipientResult`/`MailDeliveryReceipt`/`MailReconciliationResult`/`MailPolicy`/`MailDeliveryLedger`）、SDK `host.mail.*` 只读客户端与 `host.artifact.*` 能力、真实 TLS IMAP 只读客户端（`EXAMINE`/`BODY.PEEK`，可证明不改变服务端状态）、真实 TLS SMTP 状态机（DATA 前/中/后断线分级、逐收件人结果）、`MailSendExecutor`（Tool Gateway + R2 审批 + Outbox + 扩展按当前草稿版本物化精确 MIME 字节）、迁移 `0006_f06_mail_transport.sql`（通用 `mail_delivery_actions` 传输台账）。读取与发送能力独立声明/配置/授权；扩展不持有密码、不建 SMTP/IMAP 连接；无审批时 executor 调用为 0；编辑草稿后旧审批绑定失效；UNKNOWN 不自动重发并由 Sent 只读对账收敛；账户换绑在跨进程 `ExclusiveFileLock` + 活体 fail-closed guard 下不再有 check-then-act（DATA 前换绑中止、DATA 已开始则等待提交），租约 guard 以单调 deadline 为权威并锚定数据库领取/续租时刻（心跳挂起、间隔大于租约或领取后慢复核均在到期即拒绝派发），组合 guard 在全部锁内复核，注册表写操作经 `run_blocking` 取消安全执行（调用方观察到取消前提交必已落地），真实 sender 抛类型化 `ACCOUNT_CHANGED`。
  - 最终独立验收证据：`./scripts/test.ps1` → 742 passed、103 skipped、Ruff `All checks passed!`、Mypy `Success: no issues found in 192 source files`；`./scripts/test-postgres.ps1` → 103 passed（真实 PostgreSQL、personal_knowledge Worker 与 smail 生产组合根 Worker）；`pip check` 与 `git diff --check` 通过；测试后无残留 Python Worker；框架 wheel 190 个条目，包含 F06 邮件模块与 `0006_f06_mail_transport.sql`，且不包含 `nju_smail` 扩展源码。核心新增迁移 SHA-256 为 `dbc5001bdd16981f2a17f36abe4ef3fcdd36c63c3461477f1a61e1ecb6f32a01`；扩展迁移 SHA-256 为 `6320af7791bd2805e36c70f7fa731417935e7bfd6ad875b95a6d42d179469d8c`；`0001`–`0005` 未改。
  - 真实外部验收未执行：没有用户提供的 smail 账号、SecretHandle 与受控收件地址，因此未连接真实邮箱、未发送真实邮件；协议级模拟服务器与真实 TLS/socket/PostgreSQL/Worker 测试不能替代真实 smail E2E。Windows Credential Manager 仍属 F09，扩展与宿主均保留通用 SecretHandle 端口并在生产后端不可用时 fail closed。
  - 受控真实只读同步已执行（2026-09-19，用户实时参与）：本机回环页面录入客户端专用密码（仅进程内存、不落盘），真实 `imap.exmail.qq.com:993` 只读同步入库 **15 封邮件 / 15 个事件**（INBOX 13 + Sent Messages 2），第二次同步 0 新（幂等），`mail_send_actions=0`、发送能力关闭；服务器侧 `EXAMINE INBOX OK 13` 与容器 LIST 探测确认该账号 IMAP 可见邮件即这些。过程中修复 5 个真实互操作缺陷（非 ASCII 安装路径、宿主写帧越过 worker 1 MiB 输入上限、QQ Exmail 的 RFC 2971 `ID` 要求、`host.mail.fetch` 响应按 512 KiB 原始字节预算分批、`\NoSelect`/容器子文件夹枚举），均有回归测试。真实发送、凭据持久化（F09）与完整大邮件内容仍未完成，详见 `docs/NEXT_STEPS.md` F06 章节。

- [ ] **F07 — ehall 扩展（IN_PROGRESS — 修复与真实提交前验收未完成）**：已交付通用的受监督浏览器基架（`core/browser` 的会话状态机、origin/R3 策略、页面 fingerprint、canonical 预览、`BrowserSessionBroker`；`infrastructure/browser` 的 headed Playwright driver、回环 Desktop Companion 服务/客户端、`BrowserActionExecutor`、`host.browser.*` 只读宿主能力；迁移 `0007_f07_browser_sessions.sql`；`PA_BROWSER_*` 配置）与独立扩展 `extensions/nju_ehall`（`ehall.discover_apps`/`ehall.inspect_transaction`/`ehall.prepare_preview` READ-INTERNAL、`ehall.fill_form`/`ehall.submit` EXTERNAL_WRITE 且由宿主执行、`ehall.reconcile` READ；Context/Workflow/Form/Migration 槽位；扩展自有 Schema）。
  - 证据：F07 定向集合 197 passed（单元 + 契约）；真实 headed Chromium 集成 13 passed（本地 HTTPS mock）；真实 PostgreSQL 8 passed；真实 venv/Worker/Supervisor/Gateway 4 passed；`./scripts/test.ps1` 952 passed、115 skipped、Ruff/Mypy（214 files）通过；`./scripts/test-postgres.ps1` 115 passed；`pip check` 与 `git diff --check` 通过；wheel 208 条目（审计复核修正原写的 207）含 F07 模块与 `0007` 迁移且不含扩展源码；迁移 `0007` SHA-256 `b50a94f3ea627157ea347dc56f38c23ef59b07e420bdba36adf867297f785005`。完整逐项证据见 `docs/NEXT_STEPS.md` F07 章节。
  - 独立审计（2026-09-20）未通过：4 个 P1（延迟 autosave 绕过、提交未绑定网络目标、未知页面版本未按 R3/扫描不完整按无风险处理、capability 经异常链泄漏）、7 个 P2（live URL 未逐次复核、回执误报、每任务单会话非原子、`expires_at` 未执行、对账由扩展声明、双重编码开放重定向、文档陈旧）与 `test_cancel_reaps_the_browser_process` 失败。
  - 第二轮独立审计（2026-09-21）未通过：6 个 P1（导航动作可绕过 R2 发写请求、提交目标未绑定 query、DOM 文本可伪造提交成功、对账基线取错页面、UNKNOWN 被 TTL 错误释放、`transcript.json` 未接入且发现不识别 JS 按钮）与 5 个 P2（0008 升级不兼容重复数据、5xx 保留 response、PID 回收稳定失败、文档矛盾、0008 SHA 未登记）。修复：默认阻断全部非 GET（仅登录挑战窗口与提交临界区的一次性完整目标许可例外，导航期间写请求被 abort 即 `NAVIGATION_WRITE_BLOCKED` 安全暂停）；提交许可绑定完整 URL（无 query）；成功必须以 `write_requests_allowed==1` 为证据，DOM 伪造一律 UNKNOWN；基线经只读临时标签页从真实跟踪页采集并只存哈希（截断/缺失时 `RECONCILE_UNSAFE`）；UNKNOWN 为未决状态、TTL 不再释放且占用任务槽位；0008 先确定性收敛重复会话再建索引并固定 SHA-256 `5dcd9513b9767a1c67092dcf6520bbf75eb42963916b6da12b909413c91d2af3`；Worker 加载并注册双 adapter、发现读取声明式导航动作；5xx 脱敏补强；browser-level CDP PID 修复回收。详见 `docs/CONTRACTS_AND_INTERFACES.md` 第 16.10 节与 `docs/evidence/real_ehall_2026-09-21.md`。
  - 修复后证据（第二轮）：`./scripts/test.ps1` 1040 passed/120 skipped/0 failed（真实 Chromium 26 passed）；回收用例独立 5/5；真实 PostgreSQL 120 passed（含 0008 收敛反例）；Ruff/Mypy 通过。
  - 第三轮独立审计（2026-09-21）未通过：3 个 P1（登录窗口无条件放行任意非 GET、真实 POST + 伪造 DOM 回执仍被误判成功、0008 会强制取消 UNKNOWN/EXECUTING）与 1 个 P2（文档与证据不一致）。修复：登录窗口改为“冻结真实登录表单的精确认证目标（method/origin/path）+ 一次性额度 + 仅主框架文档导航”，后台 fetch/XHR 与子框架写请求一律阻断；提交不再信任 DOM，先落 `UNKNOWN` 后立即执行宿主只读 tracking 差分，只有唯一新增服务器回执才 `SUCCEEDED`；0008 先 fail-closed 检查同任务多个未决动作（拒绝升级要求人工裁定），否则优先保留唯一未决会话并只收敛副作用前状态；文档统一为“门户/SSO 只读采集已运行，交易表单/指纹/预览/提交未运行”，历史数字标注被取代。第三轮修复后证据：`./scripts/test.ps1` 1046 passed/122 skipped/0 failed（真实 Chromium 29 passed）；回收用例 5/5；真实 PostgreSQL 122 passed（含未决动作保留/多未决拒绝升级反例）；Ruff/Mypy 通过；wheel 209 条目。
  - 审计修复（2026-09-21，第一轮）：driver 持久阻断写请求 + 提交临界区单条精确匹配放行；final action 声明 `method`/`target_origin`/`target_path` 并进入预览与 canonical hash，提交前复核；`allowed_page_fingerprints` 必填、交易页版本不匹配即 `UNKNOWN_PAGE_VERSION`/R3，`scan_incomplete` 即 `PAGE_TEXT_SCAN_INCOMPLETE`，fingerprint 纳入表单；capability 脱敏（无异常链、清除局部引用）；回执需点击前后差异 + `receipt_pattern`；迁移 `0008_f07_browser_session_uniqueness.sql`（每任务单活动会话部分唯一索引 + `receipt_baseline`）；`expires_at` 强制执行；宿主驱动 `host.browser.reconcile`（移除 `record_reconciliation`）；双重编码开放重定向；PID 轮询修复回收测试。修复后证据：`./scripts/test.ps1` 1031 passed/119 skipped（含真实 Chromium 21 passed），Ruff/Mypy（214 files）通过；`pytest tests`（除真实浏览器）1108 collected/990 passed/118 skipped/0 failed；回收测试连续 5 次通过；真实 PostgreSQL 119 passed；wheel 209 条目含 `0008` 且不含扩展源码；`0001`–`0007` 未改、新增 `0008`。详见 `docs/CONTRACTS_AND_INTERFACES.md` 第 16.8 节。
  - 未标记 `DONE`：真实门户/SSO 只读采集已运行（用户亲自完成 SSO，证据 `docs/evidence/real_ehall_2026-09-21.md`）；真实教室借用已部分试填；完整填写至权威预览、真实适配器指纹与提交回执尚未验收。真实交易验收需要用户亲自在可见浏览器中完成 SSO/验证码/扫码并确认合法低风险事务。R3（退课/撤回/支付/选课变更/法律声明/未知事务/未知页面版本）在策略层永久阻断。

- [ ] **F07 第四轮独立审计修复（2026-09-23，已实现，待全量复验）**：①真实 POST payload 绑定审批——核心 `form_payload_sha256` + driver 在 route 内校验真实 `request.post_data`（声明字段子集哈希 = 获批哈希，完整 body 哈希 = 点击时模板哈希，仅 urlencoded，multipart fail closed，正文不出浏览器），隐藏输入纳入页面指纹；②tracking 精确绑定——基线与对账都要求最终 URL 精确等于声明 tracking origin/path，重定向即 `RECONCILE_UNSAFE`/`TRACKING_URL_MISMATCH`；③登录路径改为精确/显式 glob 匹配（`/sso/login-evil` 不再获得写窗口）；④quarantine 先核验持久版本、窗口内换代则恢复新版注册；⑤Companion 纯 ASGI 累计字节限额（chunked 同样 413）且 create/revoke 先验 root capability；⑥握手等待 `server.started` 后再打印；⑦`::1` base URL 加方括号；⑧文档/证据校准（仓库已提交状态、真实数字、历史数字标注取代、已提交历史的 LF/CRLF 不做改写）。冻结语义见 `docs/CONTRACTS_AND_INTERFACES.md` 第 16.12 节。

- [ ] **F07 第五轮独立审计修复（2026-09-24；实现完成，后续复验发现阻断项）**：①隐藏/密码控件值不再离开浏览器进程（隐藏输入移出 `ctl:` 索引空间、以 `hidden_fields` 进入指纹、导出值清空，核心与宿主防御性再清空）；②对账改为单次只读标签页采集并精确校验 tracking origin/path#hash 且 query 必须为空（重定向/查询参数一律 `RECONCILE_UNSAFE`，基线不符即 `TRACKING_URL_MISMATCH`）；③`LifecycleStore.save` 支持 `expected=(state, version)` 原子 CAS，`_quarantine` 用它消除同版本并发覆盖；④payload 校验按获批字段收窄（`expected_payload_locators`），静态 hidden/CSRF 字段可正常提交、注入或改动仍 abort。原始 `1104/123` 仅为该轮当时快照，已被后续复验结果取代；详见 `docs/CONTRACTS_AND_INTERFACES.md` 第 16.13–16.15 节。
- [ ] **F07 第五轮复验阻断项修复（2026-09-24，代码已修复；2026-09-26 全量复验通过）**：①PostgreSQL CAS 以 `active_version` 比较包版本而非 `manifest_version`；CAS 失败后重读持久记录，仅当最新状态仍为 `ENABLED` 才恢复其 Registry 槽位（同版本也恢复）；②driver 用 HTML successful-controls 语义生成实际 POST 模板：checkbox/radio 未选时省略、选中时发送实际 value（默认 `on`）、disabled 控件省略，另核验批准字段语义哈希与真实 body 哈希。`./scripts/test.ps1` 为 **1110 passed / 124 skipped**（含真实 headed Chromium），Ruff/Mypy 通过；`./scripts/test-postgres.ps1` 为 **124 passed**（真实 PostgreSQL/Worker）；`python -m pip check` 与 `git diff --check` 通过。此前 `1068/161/5` 和 `123/1` 是已被取代的失败快照。真实 ehall 交易表单仍未验收，F07 继续 `IN_PROGRESS`；详见 `docs/NEXT_STEPS.md` 和契约 16.15。
- [ ] **F07 跨域真实验收增量（2026-09-26，进行中）**：用户实测事项在新标签页打开 `ehallapp.nju.edu.cn`，原白名单使请求中止，旧 `CURRENT` 只能读取大厅页。新增用户手动切换的 `PA_BROWSER_ORIGIN_MODE=allowlist|open` 与采集 CLI `--origin-mode`，按用户要求默认 `open`；`open` 免 origin 白名单但仍执行 HTTPS/URL 结构校验、R3、非 GET 默认阻断和 R2 精确审批。只读采集支持用户新开的唯一标签页，报告去除 URL 动态查询参数与控件当前值。真实事务页与提交仍未验收；复验结果见 `docs/NEXT_STEPS.md`，F07 保持 `IN_PROGRESS`。
- [ ] **F07 本机手工验收入口（2026-09-27，真实打开与字段清单已验证）**：`assistantctl ehall` 已能在用户完成 SSO 后自动查找并打开事项、按字段填值，并在独立本机点击前确认页等待确认或取消；正常 Chromium 网络行为绕开旧采集链路对页面数据 POST 的拦截，旧 Companion 链路未改。`--sample "打开 教室借用 > 临时借用（仅借1天）"` 按真实两级入口逐次点击，等待异步表单后列出当前可见字段候选数、可直接填写数与只读/自定义字段，试填可确定的空白事由/说明及人数字段并停在真实页面；展开动态区域后可输入 `r` 重扫，不进入提交分支。入口仅供本机手工验收，**尚未接入邮件/PWA 消息、LLM 或 Agent Worker**。本地真实 Chromium 模拟站点验证后台 POST 加载、确认前 0 次指定按钮点击、用户确认后 1 次提交、取消后 0 次提交、两级导航、`--sample` 试填后 0 次提交以及异步字段清单；定向测试 **14 passed**。用户已确认“成绩查询”真实事务页正常显示，且教室借用字段清单与真实页面一致；完整填写至权威预览和回执仍未验收，F07 保持 `IN_PROGRESS`。完整回归结果见 `docs/NEXT_STEPS.md`。
  - 真实教室借用字段反馈后，修正用途描述标签与 HTML `text` 数字字段的试填；`--sample` 可列出自定义下拉可见选项，并接受显式字段/选项文字进行不提交试填。目标测试 **19 passed**，完整 `./scripts/test.ps1` **1141 passed / 124 skipped**，Ruff/Mypy 通过；真实选项与日期交互仍待用户复测，见 `docs/NEXT_STEPS.md`。
  - 自定义下拉探测现按当前弹层关联或新增选项取值，并在探测前后关闭旧弹层；定向 **21 passed**。本次完整 `./scripts/test.ps1` 为 **1142 passed / 124 skipped / 1 failed**（F06 TLS 超时测试，单独重跑通过）；真实下拉仍待复测。框架不编码开始/结束日期等事务字段联动，后续由 LLM 决定字段值与操作顺序；见 `docs/NEXT_STEPS.md`。
  - 真实重测表明旧弹层仍会遮挡后续下拉，且下拉选项存在先后依赖；加入失焦关闭与键盘展开回退，`r` 会重新读取字段和选项，字段清单不再混入弹层内部控件。先失败后通过的本地 Chromium 反例及目标集合 **26 passed**；完整 `./scripts/test.ps1` **1148 passed / 124 skipped**，Ruff/Mypy 通过。真实复测状态见 `docs/NEXT_STEPS.md`；不在框架中硬编码事务字段依赖。
  - 用户随后真实复测确认下拉可以展开，手动调整字段后 `r` 能识别新增的校区/节次选项且清单不混入弹层控件；程序化选中、值保持与最终预览仍待真实验证。资料库/LLM 自动选择属于阶段 10，不计入本轮完成证据。
  - 用户运行带 `借用方式=按节次借用` 的 `--sample` 后反馈“成了”，证明至少该真实下拉的程序化选择在本次会话可用；完整填写、字段差异/材料/后果预览仍待验收，F07 保持 `IN_PROGRESS`。
  - 用户决定先跳过完整真实填报，待资料库/LLM 决策接入后再测；现有证据不覆盖原阶段 8 的提交前完整预览，因此不把 F07 标为 `DONE`。本次对话的修复、测试快照、真实反馈与延期项汇总于 `docs/F07_REPAIR_REPORT_2026-09-27.md`。
  - 审计汇总后的 P2 交接修正：本机 `assistantctl ehall` 的正常页面 POST、本机指定按钮点击前确认与受监督 Companion 的 Gateway/R2/R3/Outbox/UNKNOWN 契约已在 `AGENTS.md`、接口契约和 README 中分清；本机页更名为“本机点击前确认”并说明其只列脚本记录值，不是权威完整预览。回归测试先红后绿；本轮 `./scripts/test.ps1` **1150 passed / 124 skipped**，Ruff/Mypy（216 files）、`pip check`、`git diff --check` 通过。最近一次 PostgreSQL/Worker 独立测试仍是 2026-09-26 的 **124 passed**，本轮未重跑；完整真实填报仍按用户决定延期，F07 保持 `IN_PROGRESS`。详见 `docs/NEXT_STEPS.md`。

  - 2026-09-28 提交未决 P1 修复：点击后畸形响应、点击期或未决写入期取消均按 `UNKNOWN` 持久化并只读对账，绝不重复点击；会话库写入故障时 Gateway/Outbox 仍保持 `UNKNOWN`，重启恢复处理残留 `EXECUTING`。Broker→Gateway→Outbox 反例先红后绿；`./scripts/test.ps1` **1157 passed / 124 skipped**、Ruff/Mypy（216 files）通过；`./scripts/test-postgres.ps1` **124 passed**（真实 PostgreSQL/Worker），`pip check`、`git diff --check` 通过。真实完整填写、权威预览与适配器指纹仍待验收，F07 保持 `IN_PROGRESS`，不得推进 F08。

  - 2026-09-28 自复验新发现并修复 Gateway 终态写入取消 P1：点击后浏览器已 `SUCCEEDED`、Outbox 却停在 `PREPARED`、审批停在 `EXECUTING` 的反例先失败；Gateway 现等待已启动的终态写入完成再传播连续取消，成功和 `UNKNOWN` 两条分支均有回归。真实 PostgreSQL 连续取消反例通过；`./scripts/test.ps1` **1159 passed / 125 skipped**，Ruff/Mypy 通过；`./scripts/test-postgres.ps1` **125 passed**；wheel 211 条目核验通过。完整真实提交前验收、真实适配器指纹仍未完成，F07 保持 `IN_PROGRESS`，F08 不启动。

  - 2026-09-28 第二课堂真实受监督试填增量：用户完成 SSO 并进入“第二课堂 → 第二课堂成绩单（在线办理） → 志愿服务经历（申请）”；脚本接管新标签页嵌套 frame，经 `BrowserSessionBroker` 从上到下填写 7 个业务字段，在学院“文学院”改变审核人选项后复核新页面指纹。Broker 返回 `PREVIEW_READY`、缺失字段 0；用户目视确认“七项正常”且无页面报错；最终“提交”未点击。脱敏报告明确 `target_bound=false`、`acceptance_complete=false`：真实提交目标、权威材料/后果、正式扩展 Adapter 与 Gateway/Outbox 链路仍缺，不能据此标记 F07 `DONE`。定向单元/契约 63 passed、真实 Chromium 本机交互 23 passed、Companion 嵌套 frame 2 passed，Ruff/Mypy 通过；按用户要求本次未跑全量测试。证据与复现命令见 `docs/evidence/real_ehall_volunteer_supervised_2026-09-28.md`；F07 继续 `IN_PROGRESS`，F08 不启动。

- 2026-09-30 本机直接交互烟测：用户在可见 Chromium 亲自登录一次后，脚本自动进入“第二课堂 → 第二课堂成绩单（在线办理） → 志愿服务经历（申请）”，从上到下试填七项合成值并停在最终“提交”前；用户确认“七项正常”，脚本没有点击提交。针对刷新后继续与隐藏下拉的定向回归 **44/44 通过**；完整命令、脱敏证据、风险边界见 `docs/F07_AUTO_ROUTE_SMOKE_REPORT_2026-09-30.md`。本轮未跑全量或 PostgreSQL/Worker；受监督链路的真实目标/材料/后果权威预览仍未完成，F07 保持 `IN_PROGRESS`，等待独立审计，不启动 F08。
- 2026-09-30 烟测后审计修复：修复隐藏下拉误改无关字段及其 `change` 事件的 P1，并修正文档契约断言的 P2；本地真实 Chromium 反例先失败后通过。未经排除的 `./scripts/test.ps1` **1185 passed / 125 skipped**，Ruff/Mypy（216 files）、`pip check`、`git diff --check` 通过。旧实站烟测发生在 P1 修复前，修复后未重跑真实 ehall；正式 Adapter、目标/材料/后果权威预览和受监督 Gateway/Outbox 仍待验收。详情见 `docs/F07_AUDIT_REPAIR_2026-09-30.md`；F07 保持 `IN_PROGRESS`，F08 不启动。
- 2026-09-30 第二轮复审修复：首轮 P1 修复仍可误点点击后才出现的无关菜单；新增本地 Chromium 反例先复现无关下拉变值，随后要求隐藏下拉和菜单均有明确字段归属，模糊时在选项点击前停止。交互层/单元 **40/40** 通过，改动路径 Ruff/Mypy 通过；`scripts/test.ps1` 应用户要求在约 16% 处中断，当前快照全量结果由审计 agent 复验，首轮 1185/125 只是历史值。修复后未重新访问真实 ehall；正式 Adapter、受监督权威预览和 Gateway/Outbox 仍未验收。见 `docs/F07_AUDIT_REPAIR_ROUND2_2026-09-30.md`。F07 保持 `IN_PROGRESS`，F08 不启动。

F05–F10 和两条最终 E2E 的完整范围见 `docs/NEXT_STEPS.md`。F02–F06 均已完成独立验收；F07 与 F08 均为 `IN_PROGRESS`，F08.1/F08.2 已通过定向验收。

- [ ] **F08 — PWA 与 Web Push（IN_PROGRESS）**：按用户的排期例外拆为独立子任务。**F08.1–F08.5 DONE**：任务列表/详情、连接状态、消息交互、Schema 表单、来源与版本化草稿、已有审批记录的手机核对与批准，以及订阅/撤销与最小元数据 Web Push，均已通过各自约定范围的独立验收。F08.4 邮件路径核对当前草稿、真实 MIME 与完整附件清单；F07 ehall 试填记录在公共 API 与页面均不可批准。**F08.6 BLOCKED（唯一当前项；最终独立复验待办）**：真机补证已获独立认可；本轮四份测试目录隔离与完整本地/临时PostgreSQL/真实Worker门禁通过，不追加手机动作。按上方2026-10-30报告复验后决定签收；最小F09.1/F06.P2已DONE，F07/F08整体继续 `IN_PROGRESS`。
  - 2026-10-01 F08.4 独立验收 P2 附件清单缺口修复：审批 GET/POST 现始终对比持久快照与实际 MIME 附件清单，包括空清单；宿主 `MailSendExecutor.prepare_call` 从当前草稿经 Worker 只读物化、MIME 核验后填充 `ToolCall.attachments`。先红后绿的 API 反例覆盖空/非空双向、名称/大小/哈希不一致，真实 PostgreSQL + Worker 用例覆盖带附件草稿到持久审批、手机预览与模拟 SMTP。实施者定向 API/单元 **31 passed、2 warnings**，真实 Worker **1 passed**；上行 1218/128 与 128/1 是修复前历史结果，不能充作修复后全量结果。应用户要求中止已启动的全量与 PostgreSQL 脚本。用户随后独立复验 API、邮件单元、390px 浏览器和契约 **39 passed**，真实 Worker＋临时 PostgreSQL＋模拟 TLS SMTP **1 passed**，Ruff/Mypy/`git diff --check` 通过；据此 **F08.4 DONE**，**F08.5 IN_PROGRESS，唯一当前项**。产品 Agent 自动创建邮件审批尚未接线，不在 F08.4 验收范围。F08 整体及 F07 仍为 `IN_PROGRESS`。精确命令与边界见 `docs/NEXT_STEPS.md`。
  - 真实 Worker 用例已纳入 `scripts/test-postgres.ps1` 的显式文件列表，后续运行该脚本将覆盖该链路；本轮只定向复测该用例，没有运行整个脚本。精确命令和边界见 `docs/NEXT_STEPS.md`。
  - **F08.5 修复与验收（2026-10-02，DONE）**：助手公共 API 提供设备订阅、状态与撤销；订阅端点和浏览器密钥经宿主加密后持久化，受信 Push 服务 origin 显式配置。Gateway 持久准备 R2 审批后发送只含待处理提示、`R2` 和非敏感任务 ID 的一次性加密通知；浏览器需用户主动授权，通知点击只打开助手任务页。白名单、旧密钥隔离、同键重放及 Push 服务 410 后复用失效浏览器端点的缺口均经反例修复和独立定向复验。410 后仅在服务端确认无有效记录且无结果未确认的原请求时，解除并核验旧浏览器订阅，再创建新端点；无法清理或重获旧端点不重报已订阅。Service Worker v11 仍只缓存固定静态资源。独立定向 **45 passed、2 warnings**；修复后 `./scripts/test.ps1` **1236 passed、130 skipped、6 warnings**，Ruff/Mypy（228 files）通过；`./scripts/test-postgres.ps1` **130 passed、1 warning**。真实 Android、Cloudflare、真实 Push 服务和产品 Agent 自动发起邮件动作均未验收；默认生产 `SecretStore` 仍由 F09 补齐。精确命令与边界见 `docs/NEXT_STEPS.md`。**F08.5 DONE；F08 与 F07 整体继续 `IN_PROGRESS`。**
  - **F08.6 本机预检（2026-10-02，BLOCKED）**：先写失败反例发现 health-only 应用将非精确 `/healthz/` 返回 307，现关闭斜杠自动重定向；真实本机 Uvicorn 进程黑盒确认只有 `/healthz` 返回最小 200，其余任务、管理、文档及 `/healthz/` 为 404。目标安全回归 **73 passed、5 warnings**，PWA 浏览器回归 **7 passed**，草稿/审批浏览器回归 **9 passed**，真实本机监听黑盒 **1 passed**；Ruff、Node 两份语法检查及 `git diff --check` 通过。用户确认固定域名/Access、Android 和 Tailscale 均尚未准备；默认生产 VAPID 秘密后端仍待 F09，真实 Push 不能据本机测试宣布通过。没有运行本轮全量、完整 PostgreSQL/Worker 或外部验收。详见 `docs/NEXT_STEPS.md`；F08.6 保留为唯一当前子任务，不启动后续编号。
  - **F08.6 环境准备协助（2026-10-04，仍 BLOCKED）**：用户没有域名和 Cloudflare 账号。已安装并核对官方 cloudflared 2026.9.3；官方 Tailscale 1.102.4 安装正在等待用户处理 Windows UAC，未确认安装完成。新增不激活、不覆盖的模板生成及候选配置检查工具，已在忽略目录 `var/deployment/f08-2026-10-04` 生成三份模板；数据库与 Access 字段留空，检查按预期拒绝。先红后绿覆盖 DSN、变量替换、继承环境覆盖及敏感值不回显；本轮准备工具与 health 边界定向 **16 passed、2 warnings**，Ruff/Mypy 通过。Docker 引擎未运行，未启动/迁移既有数据库，未注册账号、购买域名、发布 Tunnel/Serve 或执行真实 Push。指南见 `docs/F08_ENVIRONMENT_PREPARATION.md`，精确命令见 `docs/NEXT_STEPS.md`；F08.6 保持唯一当前项，不启动 F09/F10。
  - **2026-10-04 准备续查（仍 BLOCKED）**：用户已取得 `yanshuheng.dpdns.org` 并有 Cloudflare 账号，但明确仍在 DNS/NS 步骤，尚未 Active。只读核对 cloudflared 2026.9.3、Tailscale 1.102.4 已安装且 Tailscale 服务 Running；Docker 引擎未运行，未观察到助手/数据库目标端口监听。未确认 tailnet 登录、Access 应用或 Tunnel，未启动/迁移数据库、发布路由或执行外部动作。更新准备指南与记录，不改产品代码，不重跑测试。
  - **发布前准备快照与门禁（2026-10-04，仍 BLOCKED）**：本人 Cloudflare 授权完成，预注册零连接 Tunnel，固定主机名→public 8000 与兜底 404 的 ingress 验证通过，凭据文件各自限制为本人/SYSTEM。当时 Windows/Android 节点在线、无 Serve/Funnel 映射；原库只读预检确认 7 个已执行迁移校验和匹配、待执行 0008–0011，无重复浏览器会话收敛或过期邮件恢复行。按 AGENTS.md 发布要求运行当前工作区全量脚本 **1248 passed、130 skipped、7 warnings**（退出另有异步管道清理警告），Ruff/Mypy（228 files）通过；真实 PostgreSQL/Worker 脚本 **130 passed、1 warning**，临时库清理前后数据库集合一致。Node 两份语法、pip check、准备脚本 Ruff/Mypy 通过。该行是部署前证据，不能解释为当前尚未上线。
  - **最新授权接通与登录排障（2026-10-05，仍 BLOCKED）**：用户明确授权后启动原助手/health/Tunnel，原库已应用0008–0011，11份校验和一致。唯一占位 DNS 的精确替换获本人许可后成功；public/health/metrics 只监听回环8000/8010/20241，Admin未启动，Tunnel `/ready`200。本机无认证12项与公网边缘3项黑盒通过；私有 Serve 仅代理8010、`AllowFunnel=false`，同节点 HTTPS `/healthz`最小200和七条禁止路径404通过。用户误选的是账号 Funnel 功能开关，未发现实际公网Funnel转发，未声称已撤销该账号权限。手机Access页只提供Cloudflare账号登录并拒绝该身份；团队One-time PIN截图已确认，应用仅选该方式的截图已确认，本人反馈及手机截图确认首次登录和 SSE 连通。Android私有health与任务/管理404已获本人三项反馈；Chrome安装、其余跨设备路径、网络切换/缓存、真实Push及完整真机验收仍待补齐；这些部署配置与文档更新未新增业务代码，也未重跑前述完整门禁。精确命令与探测边界见 `docs/NEXT_STEPS.md`；不启动F09/F10，不提交或推送。
  - **Android安装资源修复（2026-10-05，F08.6仍BLOCKED）**：本人反馈Chrome只提供创建快捷方式。先红测发现Manifest缺图标、受认证Manifest未指定携带凭据、wheel遗漏图标；新增真实v11受控页面的缓存升级反例。补固定192/512 PNG、Manifest声明、`crossorigin="use-credentials"`与包装清单，SW升v12但仍仅四个固定缓存路径。定向API/契约 **78 passed、5 warnings**；PWA/草稿/审批浏览器 **17 passed**，包括真实旧缓存清理、PNG原生解码和敏感正文不入缓存/本地存储。Ruff、两份Node语法和离线wheel内容检查通过。没有数据库迁移、核心状态或审批/Outbox变更，本轮只跑相关定向集合；此前发布前1248/130不是本次修复后的全量结果。Android安装及其余真机/Push仍待复验，F07/F08整体IN_PROGRESS，不推进F09/F10。精确命令、失败及验证边界见 `docs/NEXT_STEPS.md`。
  - **Pura 70 安装续查（2026-10-05，F08.6仍BLOCKED）**：本人反馈修复后已有安装选项，但仅提示“开始安装”后无下文；手机不能使用Google Play。已核对官方WebAPK/GMS机制，将设备安装服务列为候选原因，未确认唯一故障原因或安装终态；已请求检查应用记录并尝试Chrome官方创建快捷方式入口。此次仅更新状态和指南，无新增产品修改或测试数量；快捷入口、独立安装及真实Push分别记录，待本人反馈后继续F08.6网络/恢复验证。
  - F08.6 同日手机续报：应用列表没有助手，添加桌面也无反应，入口未通过；已请求实际鸿蒙版本与 Chrome 快捷方式权限检查，备选为华为自带浏览器的官方版本对应步骤。仅文档续记，未新增代码/测试结果或改动网络保护，状态不变。
  - F08.6 随后本人反馈“应用安装到桌面了”，已记录桌面入口出现；浏览器/包装类型、从图标启动的列表/SSE及Wi-Fi→移动数据恢复仍待反馈。下一步继续这几项真机检查，未新增代码或测试数量；F08.6仍BLOCKED，F07/F08整体IN_PROGRESS。
  - **F08.6 最新手机观察（2026-10-05）**：本人确认Chrome标识桌面入口无地址栏启动，空任务列表和事件连接正常，Wi-Fi切到移动数据后恢复；随后对联网切后台或锁屏约一分钟、不整页刷新即可正常恢复回复“是”。这些仅为相应人工观察，未证明系统包类型、非空任务/草稿/审批、后台长期存活或Push。断网/恢复的另一问题仍待反馈；本次只更新文档，无新增测试集合结果。
  - 本人随后确认恢复联网会自动连接，断网时出现设备离线或事件连接中断；基础手机连接观察已补齐。本轮先请求本人操作标注仅记录的自检任务/消息并确认刷新恢复，随后收到“是的，都只有一条”；原库只读聚合在后续新增操作前也确认匹配自检任务1条、首条消息1条。电脑页面不手动刷新接收手机新增任务/消息的检查获本人“我试了新增任务和新增消息，都可以”反馈；不据此推断当前总条数或延迟。Agent自动执行未接线，QUEUED为预期，不属于外部业务事务或阶段10。本人通过USB远程DevTools提供手机CacheStorage截图，任务/消息使用后所选缓存只有四条固定静态路径；本人文本确认左侧仅一个节点、名称 `assistant-static-v12`。手机Network截图确认GET `/api/v1/tasks?limit=20` 返回200 OK、`Cache-Control: no-store`、`Cf-Cache-Status: DYNAMIC`，Disable cache未勾选。此次有限缓存确认只覆盖上述CacheStorage与任务GET响应头；HTTP缓存底层、LocalStorage/IndexedDB、未访问的真机Schema草稿/审批、其余边界及真实Push待验收。F08.6仍BLOCKED，F07/F08整体IN_PROGRESS；命令及观察边界见docs/NEXT_STEPS.md。
  - **Access暂不可用续查（2026-10-05）**：本人报告重新登录仍报校验不可用，随后确认手机/电脑均恢复；宿主官方JWKS两次只读检查为200、各2个有效公钥，具体中断原因未捕获。控制台只读确认应用会话24小时、唯一Email_policy沿用应用时长，全局会话读取超时未确认，未改安全设置。先红测1 failed/1 passed，再仅补任务列表精确503错误码的中文恢复提示，沿用前台联网30秒只读轮询，静态缓存升v13；相关API/390px浏览器20 passed、2 warnings，目标Ruff和Node语法通过，命令见NEXT_STEPS。原真机缓存证据仍是v12，v13手机更新/复验待确认。现有禁用项已从电脑实际页面定位为nju.smail/smail.account_settings，当时Example Echo为DISCOVERED、可用Schema草稿真机条件仍缺；后续本人明确授权指定材料安装启用，最新记录见下行。F08.6保持BLOCKED，F07/F08整体IN_PROGRESS。
  - **已授权Schema夹具与手机草稿续验（2026-10-05）**：本人确认材料sha256:1c3725193434fa8d027ce95b87b5992fec4bef2fa72f82da18525a241ec70725的example.echo@0.1.0用于草稿验收。正式Admin预览复核同一哈希，install/enable均SUCCEEDED且最终ENABLED；实际公共PWA出现example.echo_settings；手机来源由未确定变为用户输入，保存后草稿版本2及刷新恢复同一测试值已有本人反馈。随后电脑保存版本3获原库只读确认，手机截图显示保留基准2及本机未保存值，并展示服务器3/电脑值及两种显式冲突选择；本人随后确认显式保留后保存为版本4并刷新恢复，原库只读核对一致；409/412写入反例及审批仍待核验。本人随后提供草稿使用后的远程DevTools截图：Cache Storage树仅一个assistant-static-v12节点，未设置路径过滤，列表仍只有四个固定静态路径；GET form-draft为200 OK、Cache-Control: no-store，Disable cache未勾选。本次仅确认该CacheStorage及草稿GET响应头，不扩大为浏览器HTTP缓存底层、LocalStorage/IndexedDB、其他敏感API或审批场景；手机仍为v12，v13更新尚待确认。临时Admin及其两个直属Worker已优雅退出，8001无监听，原助手/health/Tunnel保留；没有重跑测试集合，未进行外部业务动作或提交/推送。F08.6仍BLOCKED，F07/F08整体IN_PROGRESS；精确命令与边界见NEXT_STEPS。
  - **手机v13静态更新复核（2026-10-05）**：本人按Update/刷新步骤提供远程DevTools截图，助手origin的Cache Storage展开且仅assistant-static-v13节点，旧v12节点不在树中；未设置路径过滤，只有/ui/、/ui/app.js、/ui/manifest.webmanifest、/ui/styles.css四固定静态路径，没有API条目。此证据限定缓存版本/路径，不证明浏览器HTTP缓存底层、LocalStorage/SessionStorage/IndexedDB、所有敏感API或实际503恢复；本人随后反馈助手origin的Local storage与Session storage均为空，IndexedDB界面显示未检测到数据库。该项按本人文本反馈记录，未获得这三处的独立截图或存储导出；未读取值/Cookies，不推断其他origin或浏览器HTTP缓存底层。仅更新文档，无新测试/发布或提交推送；F08.6仍BLOCKED，F07/F08整体IN_PROGRESS。
  - **Android私有health路径补齐（2026-10-05）**：本人保持Tailscale在线，对/api/v1/approvals、/healthz/、/docs、/openapi.json及/均Not Found回复“是的”。结合此前health正常、任务和管理Not Found的三项反馈，手机私有HTTPS八项页面观察已补齐；未取得新增五项的Network状态码，不把页面反馈升级为独立HTTP抓包。本人先反馈两项无法访问，后明确Chrome两个指定端口均ERR_CONNECTION_TIMED_OUT；Edge初次DNS_PROBE_FINISHED_NXDOMAIN，复试也为ERR_CONNECTION_TIMED_OUT。初次DNS错误不作为端口结果，保留重试历史；未修改DNS、代理或网络安全配置。此为手机浏览器在指定地址下的未成功直连观察，不独立归因于防火墙丢弃、未提供逐项最终URL截图或TCP抓包。结合宿主8000/8010仅回环、8001无监听，以及此前手机health正常，当前私有HTTPS路径与两项直接端口的人工观察已记录；私有地址不写Git。F08.6仍BLOCKED，审批/响应不明重试等及真实Push仍待验收。
- **F07 延期边界不变**：一次登录自动路由和七字段合成值试填烟测已有用户确认，未点击真实最终提交；正式真实事务 Adapter、绑定实际提交目标和完整材料/后果的受监督权威预览、正式 Gateway/Outbox 实站链路仍未验收。因暂无确有需要的事务，真实完整填报延期，不制造申请。

- [ ] **后续组合扩展规划（F07 导航基础已实现；PWA 远程确认属 F08，邮件/模型路由属阶段 10）**：用户只完成 SSO；助手接手后由模糊指令路由到目标事项、填报、生成权威预览，用户远程批准后提交。所需增量：适配器“导航动作”声明（绑定页面指纹/期望落地 origin+path，打开事项按写操作定级）、多事项/多页指纹、Agent 模糊指令路由（低置信度/R2+ 必须用户确认）、远程批准 UI/Web Push（F08 依赖）。真实站点只读采集证据：大厅为 SPA、卡片无 `<a href>`，当前契约只允许点击唯一 final submit，故自动进入事项尚未实现；CAS `service=` 开放重定向误判与 Companion `status` 缺 url/login_page 两个真实缺陷已在本轮修复。完整差距、契约增量与验收反例见 `docs/NEXT_STEPS.md` F07 章节。首版已交付：声明式导航动作（`kind="navigate"` + `transaction_id` + `navigates_to_path`）、宿主 `execute_navigation`（状态/动作 label/落地路径/事务指纹四重校验，漂移即 `SAFETY_PAUSED`）、`browser.navigate` capability 与 `ehall.open_transaction`（INTERNAL_WRITE）、`DesktopBrowserPort.activate`/Companion `/activate`/driver `activate_navigation`、按事务拆分的多 adapter（`proof.json` + `transcript.json`，首次使用全部注册）；证据：导航单元 15 passed、真实 Chromium +2、真实 Worker/Gateway +1，详见 `CONTRACTS_AND_INTERFACES.md` 第 16.9 节。

- [ ] **F09 — Windows 生产适配器（IN_PROGRESS；最小排期例外）**
  - [x] **F09.1 Windows 安全凭据后端（DONE，最小授权范围独立验收通过）**：Credential Manager、wcm1_ 句柄、平台选择及独立组合根/隐藏 CLI 接线已实现，无新增依赖/迁移。首轮28 failed/1 passed；最终定向及相邻42 passed，含两项真实Windows合成凭据跨进程及VAPID解析。全仓Ruff/Mypy（232 files）、pip check通过。
  - 以下两条是独立验收前的实现/失败历史，当前结论以上方2026-10-09验收记录为准。
  - 首轮全量9 failed/1284 passed/130 skipped/15 warnings；PostgreSQL/Worker3 failed/127 passed/2 warnings。CLI边界已修复且原断言保留；运行器Windows编码问题修正后失败路径20 passed/1 failed，邮件/ehall Worker串行6 passed。既有RPC elapsed<0.7s断言重复未过（0.703/1.641s），不弱化断言或跨范围改核心，不宣布全量全绿或F09.1 DONE；未重跑第二遍完整门禁。
  - [本轮实现报告](docs/F09_1_IMPLEMENTATION_REPORT_2026-10-08.md)列出精确命令、文件及风险。原库临时DSN未迁入安全后端，原服务未重启/换包，真实Push未配置。先核对门禁及独立复验F09.1，随后回到仍BLOCKED的F08.6；其他F09功能不启动，不提交/推送/部署。

## 每次交接必须留下

1. 本文件中的状态变更，只能把实际完成项标为 `DONE`。
2. `docs/NEXT_STEPS.md` 中对应任务的命令、测试数量、真实限制和剩余风险。
3. 目标测试、`scripts/test.ps1`、Ruff、Mypy 的原始结果摘要。
4. 新迁移、公共接口或行为变化的说明；既有迁移不得重写。


## 2026-10-13 集中交付状态（历史；当前见文首）

用户授权本批次完成全部后续开发，并提前接入真实模型；仅真实邮箱/通知/站点配置和联调延期。本地开发代码、运行手册和离线wheel已准备，本轮门禁已收尾；F09后续适配/F10运维实现待独立复验。当前唯一待办为 DELIVERY.2 修复与独立复验（BLOCKED），真实条件准备后回到F08.6；不启动新的开发设计。

新增持久运行、调度/事件去重、版本工作流、模型披露/严格提案、规则候选审核/Git回滚、PWA运行入口、本机Admin、Windows主机/进程控制、加密备份/空目标恢复和离线升级。新0012迁移未应用原库，0001–0011未改，原已验收F06三文件摘要保持。本轮没有提交、推送、部署、安装新业务扩展或真实外部动作。

用户已运行DeepSeek deepseek-flash的PUBLIC单次试调用，ok=true/104token；真实任务材料仍须单独披露许可。F07/F08整体IN_PROGRESS，F08.6 BLOCKED，F06.P2/最小F09.1 DONE保持不变。下方历史“其他F09未授权”等说明已由本次授权取代；历史测试数量不作本轮结果。

本轮精确命令、改动文件、门禁结果、未完成项和风险见 [集中开发报告](docs/DEVELOPMENT_DELIVERY_2026-10-13.md)；实际运行方式见 [本地运行手册](docs/RUNBOOK_LOCAL_BETA.md)。


### DELIVERY.2 — 集中开发本地独立复验（DONE，本地范围）

2026-10-20用户独立复验通过，本地验收阻断关闭；证据及限制见文首。本轮未重复全量或PostgreSQL/Worker。后续回到F08.6真实条件验收，不据此关闭F07/F08整体或宣称Beta已发布。

以下为2026-10-13实施时的历史证据：

2026-10-13 实施收尾：全量本地1373 passed/132 skipped/7 warnings，Ruff与Mypy263通过；临时PostgreSQL/Worker完整运行128 passed/4 failed（迁移枚举未含0012），补齐精确版本和checksum后受影响集合7 passed。没有再跑完整PostgreSQL门禁，不称全部门禁一次全绿。全部源/SDK/扩展/脚本Mypy269、Ruff、三份JS语法、pip check、diff check通过；最终wheel265条目、10web资源、12迁移逐字节核验，无业务扩展源码。

开发包实现就绪待独立复验。原库未迁移/原服务未重启，未提交/推送/部署；测试容器删除路径已执行但Docker收尾复查超时、剩余数未独立确认；pytest结束。真实配置与验收延期，F08.6继续BLOCKED、F07/F08整体IN_PROGRESS；原已验收F06.P2/F09.1 DONE不变。精确命令、日志、文件清单、历史失败与风险见集中报告。独立本地复验后，才根据用户安排进入API配置/真实环境验收，不提前宣布整体DONE。
