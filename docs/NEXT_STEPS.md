# 实现接力清单

## 2026-10-20 当前结论：DELIVERY.2 本地验收 DONE；F08.6 BLOCKED

用户已独立复验通过：相关160 passed，原两项连续串行5轮/每轮2 passed，真实总deadline到期回收及清理失效反例通过，Ruff/Mypy/差异及118项摘要匹配，活动测试运行器0。用户核对实施者完整日志1405 passed/135 skipped/6 warnings；该数字不是用户本轮重跑。本轮只记录验收结论和项目现状，不新增运行测试，不操作原库/原服务，不提交/推送/部署。**DELIVERY.2 的本地阻断关闭并标DONE**；**F08.6仍BLOCKED，F07/F08整体IN_PROGRESS**，F06.P2/最小F09.1 DONE。下方BLOCKED/待复验段落为当时历史。

当前项目是本地优先的单用户助手：手机/电脑PWA只与助手通信，助手统一连接模型、知识资料、邮件和桌面浏览器。持久任务执行/工作流、模型披露、审批/Outbox、Windows主机、加密备份恢复/升级已有本地实现；DeepSeek仅有公开连接自检证据，邮件代写代发和完整业务流程仍需真实验收。新批次未部署到原手机入口；开发18100模板为内存，正式持久使用需PostgreSQL、独立Worker、凭据句柄及新迁移，不能把仓库新版当作线上已运行。

当前后续编号为 **F08.6（BLOCKED）**：核对实际运行版本、真实手机丢回执重试、正式单次审批及敏感缓存、独立Cloudflare中断、无Google环境ntfy投递/撤销/锁屏/点击。之后补真实模型任务和受控邮件“资料→草稿→手机核对→精确审批→发送→归档”，以及冷启动/长期运行/生产备份恢复/升级演练。F07正式真实事务Adapter、完整目标/材料/后果权威绑定、Gateway/Outbox实站链路仍延期；没有确有需要事务时不制造申请或最终提交。详情见[本地运行手册](RUNBOOK_LOCAL_BETA.md)和[最新验收记录](F07_SESSION_CREATION_TEST_TIMING_2026-10-20.md)。

## 2026-10-20 DELIVERY.2 测试时序局部修复（BLOCKED，待独立复验）

用户独立确认资源回收修复及额外10项反例通过，但统一150ms测试预算导致正常根撤销/主动取消提前超时，独立完整本地1402 passed/135 skipped/2 failed，故整体验收未通过。本轮只修改 `tests/integration/test_companion_failed_creation_f07.py` 与交接文档：正常请求5秒、故意创建响应超时只在driver进入后注入、创建任务提前结束立即取异常、finally收尾；正常TTL与唯一idle TTL用例隔离，全部原回收断言保留。先红 **3 failed**，单文件 **8 passed**，原两项连续串行 **5轮各2 passed**，相关合并 **160 passed、2 warnings**；本轮完整本地 **1405 passed、135 skipped、6 warnings**（21:46），Ruff/Mypy263通过、exit0，修复文件8项在全量中全部通过。活动测试运行器0、测试标签容器0，查询成功。Ruff/Mypy测试1文件及源码269文件、JS/依赖/差异检查通过，118项材料仅该测试一项摘要改变。精确命令、日志和最终门禁见[本轮报告](F07_SESSION_CREATION_TEST_TIMING_2026-10-20.md)。DELIVERY.2及F08.6保持 **BLOCKED**，F07/F08 **IN_PROGRESS**；生产代码、30秒超时、旧迁移、原库与原服务不改。本轮不重新运行PostgreSQL/Worker，因为只改测试且未涉及数据库；此前135 passed属于上一轮实施证据，不计本轮。未提交、推送或部署。下方为历史实现/门禁记录。

## 2026-10-19 DELIVERY.2 相邻 F07.P2 创建失败清理（BLOCKED，待独立复验）

用户已独立确认原三项 DELIVERY.2 修复通过；新增阻断为创建超时后迟到会话无法撤销、TTL 无访问时不回收。本轮仅修复此清理缺口：已知 ID 的宿主根撤销、先撤销后启动竞态、独立 TTL 及抗重复取消收尾；不增加命令超时。先写失败反例后实现，合并定向 **159 passed、2 warnings**；真实本机浏览器取消回收 **1 passed**。本轮串行完整本地 **1404 passed、135 skipped、8 warnings**，Ruff/Mypy263通过；随后完整临时 PostgreSQL/真实 Worker **135 passed**，两门禁exit0。另独立Ruff/Mypy269、三份JS、依赖/差异检查及wheel265条目/10web/12迁移/118项摘要通过；活动测试运行器0、测试标签容器0且查询成功，见[本轮报告](F07_SESSION_CREATION_CLEANUP_2026-10-19.md)。DELIVERY.2 保持 **BLOCKED** 待独立复验；F08.6 真实缺口仍 **BLOCKED**，F07/F08 **IN_PROGRESS**，F06.P2/最小 F09.1 **DONE**。不操作原库/服务，不调用真实模型、邮箱或 ehall，不提交、推送或部署。下节为原三项修复的历史门禁，不能替代本轮结果。

## 2026-10-19 DELIVERY.2 修复与复验（BLOCKED）

独立验收确认 1 项 P1、2 项 P2：实际 EXTERNAL_WRITE 未决动作被普通继续绕过、已绑定工作流切换后错误成功收尾、恢复 EXECUTING 审批时丢失 UNKNOWN 与审核来源。本轮仅修复上述缺陷并补本地完整门禁；未决结果不自动重放，旧授权不可重用。DELIVERY.2 保持 **BLOCKED**，等待修复后的独立复验；F08.6 继续 **BLOCKED**，F07/F08 整体 **IN_PROGRESS**，F06.P2/最小 F09.1 **DONE**。原库、运行服务、真实模型、邮件及站点不操作，不提交/推送/部署。本轮先失败后修复：相关回归 **38 passed、2 warnings**；完整临时 PostgreSQL／真实 Worker **135 passed**。完整本地 **1383 passed、135 skipped、1 failed、6 warnings**，失败为 F07 创建 Companion 会话超时；隔离复跑 **1 passed**，不等于全量全绿或根因已解决。Ruff、Mypy269、三份 JS、依赖与差异检查通过；离线 wheel265条目/10web/12迁移逐字节核验，当前112项摘要。收尾活动 pytest/测试运行器0、测试标签容器0，查询成功；原库/服务未动。精确命令、日志、文件及剩余风险见 [本轮修复报告](DELIVERY_2_REMEDIATION_2026-10-19.md)。历史数字不算本轮运行。


2026-10-13 用户明确调整排期：授权一次性推进全部后续项目开发，范围不限 F08.6；外部邮件/通知/站点 API 配置、真实环境联调与验收留待后续；随后用户另行授权本批次提前接入真实模型，并已完成 DeepSeek 单次 PUBLIC 自检，本地自动化验证继续执行。本授权取代此前唯一 NEXT/不得推进其他 F09 的排期限制；按依赖顺序完成 Windows 运行适配、持久任务执行与调度、组合工作流/可审核规则、管理与手机入口、备份恢复及交付手册。审批、凭据、可信扩展确认、UNKNOWN 不自动重放及 health-only 边界仍适用。F07/F08 真实验收状态不冒改 DONE；开发实现和独立验收分别记录。当前交付包 **BLOCKED（独立验收发现阻断，修复后待复验）**，见 [集中开发报告](DEVELOPMENT_DELIVERY_2026-10-13.md)。

2026-10-10 F08.6 ntfy 最小接线（本轮）：本人已明确授权无 Google 通知通道的主机接线和本地测试，要求停在手机安装或外网入口配置之前。新增默认关闭的本机 ntfy 适配器、秘密句柄配置、两种存储组合根及关闭资源接线；消息只含任务 ID/风险/提醒类型，点击只进入助手，结果不明不自动重试。先失败后实现，定向57 passed、2 warnings，全项目Ruff/Mypy234通过，依赖/JS/差异检查通过；本轮全量1312 passed、130 skipped、2 failed（F07 Companion测试启动超时与F06 TLS线程状态断言），隔离复跑1 passed、1 failed，F06断言仍失败。完整PostgreSQL/Worker门禁未继续，原因及精确命令见报告第5节；不引用旧轮数字代替，也不称全量全绿。真实 ntfy/手机客户端尚未接入，原服务未重启或部署；F08.6仍 **BLOCKED**，F07/F08整体 **IN_PROGRESS**，F06.P2/最小F09.1 **DONE**，其他F09不启动。本轮实现详情、精确命令和剩余证据见 [主机接线报告](F08_6_NTFY_LOCAL_REPORT_2026-10-10.md)。下方诊断轮未改源码的说明属于历史范围。

2026-10-10 F08.6 浏览器注册定位（此前诊断轮，后台数据为10-09只读快照）：用户恢复推进；手机点击开启通知后反馈“通知权限未授予”。现有页面在requestPermission非granted时返回，本次操作尚未进入浏览器订阅和订阅POST；不据此判断是denied还是default，也不归因于GMS或服务器。本人确认Chrome应用通知已允许，但助手单站显示“不允许”；随后页面提示“订阅结果未确认，可手动重试同一浏览器订阅”。2026-10-09 23:42（北京时间）只读复核：VAPID宿主可用、原库read_only=on，订阅0/订阅命令0，public无JWT401、health200、Tunnel ready200。该提示覆盖多个失败阶段，尚未定位浏览器subscribe或API失败；本人保留页面重试一次后，提示短暂“正在订阅”又转为“订阅结果未确认”；未记为成功。已请求一次有Network观察的定位，仅读POST是否出现及状态码，不展开正文/Cookie；定位前Chrome显示USB Pending authentication。实际用原官方ADB devices -l只投影数量/状态，exit0、1台device；未输出序列号或读取手机内容。等待本人让Chrome重读设备，保留订阅未确认的原页面；2026-10-10本人Network反馈没有POST；全局异常断点先停在草稿不存在的读取错误，换通知专用catch断点后得到AbortError: Registration failed - push service error。实际ADB包路径只读查询以android和Chrome为对照均exit0/present=true，com.google.android.gms为exit1/present=false且无输出；手机缺少Chrome Android注册所需GMS组件，是明确环境依赖缺口，与该注册错误吻合。通用SERVICE_ERROR本身不证明唯一底层成因；未读取手机用户内容/安装软件/修改权限或发出通知。已停止同配置重试；本人随后明确永久不使用 Google Play，不再以更换为具备 Google 服务的手机为前置条件；本人已明确允许 F08.6 最小 ntfy 接线和本地测试，并要求停在手机安装或外网入口配置之前；F08.6 仍 BLOCKED，其他 F09 不启动；请求移除通知断点并F8恢复原页面。开始时 public/health/Tunnel 均未监听；第一次恢复 public exit3，类型 ConnectionRefusedError，原库连接也被拒绝，Docker Linux engine pipe 不存在。执行 docker desktop start --detach 得到 already running，随后 status为running、原lab1-postgres-1为healthy；按原授权恢复可跟踪的三个运行会话。当前public8000无JWT的Push配置401、health8010/healthz 200、Tunnel ready200、匿名公网/ui/302至既有Access团队；原库只读订阅0、自检任务匹配1，VAPID句柄可用、发信/提交关闭。Tailscale现Running且自身在线、在线peer0，已保存Serve仍只代理health8010、AllowFunnel=false，未修改其配置。本轮无pytest/全量/PostgreSQL测试，前一轮13 passed不是本轮重跑结果；没有发送通知、改源码、提交或推送。手机订阅未确认，F08.6继续BLOCKED、F07/F08整体IN_PROGRESS，其他F09不启动。

2026-10-09 F08.6 本机准备续接（历史证据）：按用户“开始下一步”回到 F08.6；随后用户要求停在需要其配合之前。本轮用真实 Windows Credential Manager 生成并持久化 VAPID 私钥，忽略目录仅新增公钥/句柄/精确 FCM origin，原库临时 DSN 保留。生产组合根在另一宿主进程成功解析同一密钥；现恢复 public 127.0.0.1:8000、独立 health 127.0.0.1:8010 与既有 Tunnel，Admin 未启动，发信/ehall 提交关闭，未投递通知。实际定向 13 passed、2 warnings；本轮未重跑全量或 PostgreSQL/Worker 套件，产品源码未新增修改，恢复基线使用此前已独立通过的同一实现。手机订阅与真实 Push 尚未开始；Tailscale 当前 NoState、私有入口本轮未恢复。F08.6 保持 BLOCKED、F07/F08 整体 IN_PROGRESS，F06.P2 与最小 F09.1 DONE，其他 F09 不启动。详见 docs/F08_6_PUSH_PREPARATION_2026-10-09.md；本轮未提交或推送。

本清单把详细设计拆成较小、可独立验收的工作包。状态只允许 `TODO / NEXT / IN_PROGRESS / DONE / BLOCKED`。后续模型一次领取一个任务，完成后在本文件写入测试命令和结果。

当前状态（2026-10-09独立验收）：**F06.P2 DONE；最小F09.1 DONE**。唯一后续验收项为**F08.6（BLOCKED）**，F07/F08整体保持IN_PROGRESS，不推进其他F09。用户独立完成邮件89 passed及额外10项、全量1301 passed/130 skipped/9 warnings、PostgreSQL/真实Worker130 passed/1 warning，静态/依赖/8项摘要/wheel通过，25个记录测试进程退出；本轮实施者仅核对日志摘要和记录状态，未重跑测试。最新证据见下方及[修复报告](F06_TLS_CANCELLATION_REPAIR_2026-10-09.md)第10节。

截至此前F08.6环境验收的历史总览：简要状态见 `../TODO.md`，冻结的接口、事务与验收契约见 `CONTRACTS_AND_INTERFACES.md`。F01–F06 已通过最终复验并标记 `DONE`；F07 的一次登录自动路由与七字段试填烟测已通过，但正式真实事务 Adapter、实际目标及完整材料/后果绑定的受监督权威预览、正式 Gateway/Outbox 实站链路仍未验收，真实完整填报因暂无确有需要的事务延期，保持 `IN_PROGRESS`。2026-09-30 用户明确调整排期：允许先做不依赖这些 F07 缺口的 F08 子任务；F08.1–F08.5 已分别通过约定范围的独立验收。当时F08.6是唯一子任务；发布门禁及明确授权已取得，原助手/health 与 Access 保护的 Tunnel 已临时运行，无认证黑盒通过。Android首次登录/SSE、私有health三项、Chrome标识桌面入口的无地址栏启动/空列表/事件连接及Wi-Fi切至移动数据后恢复均获本人反馈；断网提示、恢复联网以及后台或锁屏约一分钟后恢复也获本人确认；手机自检任务/消息刷新恢复、电脑页面新增任务/消息自动同步获本人反馈，原库只读聚合核实了先前自检任务/首条消息各一条；任务/消息使用后的v12四路径CacheStorage及任务GET的200/no-store已有手机证据；随后经本人对精确材料授权，example.echo@0.1.0已安装启用，公共页面出现example.echo_settings；手机来源由未确定变为用户输入，初次草稿版本2及刷新恢复已有本人反馈；后续真实手机截图确认保留本机未保存值并显示服务器3，显式保留后保存版本4及刷新恢复获本人确认和原库只读核对。手机草稿GET的200/no-store已有截图；更新后截图确认唯一缓存为v13、旧v12节点消失，仍仅四静态路径。手机PUT的409/412、其余缓存与审批仍待核验。其余缓存及跨设备边界与真实Push仍待验收，状态为 `BLOCKED`；F08 整体仍为 `IN_PROGRESS`，不得据此把 F07 标为 `DONE`。阶段 10 的 LLM 自动路由不在本次范围。

## 基架状态

| 编号 | 状态 | 内容 | 验收证据 |
|---|---|---|---|
| F00 | DONE | 核心、API、PWA 壳、扩展 SDK、内存适配器和测试基架 | `scripts/test.ps1`：42 passed；Ruff 与 Mypy 通过；`pip check` 无冲突；wheel 含核心/SDK/PWA/配置/迁移；Uvicorn/API/CLI/Worker 子进程烟测通过 |
| F01 | DONE | 迁移运行器 + 真实 PostgreSQL 仓储/队列/outbox/审计/扩展状态适配器 + composition root 与启动生命周期 | 最终复验：`./scripts/test-postgres.ps1` 24 passed；`./scripts/test.ps1` 44 passed、24 skipped；Ruff/Mypy/`pip check`/`git diff --check` 通过；旧库 checkpoint 续写、Extension 完整回填、PostgreSQL Gateway 四类终态和内存审批单次消费均独立验证通过 |
| F02 | DONE | Extension Supervisor、确认屏障、每版本 venv/Worker、生命周期、Admin API/CLI、操作持久化与恢复 | 六轮独立审计通过；`./scripts/test.ps1` 153 passed、28 skipped；`./scripts/test-postgres.ps1` 28 passed；F02 相关集合 110 collected；Ruff/Mypy/`pip check`/`git diff --check` 与 wheel 内容核验通过 |
| F03 | DONE | Cloudflare Access JWT 验证、JWKS 缓存/轮换、public/Admin/health 边界回归 | 三轮独立验收通过；前两轮 6+2 项缺陷均修复并补反例；F03 目标集合 124 passed；`./scripts/test.ps1` 269 passed、28 skipped；PostgreSQL 28 passed；Ruff/Mypy/`pip check`/`git diff --check`、手工轮换复现与 wheel 内容核验通过 |
| F04 | DONE | 真实本地/远程模型适配器、canonical 披露许可（持久 + 内存）、迁移 0005、`PA_MODEL_*` 配置、public 披露接口 | 两轮自我审查 16 项与第三至七轮验收修复后目标集合 161 passed；`./scripts/test.ps1` 409 passed、37 skipped；`./scripts/test-postgres.ps1` 37 passed（F01 24 + F02 4 + F04 9）；Ruff/Mypy（150 files）/`pip check`/`git diff --check` 通过；wheel 含模型模块、许可模块与 0005 迁移；独立验收通过 |
| F05 | DONE | 个人知识扩展：授权目录扫描、Markdown/TXT/PDF 抽取、版本化索引、PostgreSQL FTS + pgvector 混合检索、可验证引用、删除传播；通用宿主数据能力与扩展配置通道 | 完整独立审计和修复后自审计通过；全量 597 passed、81 skipped；PostgreSQL/真实 Worker 81 passed；其余证据见下方 F05 章节 |
| F06 | DONE | smail 扩展：IMAP 只读同步、版本化草稿、R2 SMTP 发送与对账；宿主账户注册表、通用邮件端口与传输台账、生产 Gateway 路由 | 六轮独立审计及补充复验通过；`./scripts/test.ps1` 742 passed、103 skipped，Ruff/Mypy 192 files 通过；`./scripts/test-postgres.ps1` 103 passed（真实 PostgreSQL 与 Worker）；`pip check`/`git diff --check`/wheel 内容核验通过。真实 smail 账号 E2E 未执行 |
| F07 | IN_PROGRESS | ehall 扩展：用户亲自完成 SSO/验证码/扫码，有头浏览器填写一个低风险事务至提交前权威预览 | 2026-09-30 旧实站直接交互烟测完成一次登录、自动路由与七项试填，未点“提交”。第二轮独立复审发现首轮 P1 修复仍可点击新出现的无关菜单；当前已补本地 Chromium 反例并修复，交互层/单元 **40/40** 通过，改动路径 Ruff/Mypy 通过。`./scripts/test.ps1` 应用户要求在约 16% 处中断，交由审计 agent 复验；首轮 **1185 passed/125 skipped** 不代表当前快照全绿。修复后未重跑真实站点；正式 Adapter、实际提交目标、材料/后果及受监督 Gateway/Outbox 路径未验收，F07 不得标记 DONE。 |
| F08 | IN_PROGRESS | PWA 与 Web Push；F08.1–F08.5 在各自约定范围内验收通过；F08.6 BLOCKED，最小F09.1及F06.P2已DONE，回到真机/真实Push等验收；此前授权部署已接通，手机首次连通/health三项、Chrome标识桌面入口无地址栏启动及Wi-Fi→移动数据恢复已有本人反馈；断网提示/恢复联网、后台或锁屏约一分钟恢复、手机任务/消息刷新恢复与电脑自动同步获本人反馈，先前自检记录获原库只读核对；任务场景CacheStorage四路径及任务GET的200/no-store已获手机证据；Schema草稿/审批、其余缓存/跨设备边界及真实Push仍BLOCKED | F08.5 独立定向45 passed。F08.6 发布前本工作区全量1248 passed/130 skipped，PostgreSQL/Worker130 passed；授权启动后11份迁移一致、无认证本机/边缘及同节点私有HTTPS黑盒通过。24项GET复查与手机人工反馈单列，真实Push未验收。 |

F00 已冻结的公共边界见 `docs/IMPLEMENTATION_MAP.md`。不要重写基架；后续任务应替换端口适配器或新增业务扩展。当前任务进入 `QUEUED` 后不会被假 Worker 消费，这是有意的 fail-closed 行为。

## 建议实现顺序

### F01 — PostgreSQL 仓储与迁移（DONE）

范围：实现 `infrastructure/database` 中的任务、运行、审批、作业、审计和扩展状态仓储；接入 `migrations/0001_core.sql`。Schema、队列端口、lease keepalive 和开发用内存参考实现已经存在。

能力边界：只替换端口适配器，不改领域状态机或 API Schema。

验收：在临时 PostgreSQL 上迁移、重启、并发 claim、lease 过期回收、幂等冲突及审计追加测试通过；生产启动不再报 `POSTGRES_ADAPTER_NOT_IMPLEMENTED`。

完成与验收证据（2026-09-16）：

- 新增迁移 `0002_f01_persistence.sql`，补齐 task 命令幂等、task_messages、task_events、agent_runs 全字段、run_checkpoints.run_version、run_observations、approvals.version 及批准/完成字段、jobs.metadata、side_effect_intents、extensions 的 manifest/artifact/install/tombstone。
- 迁移运行器：数字版本顺序、每迁移 SHA-256 checksum、`pg_advisory_lock(743192235100001)` 串行化、失败不登记、checksum 漂移拒绝启动。
- 真实适配器：`PostgresTaskRepository`/`PostgresEventStream`/`PostgresJobQueue`/`PostgresApprovalRepository`/`PostgresRunRepository`/`PostgresCheckpointStore`/`PostgresObservationStore`/`PostgresAuditWriter`/`PostgresSideEffectOutbox`/`PostgresLifecycleStore`，由 `build_postgres_adapters` 组合并接入 `Container` 与 public/admin lifespan；导入期不连接数据库。
- 首轮验收缺陷修复（全部已补反例测试）：
  1. Outbox 消费强制校验 `expires_at`：过期审批原子置 `EXPIRED` 后抛 `ApprovalExpiredError`，绝不进入 `EXECUTING`。
  2. `action_fingerprint` 改为必填；缺失或漂移时原子取消审批（`CANCELLED`）且不插入 Intent，分别抛 `ValidationError`/`ApprovalBindingMismatchError`。
  3. 新增 `SideEffectOutboxPort.finalize`：审批与 Intent 的终态在同一事务提交，消除双写窗口；`ToolGateway` 的 SUCCEEDED/FAILED/UNKNOWN 全部经此边界。
  4. 迁移 `0002` 从 `tasks.objective` 回填 `agent_runs.objective`、从 `started_at` 回填 `active_started_at` 并加 `NOT NULL`；从 `extension_versions` 回填 extension manifest；新增带数据 0001 快照升级测试。
  5. `AgentEngine` 新增可选 `unit_of_work`：Run CAS + Checkpoint（及 Observation）在同一事务提交，并新增注入 checkpoint 失败验证回滚的测试。
  6. 修正文档状态冲突；最终复验通过后，F01 已统一标记为 DONE，F02 提升为唯一 NEXT。
- 第二轮验收缺陷修复：
  7. 迁移 `0002` 在创建 `run_checkpoint_sequence` 后用 `setval` 推进到既有 `max(sequence)`；升级测试实际为旧 Run 追加 checkpoint 并断言序列不冲突。
  8. Extension 回填补齐 `extension_versions.install_path`，`manifest_version` 从 manifest JSON 的 `manifest_version` 读取，而不再误用扩展包版本；升级测试断言安装路径、包版本与 manifest 格式版本同时恢复。
  9. 内存 Outbox 绑定 `ApprovalService`，强制校验审批状态/过期/完整指纹并真正消费审批；同一审批无法用不同幂等键再次执行。新增网关级反例测试（重复使用与漂移）。
- 命令证据：`./scripts/test-postgres.ps1` → 24 passed（真实 PostgreSQL 17.11 + pgvector，临时库自动创建/删除，仅操作 `*_test` 目标的独立临时数据库）；`./scripts/test.ps1` → 44 passed + 24 skipped，`ruff`/`mypy`/`pip check` 全通过；`PA_ENVIRONMENT=production` 下 FastAPI lifespan 成功迁移并启动。
- 最终独立复验：旧库 checkpoint 最大序号为 7 时升级后正确追加为 8；Extension 的 manifest 格式版本、安装路径和 artifact hash 正确回填；PostgreSQL Gateway 的 SUCCESS/FAILED/UNKNOWN/Cancelled 路径保持 Approval/Intent 终态一致；实际内存 Container 中同一审批第二次执行被拒绝且 executor 仅调用一次。
- 公共契约变化：`SideEffectIntent.action_fingerprint` 由可选改为必填；新增 `SideEffectOutboxPort.finalize`；新增 `core.unit_of_work.UnitOfWorkPort`；`ToolGateway`/`AgentEngine` 增加可选 `outbox`/`unit_of_work` 参数。已同步 `CONTRACTS_AND_INTERFACES.md`。
- 已知边界：外部动作完成到 `finalize` 之间崩溃会留下 `EXECUTING/PREPARED` 未决态，必须由后续 reconciliation 处理且不得自动重试；真实 Extension Supervisor/Worker 与外部集成不属于 F01。

失败红线：一次作业被两个有效 lease 同时持有；审批消费与副作用意图不能在同一事务落库；重启丢状态。

禁区：SQLite 冒充生产数据库；在仓储中写业务扩展判断；存储凭据明文。

### F02 — Extension Supervisor（DONE）

范围：把现有 Manifest/确认屏障/生命周期/Registry/JSON-RPC 契约接到真实暂存目录、venv 和进程适配器；完成排空、升级和回滚。不要重新设计已有协议。

完成与验收证据（2026-09-16，六轮独立审计通过）：

- 新增 `core/extensions/operations.py`（`ExtensionOperation`/`OperationState`/`ExtensionOperationStore`/`DIAGNOSTIC_CODES`）与 `core/extensions/supervision.py`（`ExtensionSupervisorService`：inspect、install/upgrade/enable/disable/rollback/uninstall、`wait_operation`、`recover`、`invoke_tool`、`stop_all`）。
- 新增 `infrastructure/extensions/`：`LocalArtifactStager`（本地目录/zip/whl 复制解包，拒绝 `scheme://`、`git+`、路径穿越、符号链接与超限解包）、`VenvArtifactInstaller`（每扩展版本 `<root>/<id>/<version>/{payload,venv}`，`python -m venv --without-pip` 离线装配 SDK + payload `.pth`，lockfile 有内容时用 venv pip 安装已钉版本，失败回滚自身目录）、`ProcessRuntimeSupervisor`/`ProcessContractVerifier`（真实子进程、handshake/health/slots 契约检查、超时有界 drain、崩溃隔离）、`CompatibleVersionOperator`（保留版本与 `state_schema_version` 兼容回滚）、数据保留适配器（`ext_*` namespace 只读；purge 明确不可用）。
- 新增持久化适配器：`PostgresExtensionOperationStore`（复用 0001 `extension_operations` 表，无新迁移）、`PostgresVersionCatalog`（读 `extension_versions`）、`InMemory*` 对应实现；`PostgresLifecycleStore`/`InMemoryLifecycleStore` 新增 `all()`，`manifest_to_dict`/`manifest_from_dict` 公开（保留旧私有别名供 F01 测试）。
- 确认屏障加固：`InstallCoordinator` 新增 `prepare_auto`/`prepare_upgrade`/`install_candidate`（升级候选不覆盖活动记录）/`validate`/`preview_for`/`discard_candidate`/`discard_staged_record`；安装前重新计算 staged hash，确认后变化即 `ConfirmationRequiredError`；验证失败会删除已安装版本目录并清理暂存，绝不会留下可被 Registry 采用的代码。
- 生命周期：`LifecycleManager.recover`、`enable` 接受 `QUARANTINED`（修复路径）、健康检查失败抛 `ExtensionOperationError("HEALTHCHECK_FAILED")`；`QUARANTINED` 由崩溃/健康失败进入且 Registry 立即撤销。
- RPC 加固：`JsonRpcProcessClient` 对畸形帧、超限行、id 不匹配统一终止进程并抛 `RpcCallError(-32700/-32092/-32093)`，超时 `RpcTimeoutError` 后进程停止且不静默重试。
- Admin API（仅 8001）：`inspect`（纯数据预览）、`install`（需精确绑定 `plan_id`+`nonce`+`preview_hash`+`accepted_warning`）、`enable|disable|rollback|uninstall`（返回 operation id，202）、`upgrade`（需同一确认）、`purge-data` 501、`GET /admin/v1/extension-operations/{id}`；lifespan 启动 `recover()`、关闭 `stop_all()`。公共 API 无任何管理路由。
- CLI：`inspect` / `install <source>` / `upgrade <id> <source>` 先展示预览并取得显式确认，再调用 Admin API 并轮询 operation；`enable|disable|rollback|uninstall` 同样轮询；`purge` 维持明确未实现。CLI 不导入基础设施或数据库（契约测试 `tests/contract/test_extension_independence.py` 拦截）。
- 第二轮独立复验修复（11 项 P1 + 4 项 P2，全部补反例测试）：Worker 最小环境白名单（不继承数据库 URL/Token/Cookie）；暂存/哈希/安装统一忽略集合（确认后篡改 `.venv` 不影响哈希也不进入 payload）；lockfile 要求精确版本 + `--hash=sha256` 且 pip 使用 `--require-hashes`；升级执行时重查活动版本基线（`PLAN_BASELINE_CHANGED`）；升级禁用旧版后的全部步骤统一补偿（候选持久化失败会恢复并重新启用旧版）；安装最终持久化失败删除未登记版本目录；`reject` 按计划模式处理（upgrade 不覆盖活动记录）、REJECTED 可重新安装、新预览作废旧计划、CLI 拒绝会清理计划；drain 全程受 deadline 限制并校验 `DrainReport`（`DRAIN_TIMEOUT`）；契约验证比较完整工具描述符/风险/Schema 与全部槽位能力 ID；恢复失败先停止 Worker 再隔离；`stdin.write/drain` 管道错误、读 EOF 与取消统一类型化；`data_namespace` 单射编码消除碰撞；operation store 强制诊断码允许列表；公共扩展列表改读持久 lifecycle store；`RUNNING` 转换失败原子落 `FAILED`。
- 第三轮独立复验修复（A01–A08，全部补反例测试）：`shield_cleanup`/`terminate_process` 统一“先清理后重抛取消”；drain 使用一次性单调 deadline 覆盖等锁+写入+读取并严格校验 `DrainReport`；写入期取消破坏 RPC 流；安装命令取消回收子进程、安装取消清理版本目录/staging/plan 并收敛 REJECTED；升级全链路（含禁用旧版与保存 UPGRADING）纳入同一补偿边界；install/upgrade 重放先查持久 `request_scope`，跨重启仍返回原 operation 且不与内存 plan 耦合；`interrupt_running` 也强制诊断码白名单；文档同步 0003/0004 与取消语义。
- 第四轮独立复验修复（3 项 P1 + 文档一致性）：`rollback` 先解析并校验保留候选再禁用当前版本，失败时通过 `_compensate_failed_rollback` 保持当前版本 ENABLED 且可调用（不兼容/无候选反例）；`run_blocking` 在取消时等待后台复制线程结束再传播取消，版本目录删除后不可能被线程重建；`call()` 与 drain 一样使用单一 monotonic deadline 覆盖等锁+写入+读取；契约验证 Worker 的 finally 改为 shield 关闭；新增 `tests/contract/test_f02_contract_consistency.py` 锁定文档与实现一致性。
- 第五轮独立复验修复（2 项 P1）：`LifecycleManager.rollback` 把候选启动、健康检查、Registry 发布与 ENABLED 持久化全部纳入同一补偿边界，Supervisor 删除二段式 `enable()`；`_compensate_failed_rollback` 撤销候选发布、停止候选 Worker 并重新启用原版本；反例覆盖候选启动失败、健康失败、ENABLED 保存失败与取消四类，均断言原版本仍为 Registry owner 且可调用。`run_blocking` 保存首次取消并消费线程终态，线程在取消后抛异常不再覆盖取消信号；反例覆盖“取消后线程抛异常”与“等待期间再次取消”。
- 第六轮验收材料修复（2 项 P2，未改核心实现）：取消反例不再调用 `service.stop_all()`，而是直接取消单个 `manager.rollback()` 任务并断言 Runtime 中候选 Worker 已停止、原版本 Worker 在运行；测试 `FakeRuntime` 维护 `active` 活动 Worker 映射，`stop`/`stop_all` 真正清空，未启动时 `invoke_tool` 抛 `RpcCallError(-32090)`；另增 `stop_all` 关闭全部 Worker 后 `recover()` 按持久 ENABLED 记录重启的独立测试。`NEXT_STEPS`/`TODO` 证据行更新为第六轮实际数字。
- 新增迁移：`0003_f02_operations.sql`（幂等键/命令指纹 + 部分唯一索引，SHA-256 `28cbda227918bfcd80366208b59713eb0dbb0b0cbdaa582cb5e55a91ce2e1e03`）与 `0004_f02_operation_request_scope.sql`（request_scope + `(request_scope, idempotency_key)` 部分唯一索引，SHA-256 `6b6bb9f5cea83b443c9ca345f7a9d134f6ebca69969f01e23557ecff706af6fc`）。F01 的两个迁移测试断言更新为四迁移集合。
- 命令证据：`./scripts/test-postgres.ps1` → F01+F02 共 28 passed；`./scripts/test.ps1` → 153 passed、28 skipped；F02 相关集合 110 collected（`tests/unit/test_extension_staging.py` 12、`test_extension_install_barrier_f02.py` 11、`test_extension_rpc_robustness.py` 12、`test_extension_cancel_cleanup.py` 11、`test_extension_supervision_service.py` 34、`test_extension_operations_model.py` 8、`test_cli_extension_flow.py` 2、`tests/api/test_admin_extensions.py` 7、`tests/integration/test_extension_supervisor_real.py` 5、`tests/integration/test_postgres_f02.py` 4、`tests/contract/test_f02_contract_consistency.py` 4；无数据库时 106 passed + 4 skipped）；另有 `tests/contract/test_extension_independence.py` 新增 CLI/数据库边界检查；真实集成用临时 `python -m venv` + 真实 `example_echo` Worker 子进程，并以 import 日志进程 ID 证明确认前 0 次执行、超时后不重试，覆盖契约漂移（risk/schedule）、并发启动不泄漏 Worker；取消测试覆盖 venv/pip 子进程回收、契约验证、staging 删除与最终保存；测试后无残留 python/worker 子进程；Ruff、Mypy、`pip check`、`git diff --check` 通过；wheel 构建包含 Supervisor 模块、SDK、PWA、config 与四份迁移。

能力边界：进程/依赖隔离，不承诺防御同用户恶意代码。

有意未实现（不得当作已完成）：永久 purge（501）、扩展自有 `ext_*` Schema migration 执行、扩展配置注入、Worker 崩溃后的自动重启退避（当前失败即 QUARANTINED，仅在宿主启动时恢复 ENABLED）、远程 URL/Git 来源（默认拒绝，只接受本地目录/zip/whl）、多 Admin 进程共享待确认计划与跨进程 `OPERATION_IN_PROGRESS` 互斥（DB 仅保证命令键唯一与单次 save 串行）、`NotificationProvider` 的启用前行为验证（无枚举 RPC，`deliver` 有副作用）。

剩余风险：宿主被强杀（非正常关闭）时 Windows 子进程可能成为孤儿，需 F09 的 Job Object/进程管理；待确认计划保存在 Admin 进程内存，重启后按 `REJECTED` 收敛；`extension_operations` 只保留单一诊断码，不含多步诊断；带 hash 锁的第三方依赖安装需要可达的包索引/缓存，离线时该扩展安装会安全失败。

失败红线复核：确认前无 build/install/import/execute（探针证据）；Registry 快照只整体发布（单元测试断言 snapshot 不出现半套）；升级失败旧版本保持 ENABLED 且可调用（单元 + 服务级注入故障）。

禁区：后台自动升级；从任意 URL 拉取未固定 revision；将 venv 称为安全沙箱。

### F03 — Cloudflare Access 边界（DONE，三轮独立验收通过）

范围：实现 Access JWT 的签名、issuer、audience、expiry 和代理头白名单。Origin/custom-header CSRF 基架已经存在；Cloudflare 模式此前故意对全部请求返回 503，现已替换为真实验证。

完成与验收证据（2026-09-17，三轮独立验收通过）：

- 新增 `core/auth/ports.py`：`AccessIdentity`、`AccessTokenVerifier` 注入协议、`AccessTokenError`/`AccessTokenRejectedError`/`AccessTokenUnavailableError`、`MAX_TOKEN_BYTES`（api 只依赖 `core.auth`）；`infrastructure/auth/contract.py` 只保留 `JwksProvider` 与 `UnknownSigningKeyError`，密码学实现位于 `infrastructure/auth/cloudflare_access.py`。
- 新增 `infrastructure/auth/cloudflare_access.py`：`CloudflareJwksProvider`（有界缓存、single-flight、未知 kid 受控刷新、超时/响应上限/HTTPS 固定地址）与 `CloudflareAccessTokenVerifier`（`RS256` 白名单、`kid` 精确命中、issuer/audience/exp/nbf/iat 校验、固定 30 秒 leeway、可注入时钟、`sub` -> actor）。
- 重写 `api/middleware/cloudflare_access.py`：只接受 `Cf-Access-Jwt-Assertion` 头与 `CF_Authorization` cookie 两种官方载体；载体冲突/重复/超限拒绝；仅用已验证 claims 建立身份；稳定错误码 401（缺失/无效）与 503（不可用）；开发模式保持 `development-owner`。
- `settings.py` 增加 `PA_CF_ACCESS_TEAM_DOMAIN`、`PA_CF_ACCESS_AUD`、`PA_PUBLIC_ORIGIN` 的规范化与启动校验：`__post_init__` 对所有构造路径写回 canonical 值再校验，非法 team domain（scheme/端口/userinfo/path/自定义域）与缺失生产配置一律拒绝启动。
- `app.py` 新增可注入 `access_verifier` 并在 composition root 构建/关闭；Admin 与 health app 未接线，未改动其路由。
- 运行时依赖新增 `PyJWT`、`cryptography`，`httpx` 提升为运行时依赖；`pyproject.toml`、`dependency.lock`、`.env.example` 同步。
- 测试：`tests/unit/test_cloudflare_access_verifier.py`（RSA 运行时生成 + in-memory JWKS transport；算法混淆、kid、签名、时间声明、缓存/轮换/并发/失败矩阵）、`tests/api/test_cloudflare_access.py`（载体冲突、伪造 email 头、401/503、JWKS 故障、CSRF 不回归、public 无 Admin 路由）、`tests/unit/test_settings.py`、`tests/api/test_boundaries.py`、`tests/contract/test_f03_contract_consistency.py`（依赖声明、无硬编码 JWT、core/domain 不导入密码学栈、文档一致性）。
- 第一轮独立验收修复（6 项，全部补反例测试）：
  1. **P1 手工 Settings 绕过域名校验**：`Settings.__post_init__` 使 from_env、直接构造与 `replace` 全部写回规范化值并校验（第二轮进一步写回 canonical 值）；`create_app`/`build_container` 再校验一次；`cloudflare_access_verifier_from_settings` 只使用 `normalize_team_domain`/`normalize_audience` 的规范化结果。反例：foreign team domain 直接构造/`replace`/`create_app` 全部拒绝，工厂对非规范 team domain 仍构造官方 HTTPS certs URL。
  2. **P2 JWKS 无总 deadline 且未禁止重定向**：`_fetch_keys` 外层 `asyncio.timeout` 覆盖整个 stream（慢速滴流在 deadline 内失败），每次请求显式 `follow_redirects=False`（注入客户端的重定向设置无效）。反例：注入 `follow_redirects=True` 客户端遇 302 只请求官方主机一次并 503；慢速分块在 0.1s 预算内失败。
  3. **P2 取消污染节流状态**：刷新取消（`BaseException`）时恢复 `_last_attempt_at`/`_last_unknown_refresh_at` 后重抛，等待者立即接管刷新。反例：首个刷新持锁时取消，等待中的第二个请求完成取钥并成功验签（2 次请求）。
  4. **P2 重复原始 Cookie 头未检测**：`_cookie_values` 遍历 `getlist("Cookie")` 聚合全部原始首部。反例：两个 `Cookie` 头各带一个 `CF_Authorization` 拒绝；一个无关 Cookie 头加一个令牌头接受。
  5. **P2 API 反向依赖 infrastructure**：`AccessIdentity`/`AccessTokenVerifier`/通用异常与 `MAX_TOKEN_BYTES` 移至 `core/auth/ports.py`；middleware 只导入 `core.auth`；`JwksProvider` 与 `UnknownSigningKeyError` 留在 `infrastructure/auth/contract.py`。契约测试阻止 `api/**` 出现 `personal_assistant.infrastructure`，并锁定 middleware 的 `core.auth` 导入。
  6. **P2 verifier 关闭失败跳过数据库关闭**：lifespan `finally` 用嵌套 `try/finally` 保证两个关闭都尝试。反例：`aclose()` 抛错的 verifier 下 `storage.close()` 仍被调用。
- 第二轮独立验收修复（2 项，全部补反例测试）：
  1. **P2 节流窗口把合法轮换误报为 401**：`CloudflareJwksProvider` 现在区分“本次成功刷新并确认 `kid` 不存在”（含等待其他调用者完成的刷新）→ `UnknownSigningKeyError`（401）与“因节流/失败未执行检查”→ `AccessTokenUnavailableError`（503, `retryable=true`）。反例：`test_throttled_unknown_kid_is_temporarily_unavailable`、`test_rotated_key_during_throttle_window_is_unavailable_then_recovers`（窗口内 503、旧 key 仍可用、30 秒后刷新成功）、`test_concurrent_unknown_kid_refresh_is_coalesced`（合并刷新下 401/503 均安全且只联网一次）、API 层 `test_rotated_key_during_throttle_window_is_retryable_503`。
  2. **文档归属与规范化声明不准**：`NEXT_STEPS.md` 接口归属改为 `core/auth/ports.py` 与 `infrastructure/auth/contract.py` 的分工；`Settings.__post_init__` 现在对 team domain/audience/public origin 写回 canonical 值再校验（所有构造路径满足规范化不变量），factory 保留防御性规范化；`CONTRACTS` 12.3 明确 401/503 分类与文档；新增契约检查 `test_identity_contract_lives_in_core_auth`、`test_next_steps_names_the_correct_interface_owner`，并有 `test_direct_construction_writes_back_normalized_values`、`test_replace_writes_back_normalized_values`、`test_factory_defensively_normalizes_bypassed_settings`。
- 命令证据（两轮修复后）：F03 目标集合 124 passed（`tests/unit/test_cloudflare_access_verifier.py` + `tests/api/test_cloudflare_access.py` + `tests/unit/test_settings.py` + `tests/api/test_boundaries.py` + `tests/contract/test_f03_contract_consistency.py`）；`./scripts/test.ps1` 269 passed、28 skipped；`./scripts/test-postgres.ps1` 28 passed（F01+F02 无回归）；Ruff、Mypy（138 files）、`pip check`、`git diff --check` 通过；临时目录重建 wheel 并解包确认 `core/auth`、`infrastructure/auth`、既有四份迁移与三项依赖声明齐全。
- 最终独立复验：上述目标集、全量与 PostgreSQL 结果全部独立复现；手工验证未知 key 401、节流窗口内轮换 key 可重试 503、旧 key 继续可用且窗口后刷新恢复；wheel、工作树边界与四份迁移 checksum 均复核通过，未发现新的 P0/P1/P2 缺陷。
- 契约同步：`CONTRACTS_AND_INTERFACES.md` 第 12 节（contract v1.3）、`IMPLEMENTATION_MAP.md`、`README.md`。

能力边界：只保护用户 API/PWA（8000）；本机 Admin API（8001）不经 Tunnel，健康只读侧车（8010）保持不变。

有意未实现：自定义 Access 域名或团队域之外的 JWKS 地址、应用内登录/密码/MFA、Access 会话撤销与登录重定向、Tunnel/Tailscale 部署编排（F08/F10）。

剩余风险：无法抵御被盗的 Cloudflare/浏览器会话（产品接受）；Cloudflare Access 边缘可见传输内容；密钥轮换暂态：新 `kid` 在 30 秒节流窗口内无法检查时返回 503（`retryable=true`），成功刷新后确认不存在才是 401；TTL 内已缓存 key 始终可用。

失败红线复核：不信任 email/代理头（伪造头反例）；生产配置缺失/非法启动失败；Admin 仍只允许回环且 public app 无管理路由；JWKS 失败不放行；未记录原始 JWT/cookie/私钥。

禁区：增加应用内密码/MFA 并声称消除被盗 Access 会话风险。

### F04 — 模型适配器与披露许可（DONE，独立验收通过）

范围：为现有 `ModelRouter`、字段分类、精确 disclosure consent 和本地回退策略实现真实本地/远程模型适配器与持久 consent receipt。

能力边界：路由不执行工具，不自行扩大上下文。

验收：敏感字段发往远程前展示接收方/目的/字段并取得许可；凭据、token、cookie、私钥在所有路径硬阻断。

失败红线：缺少 consent 仍远传；redaction 失败后 fail open。

禁区：把整份资料作为“方便上下文”发送；在日志记录原始模型请求。

完成与实现证据（2026-09-17，等待独立验收，未标 DONE）：

- **核心端口与服务**：`core/models/disclosure.py` 新增 `DisclosureConsentState`（`ACTIVE/REVOKED/EXPIRED`）、`DisclosureConsentRecord`、`DisclosurePreview`、`DisclosureFieldSummary`、`DisclosureConsentStore`（`create/get/save/revoke/find_active/list_for_user`）、`DisclosureAuthorizer`、`DisclosureConsentService`（preview/confirm/authorize/get/revoke）与 `canonical_field_digest`/`redacted_field_preview`/`effective_consent_state`。摘要采用 CANONICAL JSON：每字段 `{name, value_sha256, classification, source}` 按 `(name, source, classification, value_sha256)` 排序后 SHA-256；provider、purpose、字段值、分类、来源或字段集合任一变化都会使旧许可失效。TTL 1 分钟–7 天；到期即 EXPIRED；撤销是 `version` CAS 终态；`(scope, idempotency_key)` + `command_fingerprint` 保证同键同内容重放返回原记录、同键异内容 `idempotency_conflict`。存储只含元数据与摘要，不含字段原文。
- **路由器接入**：`ModelRouter` 默认只接受持久 `DisclosureAuthorizer`（`consent_id`+`user_id`），调用栈里的临时 `DisclosureConsent` 仅在显式 `allow_ephemeral_disclosure=True` 的测试构造下可用，且与持久授权互斥；SECRET 在任何许可查询/供应商调用之前阻断；本地回退只在显式配置时触发且必须是非远程 provider；远程调用失败直接抛出，绝不改投其它远程供应商；可选审计只记录 provider/model/purpose/字段摘要/许可 ID/输出哈希，不含字段值。
- **真实适配器**：`infrastructure/models/openai_compatible.py`（远程 OpenAI 兼容 chat-completions，`is_remote=True`）与 `infrastructure/models/ollama.py`（本地 `/api/chat`，强制 loopback）。公共传输层 `base.py`：每次请求 `follow_redirects=False`（注入客户端也不能改变）、单一 monotonic 总 deadline 覆盖整个流、响应字节上限、URL 禁止内嵌凭据/query/fragment、非 2xx → `ModelProviderRejectedError`（用固定状态枚举 `rejection_code`，不读取厂商正文）、超时/连接失败/畸形 JSON/空完成 → 类型化错误、取消传播并关闭流、绝不重试；远端 API key 每次经 `SecretStorePort.resolve_for_broker` 解析，失败在发请求前 `ModelCredentialUnavailableError` fail closed，`SecretHandle` 不进入请求体；适配器在发送前再次拒绝 SECRET。
- **持久化与迁移**：新增 `PostgresDisclosureConsentStore` 与内存替身 `InMemoryDisclosureConsentStore`，语义一致（唯一约束、CAS、幂等重放、撤销）。新增迁移 `0005_f04_model_disclosure.sql`（`model_disclosure_consents` + `model_disclosure_commands`，主键 `(scope, idempotency_key)`），SHA-256 `012532834b281040d0031b48744ec7298e3c9b960bb247f51fd22c050f7534f0`；`0001`–`0004` 未改动（契约测试锁定旧 checksum）。
- **配置与组合根**：`Settings` 新增 `PA_MODEL_*` 并在直接构造/`from_env`/`replace` 全部写回规范化值再校验：远端必须 https + model + SecretHandle 句柄 ID，本地必须 loopback，半配置启动失败，回退必须显式且等于已配置本地 provider。`build_container(settings, *, secret_store=None)` 接入真实适配器；生产远程凭据后端是 fail-closed 的 `UnavailableSecretStore`（F09 前无 Windows Credential Manager，绝不使用内存明文）；`environment=production` 仍拒绝内存存储。
- **API**：public API 新增 `POST /api/v1/disclosures/preview`、`POST /api/v1/disclosures`、`GET /api/v1/disclosures/{id}`、`POST /api/v1/disclosures/{id}/revoke`；统一 `no-store`，响应与错误不含原始字段值；SECRET 403 `DISCLOSURE_DENIED`，预览漂移 409 `DISCLOSURE_PREVIEW_MISMATCH`，撤销状态/版本/幂等冲突 409，不存在 404。
- **测试证据**：F04 目标集合 `161 passed`（`test_model_disclosure.py` 25、`test_model_router_f04.py` 34、`test_model_adapters.py` 45、`test_bootstrap_f04.py` 5、`test_disclosures.py` 7、`test_f04_contract_consistency.py` 15、`test_settings.py` 27、`test_model_router.py` 3；无数据库时另有 `test_postgres_f04.py` 9 skipped）。`./scripts/test.ps1` → `409 passed, 37 skipped`，Ruff `All checks passed!`，Mypy `Success: no issues found in 150 source files`。`./scripts/test-postgres.ps1` → `37 passed`（F01 24 + F02 4 + F04 9，真实 PostgreSQL 17.11 临时库）。`python -m pip check` → `No broken requirements found.`；`git diff --check` → 干净。临时目录构建 wheel 并解包确认 `core/models/disclosure.py`、`core/models/errors.py`、`infrastructure/models/{base,openai_compatible,ollama}.py`、`infrastructure/database/disclosure_consents.py`、`infrastructure/secrets/unavailable.py`、五份迁移（含 `0005_f04_model_disclosure.sql`）与 `httpx/PyJWT/cryptography` 运行时依赖齐全。
- **第一轮自我审查（2026-09-17，交接前对抗性复核）**：用独立探针复现并修复了 6 项缺陷，均先补失败测试再最小修复：① httpx 层 connect/read/write/pool 超时曾落入 `MODEL_PROVIDER_UNAVAILABLE`，现统一为 `ModelProviderTimeoutError`（`test_http_level_timeout_is_typed_as_timeout`）；② 远端/本地 provider ID 相同时 `ModelRouter` 会静默覆盖一个 provider，现 `Settings` 与 `ModelRouter` 双重拒绝重复 ID（`test_duplicate_local_and_remote_provider_ids_are_rejected`、`test_duplicate_provider_ids_are_rejected`）；③ endpoint path 曾接受 `.`/`..` 段，现启动即拒绝（`test_model_endpoint_path_may_not_contain_dot_segments`）；④ `ACTIVE` 许可记录可携带 `revoked_at`，现记录校验拒绝（`test_active_record_cannot_carry_revocation_time`）；⑤ 临时 `DisclosureConsent` 的 naive 过期时间曾抛裸 `TypeError`，现构造即 `ValidationError`（`test_ephemeral_consent_requires_aware_expiry`）；⑥ `authorize` 原样信任存储层过滤，现服务层自行复核完整绑定，存储返回不完整匹配一律无许可（`test_authorize_rejects_a_store_that_ignores_expiry`）。审计写入失败会向上传播、调用方拿不到输出（`test_audit_failure_fails_closed`）。
- **第二轮自我审查（2026-09-17，交接前第二轮对抗性复核）**：再修复 8 项（时间类型化、绑定长度约束、编码绕过、usage 边界、store limit 一致性、生产凭据回归、PostgreSQL 并发撤销）：
  ① 临时许可路径与 `effective_consent_state` 收到 naive `now` 时曾抛裸 `TypeError`，现一律 `ValidationError`（`test_naive_now_is_rejected_with_typed_error`、`test_effective_state_requires_aware_now`）；② `confirm` 曾不校验 `provider_id`/`purpose` 长度，可持久化 5000 字符值，现与 preview/authorize 一致按 `_validate_scoped_text` 拒绝（`test_confirm_rejects_oversized_provider_id`、`test_confirm_rejects_oversized_purpose`）；③ endpoint path 的 `.`/`..` 检查曾可被多层百分号编码绕过（`%2e%2e`、`%252e%252e`、`%2f` 拆分），现解码到稳定值后校验（`test_model_endpoint_path_may_not_contain_dot_segments`）；④ 供应商返回的负数 usage 计数被静默保留，现丢弃负值（`test_negative_usage_counters_are_dropped`）；⑤ `list_for_user(limit<=0)` 内存实现曾静默丢弃记录、PostgreSQL 会直接报 SQL 错，现两侧一致 `ValidationError`（`test_list_for_user_rejects_nonpositive_limit`）；⑥ 新增组合根 fail-closed 回归：即使存在合法许可，无凭据后端时远程调用在发请求前以 `MODEL_CREDENTIAL_UNAVAILABLE` 失败（`test_composition_root_fails_closed_when_credentials_unavailable`）；⑦ 新增真实 PostgreSQL 并发撤销反例：两个连接用不同幂等键同时撤销同一许可，恰好一个成功、另一个 `ConcurrentModificationError`，数据库终态 `REVOKED`（`test_concurrent_revokes_allow_exactly_one_winner`）。
- **独立验收修复（2026-09-17，验收未通过后的第三轮修复）**：按验收意见修复 6 类缺陷，均先补失败测试：
  ① 本地/远程适配器自建 client 曾读取 `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY`（实测 loopback 请求正文抵达外部代理），现固定 `trust_env=False`，真实 loopback 代理反例断言代理零流量（`test_local_adapter_ignores_environment_proxies`、`test_remote_adapter_ignores_environment_proxies`）；
  ② 许可曾只绑定可复用的 `provider_id`，同一 ID 改指其他 endpoint/model 后旧许可仍有效，且可为未配置 provider 预生成，现新增 `recipient_fingerprint`（provider_id + adapter + 规范化 endpoint + model），preview/confirm 只接受当前已注册的远端 provider，authorize 与服务层复核都精确比对，迁移 0005 同步新增 `recipient_fingerprint char(64) NOT NULL` 并更新 SHA-256（`test_repointed_provider_cannot_reuse_consent`、`test_repointed_provider_cannot_reuse_an_old_consent`、`test_unregistered_recipient_is_rejected_before_preview`、API `test_unregistered_recipient_is_rejected`）；
  ③ 类型化错误曾用 `from exc` 链入 broker/httpx/JSON 原始异常（`__cause__.request` 携带 Authorization 与敏感正文、`JSONDecodeError.doc` 携带响应正文），现适配器内无 `from exc`，错误在异常作用域外生成且 `__cause__`/`__context__` 为空（`test_credential_broker_failure_keeps_no_exception_chain`、`test_transport_failure_keeps_no_request_in_exception_chain`、`test_timeout_failure_keeps_no_exception_chain`、`test_malformed_json_keeps_no_response_body_in_exception_chain`，契约测试禁止适配器出现 `from exc`）；
  ④ `hmac.compare_digest` 直接比较 Unicode 字符串会在 authorize 抛裸 `TypeError`，现统一 UTF-8 bytes 比较（`test_unicode_binding_values_do_not_crash_authorize`、`test_unicode_ephemeral_consent_is_compared_without_crashing`）；
  ⑤ 过期许可曾可被成功撤销为 `REVOKED`，现在内存锁与 PostgreSQL 行锁内先原子落 `EXPIRED`（version+1）再抛 `DisclosureStateError`（`test_expired_consent_cannot_be_revoked` 单元 + PostgreSQL 集成同名测试）；
  ⑥ 响应 close 可被第二次取消打断，且一个 provider 关闭失败会跳过后续 provider，现关闭统一走 `core/models/cleanup.run_cleanup` 抗重复取消，`ModelRouter.aclose` 关闭全部 provider 后再抛首个错误（`test_repeated_cancellation_still_closes_the_response_stream`、`test_aclose_closes_every_provider_before_raising_first_error`、`test_aclose_is_resistant_to_repeated_cancellation`）。
- **独立验收修复（2026-09-17，验收未通过后的第四轮修复）**：再修复 4 项（2×P1、1×P2、1×文档），均先补失败测试：
  ① 响应元数据曾被信任：`ModelOutput.model_id` 现恒为已配置值，usage 只保留适配器固定键白名单（`usage_keys`），非 2xx 只映射固定 `rejection_code` 枚举、不再解析厂商正文，异常字段 `vendor_code` 更名为 `rejection_code`；路由器审计的 `resource_id`/`data.model_id` 改用已注册接收方 `provider.recipient.model_id`，`data.usage` 再按核心 `AUDITABLE_USAGE_KEYS` 过滤（`test_response_metadata_cannot_inject_audit_identity`、`test_rejection_code_is_a_fixed_enum_not_echoed_vendor_text`、`test_audit_uses_configured_model_id_not_provider_metadata`）；
  ② 凭据泄露路径：远端凭据在构造 header 前限定为 ≤4096 个可见 ASCII 字符，非 ASCII/控制字符转 `ModelCredentialUnavailableError`（不再抛出保存原始凭据的裸 `UnicodeEncodeError`）；传输 helper 在构造异常前删除 `payload`/`headers`/`request`/`response` 等敏感局部引用，反例断言出错异常的 traceback frame locals 取不到 API key、Authorization 或请求正文（`test_non_ascii_credential_is_rejected_before_http_encoding`、`test_transport_failure_leaves_no_secret_in_traceback_locals`）；
  ③ 响应 `aclose` 失败曾覆盖取消或原始类型化错误并泄露裸关闭异常，现优先级固定为“取消 > 原始类型化错误 > 已安全脱敏的关闭错误”，流关闭异常也统一转类型化错误（`test_close_failure_does_not_override_cancellation`、`test_close_failure_does_not_override_original_typed_error`、`test_close_failure_without_other_error_is_sanitized_typed`）；
  ④ 契约 13.2 的存储最小化字段清单补齐 `recipient_fingerprint`（与 0005 及 13.6 一致）。
- **独立验收修复（2026-09-17，验收未通过后的第五轮修复）**：再修复 3 项（2×P1、1×P2）并统一轮次文案，均先补失败测试：
  ① `ContextField` 未做运行时规范化，值为 `SECRET` 的外来 `StrEnum` 可绕过对象身份检查并把秘密发往远端：现构造时按值规范化为 `DataClassification`（字符串 `"SECRET"` 同样归一，非法值 `ValidationError`），Router/适配器/披露服务对未知分类 fail closed（`test_foreign_or_string_secret_classification_cannot_bypass_block`、`test_duck_typed_secret_field_still_fails_closed`、适配器 `test_foreign_and_string_secret_classifications_are_rejected`、`test_duck_typed_secret_field_fails_closed`、`test_local_adapter_rejects_foreign_secret_classification`、披露服务 `test_preview_rejects_foreign_and_string_secret_classifications`、`test_invalid_classification_is_rejected_at_construction`）；
  ② 脱敏边界只覆盖 `_post_json`：凭据解析失败时 `complete` frame 仍保留含敏感字段的 `request`，解析失败时 `raw`/解析对象仍在 frame locals，深层 JSON 还会抛裸 `RecursionError`；现 `complete()` 统一覆盖凭据解析、payload 构建、传输与解析，抛出前删除 `request`/`payload`/`headers`/`raw`，解析失败（含 `RecursionError`）统一转 `ModelProviderProtocolError`（`test_credential_failure_leaves_no_request_fields_in_traceback_locals`、`test_parse_failure_leaves_no_response_body_in_traceback_locals`、`test_deeply_nested_json_is_a_typed_protocol_error`）；
  ③ 审计曾直接记录调用方传入的 `consent_id`，远端拒绝转本地回退后仍写入伪造 ID；现授权决策返回实际采用的持久许可记录，只有真实远端敏感披露使用持久许可时才记录其 ID，本地/回退/公开字段/临时许可一律不记录（`test_forged_consent_id_is_never_recorded_in_audit`、`test_ephemeral_consent_is_recorded_without_a_forged_id`）。
- **独立验收修复（2026-09-17，验收未通过后的第六轮修复）**：修复 2 项 P1，均先补失败测试：
  ① 接收端漂移：Router 原来只在构造时计算 fingerprint，且审计在调用后重读 `provider.recipient`；现注册时冻结完整 `RecipientIdentity` 快照，远端调用前重新计算当前 fingerprint 并与快照比较，不一致即 `DisclosureDenied` 且零远端请求，审计 `model_id` 只取快照；内置适配器让出站 URL、payload model 与 `recipient` 共用同一冻结身份，运行时换端点只能重建 provider（`test_repointed_recipient_invalidates_the_old_consent`、`test_repointed_model_invalidates_the_old_consent`、`test_audit_identity_comes_from_the_registration_snapshot`、适配器 `test_adapter_recipient_matches_the_sent_request`）；
  ② 请求构建失败泄露敏感 locals：`ModelRequest`/`ContextField` 现做完整运行时校验并把 duck-typed 字段强制转换为真实 `ContextField`（非 str instruction/value/source 与非法分类在构造时 `ValidationError`）；`complete()` 把 payload 构建纳入统一脱敏边界，构建失败先丢弃原始异常与敏感引用、再从干净帧抛无链 `ValidationError`（`test_malformed_request_is_rejected_before_any_call`、`test_duck_typed_public_field_is_coerced_safely`、适配器 `test_payload_build_failure_is_typed_and_scrubbed`、`test_malformed_instruction_is_rejected_before_any_request`、`test_malformed_field_components_are_rejected`）。
- **独立验收修复（2026-09-17，验收未通过后的第七轮修复）**：修复 2 项 P1 竞态与 1 项 P2 校验缺口，均先补失败测试：
  ① 接收端检查与发送之间的竞态：Router 现在在授权返回后、调用 provider 前再次复核 fingerprint（授权期间改指向即 `DisclosureDenied`、零调用）；内置适配器在 `complete()` 进入且尚未 `await` 时捕获请求级 `RecipientIdentity`，URL、payload model 与输出身份全程使用该快照，凭据解析期间替换 `_recipient` 不会改变发送目标或记录身份（`test_recipient_change_during_authorization_is_denied`、`test_recipient_snapshot_is_pinned_across_credential_awaits`）；
  ② 字段规范化曾被 duck field 的 `__eq__` 跳过：现规范化元组无条件写回，可变 duck 对象不会在许可绑定后继续存活（`test_field_with_lying_equality_is_still_copied`，契约测试禁止 `!= self.fields`）；
  ③ 非可迭代 `fields`（`None`/`123`）与字段属性访问异常统一安全归一为无链 `ValidationError`，不再抛裸 `TypeError`/`AttributeError`（`test_malformed_field_components_are_rejected`）。
- **独立验收结果（2026-09-17，通过）**：第七轮定向反例（接收端授权期间竞态、duck `__eq__` 绕过字段绑定、非可迭代 `fields`）均通过复验；F04 目标集合 `161 passed`；`./scripts/test.ps1` `409 passed, 37 skipped`；`./scripts/test-postgres.ps1` `37 passed`；Ruff、Mypy、`pip check`、`git diff --check` 通过；`0001`–`0005` checksum 与报告一致，旧迁移未改动。
- **逐项反例映射**：SECRET 本地/远程/回退 0 次 provider 调用（`test_secret_is_blocked_before_consent_lookup_on_every_path`、`test_secret_is_denied_even_for_local_provider`、适配器 `test_adapter_refuses_secret_fields_before_sending`、`test_local_adapter_refuses_secret_fields_before_sending`）；未授权敏感字段不外发（`test_remote_requires_persisted_consent`）；provider/purpose/值/分类/来源变化失效（`test_binding_changes_invalidate_persisted_consent`、`test_digest_is_order_independent_and_binds_every_component`）；过期/撤销拒绝（`test_expired_and_revoked_consents_are_denied`、`test_revoke_is_terminal_and_replay_safe`）；重启与 Container 重建后可用（`test_container_rebuild_keeps_persisted_consent_usable`）；幂等重放与冲突（`test_confirm_is_idempotent_and_conflicts_on_new_content`、`test_idempotency_conflict_on_changed_content`、`test_concurrent_connections_cannot_create_conflicting_consents`）；并发唯一（PostgreSQL 集成测试）；无原文泄漏（`test_audit_records_binding_without_sensitive_values_or_credentials`、`test_raw_values_never_reach_consent_tables`、`test_preview_never_contains_raw_values`）；HTTP 超时/慢速/畸形/超限/取消类型化且关闭流（`test_slow_response_times_out_and_closes_stream`、`test_oversized_response_is_rejected_and_stream_closed`、`test_cancellation_propagates_and_closes_stream`、`test_malformed_and_incomplete_responses_are_protocol_errors`）；远程失败不改投（`test_remote_failure_never_falls_back_to_another_provider`、`test_provider_is_never_retried_silently`）；显式本地回退与 SECRET 复核（`test_fallback_only_on_missing_consent_and_must_be_local`、`test_default_fallback_comes_from_constructor_configuration`）；工具样式输出不执行（`test_tool_style_model_output_is_text_not_execution`）；依赖方向（`test_core_and_domain_never_import_http_or_vendor_sdks`、`test_router_never_executes_tools_or_touches_gateways`）；旧迁移不变（`test_frozen_migration_files_are_byte_identical`、`test_upgrade_from_0004_adds_only_0005_and_keeps_old_checksums`）；wheel（`test_0005_is_registered_for_wheel_packaging` + 手工解包核验）。
- **未实现（不得当作已完成）**：Windows Credential Manager 等真实宿主凭据后端（F09；当前 fail-closed 占位，生产远程调用会以 `MODEL_CREDENTIAL_UNAVAILABLE` 失败）；真实厂商端到端（无真实凭据，只有运行时 HTTP transport 协议测试，不能宣称厂商 E2E 通过）；Embedding/结构化输出；PWA 披露界面与 Agent 模型调用编排（F08/后续）；模型调用 HTTP 路由。
- **剩余风险**：许可 TTL 内被盗的 Access/浏览器会话可继续使用既有许可（与 R2 审批共享的产品边界）；SSE/审计只记录摘要，无法从审计重建被披露内容（有意的隐私取舍）；`model_disclosure_commands` 永久保留命令指纹（不含原文）；本机 Admin 进程重启会丢弃未确认预览（预览无状态，重新生成即可）。
- **失败红线复核**：SECRET/D3 未到达任何适配器（路由器 + 适配器双重拒绝，反例断言 provider 调用次数为 0）；无许可不向远程发送 PERSONAL/SENSITIVE；许可记录不含敏感原文；API key 只在 `resolve_for_broker` 返回值中短暂存在，不进入请求体/日志/异常/数据库/审计/fixture；未修改 0001–0004；远程失败不改投；模型输出不执行工具；production 不自动使用内存许可存储；未删除或弱化任何测试。

### F05 — 个人知识扩展（DONE，完整独立审计与修复后自审计通过）

范围：增量扫描、内容哈希、抽取、PostgreSQL FTS + pgvector、证据引用、删除传播。

能力边界：只读用户授权目录；原文件是事实源。

验收：修改/删除/重命名后索引正确；检索能返回文件与页/段位置；不同格式契约测试通过。

失败红线：索引结果无出处；删除原文后长期返回旧片段；修改用户原文件。

禁区：把向量库当唯一副本；把个人正文提交 Git。

#### 架构缺口清单（实现前检查，2026-09-17）

1. **扩展配置通道缺失**：`RuntimeContext.non_secret_config` 生产路径恒为空，无配置存储与注入。F05 新增通用 `ExtensionConfigStore` + `FileExtensionConfigStore` + Admin `GET/PUT /admin/v1/extensions/{id}/config`，由 `ProcessRuntimeSupervisor` 在 handshake 时注入；核心无 `personal.knowledge` 分支。
2. **扩展迁移未执行**：宿主只校验 `migration.list` 数量，没有 `ext_*` 迁移执行入口。F05 新增通用迁移机制：扩展通过 `host.data.migrate` 提交 `{version, path, checksum, description}`，宿主在已安装 payload 内解析、校验 SHA-256、经语句守卫后执行，并在扩展自有 Schema 内登记 `extension_data_migrations`（命名空间 advisory lock 串行化，批次单事务）。
3. **无数据库访问通道**：Worker 不能拿连接串/密码。F05 新增全双工 stdio（worker→宿主请求）与通用数据代理 `host.data.execute/transaction/migrate`：单语句守卫、参数/超时/行数上限、`search_path` 只含扩展 Schema + pgvector 类型 Schema + `pg_catalog`，并动态拒绝核心表未限定引用。`PA_STORAGE_BACKEND=memory` 时 fail closed。
4. **无 embedding 通道**：F04 只交付聊天模型。F05 在扩展内定义 `EmbeddingProvider` 端口与身份（provider/model/dim/version），默认 `none` 显式降级为仅 FTS；`ollama` 仅允许回环 HTTP；测试用确定性 hash 替身并明确标注非语义模型；身份随版本持久化，变化触发重建。

#### 交付与证据（2026-09-18，已标 DONE）

- **扩展制品**：`extensions/personal_knowledge`（Manifest + 4 个 JSON Schema + 扩展自有迁移 `migrations/0001_knowledge_index.sql` + `src/personal_knowledge/*`），工具风险仅 `READ`/`INTERNAL_WRITE`，无 `EXTERNAL_WRITE`。
- **SDK 兼容新增**：`HostDataClient`、`RuntimeContext.host_data`、`MigrationDescriptor.path`、`HostBroker`/`HostCapabilityError`、`rpc.decode_frame`；全双工宿主客户端在请求 id 不匹配时仍破坏流，未配置 `host_handler` 时返回 `DATA_UNAVAILABLE` 且流可用。
- **通用宿主能力**：`core/extensions/data_access.py`（守卫与协议）、`core/extensions/config.py`（JSON-Schema 子集校验与配置端口）、`infrastructure/database/extension_data.py`（真实执行 + 迁移 + 绑定校验）、`infrastructure/extensions/config_store.py`、`infrastructure/memory/extension_data.py`（fail closed）。
- **扩展能力**：路径安全（拒绝 `..`/绝对路径/盘符/根外符号链接与 junction/大小写绕过；遍历不跟随链接；只读）、Markdown/TXT/PDF 抽取与稳定 locator、SHA-256 版本化分块、`active_version` 原子切换、RRF 融合、引用复核与删除传播、`EventSource` 轮询式变更检测 + `reconcile` 计划。
- **契约文档**：`CONTRACTS_AND_INTERFACES.md` 第 14 节（contract v1.5）逐项冻结上述语义；`tests/contract/test_f05_contract_consistency.py` 锁定槽位/风险/依赖方向/迁移历史/文档表述。
- **独立验收修复（第二轮，2026-09-17，最终复验通过）**：独立验收提出 6 项 P1 + 2 项 P2，全部先补真实反例再修复：① `forbidden_relations` 统一进入 execute/transaction/migrate 三条路径（真实 PostgreSQL 证明 `SELECT/ALTER tasks` 与恶意迁移被拒绝且核心表未变、ledger 未登记）；② 授权根成为即时查询边界并删除已移除根（`search` 强制 root 交集、`reconcile` 读取全部 source 并 tombstone 未授权根）；③ `active_version IS NULL` 强制重试首次失败的构建；④ 取消/失败统一 shield 清理候选、reconcile 清理孤立 `BUILDING`，最终由第三轮收紧为 owner 绑定且 CAS 资格检查先于 READY 提升；⑤ 增量差异纳入 extractor 与完整 embedding 身份，Ollama 维度先 probe 再持久化；⑥ Ollama 改为 `asyncio.open_connection` 直连（无环境代理、无重定向、单调总 deadline、取消即关闭）；⑦ capability 授权门（未声明/不可用即拒绝 enable，`REQUIRED_CAPABILITY_UNAVAILABLE`）；⑧ `SearchResult` 只让 `CURRENT` 携带正文，STALE/DELETED 仅元数据且 `retrieve` 只返回 CURRENT。反例见 `tests/integration/test_postgres_f05.py` 的 acceptance counterexamples 段落、`tests/unit/test_extension_capability_gating.py`、`tests/unit/test_knowledge_embedding.py` 的代理/滴流/取消测试。
- **第三轮完整修复（2026-09-17，最终复验通过）**：针对再次审计的 8 项缺陷逐项补反例并修复：① execute/transaction/migrate 在设置 `search_path` 前以有界 advisory lock 创建专属 Schema，避免首次 DDL 落入 pgvector 所在 `public`，核心关系禁表每次请求刷新；② worker SDK 把 deadline 写入请求参数，数据库 statement timeout 统一映射 `DATA_TIMEOUT`；③ `begin_version` 不再覆盖既有 READY/他人 BUILDING，同一 `built_by` owner 贯穿 chunk 写入、无副作用 CAS 激活和失败清理；④移动后重用旧路径时稳定选择碰撞后备 source id；⑤向量查询只在 MATERIALIZED identity 匹配集上计算距离，模型/维度漂移在 reconcile 前安全退回 FTS；⑥超长单行/大 PDF 页使用可复算字符 locator 分片，embedding ≤512 条/批，数据库帧按字节有界批处理；⑦ Ollama 截断 Content-Length 转 `EMBEDDING_PROTOCOL_ERROR`；⑧ source generation 进入事件幂等键，重复真实转换不再永久丢事件。新增真实 PostgreSQL 反例覆盖 namespace、动态核心表、timeout、owner、路径复用、长单行、identity 漂移与重复事件。
- **迁移**：F05 未新增核心迁移（`0001`–`0005` 未改动，checksum 由契约测试锁定）；扩展自有迁移 `extensions/personal_knowledge/migrations/0001_knowledge_index.sql`，SHA-256 `0f8758f97cce7206ac0b5cd4aa159f1b7b43de79cf1fa77a811a67f8aecd759d`，由宿主在扩展 `ext_*` Schema 内执行并登记 `extension_data_migrations`。
- **制品核验**：`pip wheel . --no-deps --no-build-isolation` 产出框架 wheel（171 个条目），含 `core/extensions/{data_access,config,rpc}.py`、`infrastructure/database/extension_data.py`、`infrastructure/extensions/config_store.py`、`memory/extension_data.py`、`personal_assistant_sdk/host.py`、PWA、config 与 `0001`–`0005` 全部迁移，且不含任何 `personal_knowledge`/`extensions/` 条目；扩展以独立 zip 制品（`extension.toml` 位于制品根）经真实 Supervisor 完成 install → enable → reindex → search → uninstall 烟测（自动化测试 `test_zip_artifact_installs_and_serves`）。注：Python wheel 形式的扩展包会把 manifest 放在 `.data/data/` 下，不满足 Supervisor 的“manifest 位于制品根”要求，因此制品格式为目录或 zip。
- **失败/原子性反例**：注入抽取器故障后旧活动版本仍可查询、无 BUILDING 残留、下次 reconciliation 成功切换（`test_build_failure_keeps_old_version_queryable`）；手工插入的半成品版本对查询不可见（`test_building_version_is_invisible_to_queries`）；并发 reconciliation 收敛为每来源唯一活动版本（`test_concurrent_reconciliation_converges`）；删除后分块/版本/tombstone 语义与检索为空（`test_delete_removes_content_and_keeps_minimal_tombstone`）；引用在源文件变化后为 STALE、删除后为 DELETED（`test_stale_citation_after_change_is_not_current`、`test_worker_retrieve_marks_deleted_sources`）；索引过程不修改源文件（`test_indexing_never_modifies_source_files`）；越界符号链接不被索引（`test_out_of_root_symlink_is_never_indexed`）。
- **黄金查询集**：合成语料 9 个文件、12 条查询，FTS-only 与（确定性测试替身）混合模式均 `Recall@5 ≥ 0.90`，所有返回引用的 locator 切片与源文件逐字一致、哈希匹配（`test_golden_query_set_recall_and_citations`）；无证据查询返回 `unknown=true`（`test_search_is_unknown_without_evidence`）。
- **第四轮完整独立审计（8 组缺陷）**：① 配置校验拒绝非有限数值与隐藏在未支持结构内的 Schema 关键字；② 文件实际读取和 PDF Flate/页面聚合解压均在读后再次执行硬字节上限；③ `.txt`↔`.md` 跨媒体类型移动触发抽取器身份重建；④并发陈旧移动不能重复推进 generation/事件；⑤数据适配器首次向量 Schema 探测复用当前连接，不在单连接池内自锁；⑥ namespace advisory lock 超时统一类型化；⑦迁移禁止通过 `SET LOCAL search_path` 越权；⑧反例覆盖上述路径并在真实 PostgreSQL 上复验。
- **第五轮修复后自审计（严格同级）**：继续发现并修复配置存储严格 JSON/大小/并发替换、启动时按当前版本 Schema 复核、路径读后重新解析、来源激活/删除完整快照 CAS、删除消耗 generation、长构建 heartbeat 租约、事件轮询与 reconciliation 共享 rename/re-add/dedupe 语义、向量 JSON 帧与数据库结果总字节限制、参数深度/非有限值、迁移单次校验读取与逐语句白名单、PDF 页面聚合解压上限。修复后再次执行目标、全量、PostgreSQL/Worker、静态与制品验证。
- **最终命令证据（第五轮审计后）**：`./scripts/test.ps1` → `597 passed, 81 skipped`，Ruff `All checks passed!`，Mypy `Success: no issues found in 166 source files`；`./scripts/test-postgres.ps1` → `81 passed`（PostgreSQL 17.11 + pgvector，含真实 Worker）；`python -m pip check` 与 `git diff --check` 通过。

未实现（有意）：宿主在 enable 时自动执行 MigrationProvider（当前由扩展经数据能力触发）；OS 级文件监听（F09）；无维度约束向量列的 HNSW 索引（当前精确检索）；真实远程 embedding 与语义向量质量验收；PWA Schema 表单渲染（F08）；永久 purge 仍 501。

剩余风险：本机进程创建延迟会影响 F02 既有的亚秒级 RPC 计时测试（与 F05 代码无关，环境负载敏感）；`plainto_tsquery('simple')` 对无空格中文长句只能整句匹配（黄金查询集使用词边界清晰的内容）；向量维度未约束时无法建 ANN 索引，规模上限依赖精确检索。

### F06 — smail 扩展（DONE — 独立验收通过）

#### F06.P2 — SMTP TLS取消与准备失败回收（DONE，独立验收通过）

最新独立验收已通过，F06.P2 DONE、最小F09.1 DONE；邮件89 passed及额外10项、全量1301 passed/130 skipped/9 warnings、PostgreSQL/真实Worker130 passed/1 warning。日志为 `var/verification/f06_tls_cancel/full.log` / `postgres.log`；摘要已只读核对，完整独立命令文本未在反馈中提供，可复现实施命令保留在修复报告第5/8节。静态、依赖、8项摘要及wheel通过，用户确认记录25进程退出。本轮只记录验收，无测试重跑或产品/服务操作。以下修复过程和旧BLOCKED/待复验表述为历史，以本段及报告第10节为准。

用户要求先提交推送，再修F06：快照9891102已成功推送origin/main，随后才开始本项。最小范围是smtp_client.py、test_mail_protocol_f06.py、tests/support/mail_servers.py中的SMTP/TCP测试服务及状态/契约/修复报告。隐式TLS和STARTTLS均存在握手完成后才登记实际socket的问题，沿用同一连接生命周期修复；保留能力检查、证书校验、SMTP状态重置、审批/Outbox和FAILED/UNKNOWN分类。

第一轮修复计划（历史，已执行）：先红测试，在真实TCP/TLS握手窗口捕获实际SSLSocket，取消在0.75秒内完成并关闭socket/回收线程；把底层socket超时与宿主总时限分别设定，显式区分两个分支；两种TLS模式都保留零发送、实际peer关闭、线程回收断言。补正常STARTTLS精确MIME通过路径。先运行新增/修改定向反例，再做最小实现；修复后串行scripts/test.ps1及适用scripts/test-postgres.ps1完整复验。F09.1/F08.6仍BLOCKED，不以实现者测试替代独立验收，不推进其他F09。


范围：IMAP TLS 只读轮询、UIDVALIDITY/UID 游标与 Message-ID/内容哈希辅助去重、线程/联系人/附件来源、版本化草稿、受控 SMTP TLS 单封发送及 Sent 只读对账。

能力边界：读取与发送能力分离；客户端专用密码仅经 `SecretHandle` 由宿主邮件代理使用；扩展不建立 IMAP/SMTP 连接。

#### F06.P2 第一轮修复结果（2026-10-09，历史快照）

- 最小实现：两种TLS模式共用创建实际TLS socket→登记→检查中止→显式握手→再次检查；失败关闭实际socket。STARTTLS保留能力检查、220响应检查与RFC 3207状态重置；审批/Outbox、租约、风险与FAILED/UNKNOWN分类未改。
- 首红命令：`.\.venv-win\Scripts\python.exe -m pytest -p no:cacheprovider -o 'addopts=--strict-markers' -q --tb=short tests/integration/test_mail_protocol_f06.py -k 'tls_handshake or starttls'`，**4 failed/3 passed/34 deselected**；实际两种取消和宿主总时限均等到约2秒socket超时。修复首次同命令 **6 passed/1 failed**（一项STARTTLS总时限1.5秒>1.25秒，隔离1 passed），耗时断言均保留。
- 最终定向：同环境运行pytest文件 `tests/integration/test_mail_protocol_f06.py tests/unit/test_mail_send_gateway_f06.py tests/contract/test_f06_contract_consistency.py`，**86 passed**（142.48秒）；目标Ruff/Mypy通过。完整命令见修复报告。
- 完整门禁：`.\.venv-win\Scripts\python.exe -X utf8 var/verification/f06_tls_cancel/run_gates.py full` → **1298 passed、130 skipped、11 warnings**（1274.56秒），全仓Ruff、Mypy232文件通过；随后串行同运行器 `postgres` → **130 passed、1 warning**（569.21秒）。分别调用scripts/test.ps1和scripts/test-postgres.ps1，后者仅派生*_test DSN/临时库，日志脱敏。没有新增skip或弱化断言。
- 警告保留：依赖弃用/测试用短HMAC、Windows管道析构回收与pytest缓存目录权限；本轮不扩大修复到RPC/进程监督。全量本次通过不表示历史时限波动已根治。
- [修复报告](F06_TLS_CANCELLATION_REPAIR_2026-10-09.md)记录文件、精确命令、摘要、首轮失败与风险。F06.P2待独立复验；F09.1/F08.6继续BLOCKED，F07/F08整体IN_PROGRESS。推送的仅9891102快照；F06修复留在工作区，未再次提交/推送/部署。

#### F06.P2 独立反馈后的局部补修（2026-10-09，历史交接）

- 用户独立反馈：上一轮邮件86 passed、取消/交接6项通过，但缺STARTTLS或454时TCP仍打开；无认证或发送。实施者不将用户数字算作本轮执行。
- 本轮先补真实TCP的缺STARTTLS、454及EHLO拒绝，pytest过滤 `starttls_unsupported or starttls_454 or starttls_ehlo_refusal`：3 failed/41 deselected（1.16秒）；统一保护_connect准备流程，所有异常close再原样抛出后同命令3 passed/41 deselected（0.88秒）。保留peer关闭、fd=-1、线程回收及零认证/发送断言。
- 相邻命令：`.\.venv-win\Scripts\python.exe -m pytest -p no:cacheprovider -o 'addopts=--strict-markers' -q --tb=short tests/integration/test_mail_protocol_f06.py tests/unit/test_mail_send_gateway_f06.py tests/contract/test_f06_contract_consistency.py` → **89 passed，134.30秒**。Ruff三改动文件、Mypy产品1文件及pip check通过；git diff --check及全部9项改动（含未跟踪报告）的行尾空白检查通过。
- 仅连接准备异常回收，审批/Outbox、共享状态及数据库/Worker接口未改，按AGENTS局部规则不重跑全量或PostgreSQL/Worker；1298/130完整门禁是补修前历史。当前精确命令、限制及摘要见修复报告第8/9节。
- F06.P2保持IN_PROGRESS等待独立复验；F09.1/F08.6仍BLOCKED，F07/F08整体IN_PROGRESS。没有新提交/推送/部署、服务重启、真实邮箱或ehall操作；独立通过后再关闭F09.1并回到F08.6。

#### 架构缺口清单（实现前检查，2026-09-18）

1. **无通用邮件宿主端口**：核心只有模型/知识能力。F06 新增 `core/mail/ports.py`（`MailTransportBroker`、`MailAccountBinding`、`MailReadSession`、`MailEnvelope`、`MailRecipientResult`、`MailDeliveryReceipt`、`MailReconciliationResult`、`MailPolicy`、`MailDeliveryLedger`），实现位于 `infrastructure/mail/`，核心无任何 `nju.smail`/NJU 分支。
2. **扩展无法发起宿主能力请求（只读方向）**：F05 已有全双工 `host.data.*`。F06 在同一通道新增只读 `host.mail.probe/folders/fetch/reconcile_sent` 与 `host.artifact.put/read/delete`；SMTP 发送不暴露给扩展，只能经 Tool Gateway executor。
3. **草稿是 Artifact 但扩展无制品能力**：新增通用 `artifact.read/artifact.write` 宿主能力（`core/extensions/artifact_access.py` + `FileExtensionArtifactAccess`/`InMemoryExtensionArtifactAccess`），按扩展版本持久化所有权，跨扩展读取 fail closed。
4. **无宿主传输台账**：UNKNOWN 与部分拒收需要跨重启证据。新增迁移 `0006_f06_mail_transport.sql` 的通用 `mail_delivery_actions`（无正文、无凭据、无扩展 ID）。
5. **发送执行边界**：新增 `MailSendExecutor`（`infrastructure/mail/executor.py`）：只有声明 `mail.send` 能力的工具可被它执行；先令扩展物化“当前草稿版本”的精确 MIME 字节，校验哈希、Message-ID、收件人/主题/附件哈希与审批快照一致后才调用 Broker；任何不一致在 SMTP 前确定失败。
6. **IMAP/SMTP 客户端**：`infrastructure/mail/imap_client.py` 使用 `ssl.create_default_context()`（证书+主机名验证）、`EXAMINE`/`SELECT (readonly)`、`BODY.PEEK`，禁用 `STORE/EXPUNGE/COPY/MOVE/APPEND/IDLE`；`smtp_client.py` 显式区分 DATA 前/中/后断线并保存逐收件人结果。

#### 交付与证据（2026-09-18，等待独立验收，未标 DONE）

- **扩展制品**：`extensions/nju_smail`（Manifest、12 个 JSON Schema、扩展自有迁移 `migrations/0001_mail.sql`、`src/nju_smail/*`）。Manifest 槽位：`EventSource: smail.poll_inbox`、`ContextProvider: smail.thread_history`、`WorkflowProvider: smail.reply_flow`、`ScheduleProvider: smail.poll_every_5m`（300 秒，`coalesce`）、`FormSchemaProvider: smail.account_settings`、`MigrationProvider: smail.mail_schema`；工具风险：`smail.search` READ、`smail.prepare_reply`/`smail.sync`/`smail.send_status`/`smail.reconcile_send` INTERNAL_WRITE、`smail.send` EXTERNAL_WRITE；`smail.record_send_result` 不存在，扩展无法写入发送结果。能力：required `extension.data.sql`/`mail.read`/`artifact.read`/`artifact.write`，optional `mail.send`。
- **主去重与辅助去重**：`mail_message_locations` 主键 `(account, folder, uidvalidity, uid)`；逻辑身份 `dedupe_key` 取规范化 `Message-ID`，缺失时退回 `canonical_content_hash`（含规范化主题/发件人/收件人/正文/附件哈希；截断消息计入大小）；邮件、位置、线程、联系人、附件、事件与新游标在同一宿主事务提交，重复扫描与 Worker 重启收敛为一条消息/一个事件。
- **UIDVALIDITY 变化**：检测到新 UIDVALIDITY 时先把游标归零并重新从 0 拉取；旧位置保留可追溯，同一 Message-ID/内容不会重复产生事件或草稿候选。
- **凭据失效**：认证/凭据错误进入 `NEEDS_USER_ACTION`（不再自动高频重试）；瞬时错误按 60 秒起、上限 3600 秒的指数退避写入 `mail_sync_state.backoff_until`；手动 `force` 可显式重试。
- **草稿与审批**：`mail_draft_versions` 每次编辑生成新版本、新 canonical 摘要、新稳定本地动作 ID 与 Message-ID；`smail.send` 在发送前要求“请求版本 == 当前版本”且摘要/哈希与审批参数一致，手机编辑任一字段后旧审批无法物化旧版以外的内容；实际发送的是被审批的精确 MIME 字节（含附件哈希），`mail_delivery_actions` 记录逐收件人成功/失败明细。
- **UNKNOWN 与对账**：DATA 期间/之后断线记为 `UNKNOWN`（绝不自动重发）；`smail.reconcile_send` 只读核对 Sent 文件夹：唯一命中转 `SENT_CONFIRMED`，未找到保持 `UNKNOWN`，多条命中转 `NEEDS_USER_ACTION`。
- **Prompt Injection**：邮件正文只作为不可信数据（`trust=EXTERNAL_MESSAGE`）进入检索/上下文；同步注入邮件不注册能力、不改变策略、不产生发送动作（`test_postgres_f06` 断言 `mail_send_actions` 为 0）。
- **公共契约变化**：`core/mail/ports.py`（v1.6 第 15 节）、`core/extensions/artifact_access.py`、SDK `MailAccountSpec`/`MailboxCapabilities`/`MailFolderInfo`/`FetchedMail`/`FetchedMailBatch`/`HostMailClient`/`HostArtifactClient` 与 `RuntimeContext.host_mail`/`host_artifact`、`PA_MAIL_SEND_ENABLED`/`PA_MAIL_TEST_RECIPIENTS`/`PA_MAIL_MAX_MESSAGE_BYTES`。
- **迁移**：核心新增 `0006_f06_mail_transport.sql`（SHA-256 见契约 15.6，通用 `mail_delivery_actions`）；`0001`–`0005` 逐字节未改（契约测试锁定）；扩展迁移 `extensions/nju_smail/migrations/0001_mail.sql`（SHA-256 见契约 15.6）由宿主在 `ext_nju_2e_smail` Schema 内执行并登记。
- **测试覆盖**：`tests/unit/test_mail_mime_f06.py`（边界 MIME/文件名穿越/深层 multipart/截断）、`tests/unit/test_smail_sync_f06.py`（UID/Message-ID/内容去重、UIDVALIDITY、线程）、`tests/unit/test_smail_drafts_f06.py`（版本化草稿、快照篡改、部分结果、对账）、`tests/unit/test_mail_send_gateway_f06.py`（无审批 0 派发、旧审批+新内容 0 派发、部分拒收不报成功、UNKNOWN 不重试、同键异 payload 冲突、allowlist、凭据失败转用户动作）、`tests/integration/test_mail_protocol_f06.py`（真实 TLS/socket 模拟 IMAP/SMTP：只读且状态不变、UIDVALIDITY、认证/TLS/凭据失败、逐收件人部分拒收、DATA 三阶段断线、Sent 对账 MATCHED/NOT_FOUND/AMBIGUOUS）、`tests/integration/test_postgres_f06.py`（重复同步/重启/UIDVALIDITY 幂等、NEEDS_USER_ACTION 不退避重试、草稿/发送/回执/对账跨重连恢复）、`tests/integration/test_smail_worker_real.py`（独立 zip 制品 + 真实 venv/Worker + 真实邮件协议 + 真实 PostgreSQL 的 install→enable→sync→draft→disable→recover→uninstall）。
- **最终命令证据（2026-09-18，第三轮审计修复后测量；第四轮改动未执行）**：F06 目标集合（无数据库时 125 passed、22 skipped；配置 `PA_TEST_DATABASE_URL` 后 147 passed，含真实 PostgreSQL 与真实 Worker）——`tests/unit/test_mail_mime_f06.py`、`tests/unit/test_smail_sync_f06.py`、`tests/unit/test_smail_drafts_f06.py`、`tests/unit/test_mail_send_gateway_f06.py`、`tests/unit/test_settings_f06.py`、`tests/unit/test_mail_registry_f06.py`、`tests/unit/test_mail_ledger_f06.py`、`tests/integration/test_mail_protocol_f06.py`、`tests/api/test_admin_mail_accounts.py`、`tests/contract/test_f06_contract_consistency.py`、`tests/integration/test_postgres_f06.py`、`tests/integration/test_smail_worker_real.py`。`./scripts/test.ps1` → 722 passed、103 skipped、Ruff `All checks passed!`、Mypy `Success: no issues found in 191 source files`。`./scripts/test-postgres.ps1` → 103 passed（F01 24 + F02 4 + F04 9 + F05 41 + Worker 3 + F06 PostgreSQL 21 + smail 组合根 1；PostgreSQL 17.11 + pgvector，全部为临时 `*_test` 数据库）。`python -m pip check` → `No broken requirements found.`；`git diff --check` 干净（仅 autocrlf 提示）。框架 wheel 解包核验：189 个条目，含 `core/mail`、`infrastructure/mail`、制品能力、`mail_ledger.py` 与 0006 迁移，不含任何 `nju_smail` 条目；扩展以独立 zip 制品在真实组合根（`build_container`）中完成 install → enable → refresh_tool_registry → sync → prepare_reply → R2 审批 → 生产 ToolGateway 发送（SMTP 服务器收到与审批哈希一致的精确 MIME 字节）→ 宿主台账 SUCCEEDED → 停用 → 恢复 → 再同步 → 卸载 烟测（`test_smail_worker_real.py`）。核心迁移 `0001`–`0005` SHA-256 未变（契约测试与 `test_f06_contract_consistency.py` 双重锁定）；新增 `0006_f06_mail_transport.sql` SHA-256 `dbc5001bdd16981f2a17f36abe4ef3fcdd36c63c3461477f1a61e1ecb6f32a01`，扩展迁移 `0001_mail.sql` SHA-256 `6320af7791bd2805e36c70f7fa731417935e7bfd6ad875b95a6d42d179469d8c`（审计修复更新了六态状态机 + 租约列与草稿请求幂等列）。
- **每项安全反例对应的测试**：重复 UID/同内容不同 UID/缺失重复 Message-ID/UIDVALIDITY 重置 → `test_smail_sync_f06.py`；事务批与游标原子性、重启幂等、注入邮件不产生发送动作 → `test_postgres_f06.py`；畸形 MIME/深层 multipart/超大与压缩附件炸弹/文件名穿越 → `test_mail_mime_f06.py`；TLS 证书、认证失败、限流/慢速滴流、取消 → `test_mail_protocol_f06.py::test_untrusted_certificate_fails_closed`/`test_wrong_password_is_a_typed_user_action_error`/`test_unsupported_auth_mechanism_is_needs_user_action`/`test_slow_trickle_respects_the_total_deadline`/`test_cancellation_closes_the_session`；凭据撤销 fail closed → `test_revoked_credential_fails_closed`；只读不改状态 → `test_probe_lists_folders_and_reads_without_mutating_state`/`test_reconciliation_never_deletes_or_moves`；草稿编辑使旧审批失效 → `test_smail_drafts_f06.py::test_edited_draft_makes_the_old_version_unusable`、`test_mail_send_gateway_f06.py::test_modified_payload_with_old_approval_is_rejected`；无审批 0 派发 → `test_no_approval_never_reaches_the_transport`；同键异 payload 冲突 → `test_same_idempotency_key_with_different_payload_conflicts`；DATA 三阶段 → `test_disconnect_before_data_is_a_definitive_failure`/`test_disconnect_during_data_is_unknown`/`test_disconnect_after_data_is_unknown`；部分拒收 → `test_partial_recipient_rejection_keeps_full_detail`、`test_partial_delivery_is_never_reported_as_success`；UNKNOWN 不重发与对账收敛 → `test_unknown_outcome_is_not_retryable_and_not_retried`、`test_not_found_reconciliation_keeps_unknown_and_never_resends`、`test_matched_reconciliation_converges_to_confirmed`、`test_sent_reconciliation_matched_not_found_and_ambiguous`；审批快照与实际 MIME 一致 → `test_approved_send_transmits_the_exact_artifact_bytes`、`test_stale_draft_version_never_reaches_the_transport`；凭据不进入异常 → `test_wrong_password_is_a_typed_user_action_error`。
- **真实外部验收未执行（必须如实声明）**：当前没有用户提供的 smail 账号、SecretHandle 或受控收件地址，因此未连接真实邮箱、未执行真实只读 IMAP E2E、未发送任何真实邮件。协议级模拟服务器、真实 TLS/socket、真实 PostgreSQL 与真实 Worker 测试不能替代真实 smail E2E。Windows Credential Manager 属 F09；生产凭据后端不可用时宿主 fail closed（`UnavailableSecretStore`），绝不回退明文。
- **剩余依赖**：F09 真实凭据后端；用户登记的受控测试地址与显式 `PA_MAIL_SEND_ENABLED`；真实服务器的 capability probe 结果（认证机制/速率/文件夹命名）需在真实只读验收时确认；`Sent` 文件夹名称可能因供应商不同需配置。


#### 第一轮独立审计修复（2026-09-18，6×P1 + 2×P2，均先补反例）

1. **P1 凭据重定向（confused deputy）**：新增宿主所有 `MailAccountRegistry`（`MailAccountRecord` + `FileMailAccountRegistry`/`InMemoryMailAccountRegistry`，严格 JSON、原子写）与 Local Admin `GET/PUT /admin/v1/mail/accounts`。扩展与工具参数只能提交 `account_id`；mail broker 从注册表解析端点与 `SecretHandle` 并自行构造 binding；`host.mail.account` 只返回非秘密视图（无端点/句柄）；`MailSendExecutor` 校验宿主计算的 `account_fingerprint`。反例：`test_account_not_registered_never_reaches_materialization`、`test_changed_account_fingerprint_is_rejected`、`test_host_owns_the_account_registry`（契约）。
2. **P1 分块同步跳过邮件**：游标改为只推进到当前分块的最大 UID，后块失败不会越过未提交 UID。反例：`test_failed_second_chunk_does_not_advance_past_it`、`test_first_chunk_failure_keeps_the_previous_cursor`。
3. **P1 SMTP 超时/取消后线程继续**：`TlsSmtpSender` 跟踪活动连接与阶段，超时/取消先关闭 socket 并等待线程退出（重复取消同样先回收再重抛），之后按阶段返回 `FAILED`/`UNKNOWN`。反例：`test_slow_trickle_respects_the_total_deadline`、`test_cancellation_closes_the_session`、`test_smtp_timeout_during_data_is_unknown_and_thread_stops`、`test_repeated_cancellation_still_reaps_the_thread`。
4. **P1 台账先发送后记录**：迁移 `0006` 改为六态 `PREPARED→EXECUTING→terminal` 单语句 CAS；执行器在建立 SMTP 连接前必须落 `PREPARED/EXECUTING`，发送后 `finalize` 失败向 Gateway 抛 `UNKNOWN`；终态禁止回退，`EXECUTING` 中断按 `UNKNOWN` 不重发。反例：`test_state_machine_never_regresses_and_is_idempotent`、`test_same_action_with_different_envelope_conflicts_atomically`、`test_concurrent_prepare_allows_exactly_one_digest`、`test_ledger_finalize_failure_is_unknown_not_success`、`test_interrupted_execution_never_resends`。
5. **P1 相同 Message-ID 覆盖正文**：逻辑身份改为同时绑定 Message-ID 与规范化内容哈希；相同 Message-ID 不同正文成为新消息并产生新事件。反例：`test_reused_message_id_with_new_body_is_a_new_logical_message`、`test_message_id_dedupe_binds_identity_and_content`。
6. **P1 草稿版本指针非原子**：草稿行、版本分配、版本插入与 `current_version` CAS 合并为单个宿主事务；每次编辑使用独立 revision nonce，相同正文/A→B→A 都产生新版本、新动作 ID 与 Message-ID；失败或取消删除刚创建的 MIME Artifact。反例：`test_identical_content_re_edit_is_still_a_new_version_and_action`、`test_revision_a_b_a_still_allocates_fresh_actions`、`test_failed_insert_removes_the_new_artifact`。
7. **P2 宿主台账与扩展对账分裂**：`MailHostCapability.reconcile_sent` 在唯一命中时通过 ledger CAS 把持久 `UNKNOWN` 提升为 `SUCCEEDED`（`SENT_RECONCILED`），失败返回 `UNAVAILABLE`；`PostgresMailDeliveryLedger` 全部转换为带状态守卫的单条 SQL。反例：`test_reconciliation_lifts_unknown_but_not_other_terminals`、`test_matched_reconciliation_updates_the_host_ledger`。
8. **P2 生产执行链未接通**：新增 Manifest 工具级 `capabilities` → `ToolDescriptor.required_capabilities` 桥（`core/extensions/descriptors.py`）、通用 `CapabilityRoutingExecutor` 与组合根 `Container.tool_registry`/`tool_gateway`/`refresh_tool_registry()`；public/admin lifespan 启动时发布持久 lifecycle 的 ENABLED 工具；`lifecycle_store` 的 manifest 序列化补齐工具 capabilities（修复安装后丢失路由声明）。真实组合根反例：`test_smail_worker_real.py::test_production_gateway_install_sync_draft_send_uninstall`（install→enable→sync→draft→R2 approval→SMTPSend→ledger→recover→uninstall，全部经 `build_container` 产物）。



#### 第二轮独立审计修复（2026-09-18，4×P1 + 3×P2，均先补反例）

1. **P1 Sent 对账未绑定宿主账本**：`host.mail.reconcile_sent` 现在先读账本，要求 `account_id`、`message_id` 严格匹配且状态为 `UNKNOWN`，否则 `MAIL_ACTION_UNKNOWN`/`MAIL_ACTION_BINDING_MISMATCH`/`UNAVAILABLE(MAIL_ACTION_NOT_UNKNOWN)` 且不查询邮箱；命中后必须由 `reconcile(account_id, message_id, ...)` 的 CAS 真正返回 `SUCCEEDED` 才向扩展报 `MATCHED`。反例：`test_wrong_account_or_message_binding_is_rejected`、`test_terminal_rows_are_not_reported_as_matched`、`test_unknown_local_action_is_rejected`、`test_reconciliation_requires_matching_account_and_message`（PostgreSQL 与内存两套）。
2. **P1 崩溃遗留 EXECUTING 永久卡死**：迁移 0006 增加 `owner_id`/`lease_expires_at`；`begin_execution` 领取有界租约并拒绝烧毁活跃 lease；`recover_stale_executions()` 在 public/admin 启动时把租约过期的 EXECUTING 原子转 `UNKNOWN`，之后可经 Sent 对账收敛，全程零重发。反例：`test_recovery_sweeps_only_expired_executions`、`test_second_begin_never_burns_a_live_lease`（PostgreSQL `LedgerGuardTests` 与内存 `test_mail_ledger_f06.py` 各一套）、`test_sent_reconciliation_matched_not_found_and_ambiguous`。
3. **P1 连接/TLS 阶段取消仍可能发送**：`_TrackedSmtp` 在 DNS/TCP 后、TLS 握手前注册原始 socket，`abort_requested` 在连接后、认证前/后、DATA 前检查；超时/取消关闭全部已注册 socket 并等待线程退出；线程已完成时真实 receipt 优先，`done` 不再被归为 `FAILED`。反例：`test_timeout_during_tls_handshake_fails_closed_and_reaps`、`test_cancellation_during_tls_handshake_closes_and_reaps`、`test_completed_send_wins_over_deadline_classification`。
4. **P1 账户换绑 TOCTOU**：执行器在物化前、物化 await 后、建立连接前三次复核注册表 fingerprint 与 `send_enabled`；物化期间禁用/换绑/删除账户会在 SMTP 前确定失败且不写 EXECUTING。反例：`test_account_disabled_during_materialization_blocks_dispatch`、`test_credential_rebind_during_materialization_blocks_dispatch`。
5. **P2 草稿 CAS 失败仍提交版本**：版本插入与指针 CAS 合并为单条 data-modifying CTE（`FOR UPDATE` + `NOT EXISTS`），CAS 不匹配时 SQL 零修改，不再产生孤儿版本。反例：`test_stale_cas_leaves_no_orphan_version`、`test_duplicate_revision_request_does_not_create_a_version`。
6. **P2 草稿准备非幂等**：`revision_request_id` 取 `InvocationContext.idempotency_key` 并加唯一索引；同键同内容重放返回原版本，同键异内容冲突；新建草稿在无显式 draft_id 时按请求 ID 派生稳定 ID。反例：`test_prepare_reply_replay_and_conflict`、`test_same_request_replay_returns_the_original_version`、`test_same_request_with_different_content_conflicts`。
7. **P2 文件注册表弱类型**：`read_enabled`/`send_enabled` 必须是真实 JSON boolean，字符串/数字/null 一律拒绝；`display_name`/`tls_mode` 也做类型与长度校验。反例：`tests/unit/test_mail_registry_f06.py::test_non_boolean_flags_are_rejected`。



#### 第三轮独立审计修复（2026-09-18，3×P1 + 5×P2，均先补反例）

1. **P1 凭据解析期间换绑**：注册表记录新增 `generation`，每次管理端写入递增；`verify(lease)` 复核 generation+fingerprint。Broker 只接受 `account_id`，在凭据 `await` 返回后、创建 socket 前原子复核，换绑/禁用/删除即 `MAIL_ACCOUNT_CHANGED`。反例：`test_rebind_during_credential_resolution_blocks_dispatch`（阻塞 `resolve_for_broker`，零 SMTP 连接）、`test_admin_edit_bumps_generation_and_invalidates_lease`。
2. **P1 租约过期不等于 owner 死亡**：发送期间心跳续租（`heartbeat` CAS owner+EXECUTING）；丢失租约使发送 guard 失效并在下一命令边界中止；恢复扫描接受 `active_owners` 且只在租约过期且 owner 非活跃本机代次时转 `UNKNOWN`；宿主启动与每次 Sent 对账前都做安全扫描，finalize 故障留下的 EXECUTING 可收敛。反例：`test_heartbeat_renews_only_the_owner`、`test_active_owner_is_never_swept`、`test_recovery_sweeps_only_expired_executions`、`test_second_begin_never_burns_a_live_lease`、`test_lost_lease_guard_aborts_before_data`。
3. **P1 扩展可伪造发送结果**：删除公开 `smail.record_send_result`；新增只读 `host.mail.delivery_status` 与 `smail.send_status`，投影 SQL 只允许 `PREPARED/UNKNOWN → 宿主状态` 且第一个终态不可覆盖。反例：`test_no_public_tool_can_forge_a_send_result`（契约）、`test_projection_mirrors_host_status_and_first_terminal_wins`、`test_projection_only_accepts_host_reported_status`（PostgreSQL）。
4. **P2 草稿取消清理不抗重复取消**：`_delete_artifact_shielded` 在二次取消下仍等待删除完成再重抛。反例：`test_repeated_cancellation_still_deletes_the_artifact`。
5. **P2 DNS 阶段无界**：DNS 解析移到发送线程之前并由同一总 deadline 约束，调用方绝不继续发送；但 `loop.getaddrinfo` 的阻塞系统解析线程无法被强制终止，可能比调用更晚结束，文档已如实修正（不再声称零线程残留）。guard 在 `_check_abort` 中生效。反例：`test_dns_stall_respects_deadline`、`test_dns_stall_cancellation_returns_without_thread`、`test_lost_lease_guard_aborts_before_data`。
6. **P2 手工账户文件重复 ID**：`_read_locked` 校验唯一性，重复 ID 直接拒绝。反例：`test_manual_file_with_duplicate_ids_is_rejected`。
7. **P2 草稿 CAS 非零修改**：草稿确保语句改为 `ON CONFLICT DO NOTHING`，stale CAS 不再触碰 `updated_at`。反例：`test_stale_cas_leaves_no_orphan_version`（PG，断言指针与版本数不变）。
8. **P2 幂等指纹未绑定 thread_id**：`canonical_digest` 纳入 `thread_id`；同键同正文换线程必须冲突。反例：`test_same_request_same_body_different_thread_conflicts`。

#### 第四轮审计修复（2026-09-18，3×P1 + 4×P2；反例已补，按用户要求本轮未自行执行测试）

1. **P1 心跳 owner 与账本 owner 不一致**：执行器在 `_begin` 之前生成唯一 owner 代次，并把同一个值用于账本领取、本机活跃登记、心跳与注销。反例：`OwnerWiringTests::test_executor_uses_one_owner_for_claim_registry_and_heartbeat`。
2. **P1 复核与建连之间 TOCTOU**：新增 `MailAccountDispatchGuard`，同进程写入经注册表 `subscribe` 立即失效，跨进程写入由 ≤2 秒 monitor 复核；SMTP 在连接、认证前/后与 DATA 前检查组合 guard（broker 增加可注入 `smtp_factory` 以便确定性反例）。反例：`test_rebind_after_reverify_invalidates_dispatch_guard`。
3. **P1 删除再重建使旧 generation 复活**：注册表持久化 revision tombstone，`_bump` 以历史最高 revision 为下界，删除后重建不会复用旧 generation/指纹。反例：`test_delete_then_recreate_does_not_reuse_generation`、`test_memory_registry_aba_is_monotonic`。
4. **P2 DNS 线程无法强制终止**：保留有界等待并在超时/取消时绝不继续发送，但删除“零线程残留”表述、如实记录系统解析线程可能更晚结束（代码注释与契约同步）。反例保持 `test_dns_stall_respects_deadline`、`test_dns_stall_cancellation_returns_without_thread`。
5. **P2 `send_status` 对 PREPARED/EXECUTING 报错**：宿主返回非终态时只回报宿主状态、不做终态投影。反例：`test_host_non_terminal_status_is_reported_without_projection`。
6. **P2 Admin PUT 返回陈旧 fingerprint**：写入后重新读取注册表并返回实际 generation/fingerprint。反例：`test_second_put_returns_stored_generation_and_fingerprint`。
7. **P2 文档残留已删除工具**：`TODO.md`/`NEXT_STEPS.md`/契约中的 `smail.record_send_result` 已清理为 `smail.send_status`，并明确该旧工具不存在。

**注意（如实声明）**：本轮按用户明确要求未自行运行任何测试/静态检查；上面新增的反例尚未由实现者执行。此前一次全量测量为 `./scripts/test.ps1` 722 passed、103 skipped，`./scripts/test-postgres.ps1` 103 passed，Ruff/Mypy（191 files）、`pip check`、`git diff --check` 通过；第四轮改动后的数字需由独立复验重新测量。

#### 第五轮独立复验修复（2026-09-18，2×P1 + 1×P2；反例已补，按用户要求本轮未自行执行测试）

1. **P1 跨进程 TOCTOU 仍存在**：删除“≤2 秒 monitor + 轮询”这一非正确性边界；`MailAccountDispatchGuard` 改为活体读注册表，读/解析失败即 fail closed；`FileMailAccountRegistry` 写者在跨进程 `ExclusiveFileLock`（`<registry>.lock`）内完成 read-modify-write，SMTP 在 DATA 提交前获取同一把锁并复读绑定，`docmd("DATA")` + payload + 最终回复全程持锁。语义为“修改先完成 → 发送以类型化 `MAIL_ACCOUNT_CHANGED` 中止”；“DATA 已开始 → 修改等待提交完成”。反例：`test_cross_process_registry_rebind_aborts_before_data`（两个 `FileMailAccountRegistry` 实例 + 真实 `TlsSmtpSender` + 受控 SMTP，在 RCPT 窗口换绑，0 封邮件）、`test_cross_process_writer_waits_while_data_is_in_flight`（DATA 期间写者阻塞，提交成功后账号才换绑）、`test_rebind_after_reverify_aborts_before_data`、`test_dispatch_guard_reads_live_and_fails_closed`、`test_memory_dispatch_guard_reads_live_state`。
2. **P1 monitor/心跳读失败 fail-open**：删除 broker 轮询 monitor 与 `subscribe`；心跳循环改为维护本地 `valid_until`（续租成功才推进；数据库异常只在未过期前容忍；到期或 `heartbeat` 返回 False 立即失效 guard）。反例：`HeartbeatDeadlineTests::test_continuous_heartbeat_failures_expire_the_local_lease`、`test_transient_heartbeat_failures_keep_the_lease_until_renewal`。
3. **P2 反例未验证真实错误语义**：删除 `_GuardSpySender` 伪反例，改用真实 `TlsSmtpSender` 与受控 SMTP；真实 sender 在账户 guard 失效时抛出类型化 `MailError(ACCOUNT_CHANGED)`（租约丢失仍返回 FAILED/UNKNOWN 回执）。测试支持新增 `SmtpState.rcpt_delay_seconds`/`data_delay_seconds`/`rcpt_seen`/`data_seen` 用于确定性开窗。

**注意（第五轮，如实声明）**：本轮同样未由实现者运行任何测试/静态检查；审计方在第四轮修复后测得 F06 非数据库/Worker 目标集 131 passed、`./scripts/test.ps1` 728 passed、103 skipped，Ruff/Mypy（191 files）通过；第五轮改动后的数字需由独立复验重新测量（含真实 PostgreSQL 与真实 Worker 集合）。

#### 第六轮独立复验修复（2026-09-18，2×P1 + 2×P2；反例已补，实现者已运行静态验收命令）

1. **P1 心跳挂起时本地租约永不过期**：`_LeaseGuard` 改为单调 deadline 权威（`valid` 实时计算、`renew` 只在未过期时推进、`invalidate`/`renew` 由线程锁串行化）；每次 `ledger.heartbeat` 调用由 `asyncio.timeout_at` 以剩余租约为界。反例：`test_hung_heartbeat_cannot_outlive_the_local_lease`、`test_heartbeat_interval_larger_than_lease_fails_closed`，另有既有连续失败/瞬断容忍两条。
2. **P1 组合 guard 在锁等待期间丢失租约仍进入 DATA**：`CompositeMailGuard` 先取得账户锁，再以单一总 monotonic deadline 取得其余临界区，并在全部锁内复核所有 guard；失败时释放已持有的锁。反例：`CompositeGuardTests::test_lease_lost_while_waiting_for_the_account_lock_blocks_data`（单元）与 `test_lease_lost_while_waiting_for_the_account_lock_never_sends`（真实 `TlsSmtpSender` + 受控 SMTP，断言 FAILED 且 0 封邮件）。
3. **P2 强制静态检查未通过**：`file_lock.py` 修正 `__exit__` 签名与平台锁导入（`importlib`，避免 Windows 类型检查解析 `fcntl`）；`registry.py` 合并三处嵌套 `with`；`broker.py` 用类型化 `SmtpSenderFactory`/`SmtpSenderPort` Protocol 取代 `object`。`./scripts/test.ps1` → 739 passed、103 skipped、Ruff `All checks passed!`、Mypy `Success: no issues found in 192 source files`。
4. **P2 注册表异步写阻塞事件循环**：`FileMailAccountRegistry` 的 `upsert`/`replace_all`/`delete` 完整 read-modify-write 移入受控工作线程（`asyncio.to_thread`），同一跨进程锁覆盖整个操作，Admin 事件循环不再被文件锁等待或文件 I/O 阻塞。反例：`test_file_registry_writes_run_off_the_event_loop`。

**注意（第六轮历史记录）**：该轮实现者当时只运行了静态验收命令；之后的独立复验已补跑 `./scripts/test-postgres.ps1` 与真实 Worker 集合，最终结果见本节末尾。

#### 第六轮复现补充修复（2026-09-18，1×P1 + 1×P2；实现者已运行静态验收）

- **P1 本地 guard 在 `_begin()` 后重启完整租期**：guard 的 deadline 现在锚定数据库领取时刻——`claim_started` 在 `_begin` 调用前取得并传入 `_LeaseGuard(started_at=...)`，心跳续租同样锚定 `heartbeat` 调用起点；若领取后的复核工作已耗尽租期，执行器在创建 guard 后立即拒绝派发（抛 `OutcomeUnknownError`，绝不开 SMTP，`EXECUTING` 行留给恢复/对账）。反例：`test_guard_deadline_is_anchored_to_the_claim_origin`、`test_post_claim_delay_never_dispatches_with_an_expired_lease`（慢复核超过租期：Gateway 结果 `OUTCOME_UNKNOWN`、broker 0 次调用、账本保持 `EXECUTING`）。注：独立审计的该 P1 复现快照为该修复提交之前（审计记录 `./scripts/test.ps1` 739 passed，修复后为 742）。
- **P2 取消注册表写入后后台线程仍提交**：`FileMailAccountRegistry` 的写操作改用项目既有取消安全模式 `run_blocking`（取消时继续 shield 等待工作线程、消费终态后才重抛 `CancelledError`，重复取消不中断回收），调用方不再可能在后台换绑落地前观察到取消。反例：`test_cancelled_write_waits_for_the_background_commit`（持锁阻塞写线程→取消→任务保持未完成→释放锁→提交完成后才抛 `CancelledError`，落盘状态证明无取消后写入窗口）。
- 最终独立验收：`./scripts/test.ps1` → 742 passed、103 skipped、Ruff `All checks passed!`、Mypy 192 files 通过；`./scripts/test-postgres.ps1` → 103 passed（真实 PostgreSQL、personal_knowledge Worker 与 smail 组合根 Worker）；`pip check`、`git diff --check` 与 wheel 内容核验通过，测试后无残留 Python Worker。

#### 受控真实只读同步（2026-09-19，用户实时参与；F06 保持 DONE）

- **方式**：新增一次性本机回环页面 `scripts/mail_real_readonly.py`（仅监听 `127.0.0.1`，页面输入邮箱地址与客户端专用密码；密码只进入该进程内存的 `InMemorySecretStore`，不落盘、不入库、不进日志；页面含显式安装/只读确认）。安装并启用仓库内 `nju.smail`（真实 venv/Worker/Supervisor），经生产 `build_container` + 真实 PostgreSQL 执行 `smail.sync`，全程不调用任何发送工具、`PA_MAIL_SEND_ENABLED=false`。
- **结果**：真实 `imap.exmail.qq.com:993`（TLSv1.3，QQ Exmail）只读同步成功；全部可读文件夹（INBOX/Drafts/Deleted Messages/Junk/Sent Messages）合计 **15 封邮件 / 15 个事件**（INBOX 13 + Sent Messages 2，其余 0），第二次同步 0 新（幂等）；`mail_sync_state` 各文件夹 `IDLE`；`mail_send_actions` 为 0；扩展 `ENABLED`。证据：`var/realtest/mail-readonly-*.json`（含邮箱掩码、逐文件夹结果与库内计数；真实地址不进入 Git）。服务器侧独立确认：`EXAMINE INBOX` 返回 `OK 13`、`UIDVALIDITY 1789818241`；对 `&UXZO1mWHTvZZOQ-`（“其他文件夹”）的 `ref-star`/`ref-pct`/`prefix-star` 三种 LIST 探测均无子文件夹，证明该账号 IMAP 可见邮件就是这 15 封。
- **只读命令序列**（逐步诊断实测）：`connect → CAPABILITY → AUTHENTICATE PLAIN → CAPABILITY → ID → EXAMINE/SELECT(readonly) → UIDVALIDITY`，无任何 `STORE/EXPUNGE/COPY/MOVE/APPEND`。
- **过程中发现并修复的 4 个真实互操作缺陷**（均先复现、后最小修复、再加回归测试）：
  1. **非 ASCII 安装路径**：`VenvArtifactInstaller._venv_purelib` 用 UTF-8 解码子进程 stdout，而 Windows 中文路径按本地代码页输出，导致 `INSTALL_FAILED`。修复为让子进程显式写 UTF-8 字节；回归 `tests/integration/test_extension_non_ascii_install_path.py`。
  2. **宿主写帧越过 worker 输入上限**：worker 输入上限 1 MiB，宿主原按自己的 4 MiB 上限写入，超限帧被 worker 回 `-32700 (id=None)`，宿主误报为 “id mismatch” 并隔离扩展。新增 `WORKER_INPUT_FRAME_BYTES`、宿主出站帧限制与 typed `DATA_RESULT_TOO_LARGE`、`id=None` 精确诊断；回归 `tests/unit/test_extension_rpc_robustness.py::test_oversized_host_result_never_breaks_the_worker_stream`、`test_idless_worker_protocol_error_is_not_an_id_mismatch`。
  3. **QQ Exmail 需要 RFC 2971 `ID`**：未识别客户端时 `SELECT` 被拒（“Unsafe Login”）。客户端现在仅在服务器声明 `ID` 时于认证后发送无秘密的客户端标识；回归 `tests/integration/test_mail_protocol_f06.py::test_client_identifies_itself_when_the_server_requires_id`（模拟服务器可要求 ID）。
  4. **`host.mail.fetch` 响应超帧**：每封邮件原始 MIME 全量 base64 会超过 1 MiB 帧；宿主现在按 512 KiB 原始字节预算分批（超预算的单封截断并标记 `truncated`，返回 `batch_limited`），扩展保留真实宿主错误码不再伪装成 `MAIL_UNAVAILABLE`；回归 `tests/unit/test_mail_host_fetch_budget_f06.py` 与 `tests/unit/test_smail_sync_f06.py::HostErrorCodeTests`。
  5. **文件夹枚举不完整**：`\NoSelect` 属性检查改为大小写不敏感，并对带 `\HasChildren` 的容器执行一次只读引用 LIST 枚举子文件夹（QQ 的“其他文件夹”）；回归 `tests/integration/test_mail_protocol_f06.py::test_no_select_attribute_is_case_insensitive`、`test_child_folders_of_a_container_are_discovered`。
- **如实声明（未完成部分）**：本次是**受控只读**验收：未发送任何邮件；客户端专用密码只存在于该次进程内存（F09 未实现，生产凭据后端仍 fail-closed），因此应用内持续同步仍需 F09 后重新绑定同一 `account_id`；15 封中 1 封因超过 512 KiB 批次预算被截断存储（`truncated=true`，内容前缀入库），如需完整大邮件内容需要后续的单封分页取回能力；未做真实 SMTP 发送验收。

- **F06 已通过独立验收并标记 `DONE`；F07 提升为唯一 `NEXT`。**

### F07 — ehall 扩展（IN_PROGRESS，修复与真实提交前验收未完成）

范围：有头 Playwright + Desktop Companion；用户亲自完成 SSO/验证码/扫码；只读枚举后选择一个低风险事项；填写至提交前预览。

能力边界：首版只支持一个经确认的低风险事务。R3 事务永远禁止。

验收：登录挑战不被绕过；最终字段/后果在 Android 预览确认；真实低风险提交（若启用）有独立审批和回执；DOM 变化安全失败。

失败红线：退课/撤回/付款/选课变更可执行；隐藏浏览器提交；结果不明自动重复点击。

禁区：逆向私有 XHR、绕过验证码、保存会话 cookie 到日志/模型。

#### 交付与证据（2026-09-19，等待独立验收，未标 DONE）

- **核心契约（F07 contract v1.7，见 `CONTRACTS_AND_INTERFACES.md` 第 16 节）**：`core/browser` 定义供应商无关的会话状态机（`REQUESTED → WAITING_USER → AUTHENTICATED → DISCOVERED → PREPARING → PREVIEW_READY [→ APPROVED → EXECUTING → SUCCEEDED|FAILED|UNKNOWN]`，另有 `CANCELLED`/`SAFETY_PAUSED`）、origin/路径策略（仅显式 allow-list 的 https origin，拒绝 userinfo、非白名单端口/子域、编码 `..`、反斜杠、协议相对与开放重定向、`javascript:`/`data:`/`file:`）、永久 R3 分类目录（退课/退学、撤销/撤回、支付/缴费/退费、选课变更、法律声明、违约金等，含中英文词表）、页面 fingerprint（仅结构化控件/标题/链接，值不参与，值变化算字段漂移）、canonical 结构化预览（字段顺序与附件顺序不敏感；值、来源、证据哈希、附件哈希、页面版本、事务、适配器/扩展版本任一变化都改变摘要；nonce/时间不参与摘要）、`DesktopBrowserPort`、`BrowserSessionStore`/`BrowserAdapterStore` 与 `BrowserSessionBroker`。会话上限：每任务 1 个活动会话、每路径 3 次重导航、快照 ≤1 MiB、字段 ≤128、附件 ≤20、命令 deadline 30 秒。
- **Desktop Companion**：`infrastructure/browser/{driver,companion,companion_app,companion_client,companion_entry}.py`。唯一导入 Playwright 的模块是 `driver.py`；浏览器强制 `headless=False`、每次请求按 origin 拦截（非白名单请求被 abort 并计数）、fill 阶段阻断全部非 GET/HEAD/OPTIONS（页面无法在填写时自动保存）、不使用 `page.evaluate`、不导出 cookie/storage state、不截图；Companion 只绑定 `127.0.0.1`，root capability 与每会话 capability 都是 `secrets.token_urlsafe(32)`（256 bit），绑定会话与用途、TTL 10 分钟、可撤销、仅内存保存；Companion 进程重启后 capability 立即失效。关闭先走 Playwright 有界关闭（5 秒）升级到停止 driver。
- **宿主执行与 Tool Gateway**：`BrowserActionExecutor` 只执行声明 `browser.fill`/`browser.submit` 的工具；`browser.fill` 在执行前重新读取页面并核对 origin/fingerprint/旧值/未知字段/必填/模式/选项/风险，任一不符即 0 次页面写入并进入 `SAFETY_PAUSED`；获批后填写并生成权威预览（PREVIEW_READY），不做提交。`browser.submit` 需要新的 R2 审批，只点击适配器声明的唯一 final action，成功必须拿到回执；结果不明一律 `UNKNOWN`，不自动重试，只能经只读流程跟踪 `ehall.reconcile` 收敛。
- **扩展 `extensions/nju_ehall`**：工具 `ehall.discover_apps`(READ)/`ehall.inspect_transaction`(READ)/`ehall.prepare_preview`(INTERNAL_WRITE)/`ehall.fill_form`(EXTERNAL_WRITE, capability `browser.fill`)/`ehall.submit`(EXTERNAL_WRITE, capability `browser.submit`)/`ehall.reconcile`(READ)；槽位 Context/Workflow/Form/Migration；扩展自有迁移 `migrations/0001_ehall.sql`（`ehall_transactions`/`ehall_actions`，位于 `ext_nju_2e_ehall`）。扩展只导入 `personal_assistant_sdk`，不建立任何网络连接，不接触 cookie/密码/验证码；适配器定位符为结构性索引（`ctl:<group>:<index>`/`act:<index>`），不含自由 CSS。
- **迁移与配置**：核心新增 `0007_f07_browser_sessions.sql`（`browser_sessions` + `browser_adapters`，含 `visited_paths`、preview JSON、UNKNOWN/owner 恢复字段），`0001`–`0006` 逐字节未改（F07 契约测试锁定 SHA-256）。新增 `PA_BROWSER_COMPANION_URL`（必须 loopback）、`PA_BROWSER_ALLOWED_ORIGINS`（默认空 = 拒绝一切会话）、`PA_BROWSER_SUBMIT_ENABLED`（默认 false）；capability 只经 `PA_BROWSER_COMPANION_CAPABILITY` 进程内存传入，不写入 Settings/Settings repr。启动恢复把崩溃遗留的 `EXECUTING` 会话原子转为 `UNKNOWN`，绝不自动恢复点击。
- **Gate 1 反例（定向集合 196 collected）**：URL/origin 绕过 24 例（http、`javascript:`、`data:`、`file:`、`blob:`、userinfo、非白名单端口、子域、后缀混淆、`%40` 主机、`..`/`%2e%2e`/`%252e%252e`、反斜杠、`url/next/target/redirect_uri` 开放重定向、端口 0、控制字符、超长 URL、路径不允许、空 allow-list）；R3/未知风险 14 例；fingerprint 漂移 13 例；预览绑定 24 例；会话状态机（含 WAITING_USER 10 例、重导航上限、快照/填写全部拒绝）41 例；Gateway/R2 审批（占位→批准→漂移烧毁→过期→重放→UNKNOWN→对账→R3 拒绝）16 例；扩展业务逻辑（含 prohibited 发现、材料缺口、reconcile MATCHED/NOT_FOUND）20 例。全部通过。
- **Gate 2 真实浏览器（`test_browser_companion_real_f07.py`，初版 13 passed；审计修复后 21 passed）**：真实 `headless=False` Chromium（本地 HTTPS mock 站点，绝不连接真实 ehall）。证据覆盖：登录挑战进入 `WAITING_USER` 且 `headless=False`/`browser_alive`；用户经 CDP 亲自输入并点击登录（系统 0 次输入密码）；读只应用列表并按 R3 分类；填写到 `PREVIEW_READY` 且 0 次提交；autosave 页面在 fill 期间到达服务器的请求数为 0；未知字段/页面版本漂移/高风险文字均 0 次填写并 `SAFETY_PAUSED`；非白名单 origin 导航被拒且页内非白名单子资源被 abort 计数；登录 cookie/密码从未出现在宿主会话记录、宿主能力响应、Companion 诊断、Settings 或临时文件中；取消与重复取消后 Chromium 进程被回收（CDP 进程 id 轮询归零，修复后连续 5 次通过）；Companion 重启使旧 capability 失效；提交开关关闭时 0 次点击；响应丢失（504）→ `UNKNOWN`、0 次重试、宿主只读流程跟踪命中回执后经 `host.browser.reconcile` 收敛为 `SUCCEEDED`。审计修复后新增：live fingerprint 与人工核验常量一致、`?note=1`/`?extra=1` 与加载期 action 改写按 `UNKNOWN_PAGE_VERSION` 拒绝、显式固定变体后未知字段仍 `UNKNOWN_FIELD`、超大正文 `PAGE_TEXT_SCAN_INCOMPLETE`、0.5/1.5/2.5 秒延迟 autosave 与填写后读取均 0 次到达、提交期 action 改写 0 次写入、脚本二次 POST 恰 1 次提交且 ≥1 次 abort、点击前已存在回执不会被误报为 MATCHED。
- **Gate 3/5 真实 Worker 链（`test_ehall_worker_real.py`，4 passed）**：真实 PostgreSQL + 每版本 venv + 真实 Worker 子进程 + 真实 Supervisor + 生产 Gateway：独立 zip 制品 install → enable → discover（R3 应用被标记）→ inspect → prepare_preview（扩展迁移在其自有 Schema 执行）→ R2 审批 → `fill_form`（PREVIEW_READY）→ R2 审批 → `submit`（回执入库）→ UNKNOWN 提交 0 重试 + 只读 reconcile MATCHED；disable 后调用被拒、enable 恢复；uninstall 删除代码但保留 `ext_nju_2e_ehall` 数据。
- **PostgreSQL（`test_postgres_f07.py`，8 passed）**：迁移建表；预览、nonce、`PREVIEW_READY`/`SAFETY_PAUSED` 状态跨连接池重建恢复；`EXECUTING` 在重启后按 owner 扫描转 `UNKNOWN`（活跃 owner 不被清扫）；并发保存 CAS 恰好一个赢家；适配器描述符跨重启保留且 upsert 版本递增；内存实现与 PostgreSQL 状态机一致。
- **契约扫描（`test_f07_contract_consistency.py`）**：core/domain 不导入 Playwright/Win32/httpx/uvicorn；只有 `infrastructure/browser/driver.py` 导入 Playwright；扩展只导入公开 SDK、无 socket/httpx/SMTP/IMAP；driver 不使用 `page.evaluate`、无 `headless=True`、src 中无 `storage_state`/cookie 导出；R3 词表与默认关闭配置存在；`0001`–`0006` 字节未变、0007 打包；文档包含 F07。
- **命令证据（2026-09-19，本机 Windows + 已缓存 Chromium 1234）**：
  - F07 定向集合（单元 + 契约，`test_browser_policy_f07`/`test_browser_risk_f07`/`test_browser_preview_f07`/`test_browser_fingerprint_f07`/`test_browser_session_broker_f07`/`test_browser_executor_f07`/`test_ehall_extension_f07`/`test_f07_contract_consistency`）→ **197 collected / 197 passed**。
  - 真实浏览器集合 `tests/integration/test_browser_companion_real_f07.py` → **13 passed**（`headless=False` Chromium、本地 HTTPS mock）。
  - `./scripts/test.ps1` → **952 passed、115 skipped**，Ruff `All checks passed!`，Mypy `Success: no issues found in 214 source files`。
  - `./scripts/test-postgres.ps1` → **115 passed**（真实 PostgreSQL 17 + pgvector、personal_knowledge Worker、smail 组合根 Worker、F07 store/恢复、`nju_ehall` 真实 venv/Worker/Supervisor/Gateway）。
  - `python -m pip check` → `No broken requirements found.`；`git diff --check` 干净（仅 autocrlf 提示）；测试后 `chrome.exe`、`chrome-headless-shell.exe`、`node.exe` 与 personal-assistant/companion Python 进程计数均为 0。
  - 临时目录 wheel：`personal_assistant_framework-0.1.0-py3-none-any.whl`，初版 **208 个条目**（审计复核修正文档原写的 207）；审计修复后重新构建为 **209 个条目**，含 `core/browser/*`、`infrastructure/browser/*`、`infrastructure/database/browser_store.py`、`infrastructure/memory/browser.py`、SDK `host.py`、`share/personal-assistant/migrations/0007_f07_browser_sessions.sql` 与 `0008_f07_browser_session_uniqueness.sql`，且不含任何 `nju_ehall`/`extensions/` 条目。
  - 迁移 SHA-256：`0007_f07_browser_sessions.sql` = `b50a94f3ea627157ea347dc56f38c23ef59b07e420bdba36adf867297f785005`；`extensions/nju_ehall/migrations/0001_ehall.sql` = `3af6485cd3f9614b031af1922cf6f299f1dc3d58e87ed1d71a300ad8c76e3a9e`；`0001`–`0006` 与 F06 报告一致、未改动（契约测试锁定）。
- **独立审计（2026-09-20）：未通过**。审计报告判定 4 个 P1（延迟 autosave 绕过、提交未绑定真实网络目标、未知页面版本未按 R3 处理/正文扫描不完整按无风险处理、capability 经异常链泄漏）、7 个 P2（真实 URL 未逐次复核、回执可误报、每任务单会话非原子、`expires_at` 未执行、对账由扩展声明、双重编码开放重定向、文档陈旧）以及 `test_cancel_reaps_the_browser_process` 失败。F07 保持 `IN_PROGRESS`。
- **审计修复（2026-09-21，见 `CONTRACTS_AND_INTERFACES.md` 第 16.8 节）**：driver 写请求改为持久 `_mutations_blocked` + 提交临界区单条 `_submit_allowance`（只放行一条精确匹配 method/origin/path 的写请求，延迟 autosave/二次 POST/动态改写 action 全部 abort 并计数）；`AdapterActionSpec` 增加 `method`/`target_origin`/`target_path` 并进入预览与 canonical hash（R2 审批绑定），`execute_submit` 复核预览目标与当前 final action 一致；`TransactionAdapterDescriptor.allowed_page_fingerprints` 必填，交易路径未命中即 `UNKNOWN_PAGE_VERSION`/R3 + `SAFETY_PAUSED`，`scan_incomplete`（正文读取失败或 >65536 字符）即 `PAGE_TEXT_SCAN_INCOMPLETE` + `SAFETY_PAUSED`，fingerprint 纳入表单 `action`/`method`/`id`/`name`；companion 客户端错误路径清除 token/headers/body/response 局部引用且不再 `raise ... from exc`；回执要求点击前后差异 + `receipt_pattern`；迁移 `0008_f07_browser_session_uniqueness.sql` 以部分唯一索引原子保证每任务单活动会话并新增 `receipt_baseline`（对账扣除点击前回执）；`expires_at` 在读取时强制执行并释放任务槽位；对账改为宿主驱动 `host.browser.reconcile`（`record_reconciliation` 从 host/SDK/扩展移除）；开放重定向覆盖双重编码；driver 轮询新 Chromium PID 修复回收测试。
- **第二轮独立审计（2026-09-21）未通过**：6 个 P1（导航动作可放行未审批写请求、提交目标未绑定 query、DOM 文本可伪造成功、对账基线取错页面、UNKNOWN 被 TTL 错误释放、`transcript.json` 未接入且发现流程不识别 JS 按钮）与 5 个 P2（0008 升级不兼容重复数据、5xx 路径保留 response、PID 回收稳定失败、文档矛盾、0008 SHA 未登记）。修复与冻结语义见 `CONTRACTS_AND_INTERFACES.md` 第 16.10 节。
- **第五轮独立审计（2026-09-24）未通过**：2 个 P1（隐藏控件值可能经 Companion/Host RPC 外泄、对账在导航与采集之间的窗口内仍可能从被重定向页面生成证明且忽略 query）与 2 个 P2（`_quarantine` 同版本并发覆盖窗口、payload 校验会误伤带静态 hidden/CSRF 字段的正常表单）。修复与冻结语义见 `CONTRACTS_AND_INTERFACES.md` 第 16.13 节。
- **第五轮修复后历史快照（2026-09-24，已被后续复验取代）**：当时记录 `test.ps1` 1104 passed/123 skipped、Chromium 35 passed、PostgreSQL 123 passed；随后独立复验发现 CAS 错列和 successful-controls 模板缺口，因此这些结果不能作为当前验收结论。
- **第五轮复验阻断项修复（2026-09-24）**：PostgreSQL 条件更新改用 `active_version` 匹配包版本；`_quarantine` CAS 失败时重读 durable row，并仅在最新记录仍为 `ENABLED` 时恢复对应 Registry（同版本反例覆盖）。浏览器实际 body 模板改按 successful controls 规则：未选 checkbox/radio 与 disabled 控件省略，选中 checkbox/radio 使用实际 HTML value/缺省 `on`；真实 body 哈希与语义批准字段哈希分开核验。定向复验命令（F02 PostgreSQL CAS、Supervisor 单测、driver 路由单测、两个真实 Chromium 表单场景）通过。
- **历史失败快照（2026-09-24，已被后续复验取代）**：无浏览器 `scripts/test.ps1` 为 `1068 passed, 161 skipped, 5 failed`（既有邮件/RPC/lease 短时限测试）；真实 PostgreSQL/Worker 为 `123 passed, 1 failed`（F05 `test_cancelled_build_leaves_no_building_rows` 等待 embedder 启动超时）；真实 Chromium 全量尝试未完成。此快照不能作为当前验收结论。
- **当前复验（2026-09-26）**：`./scripts/test.ps1` → **1110 passed、124 skipped**（真实 headed Chromium 包含 checkbox/radio/disabled 两个新增表单场景），Ruff `All checks passed!`，Mypy `Success: no issues found in 214 source files`；`./scripts/test-postgres.ps1` → **124 passed**（真实 PostgreSQL + Worker，含包版本 `0.1.0` 与 manifest 格式版本 `1` 不同的 CAS 反例）；`python -m pip check` → `No broken requirements found.`；`git diff --check` 退出码 0（仅 LF/CRLF 提示）。测试全绿不替代真实 ehall 交易验收：真实表单、真实适配器指纹、填写至预览和提交仍 `NOT_RUN`，F07 保持 `IN_PROGRESS`，推荐下一步仍是用户参与的低风险真实交易验收与独立复审。
- **跨域真实验收增量（2026-09-26）**：用户报告具体事务在新标签页跳转至 `ehallapp.nju.edu.cn`，旧 `capture` 的白名单拦截该域名且 `CURRENT` 只读取原大厅标签页。按用户最新要求新增可手动选择的 `PA_BROWSER_ORIGIN_MODE=allowlist|open`（默认 `open`）与采集 CLI `--origin-mode`（默认 `open`）；开放模式免 origin 白名单但保留 HTTPS/URL 结构、R3、写请求阻断和 R2 绑定。采集器只读采用唯一新标签页，报告去除动态查询参数和当前控件值，带动态参数的页面不直接重放预览。先写的默认模式测试按预期失败，修改后通过；白名单场景在测试夹具中显式选择 `allowlist`。`./scripts/test.ps1` → **1119 passed、124 skipped**，Ruff `All checks passed!`，Mypy `Success: no issues found in 214 source files`；`./scripts/test-postgres.ps1` → **124 passed**；`python -m pip check` → `No broken requirements found.`；`git diff --check` 退出码 0（仅 LF/CRLF 提示）。可用的真实事务页仍未成功采集，填写至预览和真实提交仍未运行，F07 继续 `IN_PROGRESS`。
- **跨域真实采集复现（2026-09-26，未通过）**：用户在采集器弹出的 Chromium 点开具体事务后，事务页显示“网络错误”；同一事务从普通 Chrome/Edge 大厅进入可正常打开。用户在错误页写入 `CURRENT` 后，`ehall_capture.json` 显示 `origin_mode=open`，捕获到 `ehallapp.nju.edu.cn` 的 `/jwapp/sys/cjcx/*default/index.do#/cjcx`（报告已删除动态查询），`blocked_origin_requests=0`、`blocked_mutating_requests=21`，捕获页有 0 个控件、0 个表单、0 个动作。可排除旧 origin 白名单阻断，非 GET 请求拦截是当前最强的原因线索；计数为会话累计，尚未定位每个请求的方法、目标与只读语义。错误页 fingerprint 不得用作真实事务 adapter 版本。继续保持 F07 `IN_PROGRESS`；下一步需核验事务应用用于读取的请求，再决定能否增加精确限定的只读入口，不能直接放开全部 POST。
- **跨域请求诊断补强与真实复测（2026-09-26，未通过）**：针对用户从 Network 看到的事务 XHR `(failed) net::ERR_FAILED`，增加最多 32 种被拦截非 GET 请求的脱敏摘要（方法、资源类型、静态 `.do`/`.json` 端点名、路径哈希前缀、次数），由 Companion diagnostics 传至只读采集报告；不得记录请求头、Referer、body、原始路径或完整 URL。单元反例覆盖会话查询值/请求体不泄漏及摘要上界；真实 Chromium 延迟 autosave 回归证明 Companion 透传且仍阻断写入。`./scripts/test.ps1` → **1122 passed、124 skipped**，Ruff `All checks passed!`，Mypy `Success: no issues found in 214 source files`；PostgreSQL/Worker **124 passed** 是诊断补强前的结果，本次只改浏览器与采集脚本，未重跑该独立套件。用户重新运行 `capture` 后，报告显示 `origin_mode=open`、来源拦截 **0**、非 GET 拦截 **30**（会话累计）、**15** 种摘要且未截断；样本均为 POST，含 `queryTableConfig.do`、`cxjwggbbdqx.do`、`cxxscjd.do` 等查询型名称，也含 `setJwCommonAppRole.do` 这类疑似写入名称。报告捕获的最后一页 `/jwapp/sys/bylwnjdx/*default/index.do#/xskt` 仍无控件、表单或动作；会话计数不能逐项归因于最后一页。此更改只增强诊断，不改变非 GET 阻断或 R2 审批。接口语义未核验前不得扩大放行，F07 保持 `IN_PROGRESS`。
- **直接交互办理入口（2026-09-26，真实站点待验收）**：按用户新授权新增 `assistantctl ehall`。该入口在本机可见 Chromium 中按 `办理 事务名；填写 字段=值；提交按钮=按钮文字` 接收指令，等待用户完成 SSO，点击门户事项并跟随新标签页，按可见控件标签填写；独立本机点击前确认页列出脚本记录的填充值，只有用户点击“确认提交到 ehall”才点击真实页面提交按钮，取消则不点击。浏览器正常发送页面的后台 POST，不安装请求拦截器或 origin 白名单；它与原 Desktop Companion/Tool Gateway 受监督链路并存。新增单元与真实 Chromium 本地 HTTP 站点反例，覆盖后台 POST 成功、确认前 0 次指定按钮点击、确认后 1 次、取消后 0 次以及字段歧义拒绝。初次全量发现新增 CLI 违反 `tests/contract/test_extension_independence.py` 的依赖边界、新增按钮查找误用异步生成式，均修复且定向 8/8 通过。随后 `./scripts/test.ps1` **1127 passed、124 skipped**（Ruff `All checks passed!`，Mypy `Success: no issues found in 216 source files`）；`.venv-win/Scripts/python.exe -m pip check` 为 `No broken requirements found.`，`git diff --check` 退出码 0（仅 LF/CRLF 提示）。真实 ehall 事务页自动打开、非标准控件、站点回执仍 `NOT_RUN`，因此 F07 保持 `IN_PROGRESS`。
- **登录后大厅未自动打开事务（2026-09-26，待真实重测）**：用户确认运行新入口后登录成功，但事务“成绩查询”不在大厅首页可见区域，原 `open_service` 只在首页直接查找文字，故一直等待。已新增“首页 → 全部服务 → 事务卡片”和“首页 → 全部服务 → 搜索 → 事务卡片”两个先失败后通过的真实 Chromium 本地站点测试；入口现在查看所有标签页与框架，首页找不到时点击“全部服务”，再搜索，并每 10 秒输出查找进度。`tests/integration/test_ehall_interactive_browser_f07.py` 4 passed；`./scripts/test.ps1` 为 **1129 passed / 124 skipped**，Ruff、Mypy 通过，`git diff --check` 退出码 0。真实 ehall 重测仍为 `NOT_RUN`，F07 保持 `IN_PROGRESS`。
- **SSO 导航期间 frame 被移除（2026-09-27，待真实重测）**：用户运行新 `assistantctl ehall` 时，在尚未完成登录前得到 `Locator.count: Frame was detached`。原因是事务扫描持有旧登录 iframe，SSO 导航移除该 frame 后 Playwright 查询抛错，进程提前退出。新增先失败后通过的定向测试，模拟旧 iframe 消失而新 frame 中存在事务卡片；现在仅对 Playwright 的 frame detach/执行上下文销毁错误跳过当前 frame 并继续轮询，其他错误继续抛出。`pytest tests/unit/test_ehall_interactive_f07.py tests/integration/test_ehall_interactive_browser_f07.py -q` **8 passed**；`./scripts/test.ps1` **1130 passed / 124 skipped**，Ruff、Mypy 通过，`git diff --check` 退出码 0。真实 ehall 登录与事务页仍待重测，F07 保持 `IN_PROGRESS`。
- **真实事务页打开验证（2026-09-27，用户实测通过）**：用户重新运行 `.venv-win\Scripts\python.exe -m personal_assistant.cli.main ehall "打开 成绩查询"`，确认新标签页正常显示“成绩查询”事务内容，且没有“网络错误”。这证明本机手工验收入口已能在该次会话中完成 SSO 后打开一个真实 ehall 事务页；证据是用户交互反馈，未保存含动态查询参数的 URL、cookie 或页面内容。此结果仅覆盖“打开并正常加载”验证点；当时真实字段填写、提交前预览、提交与回执尚未运行（后续部分试填见下文），F07 保持 `IN_PROGRESS`。
- **教室临时借用试填入口（2026-09-27，真实页面待运行）**：按用户指定的“教室借用-临时借用”增加 `assistantctl ehall --sample`。入口读取可见标准控件，只为可唯一定位且原本空白的事由/用途/理由/说明/备注/主题文本框及人数字段生成固定测试值；其他控件仅列名称与类型，不打印现有值。试填后保持真实页面供用户检查，不进入确认提交分支。单元测试覆盖跳过联系方式、教室下拉与已有值；真实 headed Chromium 本地站点测试验证测试值进入表单且最终提交请求为 0；入口测试验证 `confirm_and_submit` 未调用。`pytest tests/unit/test_ehall_interactive_f07.py tests/integration/test_ehall_interactive_browser_f07.py -q` **11 passed**；`./scripts/test.ps1` **1133 passed / 124 skipped**，Ruff、Mypy 通过；`git diff --check` 退出码 0。当时真实校园表单尚未运行；后续部分试填见下文，F07 保持 `IN_PROGRESS`。
- **教室借用两步导航（2026-09-27，真实页面待运行）**：用户澄清真实流程为大厅先点“教室借用”，跳转后再点“临时借用（仅借1天）”，然后才出现表单。手工入口现支持用 `>` 显式指定步骤；第二步仅在第一步进入的页面和框架内查找，避免误回大厅。先失败后通过的单元与真实 headed Chromium 本地站点测试覆盖路线解析、两次点击和最终表单试填；`pytest tests/unit/test_ehall_interactive_f07.py tests/integration/test_ehall_interactive_browser_f07.py -q` **13 passed**；`./scripts/test.ps1` **1135 passed / 124 skipped**，Ruff、Mypy 通过。真实 ehall 两步导航与试填仍 `NOT_RUN`，F07 保持 `IN_PROGRESS`。
- **动态表单字段清单（2026-09-27，用户实测通过）**：用户反馈教室借用流程“基本成了”，但旧入口只读一次原生控件，无法说明页面可见字段数量。新增页面字段候选观察：等待异步渲染，统计原生控件、只读控件、常见 ARIA 自定义控件和仅有表单标签的自定义字段；不读取或打印已有字段值。`--sample` 输出当前可见候选数与可直接填写数，用户展开动态区域后可输入 `r` 重扫。先写失败的真实 headed Chromium 本地站点测试覆盖延时 1.2 秒出现的四个字段、只读与自定义类型及已有值不泄漏；`pytest tests/unit/test_ehall_interactive_f07.py tests/integration/test_ehall_interactive_browser_f07.py -q` **14 passed**；`./scripts/test.ps1` **1136 passed / 124 skipped**，Ruff `All checks passed!`，Mypy `Success: no issues found in 216 source files`，`git diff --check` 退出码 0。用户随后确认终端字段清单与真实页面一致；这是用户交互反馈，未记录字段数或字段值，也不代表提交前预览和回执已验收。F07 保持 `IN_PROGRESS`。
- **教室借用真实字段反馈后的试填增量（2026-09-27，待用户重测）**：用户提供真实 `--sample` 输出：可见候选 16 个、原生可填写 6 个，但自动试填 0 个；用途描述控件被长提示文字盖过字段名，人数/容量/数量虽是数字语义却使用 HTML `text`，其余有多个自定义 `combobox` 和只读日期。修正控件标签优先级（表单组标签先于 placeholder），给空白的总人数/容量/借用数量分别生成 `10`/`30`/`1`，用途描述生成“测试填写（未提交）”；下拉控件可读取当前可见选项，并在明确给出字段名与选项文字时选择。`--sample` 可带显式 `字段=值`，仍不进入最终提交分支；每个字段独立试填，失败会报告且继续保留页面供检查。先失败后通过的单元与真实 headed Chromium 本地站点测试覆盖上述标签、数字文本框、ARIA 下拉列项与选择、显式试填不提交、单字段失败后继续；`pytest tests/unit/test_ehall_interactive_f07.py tests/integration/test_ehall_interactive_browser_f07.py -q` **19 passed**；`./scripts/test.ps1` 最终 **1141 passed / 124 skipped**，Ruff `All checks passed!`，Mypy `Success: no issues found in 216 source files`。首轮完整脚本的 pytest/Ruff 已通过，但 Mypy 报 5 个局部变量重名错误；改名后定向测试、独立 Mypy 与完整脚本均通过。真实 ehall 下拉 DOM/选项、开始日期与最终预览尚未验证，F07 保持 `IN_PROGRESS`。
- **真实下拉探测串项修复（2026-09-27，待用户重测）**：用户第二次真实运行确认 16 个候选、6 个原生可写，空白的用途描述、总人数、教室容量已试填；教室借用数量未自动填写，可能已有页面值，未导出当前值。真实下拉探测把先前单位选项混入校区和布尔字段，且部分控件点击超时。通用交互层现先用 `aria-controls`/`aria-owns` 定位当前下拉的选项容器；缺少关联时只读取点击后新增的可见选项；每次探测前后用页面键盘 Escape 关闭上个弹层。先失败后通过的真实 headed Chromium 本地站点反例覆盖旧菜单留在 DOM 中导致串项、两个布尔下拉存在同名选项、遮挡层阻断下一次点击；`pytest tests/unit/test_ehall_interactive_f07.py tests/integration/test_ehall_interactive_browser_f07.py -q` **21 passed**，Ruff/Mypy 已通过。`./scripts/test.ps1` 本次为 **1142 passed / 124 skipped / 1 failed**，失败是 F06 `test_timeout_during_tls_handshake_fails_closed_and_reaps` 的 `_abort` 状态断言；该测试随后单独重跑通过，不能据此声称完整套件全绿。`git diff --check` 退出码 0（仅 LF/CRLF 提示）。用户明确要求框架只提供不同控件类型的通用填写能力，不编码“开始日期 → 结束日期”等事务字段联动；字段值和操作顺序交给后续 LLM 决定。此结果不证明真实站点自定义下拉已修好，需用户再次运行原 `--sample` 命令；F07 保持 `IN_PROGRESS`。
- **真实下拉遮挡与选项依赖复测（2026-09-27，修复待真实重测）**：用户再次运行原 `--sample`，串入旧字段选项的现象减少，但“借用人所在单位”“借用方式”“开始节次”仍报 `TimeoutError`；手动 `r` 后出现下拉内部 `listbox` 和搜索输入，用户观察到未收起的下拉会遮住其他下拉，部分字段的选项依赖先前选择。新增先失败后通过的 headed Chromium 反例：Escape 无效但控件失焦可关闭的遮挡层、指针被遮挡但可键盘展开的 combobox、先选前置字段后才出现的下拉选项、弹层内部 listbox/搜索输入不属于表单字段。通用交互层在探测前后发送 Escape 并使当前 frame 活动元素失焦；点击超时则尝试聚焦并用 Alt+ArrowDown 展开，不强制穿透遮挡层点击。字段清单跳过选项弹层内部元素；`--sample` 的 `r` 现在重新探测字段及下拉选项，显式多字段按指令顺序执行，未编码“校区 → 节次”等事务依赖。`pytest tests/unit/test_ehall_interactive_f07.py tests/integration/test_ehall_interactive_browser_f07.py -q` **26 passed**；`./scripts/test.ps1` **1148 passed / 124 skipped**，Ruff `All checks passed!`，Mypy `Success: no issues found in 216 source files`。真实站点修复效果仍待用户重测，F07 保持 `IN_PROGRESS`。
- **真实下拉复测反馈（2026-09-27，用户实测）**：用户重新运行相同 `--sample` 命令，下拉均能展开，原先报 `TimeoutError` 的“借用人所在单位”“借用方式”“开始节次”已能返回可见选项；手动调整页面字段后输入 `r`，清单仍为 16 个候选，未再混入弹层内部 listbox/搜索输入，校区及节次等后续选项可被重新识别。用户仍需手动选择；这次验证只覆盖展开、观察和依赖项重扫，未证明真实页面上的程序化选项点击、值保持、最终预览或提交回执。`--sample` 默认不代用户选择，未来资料库/模型驱动选择属于阶段 10 的逐步观察与决策；F07 保持 `IN_PROGRESS`。
- **真实程序化下拉选择（2026-09-27，用户反馈成功）**：用户按给出的 `--sample "打开 教室借用 > 临时借用（仅借1天）；借用方式=按节次借用"` 指令操作后回复“成了”。该命令会通过浏览器层选择页面可见的精确选项，且 `--sample` 不进入最终提交分支；这是该下拉在真实页面可程序化选择的用户反馈，未保存独立页面截图或回执，也不代表所有控件、完整表单、提交前预览已验收。下一验收点是有实际需要的低风险事务完整填写与提交前字段差异/材料/后果预览；标准 F07 验收止于最终提交前，真实提交只在确有合法办理需要时另行验收。F07 保持 `IN_PROGRESS`。
- **用户决定延期完整真实填报（2026-09-27）**：用户认为本轮已足以证明浏览器能填写具体事务的具体字段，决定暂缓完整真实表单填报，待资料库/LLM 逐步选择接入后再测。此次延期不改变原阶段 8 提交前完整预览尚未验收的事实；F07 继续 `IN_PROGRESS`，不标记 `DONE`。本次对话的全部修复、测试快照、真实反馈和剩余范围集中于 `docs/F07_REPAIR_REPORT_2026-09-27.md`。
- **审计汇总后的边界与文案修正（2026-09-27，本次）**：`docs/F07_AUDIT_SUMMARY_AND_CORRECTION_GUIDE_2026-09-27.md` 撤回对本机直接交互入口的旧 P1 定性，保留 1 项 P2 文档交接问题。已在 `AGENTS.md`、契约与 README 明确：`assistantctl ehall` 的页面导航、观察、填写可照常发起 POST；脚本只在点击用户指定的最终提交按钮前等待本机确认，不能保证确认前无服务器写入；本机确认页只列脚本记录的已填字段与指定按钮，不是受监督链路的完整 payload/材料/后果权威预览。受监督 Companion 的 Tool Gateway、R2/R3、Outbox、`UNKNOWN` 规则保持原状。`--sample` 不进入最终提交分支，普通模式本机确认前对指定按钮 0 次点击，取消后 0 次、确认后 1 次；未增加“填写期间 0 次 POST”的断言。契约与真实 headed Chromium 本地站点回归先失败后通过：`.venv-win\Scripts\python.exe -m pytest tests/contract/test_f07_contract_consistency.py tests/unit/test_ehall_interactive_f07.py tests/integration/test_ehall_interactive_browser_f07.py -q` 通过。原修复报告的本机运行是 **1148 passed / 124 skipped**；随后独立复验是 **1147 passed / 124 skipped / 1 failed**（RPC 锁等待计时阈值，单独 3/3 通过），两者是不同快照。本次首轮 `./scripts/test.ps1` 的 pytest 为 **1150 passed / 124 skipped**，随后 Ruff 因新 HTML 单行超长失败；拆行后重跑完整 `./scripts/test.ps1` 为 **1150 passed / 124 skipped**、Ruff 通过、Mypy **216 source files** 无问题；`.venv-win\Scripts\python.exe -m pip check` 与 `git diff --check` 通过。真实 PostgreSQL/Worker 独立脚本本次未重跑，最近证据仍为 2026-09-26 的 **124 passed**。本轮未访问真实 ehall，F07 保持 `IN_PROGRESS`。
- **提交结果未决 P1 修复（2026-09-28，待独立复验）**：先失败后通过的模拟反例覆盖“点击已发生、Companion 返回畸形结果、跟踪页出现新回执”；点击后的返回解析与取消都进入 `UNKNOWN` 持久化和只读对账，只有跟踪页唯一新增回执才收敛成功，绝不再次点击。对账意外异常保留 `UNKNOWN` 且不重复写终态；`UNKNOWN` 写入不可用时向 Executor 抛类型化未决错误，使 Gateway/Outbox 记录 `UNKNOWN`，重启恢复可收敛残留 `EXECUTING`。Broker 单元反例覆盖点击期取消和未决状态写入期取消；Gateway/Outbox 单元反例覆盖畸形响应、取消、会话库故障；真实 PostgreSQL + Worker 反例核对 `browser_sessions` 与 `side_effect_intents` 均为 `UNKNOWN`、重试不点击、后续只读对账命中。`./scripts/test.ps1` **1157 passed / 124 skipped**，Ruff 全绿、Mypy 216 个源文件通过；`./scripts/test-postgres.ps1` **124 passed**；`pip check` 与 `git diff --check` 通过。本轮未连接真实 ehall，也未制造申请；阶段 8 的真实完整填写、权威预览和真实适配器指纹仍未验收，F07 保持 `IN_PROGRESS`，F08 不启动。
- **提交终态持久化取消窗口（2026-09-28 自复验发现 P1，已修复）**：只修 Broker 点击/解析窗口仍不够；原 Gateway 在执行器返回后、Outbox `finalize` 完成前遭遇取消时，本地反例得到浏览器 `SUCCEEDED`、Outbox `PREPARED`、审批 `EXECUTING`（点击 1 次），结果不明分支也会留下相同中间态。新增先红后绿的 F07 Gateway 两条反例，分别模拟已取得权威回执与畸形点击响应，并在终态写入期间连续取消；Gateway 现在等待已启动的终态持久化完成再传播取消，已知成功保持 `SUCCEEDED`，不明结果保持 `UNKNOWN`。真实 PostgreSQL 反例在连续取消后核对 intent/approval 均为 `SUCCEEDED`。定向浏览器/Gateway/F06 邮件测试通过；`./scripts/test.ps1` **1159 passed / 125 skipped**，Ruff/Mypy（216 files）通过；`./scripts/test-postgres.ps1` **125 passed**；`pip check`、`git diff --check` 通过；wheel **211** 条目含 Gateway、Browser 模块及 0007/0008，排除扩展源码。本轮没有连接真实 ehall 或进行真实提交；完整真实提交前验收和适配器指纹仍缺，F07 继续 `IN_PROGRESS`。
- **第二课堂真实申请表结构采集（2026-09-28，提交前验收进行中）**：用户选择“第二课堂 → 志愿服务经历（申请）”。首轮把自动到达的“第二课堂成绩单（在线办理）”页标题误当成待点击项；次轮只认标准按钮而没识别表格内的自定义“申请”。现本机入口可在多个标签页中按“志愿服务经历”所在行定位并点击唯一“申请”，本地相邻行/自定义按钮/迟到标签页反例先红后绿。用户亲自完成 SSO 后，真实 Chromium 自动进入 `youth.nju.edu.cn` 的内层申请表；`--capture-out` 只读保存 3 个 frame 的脱敏结构指纹（当前值、选项文字和动态 URL 查询参数不落盘），扫描到 **9 个底层控件候选**；本次未填值、未点最终提交。用户截图确认 **7 个业务字段**：名称、开始时间、结束时间、团队职务、服务时长、审核学院（六项必填）及审核人（非必填），页面未见附件控件；名称提示 20 字以内、团队职务无值时填“无”、服务时长以小时计且最多一位小数。真实站点控件与左侧标签缺少标准关联，终端仍显示内部 `data.*` 名称；已新增通用表格相邻标签识别并用本地真实 Chromium 反例先红后绿，**尚未在真实站点复测**。真实页面含嵌套 frame、后台请求和未知的 JavaScript 提交目标；这份本机结构报告不是受监督 BrowserSessionBroker 的权威预览，也不能直接作为可提交 Adapter。材料清单、实际填值差异、后果和安全停止在最终提交前仍待验收。`./scripts/test.ps1` **1167 passed / 125 skipped**，Ruff 通过，Mypy **216 source files** 无问题；`pip check` 和 `git diff --check` 通过，收尾时 `python=0 chrome=0`。本轮未重跑独立 PostgreSQL/Worker 套件，因为未修改数据库或受监督 Gateway。F07 继续 `IN_PROGRESS`，不推进 F08。
- 上述结构报告副本为 `docs/evidence/real_ehall_volunteer_structure_2026-09-28.json`，SHA-256 `5c8983ca23a413c3f671e44a6337e023e2ca70043e4145594bf144a863946d02`；文件不含字段当前值、选项文字和动态查询参数。用户提供的表单截图仅在会话中核对，未写入仓库。
- **第二课堂真实受监督提交前试填（2026-09-28，部分通过）**：用户自行完成 SSO 并进入“第二课堂 → 第二课堂成绩单（在线办理） → 志愿服务经历（申请）”；验收脚本接管新标签页的嵌套 `/tw/xssq/xssq/create` frame，先核验 9 个底层控件和页面指纹，再经 `BrowserSessionBroker` 从上到下填写 7 个业务字段。学院选为“文学院”后，审核人选项随之变化，脚本复核新指纹才继续；Broker 返回 `PREVIEW_READY`、`missing_fields=[]`、附件观察为 0，用户目视确认“七项正常”且页面无报错。脚本 `submit_enabled=False`，未点击最终“提交”；合成测试值不代表真实志愿服务申请。真实试填过程中修复隐藏原生下拉与可见控件不同步、依赖下拉指纹变化和结束时间规范化造成的校验失败，先红后绿的有头 Chromium 反例覆盖前两项。复现命令、脱敏 JSON 哈希及剩余限制见 `docs/evidence/real_ehall_volunteer_supervised_2026-09-28.md`。当前报告 `target_bound=false`、`acceptance_complete=false`：真实提交目标、权威材料/后果、正式 `nju_ehall` Adapter 与 Tool Gateway/Outbox 路径尚未核实，因此不称完整权威预览，不标记 F07 `DONE`。本轮按用户要求仅跑定向回归：单元/契约 **63 passed**、本机交互 Chromium **23 passed**、Companion 嵌套 frame **2 passed**，Ruff/Mypy 通过；未重跑全量测试或 PostgreSQL/Worker 套件。
- **一次登录自动路由与合成值试填烟测（2026-09-30，用户目标通过、待独立审计）**：用户在新开的可见 Chromium 中自行完成 SSO；`scripts/ehall_volunteer_smoke.py` 随后自动走“第二课堂 → 第二课堂成绩单（在线办理） → 志愿服务经历（申请）”，从上到下填写七项合成值，学院选择“文学院”后重新读取并选择审核人；只读校验七项可见值、唯一申请表 frame 和最终“提交”按钮，用户目视回复“七项正常”，进程退出码 0，未点击最终“提交”。修复了 500 页刷新后已在在线办理页却仍等待旧入口、可见只读镜像加隐藏原生 select 导致学院填写失败的问题；相关本地 Chromium 与单元反例先红后绿。脱敏 JSON `docs/evidence/real_ehall_volunteer_auto_route_smoke_2026-09-30.json` 的 SHA-256 为 `4bdfc61c66566901744a49f937f93b160d214f24ad5d8b9ba08ff915702ffd75`；详见 `docs/F07_AUTO_ROUTE_SMOKE_REPORT_2026-09-30.md`。定向命令 `.venv-win\Scripts\python.exe -m pytest tests/unit/test_ehall_volunteer_smoke_f07.py tests/unit/test_ehall_interactive_f07.py tests/integration/test_ehall_interactive_browser_f07.py -q` → **44/44 通过**；改动路径 Ruff/Mypy、`git diff --check` 通过。按用户的小型改动测试偏好未运行全量或 PostgreSQL/Worker；不借用旧快照宣称本轮全绿。此烟测为本机直接交互，不是受监督 Gateway/Outbox 与目标/材料/后果权威预览；F07 保持 `IN_PROGRESS`，F08 不开始。
- **第四轮独立审计（2026-09-23）未通过**：3 个 P1（真实 POST payload 未绑定审批、对账可从被重定向页面伪造 `host_tracking`、登录路径前缀误匹配导致 `/sso/login-evil` 获得写窗口；另有 1 项由 P1 校准为 P2：旧 Worker 迟到故障移除新版 Registry）与 5 个 P2（chunked 绕过 64 KiB 限额且 create/revoke 先解析后认证、握手早于服务就绪、`::1` 生成非法 URL、报告/仓库证据不一致）。修复与冻结语义见 `CONTRACTS_AND_INTERFACES.md` 第 16.12 节。修复证据（本轮实测）：真实 Chromium 33 passed（含隐藏字段注入 abort、tracking 重定向拒绝、握手即就绪）；真实 PostgreSQL 123 passed；单元+契约 944 passed；Ruff/Mypy 通过。全量 `test.ps1` 与制品复验待下一轮重跑（本轮一次运行出现 1 个 Companion 超时误报，同一用例独立复跑 3/3 通过）。
- **第三轮独立审计（2026-09-21）未通过**：3 个 P1（登录窗口无条件放行任意非 GET、真实 POST + 伪造 DOM 回执仍被误判成功、0008 强制取消 UNKNOWN/EXECUTING）与 1 个 P2（文档/证据不一致）已修复，冻结语义见 `CONTRACTS_AND_INTERFACES.md` 第 16.11 节。
- **第三轮修复后证据（2026-09-21）**：`./scripts/test.ps1` → **1046 passed、122 skipped、0 failed**（含真实 Chromium `test_browser_companion_real_f07.py` **29 passed**），Ruff `All checks passed!`，Mypy `Success: no issues found in 214 source files`；`test_cancel_reaps_the_browser_process` 独立连续 **5/5 通过**；真实 PostgreSQL 集合（F01/F02/F04/F05/F06/F07 + 两个真实 Worker 链，含 0008 未决动作保留/多未决拒绝升级反例）→ **122 passed**；`0008` SHA-256 `5dcd9513b9767a1c67092dcf6520bbf75eb42963916b6da12b909413c91d2af3`（契约测试锁定）；`0001`–`0007` 逐字节未改；wheel 209 条目含 `0008` 且不含扩展源码；`pip check` 与 `git diff --check` 通过；无残留 chrome/companion 进程。
- **第二轮修复后证据（已被第三轮结果取代）**：`./scripts/test.ps1` → **1040 passed、120 skipped、0 failed**（含真实 Chromium 26 passed），Ruff `All checks passed!`，Mypy `Success: no issues found in 214 source files`；`test_cancel_reaps_the_browser_process` 独立连续 **5/5 通过**（browser-level CDP PID）；真实 PostgreSQL 集合 → **120 passed**（当时快照）。
- **上一轮修复后证据（2026-09-21，第一轮快照，已被第二轮取代）**：`./scripts/test.ps1` → 1031 passed、119 skipped（当时真实 Chromium 23 passed）；真实 PostgreSQL 集合 → 119 passed；新增反例：延迟 autosave、提交目标绑定、版本/扫描、capability 脱敏、对账、过期、并发建会话、双重编码开放重定向与 live URL 复核。
- **仓库状态说明（2026-09-23）**：仓库已提交（HEAD=`f3be2cd`=`origin/main`），本轮修复位于工作区未提交改动；`git diff --check 1ef9ef5..HEAD` 报出的 LF/CRLF 混用属于**已提交历史**，未经用户决定不做历史改写（工作区文件统一 LF）。历史测试数字均标注“已被后续结果取代”。
- **真实 ehall 状态**：真实门户/SSO **只读采集已运行**（用户亲自完成 SSO；证据见 `docs/evidence/real_ehall_2026-09-21.md`）；真实教室借用已部分试填；2026-09-28 第二课堂“志愿服务经历（申请）”在受监督 Broker 下填入 7 项合成测试值并生成 `PREVIEW_READY`，用户核对七项正常，最终“提交”未点击。该临时预览没有绑定真实提交目标，也未核实完整材料与后果；脚本内存 Adapter 尚未进入正式 `nju_ehall` 扩展，Gateway/Outbox 真实链路未验收。`adapters/*.json` 当前仍为确定性测试夹具指纹。按规则 F07 保持 `IN_PROGRESS`，不自行标记 `DONE`。
- **有意未实现**：真实 ehall 页面适配器（当前只有 mock 低风险事务适配器 `nju.ehall.proof` 与示例 origin）、附件上传到站点（预览中只绑定名称/大小/SHA-256，不上传）、PWA 表单渲染与手机确认界面（F08）、真实提交的校园级验收、Windows Credential Manager 与 service/Job Object 管护（F09）、Companion 自动启动编排（当前由运维/测试显式启动并注入 capability）。
- **真实站点只读采集（2026-09-21，用户实时参与）**：`scripts/ehall_real_acceptance.py capture` 经真实 Desktop Companion 打开 `https://ehall.nju.edu.cn`，用户在可见 Chromium 中**亲自完成 SSO**（脚本从不输入凭据、不处理验证码），助手只做只读采集。过程中发现并修复两个真实互操作缺陷：①CAS 登录跳转的 `service=https://ehall.nju.edu.cn/...` 被开放重定向启发式误判为 `OPEN_REDIRECT`，driver abort 请求导致 `ERR_FAILED`；现在重定向目标只要落在用户 allow-list 的 https origin 内即放行（非白名单/非 https/userinfo/反斜杠仍拒绝），新增 2 组回归测试；②Companion `status` 从未返回 `url`/`login_page`（与端口契约不符），已由 driver diagnostics 补齐并加真实浏览器断言。采集结果：门户与列表页均为 SPA（`#/hall`、`#/home/official_home`、`#/role_matter`），事项卡片是 JS 点击而非 `<a href>`（门户链接数 1，仅“全部服务”），列表页无表单控件；已记录真实列表页指纹 `d6d974e37414a98b612964a344c0b6fe6288c2f4b13d81b6769528f31ee87f87`（仅作证据，未写入 `proof.json`；真实交易页指纹需在后续验收中捕获并替换测试夹具值）。
- **路线图边界（2026-09-28 更新）**：F07 已有真实打开事务页、教室借用部分试填及第二课堂表单七项受监督试填证据；仍缺绑定真实提交目标、材料与后果的权威预览，以及正式扩展 Adapter 的受监督 Tool Gateway/Outbox 验收。实际提交与回执另行验收，不为合成经历制造申请。Android PWA 确认界面属于 F08/阶段 9；邮件事件、任务消息、LLM 决策与 ehall 的自动组合属于阶段 10。`assistantctl ehall` 仅是本机手工验收入口，不作为组合流程已完成的证据。
- **未来模型驱动的逐步页面循环（用户 2026-09-27 明确的目标）**：每轮由浏览器读取当前页的可见内容、字段及可操作元素，形成有界结构化观察；模型根据任务目标只选择一步操作（如点击、填写、选择、等待或返回），宿主核对该操作确实属于当前观察后执行，再重新观察下一页，直到形成最终提交前预览。现有固定 `>` 路线和 `--sample` 值只是 F07 真实站点测试手段，不是未来必须预写死的页面流程。目标交互是用户在**最终提交前只作一次明确确认**，该确认应同时绑定实际提交目标和完整 payload，并满足一次性 R2 审批；当前开发设计把预览确认与实际提交审批分成两次，F08/阶段 10 实施时需统一这处交互。登录与验证码仍由用户完成；若中间按钮会产生实际提交或服务器保存，不能把它当成无副作用的导航静默执行，应停止该路线并说明原因。此条仅记录后续目标，F07 尚未接入 LLM 或自动循环。
- **后续扩展规划：登录后自动路由 + 模糊指令 + 远程批准（待用户授权，未开始）**。目标形态（用户 2026-09-21 描述）：用户只亲自完成 SSO/验证码，之后助手接手——远程模糊指令 → 自动路由到目标事项 → 填报 → 生成权威预览 → 用户远程批准 → 提交并留存回执。差距与所需契约增量：
  1. **导航动作**：适配器声明“进入事项”的动作（结构性 locator 或声明式入口路径），点击前绑定当前页面指纹/动作标签/期望落地 origin+path，落地后复核真实 URL 与落地页指纹；打开事项可能创建服务端草稿 → 按 `INTERNAL_WRITE`/`EXTERNAL_WRITE` 定级，默认需要事项 allowlist 预授权或每次审批。当前契约只允许点击唯一 final submit 动作，因此真实 SPA 卡片无法自动进入。
  2. **多事项/多页适配器**：每个 `transaction_id` 声明入口路径、列表/详情/表单各自的 `allowed_page_fingerprints`、字段与 final action；适配器/扩展版本变化使旧审批失效（沿用既有规则）。
  3. **模糊指令路由**：Agent/工具层把指令映射到候选 `transaction_id`，低置信度或 R2+ 必须让用户确认；核心仍不得出现业务路由（由扩展声明）。
  4. **远程批准**：复用现有一次性 R2 审批（绑定目标/payload/扩展版本/nonce）与 outbox；手机端 UI/Web Push 属 F08。
  5. **审计与限额**：导航动作次数、路由尝试、版本不匹配全部落审计并 fail closed；R3 永久阻断、不保存 cookie/密码、不自动处理验证码等边界不变。
  未来验收（含反例）：用户仅完成 SSO；从模糊指令到预览无需 PC 操作；错误卡片/路径不点击、列表页指纹漂移拒绝、入口产生服务端写时按声明风险审批、模糊指令命中 R3 直接拒绝、审批后任一绑定变化立即失效、重放不产生第二次写。按仓库规则，需用户明确授权后才可将 TODO 标为 `NEXT` 并开始实现。
- **首版已实现（2026-09-21，用户授权“完成1”）**：声明式导航动作 + 多事项适配器，见 `CONTRACTS_AND_INTERFACES.md` 第 16.9 节。`AdapterActionSpec` 支持 `kind="navigate"` + `transaction_id` + `navigates_to_path`（静态路径，允许 hash 路由）；`BrowserSessionBroker.execute_navigation` 只允许 `AUTHENTICATED`/`DISCOVERED`，实时页面必须存在声明 label 的动作，落地路径与事务指纹双重校验，漂移即 `SAFETY_PAUSED`；新增 `DesktopBrowserPort.activate`、Companion `/activate`、driver `activate_navigation`、capability `browser.navigate` 与宿主执行工具 `ehall.open_transaction`（INTERNAL_WRITE，无 R2，提交仍 R2）。扩展改为按事务拆分 adapter：`adapters/proof.json`（在读证明）+ `adapters/transcript.json`（成绩单打印），首次使用时全部注册，`inspect` 按 `app_path`、`prepare` 按 `transaction_id` 解析。证据：`tests/unit/test_browser_navigation_f07.py` 15 passed（校验/路由/状态/动作漂移/错路径/未固定指纹/越权落地/多事项选择/executor 路由）；真实 Chromium 集成新增 2 passed（门户按钮路由到固定指纹页、落地路径不符拒绝）；真实 Worker/Gateway 链新增 `ehall.open_transaction` 1 passed。仍有意未实现：模糊指令 → 事务的 Agent 路由（低置信度/R2+ 必须用户确认）、手机端审批 UI/Web Push（F08）、真实 ehall hash 路由 adapter 的指纹捕获与替换。
- **剩余风险**：真实 SSO 需要把认证 origin（如 `authserver.nju.edu.cn`）也加入用户 allow-list；真实 ehall DOM 变化会按设计 fail closed（`SAFETY_PAUSED`）而不是猜测；驱动层的浏览器 POST 重试仍可能发生在"连接被重置且无响应"的极端网络条件下（mock 用 504 覆盖了确定性 UNKNOWN 路径），真实站点必须靠回执/流程编号对账；扩展被视为用户信任代码（与 F02 边界一致），venv/Worker 只隔离依赖与崩溃。

- **2026-09-30 烟测后审计 P1/P2 修复（待独立复审）**：审计承认一次登录自动路由、七字段试填、提交前停止的旧实站烟测，但发现直接交互隐藏下拉可能从整个表单选到其他字段，且旧代码先改隐藏 `select` 并触发 `change`；文档契约测试也因旧措辞失败。新增真实 Chromium 本地反例覆盖不同字段容器、明确异名下拉、同容器预先可见无关同名选项，旧代码实际误改，修复后无关值及镜像不变、`change` 事件为 0。通用交互层只在独立字段容器识别关联下拉，拒绝显式异名和只读无关联字段；选择通过本次展开的可见选项进行，核对可见镜像与隐藏选中项，不先改隐藏控件。文档契约改验 F07 章节的部分试填、权威预览未验收及 `IN_PROGRESS` 语义。修复后未经排除的 `./scripts/test.ps1` → **1185 passed、125 skipped、6 warnings**，Ruff 全绿、Mypy 216 个源文件通过，退出码 0；`pip check`、`git diff --check` 通过。独立 PostgreSQL/Worker 本轮未重跑；修复后未重新登录实站，旧 JSON 仍是修复前烟测证据。详情和命令见 `docs/F07_AUDIT_REPAIR_2026-09-30.md`。真实正式 Adapter、目标/材料/后果绑定的受监督权威预览及 Gateway/Outbox 链路仍未验收，F07 保持 `IN_PROGRESS`，F08 不启动。
- **2026-09-30 第二轮复审残余 P1 修复（待审计 agent 全量复验）**：审计发现无标签隐藏 `select` 与目标只读框处于同容器且有第二个字段标签时，点击目标后才出现的无关同名菜单仍可能被选中，导致先改错字段再报错。新本地真实 Chromium 反例在修复前观察到无关 `select` 变为 `college-1`；修复后要求隐藏下拉有明确 label/ARIA 字段关联，且选项只在目标以 `aria-controls`/`aria-owns` 明确拥有的唯一菜单中点击。两个新增布局均断言无关值、镜像、`change` 事件及目标点击保持原样；正常“学院→审核人”联动仍通过。`.venv-win\Scripts\python.exe -m pytest tests\integration\test_ehall_interactive_browser_f07.py tests\unit\test_ehall_interactive_f07.py -q` → **40/40 通过**，改动路径 Ruff/Mypy 通过。`./scripts/test.ps1` 应用户指令在约 16% 处中断，不能称为当前快照全绿；旧 1185/125 数字属于首轮修复历史。未重测真实 ehall，旧 JSON 为修复前证据；正式 Adapter、目标/材料/后果权威预览及 Gateway/Outbox 仍待验收。详情见 `docs/F07_AUDIT_REPAIR_ROUND2_2026-09-30.md`；F07 保持 `IN_PROGRESS`，F08 不启动。

### F08 — PWA 与 Web Push（IN_PROGRESS；F08.1–F08.5 已按约定范围验收）

范围：扩展现有响应式 PWA 壳：补齐任务列表/详情、草稿编辑、Schema 表单、SSE 可靠续传和最小元数据 Web Push。

能力边界：Schema 驱动 UI，不加载扩展 JS；Service Worker 不缓存敏感 API。

按 2026-09-30 用户授权的排期例外拆分；每次只推进一个编号：

1. **F08.1 DONE — 任务列表/详情与真实连接状态**。公共 `GET /api/v1/tasks` 采用 `limit=1..100`、`before=<上一页末项 ID>` 的有界游标分页，按 `(created_at,id)` 降序，PostgreSQL 只读取当前 owner；既有详情和 SSE 供 PWA 从服务端重读。390px 视口列表/详情、事件更新、断线及重连可用；断线/读取失败明确提示状态可能过期，不保留离线命令队列。Service Worker v2 只拦截固定静态路径，敏感 API 不缓存。前端只用 `textContent` 渲染任务及消息，不执行扩展 JavaScript；不把 F07 试填预览生成可批准动作。无新迁移。接口增量见契约第 17 节。
2. **F08.2 DONE — 任务消息/材料缺口交互**。基于既有 `POST /tasks/{id}/messages` 的版本和幂等契约，在手机端显示服务器版本并提交补充内容；409 不自动重发，重读服务器详情、保留输入并提示用户再次确认。离线时不发请求；响应丢失或成功响应不完整/属于其他任务时显示结果未确认、保留原输入/版本/幂等键，仅可同键手动重试。两项复审 P1 均已补反例修复并于 2026-10-01 通过定向独立复验。请求正文与回执按任务服务的 `strip()` 形式比较，用户原始输入保留至确认。未持久化手机端草稿，刷新页面后尚未发送的输入会丢失。
3. **F08.3 DONE — Schema 表单、来源与版本化草稿**。只渲染受支持的 JSON Schema/UI Schema，逐字段区分 `EVIDENCE`/`USER_INPUT`/`UNKNOWN`；草稿通过版本 CAS 和幂等键保存，409/412 保留本机输入并展示服务器新版，不加载扩展 JS。实施与接口见契约第 19 节。0009 迁移、真实 PostgreSQL 草稿重放和独立公共进程真实 Worker `form.list` 均获定向独立验收；验收命令与边界见下方记录。
4. **F08.4 DONE — 正式审批页面（2026-10-01 定向独立验收通过）**。手机只联系助手公共 API；邮件路径由已持久审批记录、同版本只读 Worker 与宿主 MIME 校验共同生成完整预览，批准只改变审批状态，不直接发送。ehall 试验性记录因缺少真实目标、完整字段/材料及后果，在公共 API 与 PWA 均不可批准；F07 受监督权威预览仍需后续验收。smail 新工具采用可升级的 0.2.0 包版本，不自动安装或升级。产品中的 Agent 自动发起邮件动作尚未接线；本项验收只涵盖已有审批记录的手机核对/批准与宿主准备能力。契约见第 20 节；独立复验见下方记录。
5. **F08.5 DONE — Web Push（2026-10-02 独立验收通过）**。助手公共 API 管理手机订阅/撤销，持久订阅材料先由宿主加密；Gateway 持久准备 R2 审批后发一次最小元数据加密通知，仅含待处理提示、风险和非敏感任务 ID。用户点击通知只打开助手 PWA 的任务页，不批准或执行动作。接口与边界见契约第 21 节；真实投递留待 F08.6。
6. **F08.6 BLOCKED — 安全与真机验收（授权部署已接通）**。固定域名 Cloudflare Access、CSRF/Origin/CSP 与 Android 网络切换、后台恢复、缓存检查；Tailscale 仅 health-only 的部署黑盒验收与 F10 协调。原助手、health 与 Tunnel 已经本人授权临时运行；私有 Serve 只代理8010、AllowFunnel=false，同节点检查通过。应用 One-time PIN 已由本人保存并提供截图，手机首次登录/SSE及health三项、Chrome标识桌面入口无地址栏启动/空列表/SSE、Wi-Fi→移动数据恢复及后台或锁屏约一分钟后恢复都有本人反馈；断网提示及恢复联网自动连接也已确认；手机任务/消息刷新恢复与电脑新增任务/消息自动同步获本人反馈，先前自检记录获原库只读核对；任务/消息场景CacheStorage四路径及任务GET的200/no-store已获手机证据；Schema草稿/审批、其余缓存及跨设备边界和真实Push仍待补齐。随后经本人对精确材料授权，example.echo@0.1.0已安装启用，公共页面出现example.echo_settings；手机来源由未确定变为用户输入，初次草稿版本2及刷新恢复已有本人反馈；后续真实手机截图确认保留本机未保存值并显示服务器3，显式保留后保存版本4及刷新恢复获本人确认和原库只读核对。手机草稿GET的200/no-store已有截图；更新后截图确认唯一缓存为v13、旧v12节点消失，仍仅四静态路径。手机PUT的409/412、其余缓存与审批仍待核验。正式秘密后端仍待 F09，不启动后续编号。

F08.1 验收边界：新增列表 API 的排序、分页、owner 隔离及持久重连；任务详情读最新消息；SSE 游标严格续读；手机宽度浏览器能显示服务器状态和真实连接中断/恢复；缓存仅静态资源。这里的浏览器测试使用本地模拟 SSE/API，尚非 Android 真机或 Cloudflare 远程 E2E。以下命令与结果仅指本轮实际运行，历史 F07 数字不得替代。

**F08.1 本轮证据（2026-09-30，指定目录 `C:\Users\kunji\Desktop\homework\生成式软件工程\lab1`）**：

- 先写 API/PWA 断言并运行 `.venv-win/Scripts/python.exe -m pytest tests/api/test_pwa_tasks_f08.py -q`，新列表请求原为 405、任务视图节点缺失；修正测试自身的幂等键格式后，再做实现。随后 `.venv-win/Scripts/python.exe -m pytest tests/api/test_pwa_tasks_f08.py tests/api/test_api.py tests/integration/test_pwa_browser_f08.py -q` → **11 passed**。真实 Chrome 390px 用例覆盖列表/详情、SSE 事件、断网与重连、DOM 文本注入反例及浏览器 CacheStorage 不含 `/api/`；重连指示器曾停在“连接中”，测试先失败，前端重建 EventSource 并携带已见事件 ID 后通过。
- `docker compose up -d postgres` → `Container lab1-postgres-1 Running`；`$env:PA_TEST_DATABASE_URL = 'postgresql://assistant:change-me@127.0.0.1:5432/assistant_test'; .venv-win/Scripts/python.exe -m pytest tests/integration/test_postgres_f01.py -k f08_recent_tasks -q` → **1 passed**（真实持久化后分页与 owner 隔离）。`./scripts/test-postgres.ps1` → **126 passed**（真实 PostgreSQL + Worker，含 F08.1 新用例）。测试仅创建/删除专用临时数据库，不修改既有迁移。
- `$env:RUFF_CACHE_DIR = Join-Path $env:TEMP 'pa-f08-ruff-cache'; $env:MYPY_CACHE_DIR = Join-Path $env:TEMP 'pa-f08-mypy-cache'; $env:PYTEST_ADDOPTS = '-p no:cacheprovider'; ./scripts/test.ps1` → **1189 passed、126 skipped、1 failed**，耗时 27:15；失败为未改动的 `tests/integration/test_browser_companion_real_f07.py::BrowserCompanionRealTests::test_unchecked_checkbox_is_omitted_from_successful_controls`，导航时 `BrowserUnavailableError: the desktop companion is unreachable`。同一用例单独命令 `.venv-win/Scripts/python.exe -m pytest tests/integration/test_browser_companion_real_f07.py::BrowserCompanionRealTests::test_unchecked_checkbox_is_omitted_from_successful_controls -q -p no:cacheprovider` → **1 passed**。本轮全量测试**不是全绿**；没有跳过/删除断言，F07 的偶发 Companion 可达性风险保留。
- 因 `test.ps1` 在 pytest 失败时退出，另行运行 `.venv-win/Scripts/python.exe -m ruff check src extension_sdk extensions tests` → `All checks passed!`；`.venv-win/Scripts/python.exe -m mypy src/personal_assistant extension_sdk/src/personal_assistant_sdk extensions/example_echo/src/example_echo extensions/personal_knowledge/src/personal_knowledge extensions/nju_smail/src/nju_smail extensions/nju_ehall/src/nju_ehall` → `Success: no issues found in 216 source files`。`node --check web/pwa/app.js` 与 `node --check web/pwa/service-worker.js`、`.venv-win/Scripts/python.exe -m pip check`、`git diff --check` 通过；文档改动后 `.venv-win/Scripts/python.exe -m pytest tests/contract/test_f07_contract_consistency.py -q -p no:cacheprovider` → **38 passed**。
- 未做真实 Android/Cloudflare、Web Push、正式草稿/审批、真实 ehall 提交或部署。F07 的一次登录路由/七字段试填烟测已通过，但正式 Adapter、实际提交目标和完整材料/后果权威预览、受监督 Gateway/Outbox 实站链路仍未验收；真实完整填报因暂无确有需要的事务延期，F07 继续 `IN_PROGRESS`。

**F08.1 用户定向验收与 F08.2 本轮证据（2026-09-30）**：用户独立验收 F08.1 API 与 390px 浏览器定向 **5 passed**、单个 PostgreSQL 分页 **1 passed**，JS/Service Worker 语法与 `git diff --check` 通过，明确只接受 F08.1，不代表 F08 整体完成；审查未改文件且未重跑全量或额外真实回归。F08.2 在现有消息 API 上只修改 PWA 与 API/浏览器测试，无后端、共享状态、审批或 Outbox 改动。先写浏览器反例并运行 `.venv-win/Scripts/python.exe -m pytest tests/integration/test_pwa_browser_f08.py -q -p no:cacheprovider`，新消息输入缺失 → **2 failed、1 passed**；实现后再次补充“响应丢失后设备又离线不得声称未发送”反例，先为 **1 failed、2 passed**，修复提示后通过。`.venv-win/Scripts/python.exe -m pytest tests/integration/test_pwa_browser_f08.py tests/api/test_pwa_tasks_f08.py tests/api/test_api.py -q -p no:cacheprovider` → **13 passed**（补真实 API 409 断言前）；最终 `.venv-win/Scripts/python.exe -m pytest tests/integration/test_pwa_browser_f08.py tests/api/test_pwa_tasks_f08.py tests/api/test_api.py tests/contract/test_f07_contract_consistency.py -q -p no:cacheprovider` → **52 passed**。390px 浏览器覆盖提交版本/幂等键、SSE 重绘保留输入、409 冲突重读与显式再次提交、离线不发、响应丢失同键重试、文本不执行脚本；API 回归验证真实 409 错误码及未覆盖服务端消息。`$env:RUFF_CACHE_DIR = Join-Path $env:TEMP 'pa-f08-ruff-cache'; .venv-win/Scripts/python.exe -m ruff check tests/integration/test_pwa_browser_f08.py tests/api/test_pwa_tasks_f08.py` → `All checks passed!`；`node --check web/pwa/app.js`、`node --check web/pwa/service-worker.js` 与 `git diff --check` 通过。本轮未运行全量或 PostgreSQL/Worker：改动仅为 PWA 客户端及本地测试；此前全量的 F07 单例失败仍是未消除的历史记录，不能宣称全量全绿。

**F08.2 P1 复审与修复证据（2026-09-30，仍待独立复验）**：用户独立运行 2 个浏览器用例和 1 个 API 409 用例通过，但用本地 390px 浏览器证明“服务器先写入消息、再返回 `200 {}`”时前端在渲染校验前清空输入与原幂等键，重填产生第二条消息；F08.2 因此改回 `IN_PROGRESS`，F08.3 改回 `TODO`。本轮先在 `tests/integration/test_pwa_browser_f08.py` 加入同一反例，运行 `.venv-win/Scripts/python.exe -m pytest tests/integration/test_pwa_browser_f08.py::PwaBrowserF08Tests::test_committed_message_with_incomplete_200_retries_with_original_key -q -p no:cacheprovider` → **1 failed**（输入已空）；再使客户端先校验当前任务的完整 `TaskDetail` 和本次消息、渲染详情，最后才清除输入与幂等键，未知分支显式恢复原内容/版本/键。反例扩充为 `200 {}`、错任务 ID 与一次性渲染异常，三者都按原键手动重试，服务端各只保存一条对应消息。相同单例复跑 → **1 passed**；`.venv-win/Scripts/python.exe -m pytest tests/integration/test_pwa_browser_f08.py tests/api/test_pwa_tasks_f08.py tests/api/test_api.py tests/contract/test_f07_contract_consistency.py -q -p no:cacheprovider` → **53 passed**；`$env:RUFF_CACHE_DIR = Join-Path $env:TEMP 'pa-f08-ruff-cache'; .venv-win/Scripts/python.exe -m ruff check tests/integration/test_pwa_browser_f08.py tests/api/test_pwa_tasks_f08.py` → `All checks passed!`；`node --check web/pwa/app.js` 与 `node --check web/pwa/service-worker.js` 通过。Service Worker 静态缓存版本升为 v4，以替换旧 `app.js`。仅修改 PWA 客户端、本地浏览器测试和状态文档，未改后端、共享状态、审批或 Outbox；按局部修复范围未重跑全量、PostgreSQL/Worker 或真实站点，也未据此宣称 F08.2 已验收。

**F08.2 第二项 P1 复审与修复证据（2026-09-30，仍待独立复验）**：用户独立定向 **5 passed** 后指出首尾空白反例：真实 `TaskService.add_message` 保存前 `str.strip()`，而 PWA 用原始输入逐字匹配回执；同键重试一直不能解除“结果未确认”，刷新再填会产生不同键的第二条消息。本轮先使本地 390px 浏览器替身同样 `strip()`，新增 `test_message_with_edge_spaces_uses_the_server_normalized_receipt`，执行 `.venv-win/Scripts/python.exe -m pytest tests/integration/test_pwa_browser_f08.py::PwaBrowserF08Tests::test_message_with_edge_spaces_uses_the_server_normalized_receipt -q -p no:cacheprovider` → **1 failed**（服务端已写入但页面仍显示结果未确认）。前端随后在生成 attempt 时固定与 Python `str.strip()` 同集合的规范化内容，POST 与回执比较共用此值；原始输入保存在 attempt 中，未知结果仍显示原输入并用同一键/版本/规范化正文重试。浏览器反例覆盖正常 200 与“已写入后返回 `200 {}` 再同键重试”，每个逻辑消息仅入库一次；真实 API 测试另验证去首尾空白回执及同键重放。两项单例复跑 → **2 passed**；`.venv-win/Scripts/python.exe -m pytest tests/integration/test_pwa_browser_f08.py tests/api/test_pwa_tasks_f08.py tests/api/test_api.py tests/contract/test_f07_contract_consistency.py -q -p no:cacheprovider` → **55 passed**。Service Worker 静态缓存版本升至 v5；未改后端、审批或 Outbox。本轮未跑全量、PostgreSQL/Worker 或真实站点，不能据此宣称 F08.2 独立验收通过。

**F08.2 定向独立验收（2026-10-01）**：`.venv-win/Scripts/python.exe -m pytest tests/integration/test_pwa_browser_f08.py tests/api/test_pwa_tasks_f08.py::PwaTaskReadTests::test_message_receipt_uses_stripped_content_and_replays_once tests/api/test_pwa_tasks_f08.py::PwaTaskReadTests::test_message_conflict_reports_409_and_preserves_current_server_detail -q -o addopts='' -p no:cacheprovider` → **7 passed、2 warnings**。独立核对 Python `str.isspace()` 与 JavaScript `/[\p{White_Space}\u001c-\u001f]/u` 在全部 Unicode 码点上的集合一致；`node --check web/pwa/app.js`、`node --check web/pwa/service-worker.js`、改动测试 Ruff 与 `git diff --check` 通过。原两项 P1 的定向反例均通过，未发现新的阻断项；F08.2 标记 `DONE`，F08.3 为下一唯一 `NEXT`。本次没有运行全量、PostgreSQL/Worker、Android 或真实站点，不能称 F08 整体全绿。

**F08.3 本轮实施证据（2026-10-01，`main` 起点 `b398a31`；`IN_PROGRESS` 待独立复验）**：先新增 `tests/api/test_pwa_form_drafts_f08.py` 与 `tests/integration/test_pwa_form_drafts_browser_f08.py`，运行 `.venv-win/Scripts/python.exe -m pytest tests/api/test_pwa_form_drafts_f08.py tests/integration/test_pwa_form_drafts_browser_f08.py -q -o addopts='' -p no:cacheprovider` → **4 failed**（公共 `/forms` 和草稿路由 404、390px 页面无草稿节点）。补充公共 API 独立进程中已启用 Worker 尚未运行的反例：`.venv-win/Scripts/python.exe -m pytest tests/unit/test_extension_supervision_service.py::SupervisorServiceTests::test_form_catalog_starts_an_enabled_worker_in_public_process -q -o addopts='' -p no:cacheprovider` → **1 failed**（`worker is not running`）；按需启动已授权的精确版本后，该反例与 Manifest 漂移拒绝反例复跑 **2 passed**。定向最终命令见下方最新记录。覆盖启用扩展声明目录、Schema 注入和未知关键字/widget 拒绝、证据/用户/未知来源、布尔未确定与 `false` 区分、409/412 保留本机输入并显式选择、断线与 `200 {}` 同键重试且只保存一次、刷新恢复，以及 CacheStorage URL/响应正文不含私有草稿内容。迁移期望测试已增列 0009，不改旧迁移和既有断言。

**F08.3 最终定向命令（2026-10-01）**：后补 422 明确拒绝反例，先运行 `.venv-win/Scripts/python.exe -m pytest tests/integration/test_pwa_form_drafts_browser_f08.py::PwaFormDraftBrowserTests::test_validation_error_keeps_unsaved_draft_editable -q -o addopts='' -p no:cacheprovider` → **1 failed**（本机输入被误锁为“结果未确认”），修正后单例 → **1 passed**。最终运行 `.venv-win/Scripts/python.exe -m pytest tests/api/test_pwa_form_drafts_f08.py tests/integration/test_pwa_form_drafts_browser_f08.py tests/api/test_pwa_tasks_f08.py tests/integration/test_pwa_browser_f08.py tests/unit/test_extension_supervision_service.py::SupervisorServiceTests::test_form_catalog_only_reads_enabled_declared_worker_descriptors tests/unit/test_extension_supervision_service.py::SupervisorServiceTests::test_form_catalog_starts_an_enabled_worker_in_public_process tests/contract/test_f05_contract_consistency.py::MigrationHistoryContract::test_f05_required_no_new_core_migration_beyond_the_frozen_baseline -q -o addopts='' -p no:cacheprovider` → **23 passed、2 warnings**（68.92 秒）。旧 F08.1/F08.2 的 API/390px 浏览器用例在此集合内通过；这是定向回归，不是全量结果。

**F08.3 验证边界**：`$env:RUFF_CACHE_DIR = Join-Path $env:TEMP 'pa-f083-ruff-cache'; .venv-win/Scripts/python.exe -m ruff check src/personal_assistant tests/api/test_pwa_form_drafts_f08.py tests/integration/test_pwa_form_drafts_browser_f08.py tests/integration/test_postgres_f01.py tests/contract/test_f05_contract_consistency.py tests/unit/test_extension_supervision_service.py` → `All checks passed!`；`$env:MYPY_CACHE_DIR = Join-Path $env:TEMP 'pa-f083-mypy-cache'; .venv-win/Scripts/python.exe -m mypy src/personal_assistant/core/tasks/form_drafts.py src/personal_assistant/infrastructure/memory/form_drafts.py src/personal_assistant/infrastructure/database/form_drafts.py src/personal_assistant/api/v1/forms.py src/personal_assistant/api/v1/tasks.py src/personal_assistant/bootstrap.py src/personal_assistant/core/extensions/supervision.py src/personal_assistant/infrastructure/extensions/processes.py` → `Success: no issues found in 8 source files`；`node --check web/pwa/app.js`、`node --check web/pwa/service-worker.js`、`git diff --check` 均退出码 0。初次 Ruff 使用默认仓库缓存因 `.ruff_cache` 写入权限失败，改为临时缓存后完成检查；最后一处测试行宽已修正并复查。按用户本阶段要求没有运行全量、真实 PostgreSQL/Worker、Android/Cloudflare 或 F07 实站回归。因此 0009 的生产迁移/CAS 与真实 Worker `form.list` 链路仍待独立定向验证，不能用内存测试宣称生产持久化已验收。F08.4 正式审批、F08.5 Web Push 与 F08.6 真机/网络验收均未开始；F07 正式 Adapter、绑定真实目标及材料/后果的受监督权威预览、Gateway/Outbox 实站链路和延期的完整真实填报仍未验收，不制造申请、不点击真实最终提交。

**F08.3 独立审查 P1/P2 修复（2026-10-01；继续 `IN_PROGRESS`，等待复验）**：审查指出创建请求在幂等查找前访问实时扩展目录，导致响应丢失后禁用扩展返回 404、升级后同键可能 409；另有 PWA 接受另一表单完整 2xx 回执并清除原键。先加 API 禁用/升级及 390px 错表单反例；`.venv-win\Scripts\python.exe -m pytest tests/api/test_pwa_form_drafts_f08.py::PwaFormDraftApiTests::test_create_replays_original_receipt_after_provider_is_disabled_or_upgraded tests/integration/test_pwa_form_drafts_browser_f08.py::PwaFormDraftBrowserTests::test_complete_receipt_for_other_form_keeps_original_create_key -q -o addopts='' -p no:cacheprovider` → **2 failed、2 warnings**（API 404，浏览器错误显示“已创建”）。修复后同一命令 → **2 passed、2 warnings**。服务端现在先以原任务/扩展/表单及初始值/来源查冻结命令回执，同键异请求返回 409；只有新命令查询当前目录，创建指纹不包含实时目录或版本。PWA 先核对所选扩展、表单、目录版本、版本 1 与空值/全 `UNKNOWN` 初态，并完成渲染后才释放原键；错回执保留同键手动重试。Service Worker 缓存版本升为 v7，仅缓存固定静态资源；未改迁移历史。

**本次修复的定向验证**：`.venv-win\Scripts\python.exe -m pytest tests/api/test_pwa_form_drafts_f08.py tests/integration/test_pwa_form_drafts_browser_f08.py tests/api/test_pwa_tasks_f08.py tests/integration/test_pwa_browser_f08.py tests/unit/test_extension_supervision_service.py::SupervisorServiceTests::test_form_catalog_only_reads_enabled_declared_worker_descriptors tests/unit/test_extension_supervision_service.py::SupervisorServiceTests::test_form_catalog_starts_an_enabled_worker_in_public_process tests/contract/test_f05_contract_consistency.py::MigrationHistoryContract::test_f05_required_no_new_core_migration_beyond_the_frozen_baseline -q -o addopts='' -p no:cacheprovider` → **25 passed、2 warnings**；`$env:RUFF_CACHE_DIR = Join-Path $env:TEMP 'pa-f083-repair-ruff'; .venv-win\Scripts\python.exe -m ruff check src/personal_assistant/api/v1/tasks.py src/personal_assistant/core/tasks/form_drafts.py src/personal_assistant/infrastructure/memory/form_drafts.py src/personal_assistant/infrastructure/database/form_drafts.py tests/api/test_pwa_form_drafts_f08.py tests/integration/test_pwa_form_drafts_browser_f08.py` → `All checks passed!`；`$env:MYPY_CACHE_DIR = Join-Path $env:TEMP 'pa-f083-repair-mypy'; .venv-win\Scripts\python.exe -m mypy src/personal_assistant/core/tasks/form_drafts.py src/personal_assistant/infrastructure/memory/form_drafts.py src/personal_assistant/infrastructure/database/form_drafts.py src/personal_assistant/api/v1/tasks.py` → `Success: no issues found in 4 source files`；`node --check web/pwa/app.js`、`node --check web/pwa/service-worker.js` 与 `git diff --check` 均退出码 0（Git 仅提示工作树 LF/CRLF 将转换）。本轮按 F08.3 小阶段约定未跑全量、真实 PostgreSQL/Worker、Android/Cloudflare 或 F07 实站；数据库适配器重放与真实 Worker 目录路径仍需后续独立定向验证。F08.4 尚未启动，F08.1/F08.2 既有定向验收结论不变。

**F08.3 第二次独立审查 P2 修复（2026-10-01；仍为 `IN_PROGRESS`）**：前两项修复的新增反例获独立定向复验 **2 passed**，相关 F08 回归 **25 passed、2 warnings**；审查继而指出可信宿主以整数 `1` 播种证据后，同键改用布尔 `True` 会因 Python `1 == True` 被错误当作同一请求。先在内存 API 测试和真实 PostgreSQL 重连测试分别补反例，执行 `.venv-win\Scripts\python.exe -m pytest tests/api/test_pwa_form_drafts_f08.py::PwaFormDraftApiTests::test_trusted_seed_bool_retry_conflicts_with_integer_request_in_memory -q -o addopts='' -p no:cacheprovider` → **1 failed、2 warnings**，执行 `$env:PA_TEST_DATABASE_URL = 'postgresql://assistant:change-me@127.0.0.1:5432/assistant_test'; .venv-win\Scripts\python.exe -m pytest tests/integration/test_postgres_f01.py::PostgresF01Tests::test_f08_form_draft_seed_bool_retry_conflicts_after_reconnect -q -o addopts='' -p no:cacheprovider` → **1 failed**，两者均为预期的“未抛 `ConcurrentModificationError`”。草稿命令读端口随后返回存储中的原请求 SHA-256 和冻结回执；重放先以既有 canonical JSON 算法比较原摘要与本次摘要，随后核对回执任务/扩展/表单绑定，不更改审批 canonicalization、迁移或草稿 CAS。相同两项单例复跑分别 **1 passed、2 warnings** 和 **1 passed**。完整测试及静态检查结果见本节后续记录；F08.4 未启动。

**第二次修复的全量与 PostgreSQL/Worker 验证**：`$env:RUFF_CACHE_DIR = Join-Path $env:TEMP 'pa-f083-full-ruff'; $env:MYPY_CACHE_DIR = Join-Path $env:TEMP 'pa-f083-full-mypy'; ./scripts/test.ps1` → **1210 passed、127 skipped、7 warnings**，Ruff `All checks passed!`，Mypy `Success: no issues found in 220 source files`，退出码 0。首次 `./scripts/test-postgres.ps1` → **124 passed、3 failed、2 warnings**：F04 两项迁移期望漏列 F08.3 的 0009，F08.1 分页用例假定三个快速创建必有不同时间戳；实际契约按 `(created_at,id)` 排序。保留原断言目的并更新迁移名单、校验和及分页期望（从数据库读取真实时间戳，包含同时间戳 ID 次序），不改旧迁移或产品分页逻辑。`$env:PA_TEST_DATABASE_URL = 'postgresql://assistant:change-me@127.0.0.1:5432/assistant_test'; .venv-win\Scripts\python.exe -m pytest tests/integration/test_postgres_f01.py::PostgresF01Tests::test_f08_recent_tasks_are_paginated_after_reconnect_and_owner_scoped tests/integration/test_postgres_f01.py::PostgresF01Tests::test_f08_form_draft_seed_bool_retry_conflicts_after_reconnect tests/integration/test_postgres_f04.py::PostgresF04Tests::test_empty_database_applies_0005_and_rerun_is_noop tests/integration/test_postgres_f04.py::PostgresF04Tests::test_upgrade_from_0004_adds_only_later_migrations_and_keeps_checksums -q -o addopts='' -p no:cacheprovider` → **4 passed**；`./scripts/test-postgres.ps1` 复跑 → **127 passed、1 warning**，退出码 0。`$env:RUFF_CACHE_DIR = Join-Path $env:TEMP 'pa-f083-typed-ruff'; .venv-win\Scripts\python.exe -m ruff check src/personal_assistant/core/tasks/form_drafts.py src/personal_assistant/infrastructure/memory/form_drafts.py src/personal_assistant/infrastructure/database/form_drafts.py tests/api/test_pwa_form_drafts_f08.py tests/integration/test_postgres_f01.py tests/integration/test_postgres_f04.py` → `All checks passed!`；`$env:MYPY_CACHE_DIR = Join-Path $env:TEMP 'pa-f083-typed-mypy'; .venv-win\Scripts\python.exe -m mypy src/personal_assistant/core/tasks/form_drafts.py src/personal_assistant/infrastructure/memory/form_drafts.py src/personal_assistant/infrastructure/database/form_drafts.py` → `Success: no issues found in 3 source files`。真实 PostgreSQL 测试只创建/删除专用 `*_test` 临时库；真实 Android/Cloudflare 和 F07 实站未运行。F08.3 仍须独立复验，F08.4 未启动。

**F08.3 独立公共进程真实 Worker 补测（2026-10-01；仍为 `IN_PROGRESS`）**：用户已独立确认内存/真实 PostgreSQL 的 `1`/`True` 同键异请求反例与相邻 F08 定向集合，指出替身 runtime 无法证明独立公共进程的真实 `form.list`。新增 `tests/integration/test_pwa_form_catalog_worker_f08.py`：在专用 `*_test` 库创建并最终删除临时库，复制仓库自带 `example.echo` 表单扩展，确认安装/启用后关闭 Admin 容器；另起公共 API 操作系统进程，从持久 ENABLED 记录调用 `GET /api/v1/forms`。测试记录真实 Worker 的 `form.list` 调用，精确核对响应的 Manifest 扩展 ID、版本、form ID 与 Schema/UI Schema，并在公共进程结束后检查该 Worker PID 已退出。`$env:PA_TEST_DATABASE_URL = 'postgresql://assistant:change-me@127.0.0.1:5432/assistant_test'; .venv-win\Scripts\python.exe -m pytest tests/integration/test_pwa_form_catalog_worker_f08.py -q -o addopts='' -p no:cacheprovider` → **1 passed（6.32 秒）**。该测试使用真实 PostgreSQL/venv/Worker，没有访问真实业务站点、Android 或 Cloudflare；本轮没有重跑全量与 `test-postgres.ps1` 集合。F08.3 待用户独立整体验收，F08.4 不启动。

**本轮静态检查**：`$env:RUFF_CACHE_DIR = Join-Path $env:TEMP 'pa-f083-real-worker-ruff'; .venv-win\Scripts\python.exe -m ruff check tests/integration/test_pwa_form_catalog_worker_f08.py` → `All checks passed!`；`git diff --check` → 退出码 0（只有既有 LF/CRLF 转换提示）。初次 Ruff 报 1 处 E501 超长行，修正后通过；没有删减断言。本轮未修改产品代码、提交、推送或部署。

**F08.3 独立验收结论（2026-10-01，DONE）**：用户独立运行 `tests/integration/test_pwa_form_catalog_worker_f08.py` → **1 passed**，确认临时 PostgreSQL 库、明确安装确认、独立公共 API 进程、真实 `form.list`、Manifest ID/版本与 Worker 退出；此前独立运行 F08 相邻集合 **26 passed**、相关 PostgreSQL **4 passed**，另验证数据库草稿 CAS、重连同键重放和冲突回滚。目标文件 Ruff 与 `git diff --check` 独立复核通过。按用户结论，F08.3 标 `DONE`、F08.4 升为唯一 `NEXT`，F07/F08 整体仍 `IN_PROGRESS`。维护补项：`scripts/test-postgres.ps1` 的显式测试列表现已包含真实 Worker 用例；本次状态更新只定向复测该用例与脚本语法/列表，不重跑整个 PostgreSQL 套件或全量套件。独立进程内以 TestClient 调用路由；外部监听、Android/Cloudflare、F07 实站仍未验收。

**状态与测试脚本维护的本机命令（2026-10-01）**：`$env:PA_TEST_DATABASE_URL = 'postgresql://assistant:change-me@127.0.0.1:5432/assistant_test'; .venv-win\Scripts\python.exe -m pytest tests/integration/test_pwa_form_catalog_worker_f08.py -q -o addopts='' -p no:cacheprovider` → **1 passed**；`.venv-win\Scripts\python.exe -m pytest tests/contract/test_f07_contract_consistency.py tests/contract/test_f05_contract_consistency.py -q -o addopts='' -p no:cacheprovider` → **58 passed**。PowerShell `[System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path scripts/test-postgres.ps1), [ref]$tokens, [ref]$errors)` → 0 语法错误；文件文本检查确认 Worker 用例路径恰好列出 1 次；`git diff --check` → 退出码 0（仅 LF/CRLF 提示）。本轮未执行完整 `scripts/test-postgres.ps1` 或 `scripts/test.ps1`，不能将用户历史全量结果称为本轮全量结果。

**F08.4 本轮实施与验证（2026-10-01；唯一当前项，`IN_PROGRESS` 待独立复验）**：先写公共 API F07 试填不可批准、过期 410、只读草稿与宿主 MIME 校验、390px 审批交互失败反例。首次 `.venv-win\Scripts\python.exe -m pytest tests/api/test_pwa_approvals_f08.py -q -o addopts='' -p no:cacheprovider` → **1 failed**（原 API 没有 `review`），新增只读草稿单例 → **1 failed**（无 `preview`），宿主 MIME 两单例 → **2 failed**（无 `review`），浏览器文件 → **2 failed**（无正式材料/状态节点），过期单例 → **1 failed**（409 取代既有 410）；逐一最小修复后全部转绿。正式路径：`GET /api/v1/approvals/{id}` 只读取持久快照，同版本 READ `smail.send.preview` 获取当前草稿工件，宿主核对实际 MIME 哈希、信封、正文与附件；只有核对完成才返回 nonce。`POST approve` 重新核对后仅将状态 CAS 为 APPROVED。F07 试填与其他无完整材料的工具返回不可批准并在公共 POST 阻断。手机只访问助手 API，正文与绑定快照均按文本呈现；不完整批准回执显示“结果未确认”并重读服务端，不自动重发。0.2.0 smail 新包版本满足现有严格升级版本规则；测试仅在专用临时数据库安装并启用测试扩展，未对用户已有安装执行升级，也未连接真实邮箱。

**F08.4 精确成功命令**：`.venv-win\Scripts\python.exe -m pytest tests/api/test_pwa_approvals_f08.py tests/integration/test_pwa_approvals_browser_f08.py tests/unit/test_smail_drafts_f06.py::DraftVersionTests::test_review_reads_exact_current_mime_without_preparing_action tests/unit/test_mail_send_gateway_f06.py::MailSendGatewayTests::test_review_reads_bound_mime_without_dispatch_or_ledger_write tests/unit/test_mail_send_gateway_f06.py::MailSendGatewayTests::test_review_rejects_changed_mime_before_approval tests/unit/test_mail_send_gateway_f06.py::MailSendGatewayTests::test_review_tool_cannot_request_send_capability tests/contract/test_f06_contract_consistency.py::ManifestContract -q -o addopts='' -p no:cacheprovider` → **13 passed、2 warnings**。真实 PostgreSQL/独立 Worker/助手 API 端到端用例 `$env:PA_TEST_DATABASE_URL = 'postgresql://assistant:change-me@127.0.0.1:5432/assistant_test'; .venv-win\Scripts\python.exe -m pytest tests/integration/test_smail_worker_real.py::SmailCompositionRootTests::test_production_gateway_install_sync_draft_send_uninstall -q -o addopts='' -p no:cacheprovider` → **1 passed（193.44 秒）**：预览和批准后 SMTP 与发送台账均为空，后续 Gateway 才向本地模拟 TLS SMTP 派发；该用例之后已通过完整 PostgreSQL 脚本复跑。`$env:RUFF_CACHE_DIR = Join-Path $env:TEMP 'pa-f084-full-ruff'; $env:MYPY_CACHE_DIR = Join-Path $env:TEMP 'pa-f084-full-mypy'; ./scripts/test.ps1` → **1218 passed、128 skipped、7 warnings**，Ruff `All checks passed!`，Mypy `Success: no issues found in 220 source files`，退出码 0；`./scripts/test-postgres.ps1` → **128 passed、1 warning**，退出码 0。警告包括既有依赖弃用及测试缓存目录 WinError 5，不是用例失败。浏览器反例核对 CacheStorage URL 与内容均无审批 API/正文；Service Worker v8 只缓存固定静态资源。无真实邮箱、真实 ehall 最终提交、Android/Cloudflare 或 Web Push 测试；0.1.0→0.2.0 用户升级流程未在本轮实际执行。F08.4 实施完成不代表独立验收，F08.5 暂不启动。

**F08.4 独立验收 P2 附件清单修复（2026-10-01；`IN_PROGRESS`，待复验）**：独立审查复现“审批快照为空、实际 MIME 含 `important.pdf` 时仍返回 nonce”。先补 `tests/api/test_pwa_approvals_f08.py::PwaApprovalApiTests::test_mail_snapshot_must_match_actual_attachments_even_when_empty`，执行 `.venv-win\Scripts\python.exe -m pytest tests/api/test_pwa_approvals_f08.py::PwaApprovalApiTests::test_mail_snapshot_must_match_actual_attachments_even_when_empty -q -o addopts='' -p no:cacheprovider` → **1 failed、2 warnings**（预期红：空清单错误地 `ready=true`）；再补 `tests/unit/test_mail_send_gateway_f06.py::MailSendGatewayTests::test_prepare_call_binds_verified_attachment_material`，执行 `.venv-win\Scripts\python.exe -m pytest tests/unit/test_mail_send_gateway_f06.py::MailSendGatewayTests::test_prepare_call_binds_verified_attachment_material -q -o addopts='' -p no:cacheprovider` → **1 failed**（预期红：没有 `prepare_call`）。修复后 `src/personal_assistant/api/v1/approvals.py` 无条件比较持久与实际完整附件清单，空/非空双向、名称、大小、哈希不一致时 GET 不给 nonce、POST 拒绝批准；宿主 `MailSendExecutor.prepare_call` 调用当前草稿的只读 Worker/MIME 审核，以实际材料填充 `ToolCall.attachments`，不触发发送。`.venv-win\Scripts\python.exe -m pytest tests/api/test_pwa_approvals_f08.py tests/unit/test_mail_send_gateway_f06.py -q -o addopts='' -p no:cacheprovider` → **31 passed、2 warnings**；`$env:PA_TEST_DATABASE_URL = 'postgresql://assistant:change-me@127.0.0.1:5432/assistant_test'; .venv-win\Scripts\python.exe -m pytest tests/integration/test_smail_worker_real.py::SmailCompositionRootTests::test_production_gateway_install_sync_draft_send_uninstall -q -o addopts='' -p no:cacheprovider` → **1 passed（192.28 秒）**：专用临时 PostgreSQL 库、已确认安装/启用的真实 smail Worker、托管 `important.pdf` 工件、带附件审批快照和手机预览、批准前 0 次 SMTP、批准后仅对本地模拟 TLS SMTP 发送。`.venv-win\Scripts\python.exe -m ruff check src/personal_assistant/api/v1/approvals.py src/personal_assistant/infrastructure/mail/executor.py tests/api/test_pwa_approvals_f08.py tests/unit/test_mail_send_gateway_f06.py tests/integration/test_smail_worker_real.py` → `All checks passed!`；`.venv-win\Scripts\python.exe -m mypy src/personal_assistant/api/v1/approvals.py src/personal_assistant/infrastructure/mail/executor.py` → 2 个文件无问题；`.venv-win\Scripts\python.exe -m pytest tests/contract/test_f06_contract_consistency.py::ManifestContract -q -o addopts='' -p no:cacheprovider` → **5 passed**；`git diff --check` → 退出码 0（仅 LF/CRLF 提示）。本轮全量与 PostgreSQL 脚本在用户明确缩小验证范围后分别于约 16% 和 32% 中止，不宣称本轮全量通过；修复前的 1218/128、128 passed 仅作历史记录。未连真实邮箱、Android/Cloudflare 或真实 ehall；未提交、推送、部署或启动 F08.5。

**F08.4 定向独立复验结论（2026-10-01，DONE）**：用户独立运行相关 API、邮件单元、390px 浏览器及契约测试 **39 passed**，临时 PostgreSQL＋真实 Worker＋模拟 TLS SMTP **1 passed**；Ruff、Mypy、`git diff --check` 通过且未发现测试残留进程。P2 附件清单缺口关闭，F08.4 标为 `DONE`，F08.5 成为唯一当前项。修复后没有重跑全量套件，也没有连接真实邮箱；`prepare_call` 目前由集成测试调用，产品 Agent 自动创建邮件审批尚未接线，不在 F08.4 验收结论内。F07 和 F08 整体仍为 `IN_PROGRESS`。

**F08.5 实施记录（2026-10-01；`IN_PROGRESS`，待独立复验）**：先补 API、Gateway、390px 浏览器、Service Worker 与目的地反例，分别看到接口缺失、通知参数缺失、页面控件缺失、Push 事件缺失和带换行端点被接受的预期失败；没有删断言。最小实现新增 `GET /api/v1/push/config`、订阅/状态/撤销公共 API，`0010_f08_push_subscriptions.sql`，内存及 PostgreSQL 适配器，宿主 AES-GCM 密封订阅能力、RFC 8291 单记录加密与 RFC 8292 VAPID 发送。Push 服务的密码学置于 infrastructure；完整回归曾指出 core 导入 `cryptography` 违反 F03 分层契约，已移出并对架构契约复验通过。Gateway 只在持久准备 R2 审批后发送固定最小通知，Push 未知结果不自动重试。PWA 需用户点击授权，丢失/错目标 2xx 订阅回执时保留原键、显示未确认；Service Worker v9 仍只缓存四项固定静态资源，点击通知只打开 `/ui/?task=<ID>`。

**F08.5 已取得的定向证据**：`.venv-win\Scripts\python.exe -m pytest tests/api/test_pwa_push_f08.py tests/unit/test_web_push_transport_f08.py tests/unit/test_web_push_service_f08.py tests/unit/test_web_push_gateway_f08.py tests/integration/test_pwa_push_worker_f08.py tests/integration/test_pwa_browser_f08.py::PwaBrowserF08Tests::test_push_subscription_lost_receipt_retry_revoke_and_no_sensitive_cache -q -o addopts='' -p no:cacheprovider` → **9 passed、2 warnings**；`$env:PA_TEST_DATABASE_URL = 'postgresql://assistant:change-me@127.0.0.1:5432/assistant_test'; .venv-win\Scripts\python.exe -m pytest tests/integration/test_postgres_f01.py::PostgresF01Tests::test_push_subscription_sealed_and_restored_after_database_reconnect tests/integration/test_postgres_f01.py::PostgresF01Tests::test_empty_database_migrates_and_reruns_are_noops tests/integration/test_postgres_f01.py::PostgresF01Tests::test_database_with_only_0001_upgrades -q -o addopts='' -p no:cacheprovider` → **3 passed**，每项使用专用临时库并清理。`.venv-win\Scripts\python.exe -m pytest tests/contract/test_f03_contract_consistency.py tests/contract/test_f05_contract_consistency.py tests/unit/test_web_push_service_f08.py tests/unit/test_web_push_gateway_f08.py tests/api/test_pwa_push_f08.py -q -o addopts='' -p no:cacheprovider` → **35 passed、2 warnings**。上述是修复过程中的定向结果，不替代随后完整脚本结论。

**F08.5 完整回归与边界**：`$env:RUFF_CACHE_DIR = Join-Path $env:TEMP 'pa-f085-full-ruff'; $env:MYPY_CACHE_DIR = Join-Path $env:TEMP 'pa-f085-full-mypy'; $env:PYTEST_ADDOPTS = '-p no:cacheprovider'; ./scripts/test.ps1` → **1230 passed、129 skipped、6 warnings**（13:42），Ruff `All checks passed!`，Mypy `Success: no issues found in 228 source files`；`./scripts/test-postgres.ps1` → **129 passed、1 warning**（8:32），其中 `test_postgres_f01.py` 包含真实临时 PostgreSQL 订阅密文、重连、通知和撤销用例，Worker 仍为本地模拟外部服务。`node --check web/pwa/app.js`、`node --check web/pwa/service-worker.js`、`.venv-win\Scripts\python.exe -m pip check` 和 `git diff --check` 均退出码 0；最后一项只报 Windows LF/CRLF 提示。两次完整脚本结束码均为 0；警告为既有依赖弃用和 Windows pytest 缓存写入权限。无真实 Push 服务、Android、Cloudflare、真实邮箱或 ehall 最终提交；默认生产 `UnavailableSecretStore` 仍需 F09 才能持久提供 VAPID 私钥。F08.5 实现尚待独立复验，F08.6 暂不启动，F07 和 F08 整体仍为 `IN_PROGRESS`。

**F08.5 独立验收退回与定向修复（2026-10-02；仍为 `IN_PROGRESS`）**：用户独立复跑原 F08.5 定向集合 **9 passed**，并复现白名单移除后仍投递、旧 VAPID 密文中断健康订阅、同一 `Idempotency-Key` 更换 `auth` 仍返回 201 并覆盖密文三项缺口，因此 F08.5 未通过验收。先补内存服务、API、390px 浏览器和真实 PostgreSQL 重连反例：`tests/unit/test_web_push_service_f08.py` 的三个新增故障用例先失败，`tests/api/test_pwa_push_f08.py` 的同键异请求断言先失败，浏览器“需重新配置”断言先失败，`tests/integration/test_postgres_f01.py::PostgresF01Tests::test_push_command_replay_conflict_and_revoke_survive_reconnect` 先因缺少绑定命令而失败；另补白名单清空和“原键重放成功但当前订阅失效”两个浏览器反例，也先失败。修复在每次发送前复核当前白名单、端点 ID、浏览器密钥与密文，逐条隔离不可用记录；状态 API 返回 `active` 和 `reconfigure_required`，页面在订阅 POST 回执后也重读状态，允许关闭旧订阅且不称其可投递。订阅命令先查持久原回执，再按当前配置创建新请求；新 `0011_f08_push_subscription_commands.sql` 原子保存请求 SHA-256 与原回执，同键异请求 409，撤销后同键重放不复活记录；已应用的 `0010` 原样保留。Service Worker 静态缓存升至 v10，仍只列四项静态资源。

**修复后仅定向验证**：`.venv-win\Scripts\python.exe -m pytest tests/api/test_pwa_push_f08.py tests/unit/test_web_push_transport_f08.py tests/unit/test_web_push_service_f08.py tests/unit/test_web_push_gateway_f08.py tests/integration/test_pwa_push_worker_f08.py tests/integration/test_pwa_browser_f08.py::PwaBrowserF08Tests::test_push_subscription_lost_receipt_retry_revoke_and_no_sensitive_cache tests/contract/test_f03_contract_consistency.py tests/contract/test_f05_contract_consistency.py -q -o addopts='' -p no:cacheprovider` → **42 passed、2 warnings**。`$env:PA_TEST_DATABASE_URL = 'postgresql://assistant:change-me@127.0.0.1:5432/assistant_test'; .venv-win\Scripts\python.exe -m pytest tests/integration/test_postgres_f01.py::PostgresF01Tests::test_push_command_replay_conflict_and_revoke_survive_reconnect tests/integration/test_postgres_f01.py::PostgresF01Tests::test_push_subscription_sealed_and_restored_after_database_reconnect tests/integration/test_postgres_f01.py::PostgresF01Tests::test_empty_database_migrates_and_reruns_are_noops tests/integration/test_postgres_f01.py::PostgresF01Tests::test_database_with_only_0001_upgrades tests/integration/test_postgres_f04.py::PostgresF04Tests::test_upgrade_from_0004_adds_only_later_migrations_and_keeps_checksums -q -o addopts='' -p no:cacheprovider` → **5 passed**，夹具使用专用临时库并清理。`.venv-win\Scripts\python.exe -m ruff check src/personal_assistant/core/notifications src/personal_assistant/infrastructure/push src/personal_assistant/infrastructure/memory/push.py src/personal_assistant/infrastructure/database/push.py src/personal_assistant/api/v1/push.py tests/unit/test_web_push_service_f08.py tests/api/test_pwa_push_f08.py tests/integration/test_pwa_browser_f08.py tests/integration/test_postgres_f01.py tests/integration/test_postgres_f04.py tests/contract/test_f05_contract_consistency.py` → `All checks passed!`；`.venv-win\Scripts\python.exe -m mypy src/personal_assistant/core/notifications src/personal_assistant/infrastructure/push src/personal_assistant/infrastructure/memory/push.py src/personal_assistant/infrastructure/database/push.py src/personal_assistant/api/v1/push.py` → 8 个源文件无问题；`node --check web/pwa/app.js`、`node --check web/pwa/service-worker.js`、`git diff --check` 均退出码 0（最后一项仅 LF/CRLF 提示）。**修复后未重跑全量或完整 PostgreSQL/Worker 脚本，也未连接真实 Push 服务；上文 1230/129、129 passed 仅为修复前历史实施证据，不能称本轮全量全绿。**等待独立复验，F08.6 不启动；F07/F08 整体继续 `IN_PROGRESS`。

**F08.5 第二次独立验收退回与 410 恢复修复（2026-10-02；仍为 `IN_PROGRESS`）**：用户确认上一轮三项缺口的独立定向复验通过，但 390px Chromium 发现 Push 服务 410 后宿主撤销记录、浏览器仍持旧对象时，PWA 会把同一失效端点再 POST 并显示“已订阅”，未调用浏览器 `subscribe()`。先新增 `tests/integration/test_pwa_browser_f08.py::PwaBrowserF08Tests::test_410_revocation_replaces_stale_browser_subscription`，执行 `.venv-win\Scripts\python.exe -m pytest tests/integration/test_pwa_browser_f08.py::PwaBrowserF08Tests::test_410_revocation_replaces_stale_browser_subscription -q -o addopts='' -p no:cacheprovider` → **1 failed**（预期红：POST 仍为 `send/expired`，未换为 `send/replacement`）。修复后测试还核对清理返回 false、返回 true 但浏览器仍保留旧对象、重建后仍给旧端点时不应写入服务端或显示已订阅；传输与宿主单元测试分别确认 HTTP 410 只报告 `expired`、宿主随后撤销记录。实现仅在确认服务端当前无有效记录且没有结果未确认的原请求时，解除旧浏览器订阅并重读 `getSubscription()`；新订阅若仍使用旧失效端点则停止于 POST 前。旧请求结果未确认时保留原键；浏览器对象消失时可由用户关闭旧服务端记录。行为依据 [W3C Push API](https://www.w3.org/TR/push-api/) 的浏览器订阅解除/重新订阅模型。Service Worker 升为 v11，仍只缓存四项静态路径；契约第 21 节的旧 v9 文字已更新。

**本次修复后的定向证据**：`.venv-win\Scripts\python.exe -m pytest tests/api/test_pwa_push_f08.py tests/unit/test_web_push_transport_f08.py tests/unit/test_web_push_service_f08.py tests/unit/test_web_push_gateway_f08.py tests/integration/test_pwa_push_worker_f08.py tests/integration/test_pwa_browser_f08.py::PwaBrowserF08Tests::test_410_revocation_replaces_stale_browser_subscription tests/integration/test_pwa_browser_f08.py::PwaBrowserF08Tests::test_push_subscription_lost_receipt_retry_revoke_and_no_sensitive_cache tests/contract/test_f03_contract_consistency.py tests/contract/test_f05_contract_consistency.py -q -o addopts='' -p no:cacheprovider` → **45 passed、2 warnings**；`.venv-win\Scripts\python.exe -m ruff check tests/integration/test_pwa_browser_f08.py tests/unit/test_web_push_service_f08.py tests/unit/test_web_push_transport_f08.py`、`node --check web/pwa/app.js`、`node --check web/pwa/service-worker.js`、`git diff --check` 均退出码 0。用户上轮独立运行的全量 `scripts/test.ps1` 为 **1233 passed、130 skipped**，发生在这次 410 修复前；本轮未重跑全量、完整 PostgreSQL/Worker、真实 Push 或 Android/Cloudflare，不能用上轮数字宣称本轮全量通过。F08.5 待独立复验，F08.6 暂不启动；F07/F08 整体仍为 `IN_PROGRESS`。

**F08.5 独立复验与全量回归（2026-10-02；DONE）**：410 恢复修复后，独立复跑上条定向命令 **45 passed、2 warnings**；另运行 `.venv-win\Scripts\python.exe -m pytest tests/integration/test_pwa_browser_f08.py -q -o addopts='' -p no:cacheprovider` → **7 passed**，Ruff、两份 `node --check`、`git diff --check` 均通过。随后执行 `./scripts/test.ps1` → **1236 passed、130 skipped、6 warnings**，Ruff `All checks passed!`，Mypy `Success: no issues found in 228 source files`；执行 `./scripts/test-postgres.ps1` → **130 passed、1 warning**（pytest 缓存目录写入权限警告，不是测试失败）。首次 PostgreSQL 脚本启动被 Windows 沙箱账户错误 1909 阻止，未进入测试；获准改用非沙箱进程后整套执行并退出 0。上述均为本地与模拟外部服务的证据，不覆盖真实 Android、Cloudflare 或真实 Push 服务投递；这些是 F08.6 的验收范围。F08.5 标记 `DONE`，F08.6 升为唯一 `NEXT`、尚未启动；F07 与 F08 整体仍为 `IN_PROGRESS`。

**F08.6 本机预检（2026-10-02；BLOCKED）**：先在 `tests/api/test_boundaries.py::NetworkBoundaryTests::test_health_sidecar_has_no_task_admin_or_docs_routes` 加入精确路径反例，执行 `.venv-win\Scripts\python.exe -m pytest tests/api/test_boundaries.py::NetworkBoundaryTests::test_health_sidecar_has_no_task_admin_or_docs_routes -q -o addopts='' -p no:cacheprovider` → **1 failed**：非 `/healthz` 的 `/healthz/` 返回 307。仅在独立 `health_app` 关闭斜杠自动重定向；保留反例，另增真实本机 Uvicorn 进程黑盒 `tests/integration/test_health_boundary_f08.py`，核对 `/healthz` 的最小 200，以及 `/healthz/`、任务、管理、文档和其他路径的 404，POST 任务路由也为 404。这只验证应用在本机回环监听时的路由，不证明 Tailscale 真实映射。

**本轮实际命令与结果**：`.venv-win\Scripts\python.exe -m pytest tests/api/test_boundaries.py tests/api/test_cloudflare_access.py tests/unit/test_settings.py -q -o addopts='' -p no:cacheprovider` → **73 passed、5 warnings**；`.venv-win\Scripts\python.exe -m pytest tests/integration/test_health_boundary_f08.py -q -o addopts='' -p no:cacheprovider` → **1 passed**；`.venv-win\Scripts\python.exe -m pytest tests/integration/test_pwa_browser_f08.py -q -o addopts='' -p no:cacheprovider` → **7 passed**；`.venv-win\Scripts\python.exe -m pytest tests/integration/test_pwa_approvals_browser_f08.py tests/integration/test_pwa_form_drafts_browser_f08.py -q -o addopts='' -p no:cacheprovider` → **9 passed**；`.venv-win\Scripts\python.exe -m pytest tests/contract/test_f03_contract_consistency.py -q -o addopts='' -p no:cacheprovider` → **10 passed**；`$env:RUFF_CACHE_DIR = Join-Path $env:TEMP 'pa-f086-ruff-cache'; .venv-win\Scripts\python.exe -m ruff check src/personal_assistant/health_app.py tests/api/test_boundaries.py tests/integration/test_health_boundary_f08.py` → `All checks passed!`；`node --check web/pwa/app.js`、`node --check web/pwa/service-worker.js`、`git diff --check` 均退出码 0。首次 Ruff 用仓库默认缓存目录因写入权限失败，改用临时缓存后通过。本轮仅变更独立健康入口和相关测试/状态文档，未改共享状态、审批或 Outbox；未运行本轮全量和 PostgreSQL/Worker 脚本，F08.5 后的历史全量数字不能充作 F08.6 的全量结果。

**恢复 F08.6 所需条件**：用户准备受 Cloudflare 管理的固定域名及仅限本人身份的 self-hosted Access 应用，记录公开 origin、团队域和应用 AUD；Tunnel 的唯一发布路由只能指向本机公共入口 `http://127.0.0.1:8000`，不能映射管理 `8001` 或健康 `8010`。本机生产配置需真实 PostgreSQL、`PA_TRUST_CLOUDFLARE_ACCESS=true` 与上述 Access 参数，且三个进程仍只监听回环。准备可用 Android Chrome 手机，在该固定 HTTPS 域名安装 PWA 并由用户主动授权通知；准备 Tailscale 节点，使用私有 Serve 只代理 `http://127.0.0.1:8010`，不得使用 Funnel。当前默认生产 `SecretStore` 为 fail-closed 占位；真实 VAPID 私钥与订阅加密密钥的可信持久后端由 F09 补齐或另经明确契约验收，密钥、Tunnel token 和 Access JWT 不能写入仓库/聊天。部署完成后再从 Android 的蜂窝/Wi-Fi、断网/恢复、后台唤醒与锁屏通知执行真实测试；从另一 Tailscale 节点逐一探测非 `/healthz` 路径，并验证 API/CacheStorage 不含正文。官方配置参考：[Cloudflare Tunnel](https://developers.cloudflare.com/tunnel/get-started/)、[Access 应用](https://developers.cloudflare.com/learning-paths/clientless-access/access-application/create-access-app/)、[Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve)。用户已确认这些外部环境尚未准备，因此 F08.6 保持 `BLOCKED`，F07/F08 整体仍为 `IN_PROGRESS`；未连接真实 Push 服务、Android 或 Cloudflare，也未进行真实 ehall 操作。

**F08.6 环境准备协助（2026-10-04；仍 BLOCKED）**：用户授权尽力协助准备，随后明确没有域名与 Cloudflare 账号。此次在实际 Desktop 仓库 `main`（`deeb9b2`）继续，Codex 附加 worktree 为旧 F07 快照，未将其当作当前 F08 代码。保留此前未提交的 health 边界修复与文档，不提交、推送或部署。新增 `scripts/prepare_f08.py`：仅生成未激活模板、校验本地候选文件，不导入组合根或访问数据库；三个端口必须回环且固定为 8000/8001/8010，生产 PostgreSQL、Access 与固定 HTTPS DNS origin 缺一不可。拒绝默认密码、无效 DSN 端口、`${...}` 替换和会改变实际启动配置的继承 `PA_*`（不计 `PA_TEST_*`），只输出错误码而不回显材料。候选配置校验成功仍明确报告数据库未探测、部署的 Access 策略未验证及默认生产 Push 秘密后端不可用。

**本轮先红后绿**：初次运行 `.venv-win\Scripts\python.exe -m pytest tests/unit/test_f08_environment_preparation.py -q -o addopts='' -p no:cacheprovider` → **5 failed、2 passed**（工具尚不存在；两项拒绝路径当时没有证明实现）。最小实现后 **7 passed**。只读复核发现三个错误的放行路径，三个新增反例在修复前定向执行 → **3 failed、7 deselected**，随后补充 DSN 端口、字面值和解码后的默认密码检查。继承环境反例 `.venv-win\Scripts\python.exe -m pytest tests/unit/test_f08_environment_preparation.py -k inherited_configuration -q -o addopts='' -p no:cacheprovider` → **1 failed、10 deselected**：Uvicorn 的 dotenv `override=False` 会保留继承配置，检查器原先却只看文件；现显式拒绝冲突并要求检查与启动使用同一干净壳。

**本轮实际通过命令（在 Desktop 仓库根目录）**：

```powershell
.venv-win\Scripts\python.exe -B -m pytest tests/unit/test_f08_environment_preparation.py tests/api/test_boundaries.py tests/integration/test_health_boundary_f08.py -q -o addopts='' -p no:cacheprovider
# 16 passed, 2 warnings（两项依赖弃用警告）
$env:RUFF_CACHE_DIR = Join-Path $env:TEMP 'pa-f086-prepare-ruff'
$env:MYPY_CACHE_DIR = Join-Path $env:TEMP 'pa-f086-prepare-mypy'
.venv-win\Scripts\python.exe -B -m ruff check scripts/prepare_f08.py tests/unit/test_f08_environment_preparation.py
# All checks passed!
.venv-win\Scripts\python.exe -B -m mypy scripts/prepare_f08.py
# Success: no issues found in 1 source file
.venv-win\Scripts\python.exe scripts/prepare_f08.py prepare --output-dir var/deployment/f08-2026-10-04
# exit 0, created=true, activated=false（三份文件，位于忽略的 var/）
.venv-win\Scripts\python.exe scripts/prepare_f08.py check --env-file var/deployment/f08-2026-10-04/.env.template
# exit 1（预期）：数据库、origin、团队域及 AUD 尚未填写，不能视为可启动配置
git diff --check
# exit 0（仅 LF/CRLF 提示）
```

**本机安装事实与剩余条件**：`winget show --id Cloudflare.cloudflared --exact --source winget --disable-interactivity`、`winget install --id Cloudflare.cloudflared --exact --source winget --no-upgrade --silent --disable-interactivity` 均退出 0，官方 MSI 哈希已由 winget 核验；`& 'C:\Program Files (x86)\cloudflared\cloudflared.exe' --version` → **2026.9.3**。`winget show --id Tailscale.Tailscale --exact --source winget --disable-interactivity` 确认官方 **1.102.4**，对应 `winget install --id Tailscale.Tailscale --exact --source winget --no-upgrade --silent --disable-interactivity` 已下载并通过哈希核验，但仍等待 Windows UAC（`consent.exe`，Tailscale 可执行文件和服务尚未出现），不得称安装完成。现有 Codex 壳的 PATH 尚未刷新，检查工具只报告当前壳可找到的程序。Docker 已安装但引擎未运行；应用启动会迁移数据库、恢复状态与刷新 Worker，故此次未自动启动它或既有数据库。官方账号注册链接已请求在 Codex 浏览器打开（工具返回 queued），不代表注册完成。用户需本人完成系统安装确认、账号注册/登录、域名购买选择以及 Android 安装/权限；真实 Push 仍需 F09 的可信持久秘密后端或另行明确验收的实现。步骤与官方来源见 [环境准备指南](./F08_ENVIRONMENT_PREPARATION.md)。

**验证边界**：此次只改非部署准备脚本、相关测试及状态/指南，未改 API、持久状态、审批、Outbox 或迁移；只运行上述定向测试，不运行全量、PostgreSQL/Worker 或真实网络/站点回归。未启动 Tunnel、Serve/Funnel 或公网监听，没有真实邮件发送、ehall 操作或 Push 投递。F08.6 继续 `BLOCKED`，F07 与 F08 整体继续 `IN_PROGRESS`；后续仍先准备并核对 F08.6 条件。

**2026-10-04 准备续查（仍 BLOCKED）**：用户已取得 `yanshuheng.dpdns.org`、建立 Cloudflare 账号，并明确仍在 DNS 记录/名称服务器步骤，尚未 Active。`qd.je` 当前不在 PSL，未按此前笼统的建议继续转移或添加不合格子域；当前改为 PSL 中的 `dpdns.org` 后缀。新空域名若接入 UI 要求先有记录，可用官方 originless `A @ 192.0.2.0` Proxied/TTL Auto 临时占位，之后取得此 zone 的两条真实 NS，更新 DigitalPlat 委派并等 Active；同名 Tunnel CNAME 建立前移除临时 A，先建立完整主机名 Access，不将占位说成助手已上线。

**本轮只读环境命令**：`& 'C:\Program Files (x86)\cloudflared\cloudflared.exe' --version` → **2026.9.3**；`& 'C:\Program Files\Tailscale\tailscale.exe' version` → **1.102.4**；`Get-Service -Name Tailscale,com.docker.service -ErrorAction SilentlyContinue | Select-Object Name,Status` → **Tailscale Running、com.docker.service Stopped**；`Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in 8000,8001,8010,5432 } | Select-Object LocalAddress,LocalPort,OwningProcess` → 无目标监听条目；`& 'C:\Program Files\Docker\Docker\resources\bin\docker.exe' info --format '{{.ServerVersion}}'` → **exit 1**，Docker Linux 引擎管道不存在。之前的 winget 会话已不可查询，故不补写其退出码；安装事实由现在的程序版本与服务状态确认。未读取 `.env` 或凭据，未确认 tailnet 登录/Serve，未启动公共服务、数据库或 Tunnel。只读核对代码后确认：生产 PostgreSQL + Access origin/team/AUD 可支持普通任务、消息、SSE 和草稿的首次远程连接；F09 缺失的秘密后端仍阻断真实 Push/邮件凭据/远程模型，不需要为首次任务 PWA 连通而绕过它。页面挂载 `/ui/`；公开 Origin 本身不含该路径。此次只更新准备/状态文档，不重跑测试；`git diff --check` 退出 0。F08.6 与 F07/F08 整体状态不变。

**2026-10-04 域名、Access 与现成数据库配置续查（仍 BLOCKED）**：用户确认域名接入已完成，提供团队域 `falling-boat-ac78.cloudflareaccess.com` 与应用 AUD `b0d0614b762aff31eb214d187a71d192baa1ce82c5e3a480248212fdc499d44e`。DNS 查到 `abby.ns.cloudflare.com`、`alec.ns.cloudflare.com`。无认证、无 Cookie、禁止跟随重定向的 HTTP 头探测：`https://yanshuheng.dpdns.org/`、`/ui/`、`/api/v1/tasks` 均为 **302**，目标主机与路径匹配该团队 Access 登录；公开 `/cdn-cgi/access/certs` 为 **200** 且含非空公钥集合。没有打印登录查询串、JWT 或业务正文。仅证明未登录边缘保护与公钥服务可达，不能证明仅本人策略、给定 AUD 正确、带身份请求经 Tunnel 到助手、或手机可用。

本机 Docker 已由此前不可用变为可用，此次没有启动它：`& 'C:\Program Files\Docker\Docker\resources\bin\docker.exe' info --format '{{.ServerVersion}}'` → **29.8.0**；`& 'C:\Program Files\Docker\Docker\resources\bin\docker.exe' ps -a --filter label=com.docker.compose.project=lab1 --filter label=com.docker.compose.service=postgres --format '{{.Names}} | {{.Status}} | {{.Ports}}'` → **lab1-postgres-1、healthy、127.0.0.1:5432->5432/tcp**。用户选择沿用原助手数据库。当前仓库没有活动 `.env`，但 `Settings.from_env()` 和 `scripts/mail_real_readonly.py` 具有一致的默认本机 `assistant` 库配置，Compose 也采用对应默认设置；历史启动成功并不依赖存在 `.env`。不能再将“没有 `.env`”写成“没有连接配置”。`run-public.ps1` 仍要求活动 `.env`，新生产与 Access 参数尚未激活。

当前非激活候选命令及结果：

```powershell
.venv-win\Scripts\python.exe scripts/prepare_f08.py prepare --output-dir var/deployment/f08-access-2026-10-04 --public-origin https://yanshuheng.dpdns.org --team-domain falling-boat-ac78.cloudflareaccess.com --audience b0d0614b762aff31eb214d187a71d192baa1ce82c5e3a480248212fdc499d44e
# exit 0，created=true，activated=false
.venv-win\Scripts\python.exe scripts/prepare_f08.py check --env-file var/deployment/f08-access-2026-10-04/.env.template
# exit 1（预期）：缺 PA_DATABASE_URL、INVALID_DATABASE_URL；公开参数已填，数据库字段仍为空
```

曾请求从 Docker 提取连接凭据并写入候选；自动审批因“沿用原库未明确授权复制/持久化凭据、项目 SecretHandle 要求”拒绝，命令在执行前终止，没有提取或写入。后续采用安全范围更小的只读验证，直接使用仓库已有默认配置，无 Docker 凭据提取、无 DSN 保存：

```powershell
@'
import asyncio
import json
import asyncpg
from personal_assistant.settings import Settings

async def main():
    settings = Settings.from_env()
    dsn = settings.database_url.replace('postgresql+asyncpg://', 'postgresql://', 1)
    connection = None
    try:
        connection = await asyncpg.connect(dsn=dsn, timeout=8)
        result = await connection.fetchrow('SELECT 1 AS connected, current_database() AS database_name, current_user AS role_name')
        print(json.dumps({'connected': result['connected'] == 1, 'database': result['database_name'], 'role': result['role_name'], 'source': 'existing_repository_default', 'mutated': False}))
    except Exception as exc:
        print(json.dumps({'connected': False, 'error_type': type(exc).__name__, 'source': 'existing_repository_default', 'mutated': False}))
        raise SystemExit(1)
    finally:
        if connection is not None:
            await connection.close()

asyncio.run(main())
'@ | .venv-win\Scripts\python.exe -B -
# exit 0：connected=true，database=assistant，role=assistant，mutated=false
```

执行前只检查相关进程/用户/机器环境变量的名称及是否有值，未发现 `PA_*`/PostgreSQL/数据库覆盖项；没有打印凭据或连接串。查询从 Windows 宿主执行，未调用应用/storage startup，因此无迁移、恢复、Worker 启动或外部动作。它证明原库可用，不证明口令达到生产要求；默认口令仍被候选检查器拒绝，未删除该检查。当前数据库 adapter 没有数据库 SecretHandle 解析接口，正式配置只存 Secret Reference 的设计仍待 F09；任何临时本机 DSN 保存及更换口令须用户明确授权局部例外。已向用户呈现指定候选路径、旧默认连接串失效的影响及“不启动服务/公网”的范围，授权尚未收到。

本次配置调查后仅更新 README、TODO、环境指南和本证据文档，未重跑测试集合；上文 **16 passed、2 warnings** 属此前准备工具定向结果。未运行全量/完整 PostgreSQL/Worker/真实 Push/Android/真实 ehall，不把只读 `SELECT` 算成 PostgreSQL 套件验收。下一步仍在 F08.6：解决候选凭据方式并核验仅本人 Access 策略，之后另行授权助手/Tunnel 启动，继续真实 Android 与私有 health-only 验收；不启动 F09/F10。

**原库口令与临时本机候选（2026-10-04，用户随后明确授权）**：用户回复“允许更换口令并保存临时本机配置”，覆盖原库默认口令轮换、指定 Git 忽略候选的 DSN 保存及本轮 Secret Reference 设计的局部例外；没有授权启动助手或公网。先将该候选目录和文件的 DACL 替换为新建、禁用继承的 ACL，仅真人 `kunji` SID 与 `SYSTEM` 可完全访问，再验证执行身份及文件所有者均为 `kunji`；轮换后 ACL 再次复核通过。唯一秘密落盘位置为 `var/deployment/f08-access-2026-10-04/.env.template`，没有另存秘密备份或临时副本，未打印密码/DSN，未提取 Docker 环境凭据。

一次性 Python 宿主脚本经 stdin 交给 `.venv-win\Scripts\python.exe -B -`：核对 Git 忽略、规范化公开参数、原库/角色/回环端口、原容器 project/service/healthy；随机错误口令反例明确返回 `InvalidPasswordError`，确认宿主确实校验密码。先保存新随机 URL-safe 口令对应的 DSN 到唯一候选，`flush + fsync` 并读取验证后，调用以下精确参数（口令变量只通过 subprocess `input`，不进入 argv/env；不要将本轮更换命令重复当作检查执行）：

```python
command = [
    r'C:\Program Files\Docker\Docker\resources\bin\docker.exe',
    'exec', '-i', 'lab1-postgres-1', 'psql',
    '-X', '-w', '-v', 'ON_ERROR_STOP=1', '-1',
    '-h', '/var/run/postgresql', '-p', '5432',
    '-U', 'assistant', '-d', 'assistant',
    '-c', "SET password_encryption = 'scram-sha-256'",
    '-c', '\\password assistant',
]
result = subprocess.run(
    command, input=password + '\n' + password + '\n',
    capture_output=True, text=True, timeout=20,
    creationflags=subprocess.CREATE_NO_WINDOW,
)
```

`psql` 在客户端加密口令后发送 `ALTER ROLE`，不发送明文 SQL 口令，[官方说明](https://www.postgresql.org/docs/17/app-psql.html)。宿主捕获的 psql 输出没有回显。第一次一次性脚本预检查 **exit 1**：Access 团队域已被准备器规范为 `https://...`，脚本错误地期待裸域；在写入/更换前停止，候选数据库字段仍空。改用现有 `normalize_team_domain()` 核对后执行 **exit 0**：`psql_exit=0`、`rotation=verified`、`new_connection=true`、`old_password_rejected=true`（`InvalidPasswordError`）、`same_database_oid=true`、`application_started=false`、`migration_run=false`。新旧验证均是新建 Windows 宿主 asyncpg 连接；没有修改业务数据、权限、迁移或启动 Worker。只更换原 `assistant` 集群角色口令，因此它在其他库中的旧默认认证也失效，包括历史 `assistant_test` 默认 DSN；代码/Compose/测试示例未复制新秘密。后续测试须从本机配置读入凭据并继续使用独立 `_test` 临时库。

最终本地候选检查：

```powershell
.venv-win\Scripts\python.exe -B scripts/prepare_f08.py check --env-file var/deployment/f08-access-2026-10-04/.env.template
# exit 0：local_configuration_valid=true，errors=[]；三个工具均能在 PATH 找到
# 检查器本身不探测数据库，仍报告 database=not_probed；另行宿主连接验证见上
git diff --check
# exit 0（LF/CRLF 提示）；最后文档更新后再次执行
```

候选安全检查没有删改拒绝默认密码的断言。候选仍未激活为 `.env`，没有运行 Uvicorn、Tunnel、Serve/Funnel，没有提交/推送或部署。数据库 DSN 仍是用户授权的临时本机方案，不能宣称 F09 正式秘密后端已完成。此次只有配置操作/文档更新与上述局部验证，不重跑全量或 PostgreSQL/Worker 套件；F08.6 仍等待策略、部署与真机证据，F07/F08 整体仍 `IN_PROGRESS`。

只读浏览器续查已看到 zone Overview 的“Your domain is now protected by Cloudflare”及 Zero Trust 团队 `falling-boat-ac78`；没有修改网页设置。进入可见菜单的 Applications URL 后，读取应用列表超时，工具会话重置，尚未核实具体应用主机名、AUD 和允许身份；已请用户提供 Policies 规则区域的截图/Include 条件，不请求秘密。域名激活证据已足够，不重复触发 NS 重新检查；仍不以登录重定向或团队概览代替仅本人策略验收。

**Access 策略截图及准备续查**：用户提供 `Email_policy` 截图，Action 为 Allow，Include 为一个指定邮箱，Used by applications 为 1，绑定 Self-hosted 的 `yanshuheng.dpdns.org`；随后明确回答完整 Policies 列表“只有 Email_policy 这一条”。单身份 Allow 的准备条件据此确认；没有将私人邮箱或截图副本写入 Git。唯一策略结论来自用户确认，不是自动独立读取，宿主有效 JWT/AUD 与 Tunnel 链路仍未验收。自动打开应用列表再次超时，没有因此重复要求用户提供秘密或改动策略。

本轮实际环境命令与结果：

- `Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in 8000,8001,8010,5432 } | Select-Object LocalAddress,LocalPort,OwningProcess` → 无可见条目；不能据此断言 Docker 数据库不可达。
- `Get-Process -Name cloudflared -ErrorAction SilentlyContinue | Select-Object Id,ProcessName` → 初次盘点无进程；`Test-Path C:\Users\kunji\.cloudflared` → false。
- `& 'C:\Program Files\Docker\Docker\resources\bin\docker.exe' info --format '{{.ServerVersion}}'` → **29.8.0**；`& 'C:\Program Files\Docker\Docker\resources\bin\docker.exe' ps -a --filter label=com.docker.compose.project=lab1 --filter label=com.docker.compose.service=postgres --format '{{.Names}} | {{.Status}} | {{.Ports}}'` → **lab1-postgres-1、healthy、127.0.0.1:5432->5432/tcp**。
- `.venv-win\Scripts\python.exe -B -` 中用 `dotenv_values(..., interpolate=False)` 在进程内读取唯一候选 DSN，直接 `asyncpg.connect(timeout=8) → SELECT 1 → close` → **candidate_database_connection=true、mutated=false、exit 0**，未输出 DSN/密码；解决上述 Windows 端口枚举不完整的具体疑问，没有迁移或启动应用。
- 宿主 Python 以 `capture_output=True` 分别调用 `C:\Program Files\Tailscale\tailscale.exe status --json` 和 `serve status --json`，只输出字段概要，不输出登录 URL/节点身份：两项 **exit 0**；**BackendState=NeedsLogin、self_online=false、peer_count=0、Serve 配置为空、无 TCP/Web/Funnel 映射**。安装与 Running 服务不能代替登录证据。
- `& 'C:\Program Files (x86)\cloudflared\cloudflared.exe' tunnel login --help` 与 `tunnel --help` → **exit 0**，核对当前已安装 CLI 的官方登录/创建/路由/运行命令。

为用户已授权的环境准备，先创建本机 `C:\Users\kunji\.cloudflared`，以新受保护 ACL 仅允许本人 `kunji` 和 SYSTEM；核验身份和两条 Allow ACE 后，通过隐藏控制台的宿主子进程调用精确 argv `['C:\\Program Files (x86)\\cloudflared\\cloudflared.exe', 'tunnel', 'login']`。实际已输出 **cloudflared_login_started=true、directory_acl_restricted=true、authorization_browser_requested=true、tunnel_started=false**；登录 URL、回调材料及 CLI 原始输出均未回显。此时命令仍在等待本人网页授权，没有登录完成或生成证书的证据；登录进程最长等待五分钟，不把超时或沉默当作同意。按 computer-use 技能，由本人在官方页面选择域名并完成授权；没有自动操作登录、验证码或权限确认。该步骤不创建 Tunnel、不更改 DNS、不启动 public/Admin/Worker。后续若获得部署许可，直接让 public 读取唯一候选文件，不另存根 `.env`；启动会校验/应用待执行迁移及安全恢复台账，须在许可中明确，不称只读。

此轮仅准备、环境只读核查和文档更新，没有新业务代码变更，不重跑全量、PostgreSQL/Worker 套件或真机测试。下一项仍是 F08.6：完成本人 cloudflared 授权、本人 Tailscale 登录，再按仓库发布门禁验证并取得明确启动/发布许可，才能进入固定 HTTPS 手机连接；不跳转 F09/F10。F08.6 保持 `BLOCKED`，F07/F08 整体保持 `IN_PROGRESS`。

**Cloudflare 授权、未启用 Tunnel 与双节点续查（仍 BLOCKED）**：本人回复官方页面“已授权”，原登录子进程退出 **0**，`cert.pem` 存在。证书内容未读取/回显。生成后 ACL 核对发现目录及继承的证书出现额外 CodexSandboxUsers 读取权限；用 `Set-Acl` 改现有文件失败（缺 `SeSecurityPrivilege`），随后使用 `icacls` 只修改 DACL 成功。证书与 Tunnel JSON 各自禁用继承，复查受保护且仅本人 `kunji` 与 SYSTEM 两条 FullControl Allow ACE。数据库候选仍单独受限。没有用目录权限推定后来文件权限安全。

CLI 命令在宿主子进程中捕获输出，不回显认证链接、凭据或完整节点信息。按先查询再注册的顺序实际运行：

```powershell
& 'C:\Program Files (x86)\cloudflared\cloudflared.exe' tunnel --origincert C:\Users\kunji\.cloudflared\cert.pem list --output json
# exit 0；初始零个 Tunnel（空结果为 JSON null，解析时使用 `json.loads(...) or []`）
& 'C:\Program Files (x86)\cloudflared\cloudflared.exe' tunnel --origincert C:\Users\kunji\.cloudflared\cert.pem create --output json --credentials-file C:\Users\kunji\.cloudflared\lab1-assistant-f086.json lab1-assistant-f086
# exit 0；仅预注册资源，没有 DNS 路由或连接
```

重新查询确认名称 `lab1-assistant-f086`、UUID `675e44a7-276e-4d59-ab87-ceb7c5ad02ee`、**active_connections=0**。只把 UUID 和私有凭据路径引用填入 Git 忽略的 `var/deployment/f08-access-2026-10-04/cloudflared.template.yml`；固定主机名代理 public 回环 8000、兜底 404，metrics 只绑定 `127.0.0.1:20241`，禁用自动更新。实际静态验证命令：

```powershell
& 'C:\Program Files (x86)\cloudflared\cloudflared.exe' tunnel --config var/deployment/f08-access-2026-10-04/cloudflared.template.yml ingress validate
& 'C:\Program Files (x86)\cloudflared\cloudflared.exe' tunnel --config var/deployment/f08-access-2026-10-04/cloudflared.template.yml ingress rule https://yanshuheng.dpdns.org/ui/
& 'C:\Program Files (x86)\cloudflared\cloudflared.exe' tunnel --config var/deployment/f08-access-2026-10-04/cloudflared.template.yml ingress rule https://unmatched.example.test/ui/
# 均 exit 0；固定主机名匹配 http://127.0.0.1:8000，其他主机名匹配 http_status:404
```

尚未运行 `tunnel route dns`、`tunnel run` 或 Uvicorn。首次列表空值的本机解析曾触发 `TypeError`，修正解析后才注册；没有把解析失败当作授权或服务失败。DNS 同名占位/既有记录仍须在路由前检查，不用覆盖参数盲改。

Tailscale 登录也由本人完成。先前“已登录”实际仅完成网页账号登录，当时 CLI 仍 `NeedsLogin`；本人随后登录 Windows 客户端，实际为 `Running`、电脑在线。Android 完成本人登录和 VPN 确认后，本轮只读 `status --json` 的字段概要为 **known_peer_count=1、online_peer_count=1、online_android_peer_count=1、self_online=true**。`serve status --json` 的真实 JSON 映射计数 **TCP=0、Web=0、AllowFunnel=0**。一次 PowerShell 包裹空属性得到伪计数 1，已用 Python `len(state.get(key) or {})` 纠正，不能视作已有 Serve 配置。没有启动健康 sidecar、Serve/Funnel 或访问任务/审批。电脑与手机在线不等于手机到助手或私有 health 已通过。

当前候选和路径准备完成；首次发布仍按用户“不自行部署”的要求等待明确许可。按 AGENTS.md 在批准发布前运行门禁，结果如下；不能用这些本地结果宣称 Android/真实网络验收已完成。生产数据库配置不传入全量子进程。PostgreSQL 子进程仅从唯一候选在内存中派生独立 `/assistant_test` DSN，核对回环 5432、assistant 角色、`_test` 后缀后传入 `PA_TEST_DATABASE_URL`；凭据不进入命令参数/报告/额外文件。临时库测试属于集群写操作，不是只读。

**首次启动前原库只读迁移预检**：使用唯一候选的 DSN，经 `.venv-win\Scripts\python.exe -B -` 直接建立 asyncpg 连接，在 `transaction(readonly=True)` 查询迁移账本；不调用应用或 storage startup。确认默认搜索路径解析的账本与 `public.schema_migrations` 相同。当前 **7 个已应用版本、checksum_mismatches=[]、unknown_applied_versions=[]**；原始 SQL 字节 SHA-256 与账本一致。待执行版本精确为：

- `0008_f07_browser_session_uniqueness`
- `0009_f08_form_drafts`
- `0010_f08_push_subscriptions`
- `0011_f08_push_subscription_commands`

启动许可须包含上述迁移。0008 会收敛同任务多余的非终态浏览器会话，多条 `EXECUTING/UNKNOWN` 则拒绝执行；其他三份新增草稿/Push 表。只读统计结果 **ambiguous_browser_task_count=0、migration_0008_converged_session_count=0、expired_mail_execution_count=0**；候选未配置 browser broker。未查询任务、草稿或邮件正文，也未实际迁移。每份迁移单独事务提交，整批不是一个事务。预检使用的查询为：

```sql
SELECT to_regclass('schema_migrations') = to_regclass('public.schema_migrations');
SELECT version, checksum FROM public.schema_migrations ORDER BY version;
WITH open_groups AS (
    SELECT task_id, count(*) AS open_count,
           count(*) FILTER (WHERE state IN ('EXECUTING', 'UNKNOWN')) AS pending_count
    FROM public.browser_sessions
    WHERE state NOT IN ('SUCCEEDED', 'FAILED', 'CANCELLED')
    GROUP BY task_id
)
SELECT count(*) FILTER (WHERE pending_count > 1) AS ambiguous_task_count,
       coalesce(sum(greatest(open_count - 1, 0)), 0)::bigint AS converged_session_count
FROM open_groups;
SELECT count(*) FROM public.mail_delivery_actions
WHERE status = 'EXECUTING' AND lease_expires_at IS NOT NULL AND lease_expires_at <= now();
```

**当前工作区发布门禁（HEAD `deeb9b2`，含未提交 F08.6 预检/准备修改）**：宿主 Python 以 `capture_output=True` 在干净子进程中运行，不继承 `PA_*`/PostgreSQL/数据库配置，脱敏后输出结果；全量脚本精确 argv 为：

```python
child_env = {k: v for k, v in os.environ.items()
             if not k.startswith(('PA_', 'PG', 'DATABASE_', 'POSTGRES_'))}
command = ['powershell.exe', '-NoProfile', '-NonInteractive',
           '-Command', '& .\\scripts\\test.ps1']
result = subprocess.run(command, env=child_env, capture_output=True, text=True,
                        creationflags=subprocess.CREATE_NO_WINDOW)
```

实际 `scripts/test.ps1` **exit 0：1248 passed、130 skipped、7 warnings，751.26s；Ruff 通过，Mypy 228 source files 通过**。7 warnings 包含依赖弃用、测试用短 HMAC 及旧 `.pytest_cache` 权限警告；摘要之后另有两条 Windows asyncio 已关闭管道的 `PytestUnraisableExceptionWarning`。没有删除断言、改为跳过或把警告抹去。PostgreSQL 用例在全量子进程中按原夹具要求跳过，另行门禁结果记录后才能宣称其通过。

本轮实际静态命令：

```powershell
node --check web/pwa/app.js
node --check web/pwa/service-worker.js
.\.venv-win\Scripts\python.exe -B -m pip check
.\.venv-win\Scripts\python.exe -B -m ruff check --no-cache scripts/prepare_f08.py tests/unit/test_f08_environment_preparation.py
.\.venv-win\Scripts\python.exe -B -m mypy --cache-dir var/cache/f086-mypy-preflight scripts/prepare_f08.py
```

以上均 **exit 0**；pip 无依赖冲突，Ruff 通过，准备脚本 Mypy 1 source file 通过。Mypy 初次指定 Windows `--cache-dir NUL` 触发 **exit 1 INTERNAL ERROR**，改为 Git 忽略的普通缓存目录后通过；没有因此修改实现或降低类型检查。

适用 PostgreSQL/Worker 脚本的静态子进程命令及本机 DSN 派生方式：

```python
values = dotenv_values('var/deployment/f08-access-2026-10-04/.env.template',
                       interpolate=False)
original = (values.get('PA_DATABASE_URL') or '').replace(
    'postgresql+asyncpg://', 'postgresql://', 1)
parts = urlsplit(original)
if not (parts.scheme == 'postgresql' and parts.hostname == '127.0.0.1'
        and parts.port == 5432 and unquote(parts.username or '') == 'assistant'
        and parts.path == '/assistant' and not parts.query and not parts.fragment):
    raise SystemExit(1)
test_dsn = urlunsplit((parts.scheme, parts.netloc, '/assistant_test', '', ''))
assert urlsplit(test_dsn).path.endswith('_test')
child_env = {k: v for k, v in os.environ.items()
             if not k.startswith(('PA_', 'PG', 'DATABASE_', 'POSTGRES_'))}
child_env['PA_TEST_DATABASE_URL'] = test_dsn
command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
           '& .\\scripts\\test-postgres.ps1 -DatabaseUrl $env:PA_TEST_DATABASE_URL']
result = subprocess.run(command, env=child_env, capture_output=True, text=True,
                        creationflags=subprocess.CREATE_NO_WINDOW)
```

上述 loader 经 `.venv-win\Scripts\python.exe -B -` 运行，捕获输出后用原 DSN、派生 DSN、密码及 URL 模式脱敏。**exit 0：130 passed、1 warning，348.69s**；警告为相同的旧 pytest 缓存权限问题。含真实 PostgreSQL 与真实扩展 Worker（包括 F08 表单目录 `form.list`）；外部邮件/ehall/Push 服务仍为测试替身，不是实际投递或真实站点验收。执行前后分别只读查询 `pg_database` 并在内存比较名称集合，**new_databases_after_cleanup=0、missing_preexisting_databases=0**；临时库已清理，原 `assistant` 未作为测试目标。没有输出或另存其凭据。

最后本机只读核对 Tailscale：MagicDNS suffix 与 self DNSName 均存在（不记录私人节点名），尚未验证 HTTPS 证书或 Serve 可达性。用 `socket.connect_ex` 探测回环 `8000/8001/8010/20241`，均不能建立连接，不发 HTTP/业务请求；助手/Admin/health/Tunnel metrics 未启用。控制台应用只读读取再次超时，不能声称 DNS 记录已经核对或已发布；路由前检查同名记录的边界不变。

收尾 `Get-Acl` / `GetAccessRules` 只读核对：唯一 `.env.template`、`cert.pem` 和 Tunnel JSON 各自受保护、两条本人/SYSTEM Allow ACE，无其他身份，未读取其内容。`Get-CimInstance Win32_Process` 按 Python 命令行模式只统计、不回显参数：**assistant_or_worker_python_process_count=0**；`Get-Process cloudflared` 为 **0**。默认沙箱的 CIM 查询曾拒绝访问，随后获自动审批的只读查询成功；没有终止进程。`git check-ignore -v` 确认两份候选及临时 Mypy 缓存均由 `var/` 忽略；`git diff --check` **exit 0**（仅 LF/CRLF 提示）。本次续查仅更新 README、TODO、本指南及 NEXT_STEPS 和获准的未启用本机模板，不修改业务代码，不提交/推送/部署。F08.6 保持 `BLOCKED`，待明确启动/发布授权及真实 Android/网络/Push 验收，F07/F08 整体保持 `IN_PROGRESS`。

**2026-10-05 明确授权后的首次接通（仍 BLOCKED）**：前一条是发布前快照。本人随后明确许可启动原助手、应用原库0008–0011及安全恢复、固定域Access+Tunnel和仅health8010的私有Serve。公共进程直接读取唯一获准保存DSN的 `.env.template`，清除继承的 `PA_/PG/DATABASE_/POSTGRES_` 配置，不复制根 `.env`，保留原owner、扩展及材料目录。以隐藏子进程运行，日志禁用请求访问记录，日志文件创建后先限制为本人/SYSTEM；运行元数据只记录进程/端口/配置状态，不含秘密，并置于受限Git忽略的 `var/deployment/f08-access-2026-10-04/`。

实际启动参数如下（已运行，勿重复启动）；`--env-file` 与 `--config` 在执行时使用实际Desktop仓库下的绝对路径：

```powershell
.\.venv-win\Scripts\python.exe -m uvicorn personal_assistant.app:app --host 127.0.0.1 --port 8000 --no-access-log --env-file var/deployment/f08-access-2026-10-04/.env.template
.\.venv-win\Scripts\python.exe -m uvicorn personal_assistant.health_app:app --host 127.0.0.1 --port 8010 --no-access-log
& 'C:\Program Files (x86)\cloudflared\cloudflared.exe' --no-autoupdate tunnel --config var/deployment/f08-access-2026-10-04/cloudflared.template.yml run 675e44a7-276e-4d59-ab87-ceb7c5ad02ee
```

原库启动后只读复核：**11个已应用迁移、checksum全部匹配、pending空**，未重写历史迁移。应用不会在启动时自动发送邮件或提交ehall。public包装进程12100/监听6572，health包装23548/监听14976，cloudflared8928；监听只在127.0.0.1的8000/8010/20241，8001未启动。Tunnel metrics `/ready`200，证明已建立连接。

首次 `route dns` 不带覆盖参数返回同名记录冲突，未覆盖。截图明确唯一根记录是临时A192.0.2.0/Proxied；自动审批仍拒绝首次覆盖尝试，命令未执行，理由是此前一般接入许可不够精确。本人随后明确批准替换这一占位记录，才运行下面精确命令，**exit0**，根域改为指定Tunnel的Proxied CNAME：

```powershell
& 'C:\Program Files (x86)\cloudflared\cloudflared.exe' tunnel --origincert 'C:\Users\kunji\.cloudflared\cert.pem' route dns --overwrite-dns 675e44a7-276e-4d59-ab87-ceb7c5ad02ee yanshuheng.dpdns.org
```

接通后本机GET黑盒**12项通过**：8000的缺失/伪造JWT拒绝401，敏感API `no-store`，CSP/nosniff/no-referrer；8010仅精确 `/healthz`最小200，其余七条404。最初探测错误地要求非敏感health也带 `no-store` 而失败；核对现有中间件只为 `/api/`设置该头后纠正探测条件，保留所有敏感API断言，未修改实现或既有测试。无Cookie、不跟随重定向的实际公网GET**3项通过**：`/`、`/ui/`、`/api/v1/tasks?limit=1`均302到预期团队Access登录，未输出动态查询串。

Tailscale初次Serve提示tailnet未启用并等待本人同意，超时后退出，初始诊断包装误拼接bytes/str产生TypeError；修正包装解码后读取原CLI提示，未据此改产品代码。官方启用页由工具请求打开，返回queued，不当作页面已呈现的证据。本人完成账号功能启用并反馈误选Funnel；只读复核当时TCP/Web映射均空、没有实际公开转发。随后运行已获授权的私有Serve命令**exit0**：

```powershell
& 'C:\Program Files\Tailscale\tailscale.exe' serve --bg --https=443 http://127.0.0.1:8010
& 'C:\Program Files\Tailscale\tailscale.exe' serve status --json
```

JSON只在内存解析并输出脱敏摘要：**TCP1、Web1、唯一Proxy=http://127.0.0.1:8010、AllowFunnel=false**。账号级Funnel资格本次未撤销，未声称关闭该账号开关；实际没有Funnel转发。私有HTTPS证书验证开启，同节点 `/healthz`200且仅 `{"status":"ok"}`，`/healthz/`、`/`、`/api/v1/tasks`、`/api/v1/approvals`、`/admin/v1/extensions`、`/docs`、`/openapi.json`全404，**八项通过**；这不是Android跨设备证据。私人节点主机名只写忽略的runtime元数据，不入Git。

**登录排障与当前复查**：手机截图确认处于助手的Access登录页，只提供Cloudflare账号身份并提示无权访问，未证明宿主JWT/AUD或手机PWA已通过。本人添加One-time PIN后，团队列表截图确认该provider存在；本人又明确表示应用已保存，应用Login methods截图显示Accept all关闭、仅One-time PIN、Cloudflare One Client自动认证关闭。Email_policy单邮箱规则不改，不把OTP方法单独加入Include以扩大允许集合。已请本人用Android Chrome新无痕会话打开助手URL、输入该允许邮箱的验证码；不收集验证码/Cookie/JWT。有效会话及任务/SSE状态仍待反馈。[官方OTP配置](https://developers.cloudflare.com/cloudflare-one/integrations/identity-providers/one-time-pin/)

在实际Desktop仓库运行下面只读探测，**exit0、deployment_boundary_checks=24、passed=true**。只GET、无代理环境、不跟随重定向、默认开启TLS证书验证；不读取凭据文件或业务正文。24项由public四项、两个health入口各八项、公网边缘三项、Tunnel ready一项组成，不与此前检查相加宣称新测试总数：

```powershell
@'
import json
from pathlib import Path
from urllib.parse import urlsplit
import httpx

checks = 0
try:
    runtime = json.loads(Path('var/deployment/f08-access-2026-10-04/runtime.json').read_text(encoding='utf-8'))
    assert runtime['serve_configured'] and not runtime['funnel_enabled']
    private = runtime['health_private_origin']
    assert urlsplit(private).scheme == 'https' and urlsplit(private).hostname.endswith('.ts.net')
    blocked = ('/healthz/', '/', '/api/v1/tasks', '/api/v1/approvals', '/admin/v1/extensions', '/docs', '/openapi.json')
    with httpx.Client(timeout=15, trust_env=False, follow_redirects=False) as client:
        for path, headers in (('/healthz', {}), ('/ui/', {}), ('/api/v1/tasks?limit=1', {}), ('/api/v1/tasks?limit=1', {'Cf-Access-Jwt-Assertion': 'not-a-jwt'})):
            response = client.get('http://127.0.0.1:8000' + path, headers=headers)
            assert response.status_code == 401
            assert response.headers.get('x-content-type-options') == 'nosniff'
            assert response.headers.get('referrer-policy') == 'no-referrer'
            policy = response.headers.get('content-security-policy', '')
            assert "script-src 'self'" in policy and "frame-ancestors 'none'" in policy
            if path.startswith('/api/'):
                assert 'no-store' in response.headers.get('cache-control', '')
            checks += 1
        for origin in ('http://127.0.0.1:8010', private):
            response = client.get(origin + '/healthz')
            assert response.status_code == 200 and response.json() == {'status': 'ok'}
            checks += 1
            for path in blocked:
                assert client.get(origin + path).status_code == 404
                checks += 1
        for path in ('/', '/ui/', '/api/v1/tasks?limit=1'):
            response = client.get('https://yanshuheng.dpdns.org' + path)
            assert response.status_code == 302
            target = urlsplit(response.headers['location'])
            assert target.scheme == 'https' and target.hostname == 'falling-boat-ac78.cloudflareaccess.com'
            assert target.path.startswith('/cdn-cgi/access/login')
            checks += 1
        assert client.get('http://127.0.0.1:20241/ready').status_code == 200
        checks += 1
    print(json.dumps({'deployment_boundary_checks': checks, 'passed': True, 'scope': 'loopback, unauthenticated edge, same-node private HTTPS; no Android or authenticated API'}))
except Exception as error:
    print(json.dumps({'passed': False, 'completed_checks': checks, 'error_type': type(error).__name__}))
    raise SystemExit(1)
'@ | .\.venv-win\Scripts\python.exe -B -
```

本次配置及记录更新未新增业务代码；发布前本工作区完整门禁的1248/130与PostgreSQL/Worker130仍为上述已执行证据，没有重跑或套用旧报告数字。后台进程没有服务守护或开机自启，电脑睡眠/关机会影响入口；F09仍缺可信生产SecretStore，真实Push、邮件凭据、远程模型继续fail-closed，产品Agent自动创建邮件动作仍未接线。Android首次登录/SSE与私有health三项的本人反馈已收到；Chrome安装、网络切换/后台恢复/缓存、其余跨设备路径和端口、真实Push及最终两条E2E尚未验收；没有真实邮箱发送、ehall申请或最终提交，不启动后续编号，不提交/推送。F08.6保持BLOCKED，F07/F08整体IN_PROGRESS。

**本人的手机首次连通与私有健康反馈（2026-10-05）**：本人明确回复“任务列表正常，已连接”，随后补充“手机登陆完成了”及手机截图。截图地址为固定助手域名，助手页显示“暂无任务”和“事件连接已建立，正在同步服务端状态”；记录为Android浏览器首次登录/任务列表/SSE连通的本人反馈及截图证据，未收集Cookie/JWT或验证码。截图不能确认Chrome、安装PWA、版本冲突、敏感缓存或网络切换，不把这些写成通过。截图不复制到Git，避免收录私人账号信息。

本人保持Tailscale在线后又明确回复“health正常，另两项均Not Found”，对应私有health域的 `/healthz`、`/api/v1/tasks`、`/admin/v1/extensions`。三项为Android跨设备人工反馈，不含手机网络面板的原始HTTP状态码；与上述24项自动探测单列，不能相加当作pytest数量。精确斜杠、审批、docs/openapi/根路径及直接公共/管理端口的跨设备检查仍待补齐。下一步已请求本人使用Android Chrome普通窗口登录，检查“添加到主屏幕”的实际安装选项；无痕窗口不作为安装验收环境。当前Manifest未声明icons，Chrome安装菜单/标准安装能力仍需核对，不能把桌面快捷方式当作完整PWA安装通过。[Chrome安装说明](https://support.google.com/chrome/answer/9658361?co=GENIE.Platform%3DAndroid&hl=zh-Hans)、[Manifest图标要求](https://web.dev/articles/add-manifest)

本次收尾静态与权限检查：`git diff --check` **exit0**（仅LF/CRLF提示）；`git check-ignore -v var/deployment/f08-access-2026-10-04/runtime.json var/deployment/f08-access-2026-10-04/runtime-public.log`确认均由 `var/`忽略。`Get-Acl`与 `GetAccessRules`只读核对唯一 `.env.template`、runtime元数据及三份运行日志，**每份Protected=true、两条本人/SYSTEM Allow ACE、UnexpectedRules=0**，未读取内容。当前新增记录涉及README/TODO/环境指南/NEXT和忽略的运行配置/元数据，不改业务API/审批/Outbox或迁移；没有因手机反馈重跑全量或真实环境业务回归。

**Chrome仅快捷方式的局部修复（2026-10-05；F08.6仍BLOCKED）**：本人在AndroidChrome安装检查回复“只能创建快捷方式”。这不是安装验收通过。核对实际代码后确认缺少Manifest icons及认证Manifest链接的 `crossorigin="use-credentials"`；Google文档同时要求192/512图标并说明受认证Manifest需要携带凭据。[官方Manifest说明](https://web.dev/articles/add-manifest) 此处只补已确认的静态安装资源缺口，实际Chrome设备/安装服务条件还需本人复验。

先新增 `tests/api/test_pwa_installation_f08.py` 四个用例，下面首条命令在改实现前 **3 failed、1 passed、2 warnings**：包装缺图标、Manifest无icons、链接无use-credentials各失败；缺失/伪造JWT仍401的安全回归通过。另新增390px浏览器反例，先实际安装历史v11 Worker、确认页面受控且旧Manifest来自缓存，再切换到新资源；首次 **1 failed**停在缺图标。随后发现测试的async等待谓词可能将Promise当作真值，将其改为真正await缓存读取、五秒有界轮询且超时抛错，全部后续断言保留；在仍未更改Manifest/HTML/SW时再次运行同一节点，**1 failed**，明确停在v11缓存未被替换。

```powershell
.\.venv-win\Scripts\python.exe -m pytest tests/api/test_pwa_installation_f08.py -q -o addopts='' -p no:cacheprovider
.\.venv-win\Scripts\python.exe -m pytest tests/integration/test_pwa_browser_f08.py::PwaBrowserF08Tests::test_manifest_upgrade_replaces_v11_and_decodes_installation_icons -q -o addopts='' -p no:cacheprovider
```

最小实现：`web/pwa/index.html`添加凭据属性，Manifest仅添加两份同源固定PNG声明，新增 `icon-192.png`与 `icon-512.png`，`pyproject.toml`将它们平放同一PWA包装目标，SW仅升cache名v12、STATIC/fetch白名单保持四路径。图标由本机标准库生成，192文件694字节、512文件2858字节；API验证签名/IHDR实际尺寸，浏览器 `Image.decode()`验证可解码。没有新API、数据库迁移、扩展脚本、核心业务、审批或Outbox变更。已有授权临时入口从源码目录挂载静态资源，本轮未启动第二组服务、安装扩展或改变Access策略。

修复后实际定向与静态命令：

```powershell
.\.venv-win\Scripts\python.exe -m pytest tests/api/test_pwa_installation_f08.py tests/api/test_pwa_tasks_f08.py tests/api/test_cloudflare_access.py tests/contract/test_f03_contract_consistency.py tests/contract/test_f05_contract_consistency.py -q -o addopts='' -p no:cacheprovider
# 78 passed、5 warnings，7.46s，exit0；警告为既有依赖和per-request cookie弃用。
.\.venv-win\Scripts\python.exe -m pytest tests/integration/test_pwa_browser_f08.py tests/integration/test_pwa_form_drafts_browser_f08.py tests/integration/test_pwa_approvals_browser_f08.py -q -o addopts='' -p no:cacheprovider
# 17 passed，51.58s，exit0；包括v11升级、两尺寸PNG解码与严格四路径/敏感正文缓存断言。
.\.venv-win\Scripts\python.exe -m ruff check --no-cache tests/api/test_pwa_installation_f08.py tests/integration/test_pwa_browser_f08.py
# All checks passed；首次本机执行发现新增JS测试字符串仅超行宽1字符，分行后复查通过，未改断言。
node --check web/pwa/service-worker.js
node --check web/pwa/app.js
# 均exit0。
.\.venv-win\Scripts\python.exe -m pip wheel --no-index --no-deps --no-build-isolation --wheel-dir var/build/f086-install-2026-10-05 .
# exit0，离线构建personal_assistant_framework-0.1.0-py3-none-any.whl。
```

Ruff与Node首次默认沙箱未能创建进程（Windows `CreateProcessWithLogonW:1909`），不能当作执行过；获准以限定本机进程重跑后得到上述实际结果。首次尝试 `python -m build --wheel --no-isolation --outdir var/build/f086-install-2026-10-05` **exit1**，build包无可运行 `__main__`；改为现有pip/setuptools的无网络构建成功，没有安装/升级任何依赖。

wheel内容检查实际命令及结果：

```powershell
@'
import json
from pathlib import Path
from zipfile import ZipFile

wheel = Path('var/build/f086-install-2026-10-05/personal_assistant_framework-0.1.0-py3-none-any.whl')
with ZipFile(wheel) as archive:
    prefixes = [name[:-len('manifest.webmanifest')] for name in archive.namelist() if name.endswith('/share/personal-assistant/pwa/manifest.webmanifest')]
    assert len(prefixes) == 1
    prefix = prefixes[0]
    for name in ('index.html', 'manifest.webmanifest', 'service-worker.js', 'icon-192.png', 'icon-512.png'):
        assert archive.read(prefix + name) == (Path('web/pwa') / name).read_bytes()
    manifest = json.loads(archive.read(prefix + 'manifest.webmanifest'))
    assert {icon['sizes'] for icon in manifest['icons']} == {'192x192', '512x512'}
    assert 'crossorigin="use-credentials"' in archive.read(prefix + 'index.html').decode()
print('Wheel verification passed: manifest, credentialed link, worker and both PNG files match source.')
'@ | .\.venv-win\Scripts\python.exe -B -
# exit0，目标Manifest/HTML/SW/两PNG字节与源码一致。
```

最终源文件仅调整测试字符串行宽后，复跑上述浏览器新增节点：**1 passed、7.06s、exit0**；`git diff --check` **exit0**（LF/CRLF提示），`git check-ignore -v var/build/f086-install-2026-10-05/personal_assistant_framework-0.1.0-py3-none-any.whl`确认由var忽略。限定静态复核未发现当前资源/认证/包装缺口；没有运行Mypy全量（未改生产Python）或依赖检查（未改依赖）。工作区原有health预检及准备脚本的未提交修改保留。

上述集合仅覆盖当前局部静态资源修复及相关缓存/认证回归，不是F08.6整体验收或新的全量门禁。没有重跑全量或PostgreSQL/Worker；发布前1248/130和130 passed不充作本次修复后数字。已请求本人关闭旧标签、Chrome普通窗口重新打开并触发只读刷新，等待缓存更新后再次检查安装菜单、独立启动与Android应用列表；实际结果仍待反馈。网络切换、后台恢复、真机缓存、其余私有health禁止路径和真实Push仍未验收，生产秘密后端仍待F09；F08.6继续BLOCKED，F07/F08整体IN_PROGRESS，不推进后续编号，不提交/推送或执行外部业务动作。

**Pura 70 安装终态续查（2026-10-05；仍 BLOCKED）**：静态资源修复后，本人反馈“可以点安装了，但是好像实际上没装下来”，进一步说明“弹了一个开始安装就没下文了。手机是华为p70 pura，不能用Google Play”。记录为安装选项已出现、安装终态未确认的人工反馈；没有手机安装日志或应用列表证据。Google 官方文档说明 Chrome 的 WebAPK 路径使用 GMS / Play Services，服务不可用时可回退快捷方式；该设备条件与安装服务受限的候选解释吻合，但无法使用 Play 商店不能单独证明缺少 Play Services，也没有证明这是唯一原因。[Android 安装机制](https://web.dev/learn/pwa/installation#android_installation)

已请求本人先检查“设置 → 应用”中的 `Personal Assistant` / `Assistant`，若无记录则尝试 Chrome “⋮ → 安装并创建快捷方式 → 创建快捷方式 → 添加”，反馈菜单和桌面入口结果。该步骤采用 Google 官方快捷方式路径，保留单邮箱 Access；入口与独立 WebAPK 安装、真实 Push 验收分别记录。[快捷方式操作](https://support.google.com/chrome/answer/15085120?co=GENIE.Platform%3DAndroid&hl=zh-Hans) 本次仅更新 TODO、NEXT 和环境指南，不修改产品代码、Access/Tunnel/Serve 或凭据配置，不新增 APK 包装或秘密后端；未重跑 pytest、全量、PostgreSQL/Worker 或真实网络业务回归，没有新测试数量。收尾在实际Desktop仓库运行 `git diff --check` → **exit0**（仅LF/CRLF提示）。下一步仍是 F08.6 的手机入口与网络恢复验证，正式安装及真实 Push 仍未验收；F07/F08 整体保持 `IN_PROGRESS`。

F08 整体验收仍要求 Android Chrome 完成两条 E2E 流；离线后重连恢复状态；通知不含邮件正文/表单字段；编辑冲突返回 409/412。F08.1 不宣称达到这些整体验收条件。

**同日桌面入口续查**：本人确认应用列表没有助手，点击添加到桌面也无反应；原先等待反馈项已得到该失败结果，入口生成仍未验收。已请求实际 HarmonyOS 版本及“创建桌面快捷方式”列表中 Chrome 的存在/许可状态，不能预设 Chrome 必然列在该权限页。华为官方已给出自带浏览器按系统版本添加桌面的步骤，记录在环境指南；该路径需要重新进行手机能力验证，尚未执行或认定成功。本次仅更新 TODO/NEXT/环境指南，未改产品、凭据或网络配置；没有新增 pytest/全量/PostgreSQL/Worker 结果，收尾 `git diff --check` 结果单列。F08.6 继续 `BLOCKED`，待本人权限检查后继续；正式安装及真实 Push 未通过，F07/F08 整体仍 `IN_PROGRESS`。

本次桌面入口续查收尾：在实际 Desktop 仓库执行 `git diff --check` → **exit0**，只有已有 LF/CRLF 提示。

**桌面入口已生成的本人反馈（同日；仍 BLOCKED）**：本人随后回复“应用安装到桌面了”。此前无入口的现象已有新的正向人工反馈，记录为桌面入口已出现；未取得包装类型、最终使用浏览器、应用列表或独立启动模式证据，不将图标出现等同于 Chrome WebAPK 已通过。已请求从该图标启动，反馈添加入口所用浏览器、有无地址栏、列表/SSE状态，再保持前台从 Wi-Fi 切到移动数据，观察约30秒后的实际恢复；尚未收到这些结果。未要求创建任务或审批，也没有执行真实邮件/ehall/Push。此次只更新 TODO/NEXT/环境指南，不修改产品或运行配置、不开启后续编号、不提交/推送，没有新增测试集合结果；实际 Desktop 仓库的 `git diff --check` → **exit0**（仅已有LF/CRLF提示）。只读核对当前代码确认前台30秒重读任务、SSE有15秒保活等待周期，浏览器控制重连间隔，30秒观察不作为成功期限。网络切换、后台恢复、真机敏感缓存、其余跨设备边界及真实 Push 仍待验收，F08.6 保持 `BLOCKED`，F07/F08 整体仍 `IN_PROGRESS`。

失败红线：确认页被缓存；断线导致重复审批；任意扩展脚本进入页面。

**手机桌面启动、网络切换与后台恢复反馈（同日；仍 BLOCKED）**：本人对桌面启动与前台网络检查明确回复“图标右下角有chrome标识，没有历览器地址栏。任务列表和连接显示的是已建立，但是现在没有任务。切换网络后恢复连接了”。对应已请求的Wi-Fi→移动数据方向，记录为该方向的真机恢复观察；列表仅为空态，没有任务详情、消息、持久草稿或审批交互证据。随后本人对“联网切后台或锁屏约一分钟，再从桌面图标回到助手，先观察自动恢复、不刷新整页”回复“是”，记录为这项恢复观察；未分辨采用了切后台还是锁屏，不宣称两种分别测试或后台长期存活。Chrome标识和无地址栏启动不用于推断WebAPK包类型，也不额外把特定系统包格式设为验收门槛。断开Wi-Fi/移动数据的离线提示和恢复移动数据后的自动重连问题仍待本人反馈；真机敏感缓存、其他跨设备禁止路径、非空业务交互及真实Push未验收。

此次同步README/TODO/NEXT/环境指南的当前摘要与记录，未改产品、运行配置或凭据，未运行新的pytest/全量/PostgreSQL/Worker集合，没有新测试数量；文档检查使用实际Desktop仓库的 `git diff --check`。F08.6状态仍BLOCKED，F07/F08整体IN_PROGRESS，不推进后续编号或执行外部业务动作。

本人随后补充“联网也会恢复”，并对断开Wi-Fi和移动数据期间的顶部状态选择“设备离线或事件连接中断”。断网明确提示与恢复联网后自动连接两项均已记录为人工反馈，原待反馈项已补齐。这些与手机桌面启动、Wi-Fi→移动数据及后台或锁屏约一分钟恢复一起构成本轮基础连接观察，不包括主机/Cloudflare中断、系统长期后台或真实Push。

本轮先请求本人通过手机创建明确标注的本地连接自检任务、发送一条非敏感自检消息，确认成功后刷新核对各只有一条；若失败先保留页面反馈。只读核对实际 `POST /api/v1/tasks` 和 `POST /api/v1/tasks/{id}/messages`、TaskService及PWA按钮文案后提出该检查，没有代替本人调用命令API或创建记录。随后本人回复“是的，都只有一条”，手机自检任务/消息成功后的刷新恢复已按人工反馈记录。当前Agent自动执行未接线，任务停在QUEUED属预期，不宣称已完成助理工作或接入阶段10。

本次手机反馈同步的收尾文档检查：实际Desktop仓库执行 `git diff --check` → **exit0**，仅既有LF/CRLF提示；未重跑其他测试集合。

**原库只读核对与电脑页面同步（2026-10-05；仍 BLOCKED）**：在本人执行后续跨设备新增操作之前，对原助手PostgreSQL作一次只读聚合，以下实际命令在Desktop仓库运行，exit0。只在进程内部读取既有受保护配置，不输出DSN、真实凭据、用户身份、任务ID或其他正文；连接目标限定原库的回环地址/端口/角色/库名，查询为有超时的repeatable-read只读事务。

```powershell
@'
import asyncio
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

async def check():
    import asyncpg
    from dotenv import dotenv_values

    candidate = Path('var/deployment/f08-access-2026-10-04/.env.template')
    dsn = dotenv_values(candidate, interpolate=False, encoding='utf-8').get('PA_DATABASE_URL')
    if not isinstance(dsn, str) or not dsn:
        raise ValueError
    dsn = dsn.replace('postgresql+asyncpg://', 'postgresql://', 1)
    target = urlsplit(dsn)
    if (target.scheme not in ('postgresql', 'postgres')
            or target.hostname != '127.0.0.1'
            or (target.port or 5432) != 5432
            or unquote(target.username or '') != 'assistant'
            or unquote(target.path) != '/assistant'):
        raise ValueError

    objective = 'F08.6 \u624b\u673a\u8fde\u63a5\u81ea\u68c0\uff0c\u4ec5\u8bb0\u5f55\uff0c\u4e0d\u6267\u884c\u5916\u90e8\u52a8\u4f5c'
    content = '\u624b\u673a\u8865\u5145\u4fe1\u606f\u81ea\u68c0'
    connection = await asyncpg.connect(dsn=dsn, timeout=8, command_timeout=8)
    try:
        async with connection.transaction(isolation='repeatable_read', readonly=True):
            row = await connection.fetchrow('''
                SELECT
                    (SELECT count(*) FROM public.tasks WHERE objective = $1) AS task_count,
                    (SELECT count(*)
                     FROM public.task_messages AS message
                     JOIN public.tasks AS task ON task.id = message.task_id
                     WHERE task.objective = $1 AND message.content = $2) AS message_count
            ''', objective, content)
            result = {
                'query_succeeded': True,
                'matching_task_count': row['task_count'],
                'matching_message_count': row['message_count'],
            }
    finally:
        await connection.close(timeout=4)
    return result

try:
    print(json.dumps(asyncio.run(check()), ensure_ascii=True))
except Exception as error:
    print(json.dumps({'query_succeeded': False, 'error_type': type(error).__name__}))
    raise SystemExit(1)
'@ | & .\.venv-win\Scripts\python.exe -B -
```

实际输出：`{"query_succeeded": true, "matching_task_count": 1, "matching_message_count": 1}`。此为当时已知自检文本的匹配计数，不是所有任务/消息总数，也不是PostgreSQL测试套件。随后本人按请求在电脑同Access身份的助手页面观察手机新增任务与不同消息，不手动刷新是否自动显示，回复“我试了新增任务和新增消息，都可以”。记录为跨设备页面同步人工反馈；不宣称量化延迟，也不把后续主动新增记录当重复请求。没有新的数据库写入、迁移、服务启动或外部业务动作。

下一项检查手机实际CacheStorage：本人已确认有数据USB线，已请求开启USB调试并亲自确认授权，在电脑Chrome的 `chrome://inspect/#devices` 查找手机及助手页面。仅核对缓存名称/路径，不读取Cookie、验证码、JWT或响应正文；实际结果尚待取得，不能沿用电脑浏览器测试替代。步骤见 `F08_ENVIRONMENT_PREPARATION.md` 第8节。

本轮更新README、TODO、NEXT_STEPS、环境指南，并校正契约第22节额外要求WebAPK/应用列表登记的表述，保持阶段9实际验收范围。产品代码和部署配置没有新增变更；没有重跑全量、PostgreSQL/Worker或真实Push。Schema草稿/审批、真机缓存、其余跨设备健康边界及主机/Cloudflare中断证据仍待补齐，真实Push缺生产秘密后端。F08.6继续BLOCKED、F07/F08整体IN_PROGRESS；不推进F09/F10，不提交或推送。

上述同步后的静态检查：在实际Desktop仓库运行 `git diff --check` → **exit0**，仅LF/CRLF提示。未新增或引用为当前结果的测试数量。

**手机USB调试续查（2026-10-05；缓存仍待验收）**：本人截图显示电脑Chrome已勾选Discover USB devices，但为Offline/Pending authentication。本人报告拔插及授权重置尝试后仍只收到“USB调试已连接”通知，没有电脑密钥指纹授权弹框；不能把连接通知记为调试授权成功。本人确认HarmonyOS 4.2.0.207。已请求切换“传输照片”（PTP）后反馈电脑设备页，尚待结果。

本机只读设备查询在沙箱内因CimException失败；沙箱外查询成功，未修改设备/驱动。最初带SilentlyContinue的空列表不是“没有手机设备”的证据；随后显式ErrorAction Stop区分失败与空结果。最终收窄的实际命令如下，exit0：

```powershell
try {
  $f086AdbInterfaces = @(Get-PnpDevice -PresentOnly -ErrorAction Stop | Where-Object { $_.Class -eq 'USBDevice' -and $_.FriendlyName -eq 'ADB Interface' } | Select-Object Status,Class,FriendlyName)
  [pscustomobject]@{query_succeeded=$true;adb_interfaces=$f086AdbInterfaces} | ConvertTo-Json -Depth 4 -Compress
} catch {
  [pscustomobject]@{query_succeeded=$false;error_type=$_.Exception.GetType().FullName} | ConvertTo-Json -Compress
  exit 1
}
```

实际输出：`{"query_succeeded":true,"adb_interfaces":[{"Status":"OK","Class":"USBDevice","FriendlyName":"ADB Interface"}]}`。仅证明查询时Windows枚举该接口为OK，不证明驱动全部正确、调试授权成功或CacheStorage安全。另一个Win32_PnPSignedDriver按ADB名称过滤的查询exit0但为空，未取得驱动提供者/签名证据，不能据此断言缺失驱动。Chrome进程名计数查询也不构成浏览器调试状态证据。未输出设备序列号、手机文件、Cookie或正文；未安装工具/驱动、重启进程或服务、修改部署配置或产品代码。

本轮仅追加环境指南与此证据记录，目标仍是F08.6手机实际缓存检查；PTP结果及CacheStorage内容未确认，阶段状态不变，没有运行测试集合、全量或PostgreSQL/Worker回归。后续先等待本人设备页反馈，不能用电脑缓存替代手机。

**官方ADB诊断及本人授权（2026-10-05；缓存检查继续）**：本人反馈PTP时Chrome手机/Pending条目都消失，传输文件则仍Pending。沙箱外PnP复查仍为ADB Interface OK；Get-PnpDeviceProperty读取该接口的DriverProvider/DriverVersion/DriverInfPath/ProblemCode，得到Microsoft、10.0.26100.8972、winusb.inf、0，未更改驱动。PATH和7个常见SDK路径未找到adb.exe，不宣称已扫描整机。

准备便携工具：Python标准库从 `https://dl.google.com/android/repository/repository2-3.xml` 选择platform-tools的Windows archive，再下载元数据指向的精确 `https://dl.google.com/android/repository/platform-tools_r37.0.1-win.zip`，比较声明大小及SHA-1后检查ZIP路径并解压到Git忽略的 `var/deployment/f08-access-2026-10-04/diagnostics/android-platform-tools-2026-10-05`。实际准备输出为 `{"prepared": true, "archive": "platform-tools_r37.0.1-win.zip", "bytes": 8044989, "official_checksum_verified": true, "checksum_type": "sha1", "adb_present": true}`，exit0。此为官方仓库SHA-1核对，不称官方SHA256或数字签名验证。没有修改PATH、安装驱动/Android Studio或安装手机应用。

工具版本的实际PowerShell命令：

```powershell
& '.\var\deployment\f08-access-2026-10-04\diagnostics\android-platform-tools-2026-10-05\platform-tools\adb.exe' version
```

exit0，Android Debug Bridge 1.0.41、Version37.0.1-15733141。首次查询前Get-NetTCPConnection核对5037无监听。本人与助手明确说明ADB首次可能启动本地服务、保存本机调试密钥后，本人回复“已取消勾选，可以查询”。首次子进程argv为 `adb.exe -L tcp:127.0.0.1:5037 devices`，exit1，未取得设备状态；不把空states解释为没有手机，也未确认其失败原因。改用官方默认参数后的子进程argv为 `adb.exe devices`，exit0，设备数量1、状态unauthorized。随后本人回复“出现授权弹窗，已允许”。

授权后实际执行的脱敏查询命令如下，在Desktop仓库运行，exit0：

```powershell
@'
import json
from pathlib import Path
import os
import subprocess
import sys

try:
    adb = Path('var/deployment/f08-access-2026-10-04/diagnostics/android-platform-tools-2026-10-05/platform-tools/adb.exe').resolve()
    environment = os.environ.copy()
    environment.pop('ADB_TRACE', None)
    result = subprocess.run([str(adb), 'devices'], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=15, env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
    allowed = {'device', 'unauthorized', 'offline', 'recovery', 'sideload', 'bootloader'}
    states = [fields[1] for line in result.stdout.splitlines() if len(fields := line.split()) >= 2 and fields[1] in allowed]
    print(json.dumps({'query_succeeded': result.returncode == 0, 'exit_code': result.returncode, 'device_count': len(states), 'states': states}))
    sys.exit(result.returncode)
except Exception as error:
    print(json.dumps({'query_succeeded': False, 'error_type': type(error).__name__}))
    sys.exit(1)
'@ | & .\.venv-win\Scripts\python.exe -B -
```

实际输出 `{"query_succeeded": true, "exit_code": 0, "device_count": 1, "states": ["device"]}`。5037复查只监听127.0.0.1，进程为本轮新启动的adb；暂时保留用于接下来的Inspect，检查结束后只停止经身份核对的本轮诊断服务。未读取手机文件、日志、Cookie、API正文或本机调试密钥内容；未执行shell/pull/logcat、CDP端口转发或浏览器控制。新服务及可能的本机调试密钥属于诊断工具状态变化，不修改助手服务、数据库或部署配置。

随后本人对重新勾选Chrome USB发现、手机Chrome普通窗口打开助手的检查回复“已出现助手页面及inspect”，手机调试目标及入口已按人工反馈记录。已请求本人点击inspect、打开Application/Cache Storage并提供仅名称和路径的截图；缓存尚未验收。环境指南同步以上事实；本轮没有产品修改、测试集合或全量/PostgreSQL/Worker回归，F08.6继续BLOCKED、F07/F08整体IN_PROGRESS，不推进后续编号。

实际执行 `git check-ignore var/deployment/f08-access-2026-10-04/diagnostics/android-platform-tools-2026-10-05/platform-tools/adb.exe var/deployment/f08-access-2026-10-04/diagnostics/android-platform-tools-2026-10-05/platform-tools_r37.0.1-win.zip` → exit0，两条路径均匹配忽略规则；`git diff --check` → exit0，仅既有LF/CRLF提示。未提交、推送或部署新产品代码。

**手机所选缓存截图与节点数反馈（2026-10-05）**：本人按请求在手机助手页面Inspect中提供CacheStorage列表截图，origin为 `https://yanshuheng.dpdns.org`，可见路径过滤框没有输入过滤条件，所选缓存仅有 `/ui/`、`/ui/app.js`、`/ui/manifest.webmanifest`、`/ui/styles.css` 四条固定静态路径，无API条目。图片未含左侧树及缓存名称；本人回答左侧“只有这一条”，因此缓存节点数1只按人工反馈记录，不宣称截图自身覆盖全部节点。未打开缓存响应正文、Cookie或请求凭据；截图不证明HTTP缓存/LocalStorage/IndexedDB或未访问的Schema草稿/审批页的敏感存储情况。

本人随后抄出唯一缓存名称 `assistant-static-v12`，但表示未找到任务请求。因此所选四路径由截图支持，节点总数1和名称v12由本人文本反馈支持；仅为当前任务/消息使用后的手机CacheStorage观察，不扩充为HTTP缓存/LocalStorage/IndexedDB或未访问的Schema草稿/审批页面验收。任务API响应Cache-Control实际值仍未取得。

只读核对实际app.js确认刷新按钮调用refreshTaskViews/loadTasks，产生 `GET /api/v1/tasks?limit=20`，jsonRequest指定fetch cache:no-store；安全头中间件也对 `/api/` 配置no-store，但代码契约不替代实际手机响应。已请求在远程Network选择All、录制圆点红色、Filter仅填tasks，然后在刚被Inspect的那个手机Chrome普通标签点“刷新状态”。Name列通常只显示 `tasks?limit=20`，选择该行的Response Headers，仅反馈状态码/Cache-Control；若仍无行，仅发请求列表截图。没有让本人打开聊天中格式混乱的API链接、开启Disable cache、覆盖响应头或发送完整Headers/正文/HAR。当前尚待反馈。

此处只更新环境指南与证据记录，没有新增自动化测试或产品修改，ADB诊断服务继续仅本机回环用于当前Inspect。F08.6继续BLOCKED，剩余项及F07延期边界不变。

**手机任务响应头截图（2026-10-05）**：本人随后提供远程Network截图，General显示 `GET https://yanshuheng.dpdns.org/api/v1/tasks?limit=20`、200 OK；Response Headers显示 `Cache-Control: no-store`、`Cf-Cache-Status: DYNAMIC`，Disable cache未勾选。未见请求Cookie/JWT或响应正文，没有将截图中的任务标识、CF报告标识或请求ID复制到仓库。缓存名assistant-static-v12及节点总数1仍是本人文本反馈，四固定路径与任务GET响应头分别由截图支持。当前任务/消息使用后的CacheStorage及任务GET响应头有限范围已确认，不扩大为HTTP缓存底层、LocalStorage/IndexedDB或尚未访问Schema草稿/审批场景均无敏感数据。

**Schema真机检查前置缺口**：本人在已有自检任务的Schema表单草稿区域反馈“尚无可用草稿。一个Schema不支持，已禁用”。随后电脑同身份实际助手页面的只读观察定位为 `nju.smail · smail.account_settings（Schema不支持）`；源定义使用数组及额外UI能力，超出既定扁平Schema集合。页面的Example Echo状态为DISCOVERED，不等同已安装/启用。编辑、来源及冲突真机检查尚未开始，不构建/安装/导入/执行新扩展、不改变Schema支持集合或F07审批边界；后续可先提供现有example.echo的PUBLIC字段验收夹具精确预览，按AGENTS.md在任何build/install/import/execute之前取得本人确认。

本轮同步README、TODO、NEXT_STEPS及环境指南，产品代码和部署配置没有新增修改，没有运行新的自动化测试/全量/PostgreSQL/Worker；早前数字不充作本次结果。F08.6继续唯一当前BLOCKED项，手机Schema编辑/来源/冲突、审批、其余缓存与跨设备边界及真实Push待验收，F07/F08整体仍IN_PROGRESS。ADB服务暂留仅回环供Inspect；不提交或推送。

**Access 503诊断与最小提示修复（2026-10-05；仍BLOCKED）**：本人报告“无法读取最新任务：Cloudflare Access verification is unavailable”，退出并重新接验证码仍无效，随后确认手机和电脑“都已恢复”。电脑当前实际页面显示事件连接建立及已有任务；这个503与既有中间件的 `CLOUDFLARE_ACCESS_UNAVAILABLE` 一致，不能据此认定账号或令牌失效。源码中取钥错误被汇总为该稳定错误码，现有运行日志没有记录具体取钥失败类别，本次未捕获原故障，未宣称根因已修复。

只读宿主探测：官方团队 `/cdn-cgi/access/certs` 使用项目相同httpx及RSA解析器，两种trust_env模式均设5秒总期限、禁重定向、64KiB上限，不发送Cookie/JWT；结果分别为200/2个有效公钥/5116字节/2.01秒，以及200/2个有效公钥/5116字节/1.06秒。诊断进程无代理环境变量。回环health为200且仅status=ok，回环任务无令牌为401/TOKEN_MISSING/no-store，公网无认证任务为302跳往本团队Access。只读取日志分类计数，尾部未见ERROR/WARNING等所检类别，不据此证明未发生故障；没有输出日志正文、令牌、claims或密钥。实际运行监听命令如下，返回8000、8010、20241及诊断ADB5037均为127.0.0.1，无8001：

```powershell
Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -in @(8000,8001,8010,5037,20241) } | Select-Object LocalAddress,LocalPort,OwningProcess | ConvertTo-Json -Compress
```

电脑Cloudflare页面只读确认：应用Session Duration为24 hours，唯一Email_policy的Session duration为Same as application session timeout，应用仅选One-time PIN。全局Access设置页两次读取超时，尚未确认全局覆盖；不保证实际会话必为24小时，未改Allow策略、登录方式、会话时长或JWT校验。

先新增 `tests/integration/test_pwa_access_recovery_browser_f086.py`，实际红测如下，**1 failed / 1 passed，exit1，12.29秒**；失败在期望中文503解释，失败前的旧列表、未发送输入、无POST/导航及四静态缓存检查通过，恢复后断言当时尚未执行：

```powershell
.\.venv-win\Scripts\python.exe -B -m pytest -q -o 'addopts=' -p no:cacheprovider tests/integration/test_pwa_access_recovery_browser_f086.py
```

最小实现只在 `loadTasks` 对 `status===503 && code===CLOUDFLARE_ACCESS_UNAVAILABLE` 提示“助手暂时无法校验登录；请保持本页在前台并联网，页面会自动重读任务，暂时无需退出重新登录”。其他401/503保持原错误语义；未改认证、共享状态、审批、Outbox、30秒轮询或写命令重试规则。SW缓存名升为v13以刷新缓存脚本，四固定静态路径和API不拦截规则不变，契约同步当前版本；历史v12手机证据保留，不将其算成v13验收。

修复后实际定向命令与结果：

```powershell
.\.venv-win\Scripts\python.exe -B -m pytest -q -o addopts='' -p no:cacheprovider tests/integration/test_pwa_access_recovery_browser_f086.py tests/integration/test_pwa_browser_f08.py tests/api/test_pwa_installation_f08.py tests/api/test_pwa_tasks_f08.py
# 20 passed, 2 warnings，exit0，28.59秒；警告为依赖弃用
.\.venv-win\Scripts\python.exe -B -m ruff check --no-cache tests/integration/test_pwa_access_recovery_browser_f086.py
# All checks passed，exit0
node --check web/pwa/app.js
node --check web/pwa/service-worker.js
# 均exit0
```

390px反例从任务列表/详情200切为503，驱动产品现有30秒只读轮询；503时保留原列表和未发送输入，恢复200后无需手动刷新或重登即显示新任务版本、移除读错，输入仍保留。全过程无POST或Access导航，缓存仍仅四固定静态路径且无API/输入正文；401同错误码及503其他错误码不显示新503提示。相邻任务、安装/旧缓存升级、Push及消息浏览器回归一起通过。这些为本地替身证据，不代替实际Cloudflare中断原因或v13手机复验。

此轮是局部只读提示修复，未修改认证决策、共享持久状态、迁移或副作用链路，未重跑全量、PostgreSQL/Worker或真实业务回归，旧完整门禁数字不冒充本轮结果。截至该局部修复收尾，已有助手/health/Tunnel未重启，Admin未启动、未安装或启用扩展、未提交/推送；下方另记录随后已获授权的夹具安装。`git diff --check`实际执行exit0（仅已有LF/CRLF提示），工作区保留先前改动及本次提示/测试/文档增量。F08.6保持唯一当前BLOCKED项，F07/F08整体IN_PROGRESS；下一步仍在F08.6内核对手机v13与可用Schema草稿的验收条件，真实Push缺生产秘密后端，其余审批/边界及中断验收仍待补齐。

**草稿验收夹具的安装前静态快照（当时未安装/未启用）**：宿主 `LocalArtifactStager` 只复制仓库 `extensions/example_echo` 到独立Git忽略的 `var/deployment/f08-access-2026-10-04/previews/example_echo/stage_8aef2afb6ed31e7ab00d04c9`，经 `ManifestParser` 静态校验，再以 `compute_artifact_hash` 复核材料哈希与stager一致，锁文件经 `effective_requirements` 读取。实际调用由 `.\.venv-win\Scripts\python.exe -B -` 的stdin脚本执行、exit0；没有调用安装器、ContractVerifier、Runtime、Admin、Settings或数据库，没有导入/构建/执行扩展。摘要保存为同目录 `stage_8aef2afb6ed31e7ab00d04c9.summary.json`。

摘要为example.echo@0.1.0、入口example_echo.worker:create_extension、表单槽example.echo_settings、唯一工具example.echo/READ、必需/可选宿主能力均空、锁定第三方运行依赖为空；完整材料 `sha256:1c3725193434fa8d027ce95b87b5992fec4bef2fa72f82da18525a241ec70725`。源声明表单只含PUBLIC文本prefix/maxLength40；静态材料摘要不是可消费的Admin plan/nonce，也不冒称真实Worker form.list已通过。

已请求本人按AGENTS.md确认：临时启动只监听回环8001的Admin（包含现有ENABLED Worker恢复），正式inspect核对同一材料哈希、确认精确预览后安装并启用，再核验真实form.list并做手机草稿/版本冲突检查。未获回复前不得build/install/import/execute扩展或启动Admin；若正式材料哈希漂移，须重新提供预览。venv只隔离依赖与崩溃，扩展仍是本机可信代码。当时等候授权；本人随后明确回复“允许该材料安装并启用，用于草稿验收”，后续实际操作见下文。F08.6状态与其他未验收项不变。


**已授权Example Echo安装启用与手机草稿续验（2026-10-05；仍BLOCKED）**：本人明确授权上述精确哈希的材料及临时回环Admin（包含恢复现有ENABLED Worker）。由已核对的Git忽略运行器 `var/deployment/f08-access-2026-10-04/run-admin-for-draft-check.py` 在后台Hidden启动127.0.0.1:8001；它仅从已授权候选读取配置，不打印或另存凭据，生产设置、回环端口、邮件发送和浏览器提交禁用均断言通过。两份运行日志在启动前以CreateNew创建并限制本人/SYSTEM访问。

实际安装HTTP操作由 `.\.venv-win\Scripts\python.exe -B -` 的stdin脚本执行，exit0（2.39秒）：先POST `/admin/v1/extensions/inspect` 指向冻结stage，断言Manifest ID/version/artifact hash、install模式、READ工具与空能力列表；确认预览材料与本人批准哈希一致后，使用仅内存中的plan_id/confirmation_nonce/preview_hash及accepted_warning=true POST `/admin/v1/extensions/install`，查询 `/admin/v1/extension-operations/{id}` 得SUCCEEDED/INSTALLED_DISABLED；再POST `/admin/v1/extensions/example.echo/enable` 并查询操作得SUCCEEDED/ENABLED。两项操作均复核版本0.1.0及相同材料哈希。幂等键在命令前保存到受控Git忽略回执 `var/deployment/f08-access-2026-10-04/example-echo-authorized-operations.json`；未保存或输出确认nonce、令牌或真实凭据，未重复安装。

安装后通过电脑实际公共助手页面读取任务与草稿区域：出现可选 `example.echo · example.echo_settings`，原nju.smail不支持项仍禁用。这是当前公共进程按需Worker提供的页面观察，不是浏览器替身；未取得独立原始catalog JSON截图，不把已验证的Admin版本回执冒称为手机catalog响应逐字段核验。

临时Admin收尾：首次停止前保护检查把venv启动器PID误当listener PID，守卫拒绝且未写停止标记。随后只读确认wrapper7296→监听进程39812，两者命令行均为上述授权运行器、唯一8001 listener为127.0.0.1，直属Worker PID17308/37756。仅在核对解析路径位于指定deployment目录后，以CreateNew写 `admin-draft-check.stop`，由运行器令Uvicorn优雅退出；未使用强制kill。20秒内检查成功、exit0，随后独立只读复核命令如下：

```powershell
$ErrorActionPreference='Stop'
$taskAdmin = @(Get-CimInstance Win32_Process -Filter 'ProcessId=7296 OR ProcessId=39812 OR ProcessId=17308 OR ProcessId=37756')
$taskListening = @(Get-NetTCPConnection -State Listen -LocalPort 8000,8001,8010,20241 -ErrorAction SilentlyContinue | Select-Object LocalAddress,LocalPort,OwningProcess)
[pscustomobject]@{AdminAndOwnedWorkersRemaining=$taskAdmin.Count; StopMarkerExists=(Test-Path -LiteralPath 'var/deployment/f08-access-2026-10-04/admin-draft-check.stop'); Listeners=$taskListening} | ConvertTo-Json -Depth 3 -Compress
```

实测exit0：Admin及上述Worker剩余0、停止标记存在、8001无监听；原public6572/8000、health14976/8010、Tunnel8928/20241均保留为127.0.0.1 listener。公共进程按需启动的Worker不属于临时Admin，不宣称全部Worker退出；持久ENABLED状态保留。不增加开机自启/服务守护，不属于F09。

手机本人先反馈保存前来源“未确定”、保存后“用户输入”，随后补充：任务标题“第二个自检，不执行外部动作”，草稿基准版本2，刷新并重新打开同一任务后仍恢复该测试值。电脑当时页面也显示草稿版本2、prefix测试文字及用户输入来源。这里只记录真实保存/刷新恢复及来源，跨设备409/412冲突、结果未确认/同键重试、Schema场景缓存和审批仍待真机检查；v13手机缓存更新未核对，真实Push仍缺生产秘密后端。

本段是已授权部署操作与人工观察，没有新产品代码、迁移或新增自动化测试数量，没有重跑全量/完整PostgreSQL/Worker或真实业务回归；此前20 passed是503局部修复证据。F08.6保持唯一当前BLOCKED，F07/F08整体IN_PROGRESS；未发送邮件、提交ehall、提交/推送代码或推进F09/F10。


**同一草稿的跨设备冲突与显式处理（2026-10-05；手机部分验收）**：本人按要求在手机将prefix改为“手机未保存自检-v2”，明确回复“已改，未保存”。电脑实际公共PWA在相同标题的已读版本2草稿中填写“电脑已保存自检-v2”并点击保存；首次AX显示正在保存，后续两次只读浏览器状态请求超时，因此未重复发送命令，未仅凭点击声称成功。改用原库独立asyncpg只读连接，设置default_transaction_read_only=on并在readonly=True事务内执行参数化SELECT，限定唯一标题、example.echo/example.echo_settings及0.1.0，只投影草稿版本、PUBLIC测试prefix与来源；没有调用startup/迁移/容器或读取其他正文。`.\.venv-win\Scripts\python.exe -X utf8 -B -` stdin命令实际exit0（1.28秒），返回matched_drafts=1、read_only=on、version=3、prefix为电脑测试值、来源USER_INPUT，确认写入完成。

本人随后提供真实手机截图：顶部明确“服务器草稿已更新到版本3；本机输入未保存，请显式处理冲突”，基准版本仍2；prefix仍为手机未保存测试值，来源“用户输入（未保存）”；服务器面板列出版本3及电脑值，并展示保留本机/放弃本机两个显式选项。截图证明只读同步检测到新版本后未静默覆盖；该时点截图不是手机PUT的409/412动态反例，也没有展示保存版本4。本人随后按“保留本机输入，以服务器新版保存”→保存→刷新同一任务的步骤回复“是”，确认草稿版本4及手机测试值恢复；下方原库只读核对一致。未读取Cookie/JWT或其他敏感正文，未生成外部审批/提交；F08.6状态不变。


**冲突处理后的原库只读复核**：实际执行以下命令，exit0（1.36秒），read_only=on、matched_drafts=1、version=4、prefix为手机未保存测试值、source=USER_INPUT；断言与本人手机反馈一致。不打印DSN/异常原文或其他数据，不执行迁移。

```powershell
@'
import asyncio, json
from pathlib import Path
import asyncpg
from dotenv import dotenv_values
from personal_assistant.infrastructure.database.config import normalize_dsn

async def main():
    config=dotenv_values(Path('var/deployment/f08-access-2026-10-04/.env.template'),interpolate=False)
    conn=await asyncpg.connect(normalize_dsn(config['PA_DATABASE_URL']),timeout=5,command_timeout=5,server_settings={'default_transaction_read_only':'on'})
    try:
        async with conn.transaction(readonly=True):
            mode=await conn.fetchval('SHOW transaction_read_only')
            rows=await conn.fetch("SELECT d.version, d.values_json->>'prefix' AS prefix, d.sources->>'prefix' AS source FROM task_form_drafts d JOIN tasks t ON t.id=d.task_id WHERE t.objective=$1 AND d.extension_id=$2 AND d.form_id=$3 AND d.extension_version=$4", '第二个自检，不执行外部动作','example.echo','example.echo_settings','0.1.0')
            assert len(rows)==1, 'Ambiguous self-check draft'
            row=dict(rows[0])
            assert row['version']==4 and row['prefix']=='手机未保存自检-v2' and row['source']=='USER_INPUT', 'Self-check result mismatch; suppress content'
            print(json.dumps({'read_only':mode,'matched_drafts':len(rows),'draft':row},ensure_ascii=False))
    finally:
        await conn.close()
try:
    asyncio.run(main())
except Exception as exc:
    print(json.dumps({'diagnostic_failed':type(exc).__name__}))
    raise SystemExit(1)
'@ | .\.venv-win\Scripts\python.exe -X utf8 -B -
```

该检查不是PostgreSQL测试套件或真实Worker回归。随后只请求本人查看使用草稿后的CacheStorage名称/路径与GET form-draft状态码/Cache-Control，不读取Cookie、JWT、响应正文或导出HAR；本人已提供下方两份截图。F08.6保持BLOCKED。


**草稿使用后的手机缓存与响应头截图（2026-10-05）**：本人随后提供草稿使用后的远程DevTools截图：Cache Storage树仅一个assistant-static-v12节点，未设置路径过滤，列表仍只有四个固定静态路径；GET form-draft为200 OK、Cache-Control: no-store，Disable cache未勾选。本次仅确认该CacheStorage及草稿GET响应头，不扩大为浏览器HTTP缓存底层、LocalStorage/IndexedDB、其他敏感API或审批场景；手机仍为v12，v13更新尚待确认。截图未展示Cookie、JWT或响应正文，未将URL中的任务ID或其他请求标识复制到仓库；未声称Cf-Cache-Status或其他未展示头已核对。图片1左侧展示真实手机远程目标，Cache Storage展开且仅v12一个节点；图片2Network为All、录制开启，过滤form，General明确GET与200，Response headers可见no-store。

当前源文件为assistant-static-v13，PWA根路径实现及现有editable映射均指向Desktop仓库；这是静态路径核对，不代替实际外网取得v13脚本的证据。已请求本人在同一远程Service workers点Update后刷新已保存页面与缓存列表，不注销Worker、清理站点数据或退出登录，等待版本/错误反馈。本轮仅查看已有源定义并更新TODO/NEXT_STEPS/环境指南，没有产品代码、迁移、认证或审批更改，没有重跑任何测试集合、全量/完整PostgreSQL/Worker或真实业务回归；早前20 passed不计作本轮结果。F08.6仍BLOCKED，F07/F08整体IN_PROGRESS，未提交、推送或部署新进程。


**真实手机v13缓存升级截图（2026-10-05）**：本人按上一请求的Service workers Update及刷新步骤，随后提供Application面板截图。目标origin为https://yanshuheng.dpdns.org，Cache Storage树已展开且只显示assistant-static-v13，原v12节点不在列表；路径Filter为空，列表只有/ui/、/ui/app.js、/ui/manifest.webmanifest、/ui/styles.css，无API请求条目，未选缓存响应正文。该截图补齐真实手机的缓存名升级与旧节点消失观察；历史v12的任务及草稿场景证据保留。此前草稿GET的200/no-store仍是此前Network截图证据，未假称本图又验证了响应头。

此截图未包含Service workers状态/控制器详情，不从缓存名推断Worker所有运行状态、应用脚本逐字版本或真实503中断复验。范围只覆盖本origin的CacheStorage版本及四路径，无浏览器HTTP缓存底层、LocalStorage/SessionStorage/IndexedDB、未访问的审批或全部敏感API验收。本人随后反馈助手origin的Local storage与Session storage均为空，IndexedDB界面显示未检测到数据库。该项按本人文本反馈记录，未获得这三处的独立截图或存储导出；未读取值/Cookies，不推断其他origin或浏览器HTTP缓存底层。未清理数据或要求退出登录。

本轮只读取现有状态文档/前端源引用并更新TODO.md、NEXT_STEPS.md、F08_ENVIRONMENT_PREPARATION.md；没有产品行为、认证、共享状态、迁移或审批/Outbox变更，未跑新测试集合、全量/PostgreSQL/Worker或真实业务回归，不使用旧测试数字作为本次结果。F08.6仍唯一当前BLOCKED，F07/F08整体IN_PROGRESS；真实Push仍缺生产SecretStore，审批及其余边界尚待验收，不启动F09/F10，不提交/推送或部署新进程。


**手机本地存储与私有路径续验（2026-10-05）**：本人随后反馈助手origin的Local storage与Session storage均为空，IndexedDB界面显示未检测到数据库。该项按本人文本反馈记录，未获得这三处的独立截图或存储导出；未读取值/Cookies，不推断其他origin或浏览器HTTP缓存底层。结合已提供的v13唯一四静态路径和先前任务/草稿GET no-store证据，确认范围仍限于本人实际使用场景，不扩充为所有敏感API、审批或浏览器全部缓存验收。

下一步已请求本人保持Android Tailscale在线，在既有私有health Origin上检查/api/v1/approvals、/healthz/、/docs、/openapi.json及/是否均Not Found；本人随后回复“是的”，确认这五项均显示Not Found；结合先前三项，私有HTTPS八项页面观察已补齐。未获取新增五项的Network状态码/截图，不假称独立抓包已确认404。私有主机名只留在忽略部署配置，不写入Git；直接端口跨设备边界也未据此宣称通过。当前不新增代码或运行测试集合，F08.6仍BLOCKED、F07/F08整体IN_PROGRESS。


**手机直接端口检查的只读准备**：实际执行以下宿主命令，exit0；取得当前电脑的Tailscale IPv4只用于手机检查，不将地址复制到Git。监听输出仅public8000及health8010、地址均127.0.0.1；8001无listener。该结果是宿主观察，不代替另一节点的端口访问验收，没有改Serve/Funnel或监听配置。

```powershell
$ErrorActionPreference='Stop'
$taskTailscale = 'C:\Program Files\Tailscale\tailscale.exe'
if (-not (Test-Path -LiteralPath $taskTailscale)) { throw 'Installed Tailscale CLI missing' }
& $taskTailscale ip -4
if ($LASTEXITCODE -ne 0) { throw 'Tailscale IPv4 query failed' }
@(Get-NetTCPConnection -State Listen -LocalPort 8000,8001,8010 -ErrorAction SilentlyContinue | Select-Object LocalAddress,LocalPort,OwningProcess) | ConvertTo-Json -Compress
```


**下一步手机直接端口访问**：已使用只读CLI查询取得的电脑Tailscale IPv4，向本人提供带8000/8001端口的HTTP /healthz检查地址。要求保持手机Tailscale在线，逐项反馈实际错误或HTTP页面及是否自动升级HTTPS；不输入登录信息，不使用任务/审批正文。该请求不修改任何网络配置；本人结果见下段，未仅由宿主回环监听或浏览器TLS错误宣称手机端口不可达。地址仅用于聊天，本仓库不保存私有IPv4/节点主机名。


**手机直接端口的浏览器反馈（2026-10-05）**：本人先反馈两项无法访问，后明确Chrome两个指定端口均ERR_CONNECTION_TIMED_OUT；Edge初次DNS_PROBE_FINISHED_NXDOMAIN，复试也为ERR_CONNECTION_TIMED_OUT。初次DNS错误不作为端口结果，保留重试历史；未修改DNS、代理或网络安全配置。此为手机浏览器在指定地址下的未成功直连观察，不独立归因于防火墙丢弃、未提供逐项最终URL截图或TCP抓包。此前私有health正常与非health路径Not Found、宿主只读回环监听作为分开的证据保留；本轮可记录手机未能直连8000/8001，不将超时原因描述为已定位的配置缺陷，也不要求恢复这些端口的私有访问。

技术语义核对仅使用Chromium官方源码：ERR_CONNECTION_TIMED_OUT为连接尝试超时，DNS_PROBE_FINISHED_NXDOMAIN来自DNS探测分类，两者不合并为同一验收证据。参考[连接错误定义](https://chromium.googlesource.com/chromium/+/refs/heads/trunk/net/base/net_error_list.h)和[DNS探测分类](https://chromium.googlesource.com/chromium/src/+/main/chrome/browser/net/dns_probe_service_factory.cc)。未据此确认Edge首次失败的根因。

F08.6当前已补的手机范围：任务/消息创建与同步、基础网络/后台恢复、草稿来源/保存恢复/跨设备只读同步冲突及显式保留保存4、v13四静态缓存、任务/草稿GET no-store、本地存储空的本人反馈、私有HTTPS八项页面观察及两项直接端口超时反馈。未完成：手机PUT的409/412与响应结果未确认/同键重试、正式审批、其他敏感场景/缓存、受控Cloudflare中断和助手主机离线、真实Push投递/撤销/最小通知点击；生产SecretStore依赖F09仍未满足，不把已有模拟或本地代码测试用于宣称这些真机条件通过。不擅自停原助手/health/Tunnel或改变排期，F08.6保持唯一当前BLOCKED、F07/F08整体IN_PROGRESS。

本轮只修改TODO.md、NEXT_STEPS.md和F08_ENVIRONMENT_PREPARATION.md；未改代码、迁移、风险等级、审批或网络配置，未启动新进程、提交、推送或部署。仅文档续记与只读核对，没有运行测试集合、全量或PostgreSQL/Worker回归，旧数量不充作当前结果。

**电脑断网后后台运行复核（2026-10-07；未重启）**：本人询问断网后如何重启后台。首次受限查询中Win32_Process拒绝访问，端口查询未得到可采信结果；随后经提升权限作只读查询，确认原公共wrapper12100/listener6572、health wrapper23548/listener14976及Tunnel8928仍存活，8000/8010/20241均仅监听127.0.0.1，8001未监听。因此没有重复启动或停止这些进程。

实际命令为 `Get-NetTCPConnection -State Listen`（过滤8000/8001/8010/20241并仅输出LocalAddress/LocalPort/OwningProcess）、`Get-CimInstance Win32_Process`（仅输出PID、父PID、名称及已知助手命令行匹配布尔值），以及 `.\.venv-win\Scripts\python.exe -X utf8 -B -` 的httpx只读流式GET检查：timeout=8、follow_redirects=False、trust_env=False，健康正文读取上限256 bytes；不读取登录Cookie/JWT或任务正文。两次提升权限检查均exit0，结果如下：

| 检查 | 实际结果 | 范围 |
| --- | --- | --- |
| http://127.0.0.1:8000/healthz | 401 | 公共进程有响应，未携带Access认证；不据此确认认证后的任务读取 |
| http://127.0.0.1:8010/healthz | 200，最小正文{"status":"ok"} | 独立health进程正常 |
| http://127.0.0.1:20241/ready | 200 | Tunnel在复核时已就绪 |
| https://yanshuheng.dpdns.org/ui/ | 302，重定向主机为既有Access团队 | 未登录边缘身份层仍生效；不跟随登录或保存Location查询参数 |

恢复联网后建议先保留当前页面并观察，必要时只点“刷新状态”，无需因断网重复启动既有进程。尚未收到这次电脑断网期间的手机提示或恢复后的手机结果，不将上述当前宿主/边缘检查记作主机离线与真机自动恢复验收通过。本轮仅运行状态诊断与文档更新，没有产品修改或pytest/全量/PostgreSQL/Worker测试，没有启动新后台、改变配置、提交或推送；F08.6仍BLOCKED，F07/F08整体IN_PROGRESS。

**电脑断网与恢复的手机反馈及范围认可（2026-10-07）**：本人反馈：“电脑断网期间手机提示事件连接中断，电脑恢复网络后手机任务列表自动恢复正常，顶端提示未自动恢复正常，手动刷新后才恢复。其实我认为这是可以接受的”。据此，将主机断网场景记录为按本人认可范围通过，并保留体验限制：任务列表自动恢复；顶部连接提示需要手动刷新才恢复，不能写成顶部提示或SSE自动重连均已通过。具体手动刷新方式、恢复耗时及SSE连接本身未独立观测；不推断提示滞留的根因，不以列表正常作为SSE已经恢复的证据。本轮按用户已认可的范围记录，不为此新增代码修复。

复现入口沿用本轮人工步骤：手机使用独立于电脑的移动数据并保持助手页面前台，确认草稿已保存且没有结果未确认的原请求；断开电脑网络、观察手机提示；恢复电脑网络、观察列表和顶部提示，若顶部提示仍未恢复再手动刷新。本次结果来自本人真机反馈，之前的宿主端口与health/Tunnel检查作为另项证据保留；没有重新进行断网操作，不外推为关机/进程崩溃恢复或独立Cloudflare中断已经验证。

截至此反馈，主机网络断开/恢复这一项已有本人认可；余项仍为手机PUT409/412、响应结果未确认与同键重试、正式审批及其他敏感场景缓存、独立Cloudflare中断、真实Push投递/撤销/最小通知点击。生产SecretStore仍依赖F09，未自行调整排期。F08.6保持唯一当前BLOCKED，F07/F08整体IN_PROGRESS。本轮只更新TODO.md、NEXT_STEPS.md和环境指南，没有代码、认证、共享状态、迁移或部署更改，没有运行pytest/全量/PostgreSQL/Worker集合；实际运行 `git diff --check` → exit0（仅已有LF/CRLF提示）；写入该结果后再次同命令检查。未运行测试集合，不复用旧测试数量。

**真机草稿409续验准备（2026-10-07；未执行保存）**：读取当前saveFormDraft、loadFormDraft及既有390px反例，确认真实草稿CAS契约为409；手机前台只读同步可能先发现新版本并阻止发送，因此准备在saveFormDraft请求发出前用远程DevTools普通行断点暂停，再由电脑保存N+1、恢复手机发送原N请求。当前未格式化源请求行为app.js:628，以实际Sources函数/PUT语句校对为准。环境指南新增逐步操作及恢复断点要求；未添加扩展脚本、生产测试端点、响应覆盖或认证绕过。已向本人请求手机基准版本及Inspect可用性，回复和真正PUT409结果均待收到。

桌面对照控制尝试：原IAB标签未找到、列表为空；新开助手页面的控制调用超时，重新绑定后见助手壳、Failed to fetch及无任务列表。点只读刷新仍Failed to fetch；整页重读初见正在连接，后续状态捕获又超时。本轮没有点创建任务或保存草稿；未读取Cookie/JWT、不确定失败根因，不将该浏览器控制问题外推为助手停止或手机掉线。随后以本人远程DevTools/电脑PWA操作配合完成，当前不重复启动服务、安装扩展或改Access。

本轮仅准备步骤和记录，修改NEXT_STEPS.md及环境指南；没有产品/迁移/部署修改，没有运行pytest/全量/PostgreSQL/Worker集合，没有真实提交或邮件/Push投递。当前仍缺真机409和结果未确认/同键重试证据，F08.6保持唯一当前BLOCKED，F07/F08整体IN_PROGRESS；待手机准备反馈后继续同一编号。实际运行 `git diff --check` → exit0（仅已有LF/CRLF提示），写入结果后再检查；不计入自动化测试数量。

**手机版本5准备反馈与原库只读基线（2026-10-07）**：本人回复“没保存前的基准版本是5，现在可以打开”，确认基准5与远程Inspect入口。随后已请求在saveFormDraft的PUT调用前设普通行断点，手机只点击一次保存并保持暂停，等待本人反馈；此时尚未收到暂停或请求结果，未在电脑发送对照保存。

原库只读命令实际exit0（2.06秒），transaction_read_only=on、matched_drafts=1、version=5、saved_command_receipts=5。该基线只投影版本及该草稿已保存命令回执数量，不读取表单值、任务ID、键或用户身份；目标限定已授权本机原库，repeatable-read/readonly事务与5秒连接/命令超时。不是PostgreSQL测试套件，也不能证明尚未执行的冲突/重放通过。完整实际命令如下：

```powershell
@'
import asyncio, json
from pathlib import Path
from urllib.parse import urlsplit
import asyncpg
from dotenv import dotenv_values
from personal_assistant.infrastructure.database.config import normalize_dsn

async def main():
    config = dotenv_values(Path('var/deployment/f08-access-2026-10-04/.env.template'), interpolate=False)
    dsn = normalize_dsn(config['PA_DATABASE_URL'])
    target = urlsplit(dsn)
    if target.hostname != '127.0.0.1' or target.port != 5432 or target.username != 'assistant' or target.path != '/assistant':
        raise ValueError('Original local database target mismatch')
    conn = await asyncpg.connect(dsn, timeout=5, command_timeout=5, server_settings={'default_transaction_read_only':'on'})
    try:
        async with conn.transaction(isolation='repeatable_read', readonly=True):
            mode = await conn.fetchval('SHOW transaction_read_only')
            rows = await conn.fetch('''SELECT d.version,
                (SELECT count(*) FROM task_form_draft_commands c WHERE c.receipt->>'task_id'=d.task_id) AS receipts
                FROM task_form_drafts d JOIN tasks t ON t.id=d.task_id
                WHERE t.objective=$1 AND d.extension_id=$2 AND d.form_id=$3 AND d.extension_version=$4''',
                '第二个自检，不执行外部动作', 'example.echo', 'example.echo_settings', '0.1.0')
            assert mode == 'on' and len(rows) == 1, 'Read-only or unique draft check failed'
            row = dict(rows[0])
            assert row['version'] == 5, 'Draft changed; do not send the planned stale request yet'
            print(json.dumps({'read_only':mode, 'matched_drafts':len(rows), 'version':row['version'], 'saved_command_receipts':row['receipts']}))
    finally:
        await conn.close()
try:
    asyncio.run(main())
except Exception as exc:
    print(json.dumps({'diagnostic_failed':type(exc).__name__}))
    raise SystemExit(1)
'@ | .\.venv-win\Scripts\python.exe -X utf8 -B -
```

F08.6保持BLOCKED；下一步只在本人确认手机暂停后由电脑保存版本6，再恢复手机旧版本5的请求并核对真实409。没有运行自动化测试、提交/推送或新增部署。

**手机保存已暂停、电脑对照版本6（2026-10-08；409待反馈）**：本人确认手机已在此前指定的saveFormDraft请求前暂停。桌面自动控制此前超时，因此由本人在正常电脑PWA对相同自检草稿保存“电脑冲突自检-20261007”，随后回复版本6。一次原库只读核对实际exit0（0.99秒），read_only=on、matched_drafts=1、version=6、saved_command_receipts=6、matches_computer_test=true；与先前基线5/5比较仅新增一次保存回执。查询只投影版本、数量及指定PUBLIC自检值的匹配布尔值，不输出正文、任务ID或键。该观察说明电脑保存已持久化，不代替手机409结果。

实际只读命令：

```powershell
@'
import asyncio, json
from pathlib import Path
from urllib.parse import urlsplit
import asyncpg
from dotenv import dotenv_values
from personal_assistant.infrastructure.database.config import normalize_dsn

async def main():
    config = dotenv_values(Path('var/deployment/f08-access-2026-10-04/.env.template'), interpolate=False)
    dsn = normalize_dsn(config['PA_DATABASE_URL'])
    target = urlsplit(dsn)
    if target.hostname != '127.0.0.1' or target.port != 5432 or target.username != 'assistant' or target.path != '/assistant':
        raise ValueError('Original local database target mismatch')
    conn = await asyncpg.connect(dsn, timeout=5, command_timeout=5, server_settings={'default_transaction_read_only':'on'})
    try:
        async with conn.transaction(isolation='repeatable_read', readonly=True):
            mode = await conn.fetchval('SHOW transaction_read_only')
            rows = await conn.fetch('''SELECT d.version,
                d.values_json->>'prefix'=$5 AS matches_computer_test,
                (SELECT count(*) FROM task_form_draft_commands c WHERE c.receipt->>'task_id'=d.task_id) AS receipts
                FROM task_form_drafts d JOIN tasks t ON t.id=d.task_id
                WHERE t.objective=$1 AND d.extension_id=$2 AND d.form_id=$3 AND d.extension_version=$4''',
                '第二个自检，不执行外部动作', 'example.echo', 'example.echo_settings', '0.1.0', '电脑冲突自检-20261007')
            assert mode == 'on' and len(rows) == 1, 'Read-only or unique draft check failed'
            row = dict(rows[0])
            print(json.dumps({'read_only':mode, 'matched_drafts':len(rows), 'version':row['version'], 'saved_command_receipts':row['receipts'], 'matches_computer_test':row['matches_computer_test']}))
    finally:
        await conn.close()
try:
    asyncio.run(main())
except Exception as exc:
    print(json.dumps({'diagnostic_failed':type(exc).__name__}))
    raise SystemExit(1)
'@ | .\.venv-win\Scripts\python.exe -X utf8 -B -
```

已请求本人仅取消本次手机行断点并Resume/F8，核对手机原版5的实际PUT状态、本机测试输入是否保留及服务器面板是否为6；先不再次保存或选择冲突处理。此时手机恢复/409反馈仍待收到；没有将暂停或预期冲突记成通过，没有由宿主直接改库、发送公共写请求或操纵浏览器认证。没有新增代码、自动化测试、部署、提交或推送；F08.6仍BLOCKED，下一步仍是同一真机409与同键重试项目。

**真实手机PUT409与采用服务器草稿（2026-10-08；本项已补证）**：本人先反馈手机原版5的保存返回409、输入仍为“手机冲突自检-20261007”、基准仍为5、冲突面板显示服务器6；随后明确该请求方法是PUT，并点击“使用服务器草稿，放弃本机输入”（未再次保存）。本人确认页面变为版本6、prefix变为“电脑冲突自检-20261007”、冲突面板消失。本次实际409及页面处理来自本人远程DevTools/真机反馈，没有导出请求正文、认证材料或HAR，也没有独立捕获该409截图。生产CAS契约返回409，未制造412；412兼容分支不计为新的真机证据。

采用服务器草稿后再次执行上一节完整只读命令，实际exit0（0.93秒）：read_only=on、matched_drafts=1、version=6、saved_command_receipts=6、matches_computer_test=true。与电脑成功保存后的6/6相同，说明这次旧版手机请求及显式采用服务器草稿未增加版本或成功保存回执。查询仍为原库只读事务，仅投影版本、回执数量及PUBLIC自检值匹配布尔值，没有通过直接改库制造冲突。该结论不外推为响应丢失重放已经验证。

下一步仅准备同一自检草稿的回执丢失/同键重试：手机基准6，测试文字“手机回执重试自检-20261008”。先在远程DevTools使用自定义网络限速（Download 1 Kbps、Upload 100000 Kbps、Latency 60000 ms，Keep log），不点击保存；待本人确认准备后只保存一次。必须先核对原库已提交新版本且手机尚未收到完整回执，再中断手机响应；若未写入或手机已确认成功，本轮不能计作丢回执反例。恢复No throttling后只允许原请求手动同键重试，核对版本/回执数量不再次增加；实际结果尚待执行。Chrome官方[限速配置](https://developer.chrome.com/docs/devtools/settings/throttling)和[Network离线操作](https://developer.chrome.com/docs/devtools/network/reference)仅支持操作机制，不能替代上述运行证据。自定义吞吐量使用整数，不用会被当前DevTools实现按整数转换的小数速率。

本轮仅文档更新与原库只读核对，没有产品代码修改、测试集合、全量或PostgreSQL/Worker回归；没有重启或部署服务、修改认证/迁移/网络配置、提交或推送。F08.6仍为唯一当前BLOCKED；尚缺手机响应丢失/同键重试、正式审批及其他敏感场景缓存、独立Cloudflare中断与真实Push（生产SecretStore依赖F09），F07/F08整体仍IN_PROGRESS。

**首次限速保存为正常成功，不计丢回执通过（2026-10-08）**：本人按准备步骤点击一次保存并反馈“正在保存”。在仍等待反馈时原库只读命令实际exit0（0.98秒）：read_only=on、matched_drafts=1、version=7、saved_command_receipts=7、matches_phone_retry_test=true。但随后本人明确手机已经显示“草稿已保存，服务器版本7”，因此没有切Offline，也没有制造结果未确认或执行重试。该保存仅确认正常持久化；不能把查询先于手机反馈当作完整回执已丢失。

本次精确只读命令如下（和版本6核对一样仅输出元数据及PUBLIC自检值匹配布尔值）：

```powershell
@'
import asyncio, json
from pathlib import Path
from urllib.parse import urlsplit
import asyncpg
from dotenv import dotenv_values
from personal_assistant.infrastructure.database.config import normalize_dsn

async def main():
    config = dotenv_values(Path('var/deployment/f08-access-2026-10-04/.env.template'), interpolate=False)
    dsn = normalize_dsn(config['PA_DATABASE_URL'])
    target = urlsplit(dsn)
    if target.hostname != '127.0.0.1' or target.port != 5432 or target.username != 'assistant' or target.path != '/assistant':
        raise ValueError('Original local database target mismatch')
    conn = await asyncpg.connect(dsn, timeout=5, command_timeout=5, server_settings={'default_transaction_read_only':'on'})
    try:
        async with conn.transaction(isolation='repeatable_read', readonly=True):
            mode = await conn.fetchval('SHOW transaction_read_only')
            rows = await conn.fetch('''SELECT d.version,
                d.values_json->>'prefix'=$5 AS matches_phone_retry_test,
                (SELECT count(*) FROM task_form_draft_commands c WHERE c.receipt->>'task_id'=d.task_id) AS receipts
                FROM task_form_drafts d JOIN tasks t ON t.id=d.task_id
                WHERE t.objective=$1 AND d.extension_id=$2 AND d.form_id=$3 AND d.extension_version=$4''',
                '第二个自检，不执行外部动作', 'example.echo', 'example.echo_settings', '0.1.0', '手机回执重试自检-20261008')
            assert mode == 'on' and len(rows) == 1, 'Read-only or unique draft check failed'
            row = dict(rows[0])
            print(json.dumps({'read_only':mode, 'matched_drafts':len(rows), 'version':row['version'], 'saved_command_receipts':row['receipts'], 'matches_phone_retry_test':row['matches_phone_retry_test']}))
    finally:
        await conn.close()
try:
    asyncio.run(main())
except Exception as exc:
    print(json.dumps({'diagnostic_failed':type(exc).__name__}))
    raise SystemExit(1)
'@ | .\.venv-win\Scripts\python.exe -X utf8 -B -
```

下一次受控尝试调整为同一草稿基准7、PUBLIC文字“手机回执丢失自检-20261008”，自定义网络Latency改为300000 ms，下载/上传仍1/100000 Kbps。在点击一次保存后约5秒、手机仍等待时由本人直接切Offline，再核对原库是否提交。该顺序减少人工反馈往返期间完整回执先到达的机会；只有已提交且页面结果未确认、原请求绑定保留，才继续手动同键重试。尚未收到本次中断结果；若已确认正常成功或没有提交，仍不计作该反例通过。没有将自定义网络仿真外推为独立Cloudflare中断证据。

**第二次Offline反馈与草稿读取失败修复（2026-10-08；真机丢回执仍未验证）**：本人切Offline后只反馈“无法编辑草稿：Failed to fetch”；原库只读核对实际exit0（1.09秒），read_only=on、matched_drafts=1、version=8、saved_command_receipts=8、matches_phone_retry_test=true。恢复No throttling且未刷新/另发保存后，本人反馈测试输入仍在、基准8、按钮没有“重试”、没有冲突面板。此时服务器已提交，但没有确认原PUT以失败结束或页面进入结果未确认，也没有进行同键重试；不把该场景计为真机丢回执/重放通过。不推断原PUT响应在Offline期间是否已经到达或在恢复后继续完成。没有第三次手机保存；真实同键重试仍待后续可靠的中断方式。

本次实际只读命令（仅版本/数量及PUBLIC自检值匹配，不读取/输出原请求键或正文）：

```powershell
@'
import asyncio, json
from pathlib import Path
from urllib.parse import urlsplit
import asyncpg
from dotenv import dotenv_values
from personal_assistant.infrastructure.database.config import normalize_dsn

async def main():
    config = dotenv_values(Path('var/deployment/f08-access-2026-10-04/.env.template'), interpolate=False)
    dsn = normalize_dsn(config['PA_DATABASE_URL'])
    target = urlsplit(dsn)
    if target.hostname != '127.0.0.1' or target.port != 5432 or target.username != 'assistant' or target.path != '/assistant':
        raise ValueError('Original local database target mismatch')
    conn = await asyncpg.connect(dsn, timeout=5, command_timeout=5, server_settings={'default_transaction_read_only':'on'})
    try:
        async with conn.transaction(isolation='repeatable_read', readonly=True):
            mode = await conn.fetchval('SHOW transaction_read_only')
            rows = await conn.fetch('''SELECT d.version,
                d.values_json->>'prefix'=$5 AS matches_phone_retry_test,
                (SELECT count(*) FROM task_form_draft_commands c WHERE c.receipt->>'task_id'=d.task_id) AS receipts
                FROM task_form_drafts d JOIN tasks t ON t.id=d.task_id
                WHERE t.objective=$1 AND d.extension_id=$2 AND d.form_id=$3 AND d.extension_version=$4''',
                '第二个自检，不执行外部动作', 'example.echo', 'example.echo_settings', '0.1.0', '手机回执丢失自检-20261008')
            assert mode == 'on' and len(rows) == 1, 'Read-only or unique draft check failed'
            row = dict(rows[0])
            print(json.dumps({'read_only':mode, 'matched_drafts':len(rows), 'version':row['version'], 'saved_command_receipts':row['receipts'], 'matches_phone_retry_test':row['matches_phone_retry_test']}))
    finally:
        await conn.close()
try:
    asyncio.run(main())
except Exception as exc:
    print(json.dumps({'diagnostic_failed':type(exc).__name__}))
    raise SystemExit(1)
'@ | .\.venv-win\Scripts\python.exe -X utf8 -B -
```

同时确定并修复了本项局部UI问题：loadFormDraft的GET失败原先无条件清空表单区域，遮住已保留在页面内存的输入及原请求。现仅在读取暂时失败（网络错误、408或5xx）且已有经校验的Schema与本机输入/待确认请求/冲突时，继续显示原基准、值和对应保存状态；正在发送时保持按钮禁用，结果未确认时保留原键/版本及手动重试入口，不因GET失败释放或重建保存命令。成功返回但Schema不支持的响应仍拒绝编辑，404目录与其他非暂时错误的原处理不变。不改API、CAS、幂等指纹、认证、审批、迁移、秘密后端或发送链路。Service Worker升v14以更新静态脚本，缓存与拦截仍仅原四条固定静态路径；不将本机草稿放入任何持久浏览器存储。

先新增两项390px失败反例，修正新增测试的缩进笔误后，旧实现实际两项均失败于字段元素被清空。最小实现后同命令exit0、两项通过；随后强化同一反例，加入PUT已提交但回执仍等待时GET503，确认原版本/输入/禁用状态保持，之后丢回执与恢复重读并手动同键重试仍只有一份回执、版本只增加一次。另一反例覆盖真实fetch失败、未保存输入保留与恢复保存，并验证随后返回不支持Schema仍清空编辑入口。这些为本地模拟API/390px Chromium结果，不代替手机同键重试证据。

实际测试命令与结果：

| 命令 | 实际结果 |
| --- | --- |
| `.\.venv-win\Scripts\python.exe -m pytest tests/integration/test_pwa_form_drafts_browser_f08.py -q -k 'failed_draft_reread or failed_network_read'`（初版新增测试） | exit2，新增测试缩进笔误/采集错误；纠正后才进行有效红测，不当作产品失败反例证据 |
| `.\.venv-win\Scripts\python.exe -X utf8 -m pytest -p no:cacheprovider tests/integration/test_pwa_form_drafts_browser_f08.py -q -k 'failed_draft_reread or failed_network_read'` | 实现前exit1、2 failed；实现后exit0、2项通过。禁用不可写的pytest缓存，不跳过测试 |
| `.\.venv-win\Scripts\python.exe -X utf8 -m pytest -p no:cacheprovider -o 'addopts=--strict-markers' -q tests/integration/test_pwa_form_drafts_browser_f08.py tests/integration/test_pwa_browser_f08.py tests/api/test_pwa_installation_f08.py` | exit0，21 passed、2个依赖弃用警告，55.26秒；覆盖相邻PWA及静态缓存升级/安装 |
| `.\.venv-win\Scripts\python.exe -X utf8 -m pytest -p no:cacheprovider -o 'addopts=--strict-markers' -q tests/integration/test_pwa_form_drafts_browser_f08.py`（强化在途反例后） | exit0，9 passed，19.66秒；与上一行有重复，不加总为独立用例数 |
| `.\.venv-win\Scripts\python.exe -X utf8 -m ruff check --no-cache tests/integration/test_pwa_form_drafts_browser_f08.py` | 新增fixture长行先报E501，改为多行后通过 |
| `D:\nodejs\node.exe --check web/pwa/app.js` / `D:\nodejs\node.exe --check web/pwa/service-worker.js` | 均通过 |
| `git diff --check` | 首次指出新增测试末尾空行；删去多余空行后收尾再次核对，结果见收尾记录 |

本轮改动web/pwa/app.js、web/pwa/service-worker.js、tests/integration/test_pwa_form_drafts_browser_f08.py及TODO/NEXT_STEPS/环境指南/契约文档；保留原工作区已有改动。本项属于读取失败显示的局部修复，按约定仅跑定向测试与静态检查，没有重跑全量或完整PostgreSQL/Worker套件，没有访问真实邮箱、Push或ehall，没有重启既有服务、修改网络配置、提交或推送。尚缺手机v14复验、可靠的真实丢回执/同键重试及其余F08.6条件；F08.6仍唯一当前BLOCKED，F07/F08整体IN_PROGRESS。

本次局部修复收尾：实际Desktop仓库的Ruff目标文件、两份Node --check及git diff --check均exit0；仅保留已有LF/CRLF提示。git diff人工复核确认只新增草稿读取失败的状态保留、两项反例及固定静态缓存版本14，原工作区其他改动未回退。没有用静态或本地浏览器结果宣称手机v14或真实丢回执重放已通过。

**手机仍为v13的更新诊断（2026-10-08；v14真机未确认）**：本人按此前刷新步骤反馈CacheStorage仍为v13，故没有继续用旧脚本验收局部修复。只读检查当前Desktop仓库的Service Worker第一行为v14；同一.venv-win导入的personal_assistant.app路径及_pwa_root均指向该Desktop仓库。命令实际exit0（2.09秒），没有build_container、启动应用/Worker、执行迁移或读取认证材料。这里只确认当前解释器与磁盘目录，不宣称已经读取了现有公共进程的认证后HTTP正文，也不据此确认手机或Cloudflare收到新版。

实际只读命令：

```powershell
@'
import json
from pathlib import Path
import personal_assistant.app as package
from personal_assistant.app import _pwa_root

runtime = json.loads(Path('var/deployment/f08-access-2026-10-04/runtime.json').read_text(encoding='utf-8-sig'))
print(json.dumps({'runtime_field_names':list(runtime), 'imported_app_file':package.__file__, 'pwa_root':str(_pwa_root()), 'source_worker_first_line':(_pwa_root()/'service-worker.js').read_text(encoding='utf-8').splitlines()[0]}))
'@ | .\.venv-win\Scripts\python.exe -X utf8 -B -
rg --files --hidden --no-ignore var/deployment/f08-access-2026-10-04 -g '*.ps1' -g '*.py'
```

本人已被请求在原手机远程DevTools Application → Service workers确认非Offline，点当前注册的Update一次，观察Status、错误及缓存版本；不注销注册、不清站点数据，也不勾选Update on reload/Bypass for network以替代正常生命周期。该一次更新及状态/错误的查看方式按[Chrome官方PWA调试说明](https://developer.chrome.com/docs/devtools/progressive-web-apps)核对。实际手机更新结果尚待反馈，未定位为边缘缓存、鉴权或安装失败，不凭猜测改变CDN缓存规则、认证策略或重启既有服务。

本轮只读诊断与文档续记，没有产品代码更改或新增测试执行，不复用上一轮21/9的数量作为本轮结果。下一步仍是F08.6的v14更新与读取失败真机复验；真实丢回执/同键重放仍未验证。F08.6保持唯一当前BLOCKED，F07/F08整体IN_PROGRESS；未提交、推送或新增部署。

**一次Update后的手机反馈（2026-10-08）**：本人反馈“#7 activated and is running”，CacheStorage仍v13。按Chrome官方说明，Status的编号不是本项目缓存版本，因此只记录现有注册仍运行；尚未确认运行脚本的CACHE常量、新脚本下载响应或安装错误。已请求本人读取Source第一行，并在Network核对一次更新请求的状态码与CF-Cache-Status/Age/Cache-Control；不索取完整Headers或认证材料。未清站点数据、注销注册、重启服务或修改缓存策略。Cloudflare官方默认缓存行为说明JS等静态扩展在无缓存头时可能按状态码缓存；本项目现有安全响应头只为/api/设置no-store。但缺少本次手机请求的实际头，仍不能认定边缘缓存是根因。参考[Cloudflare默认缓存行为](https://developers.cloudflare.com/cache/concepts/default-cache-behavior/)。本轮git diff --check实际exit0（仅已有换行提示），未重新运行测试集合。F08.6仍BLOCKED。

**运行源首行确认v13（2026-10-08）**：本人从当前手机注册Source抄得 `const CACHE = "assistant-static-v13";`，并反馈当前页面Network过滤service-worker.js未出现更新请求。现可确认该手机当时运行的是v13，不只依赖缓存树显示；还未确认实际网络交付的文件是否为v13。已请求在同一被Inspect的手机Chrome标签页只读打开稳定脚本URL及附PUBLIC诊断参数的同一路径：`/ui/service-worker.js`、`/ui/service-worker.js?f086=20261008-v14`，分别核对首行和响应状态/CF-Cache-Status/Age/Cache-Control。本人此前已确认保存到8且无未保存请求，故该导航不丢弃未确认的命令；诊断参数不改正式注册URL，也不执行保存或安装。两项真实HTTP结果尚待反馈，未据此改策略或宣称缓存根因已定位。本轮仅文档续记与静态差异检查，没有新增测试执行；F08.6仍唯一当前BLOCKED。


**静态 HTTP 缓存最小修复及公共进程重新加载（2026-10-08）**：用户认为反复真机实验过重，本轮收缩为固定静态脚本交付问题；停止要求手机反复操作DevTools和制造丢回执，不将未测项目计为通过。代码确认原 `/ui/` 响应未设Cache-Control，固定名JS可能被HTTP/CDN缓存；运行源v13尚不能证明具体根因。对稳定与查询参数脚本的普通Python读取，默认沙箱均URLError；提升权限后稳定地址URLError、查询地址403/no-store。已有浏览器只读诊断标签页到达Access登录页，随后关闭；未读取Cookie、令牌或绕过Access。这些请求没有取得已认证脚本内容。

最小实现仅在 `SecurityHeadersMiddleware` 的既有no-store分支加入 `/ui/`；固定UI的GET、HEAD、304及带诊断查询的Worker响应均禁止HTTP缓存，API策略保持no-store。Service Worker仍为v14、CacheStorage仍只存四个固定静态资源，未添加敏感响应、扩展JS、自动升级脚本或持久化前端状态。测试先失败，后修复；如下结果均为本轮实跑：

```powershell
# 实现前：1 failed / 4 deselected / 2依赖警告；实现后同命令：1 passed / 4 deselected / 2警告。
.\.venv-win\Scripts\python.exe -X utf8 -m pytest -p no:cacheprovider -o 'addopts=--strict-markers' -q tests/api/test_pwa_installation_f08.py -k fixed_ui_files
# 29 passed / 2依赖警告，23.18s；包含上面1例，不另行加总。
.\.venv-win\Scripts\python.exe -X utf8 -m pytest -p no:cacheprovider -o 'addopts=--strict-markers' -q tests/api/test_pwa_installation_f08.py tests/api/test_api.py tests/api/test_boundaries.py tests/api/test_pwa_tasks_f08.py tests/integration/test_pwa_browser_f08.py
# exit0：All checks passed。
.\.venv-win\Scripts\python.exe -X utf8 -m ruff check --no-cache src/personal_assistant/api/middleware/security_headers.py tests/api/test_pwa_installation_f08.py
# exit0：1 source file无问题。
.\.venv-win\Scripts\python.exe -X utf8 -m mypy src/personal_assistant/api/middleware/security_headers.py
# 两项exit0。
& 'D:\nodejs\node.exe' --check web/pwa/app.js
& 'D:\nodejs\node.exe' --check web/pwa/service-worker.js
git diff --check
```

按本轮小范围要求只跑相关定向API/390px浏览器及静态检查，未跑全套、PostgreSQL/Worker套件或真实业务环境回归；本地浏览器使用模拟服务，不能代替手机v14交付证据。Cloudflare默认尊重no-store，缓存规则仍可覆盖源站策略，因此尚不能声称已清除现存边缘副本。[Cloudflare默认缓存行为](https://developers.cloudflare.com/cache/concepts/default-cache-behavior/)

沿用用户已有的启动接入及现有已启用扩展恢复授权，只重新加载公共助手。先核对wrapper12100/listener6572及直属进程、独立控制台、原配置发送/提交关闭，排除health/Tunnel；Ctrl+C请求未使旧进程退出，20秒等待失败且没有启动替代。Ctrl+Break使四个原Worker退出并释放8000，但诊断辅助进程也退出非零、两个原公共Python仍在，故未按该命令宣称重启成功。再次核验这两个Python的精确模块/回环端口/配置参数后，执行 `Stop-Process -Id 6572`、`Stop-Process -Id 12100` 清理残留，确认原Worker均退出且8000无监听。随后使用 `Start-Process -WindowStyle Hidden`、原工作目录/原env-file及以下实际参数启动公共助手，stdout/stderr留在既有受限Git忽略目录的runtime-public-cache-fix.out.log/.err.log：

```powershell
.\.venv-win\Scripts\python.exe -m uvicorn personal_assistant.app:app --host 127.0.0.1 --port 8000 --no-access-log --timeout-graceful-shutdown 10 --env-file var/deployment/f08-access-2026-10-04/.env.template
```

10秒关停等待上限只作为本次临时Uvicorn参数，避免后续关停等待长连接；未实现F09服务守护或开机自启。新wrapper38220/listener30748，runtime.json已更新实际PID。本轮只读黑盒实际结果：公共 `http://127.0.0.1:8000/ui/service-worker.js` 和 `/api/v1/tasks` 无认证均401且Cache-Control:no-store；独立8010的health为200、最小status:ok；Tunnel回环ready为200。8000与8010仍仅监听127.0.0.1，health23548/14976和Tunnel8928保持运行。未更改DNS、Access规则、数据库凭据或迁移文件，未提交/推送；上述关停步骤仅记录本次旧PID，不能用于再次重启。

下一步仅请手机普通刷新一次，反馈列表/连接是否正常；不再要求DevTools、退出登录或保存草稿。手机升级v14、真正丢回执/同键重试仍未验证；正式审批、其余未访问敏感缓存、独立Cloudflare中断及真实Push仍未补齐，F08.6保持唯一当前BLOCKED，F07/F08整体IN_PROGRESS，不启动后续编号。


**普通刷新反馈（2026-10-08）**：公共进程重新加载后，本人对“普通刷新一次、只反馈任务列表与连接”的请求回复“列表和连接正常”。这仅作为本次正常使用恢复的人工证据，不证明手机当前运行v14或所有边缘旧副本已失效。停止反复DevTools/丢回执操作；此前真正响应丢失重试及正式审批/真实Push等未验收项继续保留，阶段状态不变。本轮新增改动文件为 src/personal_assistant/api/middleware/security_headers.py、tests/api/test_pwa_installation_f08.py、TODO.md、docs/CONTRACTS_AND_INTERFACES.md、docs/NEXT_STEPS.md，忽略目录另更新runtime.json及运行日志。


**下一实际依赖盘点（2026-10-08）**：按用户“继续”收尾现有F08.6，而非追加反复手机实验。核对真实生产组合根：PostgreSQL默认绑定 `UnavailableSecretStore`，其put/resolve_for_broker/delete均抛 `SecretUnavailableError`；当前固定入口尚未配置VAPID公钥与秘密句柄。因此真实Push有F09凭据后端依赖，不能用内存明文替代。已准备提请用户授权最小F09.1排期例外，仅Windows Credential Manager实现SecretStorePort及必要接线；F08.6维持BLOCKED、F07/F08维持IN_PROGRESS。该提议尚未被当作授权或NEXT，不在本轮实施F09。

原库只读事务（下方精确命令，exit0、1.25s）：example.echo@0.1.0与nju.smail@0.1.0均ENABLED；审批状态聚合为空（没有审批记录）；VAPID两项配置均false，mail_send/browser_submit均false。仓库nju.smail Manifest为0.2.0，存在部署版本差异，后续升级必须另作精确材料确认；本轮没有build/install/import/execute该新扩展或制造可批准的外部动作。未读取审批载荷、nonce、邮件正文或凭据值到输出。

```powershell
@'
import asyncio, json
from pathlib import Path
from urllib.parse import urlsplit
import asyncpg
from dotenv import dotenv_values
from personal_assistant.infrastructure.database.config import normalize_dsn

async def main():
    config = dotenv_values('var/deployment/f08-access-2026-10-04/.env.template', interpolate=False)
    dsn = normalize_dsn(config['PA_DATABASE_URL'])
    target = urlsplit(dsn)
    assert (target.hostname, target.port, target.username, target.path) == ('127.0.0.1', 5432, 'assistant', '/assistant')
    conn = await asyncpg.connect(dsn, timeout=5, command_timeout=5, server_settings={'default_transaction_read_only':'on'})
    try:
        async with conn.transaction(isolation='repeatable_read', readonly=True):
            mode = await conn.fetchval('SHOW transaction_read_only')
            assert mode == 'on'
            extensions = await conn.fetch("SELECT id, manifest->>'version' AS version, lifecycle_state FROM extensions ORDER BY id")
            approvals = await conn.fetch("SELECT status, count(*) AS count FROM approvals GROUP BY status ORDER BY status")
            print(json.dumps({
                'read_only': True,
                'extensions': [dict(row) for row in extensions],
                'approval_counts_by_status': [dict(row) for row in approvals],
                'push_public_key_configured': bool(config.get('PA_PUSH_VAPID_PUBLIC_KEY')),
                'push_secret_handle_configured': bool(config.get('PA_PUSH_VAPID_SECRET_HANDLE')),
                'mail_send_enabled': str(config.get('PA_MAIL_SEND_ENABLED', 'false')).lower() in ('true', '1', 'yes', 'on'),
                'browser_submit_enabled': str(config.get('PA_BROWSER_SUBMIT_ENABLED', 'false')).lower() in ('true', '1', 'yes', 'on'),
            }, ensure_ascii=False))
    finally:
        await conn.close()

try:
    asyncio.run(main())
except Exception as error:
    print(json.dumps({'diagnostic_error_type': type(error).__name__}))
    raise SystemExit(1)
'@ | .\.venv-win\Scripts\python.exe -X utf8 -B -
```

本轮只改TODO/NEXT的依赖与状态记录，没有产品代码、数据库写入、运行配置/进程或网络修改；没有运行pytest、全套、PostgreSQL/Worker回归，上一轮29 passed不作为本轮结果。文档差异检查 `git diff --check` exit0。当前等待用户对跨编号的排期例外作明确选择；未提交或推送。

**实施报告交接（2026-10-08）**：本人要求先生成当前报告，交独立审计 agent 验收 F08.6；因此本轮不推进 F09.1，不把提议设为 NEXT。见 [F08.6 实现与验收交接报告](F08_6_IMPLEMENTATION_REPORT_2026-10-08.md)：包含 main 的精确 HEAD、未提交文件及关键 SHA-256、逐轮实际命令/结果、当前回环服务与私有 Serve 只读快照、手机反馈与截图的适用边界，以及真实 Push、正式审批、手机 v14 交付和丢回执等未完成项。严格串行造成的是凭据后端排期阻塞，任务/消息/草稿运行没有因此死锁。F08.6 仍为唯一当前 BLOCKED，F07/F08 整体 IN_PROGRESS；本轮只改报告与状态文档，没有新 pytest/全量/PostgreSQL/Worker 测试，没有代码、服务配置、数据库或外部行为修改。审计只读默认，使用 Desktop 实际工作区；不把实施报告当独立验收。

本次报告收尾：报告已写入，17 个源码/测试/资源 SHA-256 已逐项复核一致；报告生成后的 git diff --check 实际 exit0（仅已有 LF/CRLF 提示）。本轮未运行测试集合，不将只读或文档检查计入 passed 数量。

**独立复验后P2修复（2026-10-08）**：2026-10-08 独立复验后 P2 修复：旧草稿回执只确认原命令，不覆盖已采用的新版本或显式保留的本机输入；原幂等键保留至完整回执验证和渲染成功。390px 三个反例先 3 failed、修复后 3 passed，草稿 API/浏览器及静态缓存相关回归 30 passed、2 依赖警告，Ruff/Node/差异检查通过。Service Worker 磁盘版本 v15，仍只缓存四条静态路径，手机交付未验证。报告已补修复证据并更新 17 项摘要；本修复等待独立复验，F08.6 继续唯一当前 BLOCKED、F07/F08 整体 IN_PROGRESS，不推进 F09.1；未运行全量/完整 PostgreSQL/Worker，未重启或改配原服务，未提交/推送。

精确失败/通过命令、用例条件、未完成项及最新摘要见 [实施报告第10节](F08_6_IMPLEMENTATION_REPORT_2026-10-08.md#10-独立复验后旧回执覆盖新草稿的-p2-修复)。旧审计40例与本轮30例是不同且重叠的集合，未相加；全量中断没有完整结果。

**P2局部独立复验通过（2026-10-08）**：2026-10-08 P2 独立复验通过并关闭：用户复跑报告第10节定向集合 30 passed、2 warnings，另有原问题、坏回执/渲染失败同键重试、迟到回执保留未处理冲突的组合反例 3 passed；Ruff、两份 JS 语法、差异检查及 17 项摘要吻合。用户确认测试浏览器已回收、原服务未受影响，本轮未发现新的局部阻断。旧回执仅确认原命令，显式保留的本机输入仍须再次保存。F08.6 整体继续唯一当前 BLOCKED，F07/F08 整体 IN_PROGRESS；尚缺手机 v15、真机丢回执重试、正式审批及缓存、独立 Cloudflare 中断、真实 Push。下一步仍补 F08.6 证据，不推进 F09.1。本轮实施者只记录审计反馈，不新增代码/测试或改动服务，不提交/推送。

禁区：把业务编排放进前端；在 localStorage 保存凭据或完整敏感正文。

### F09 — Windows 生产适配器（IN_PROGRESS，本地实现已复验，真实运行待验收）

范围：Credential Manager、服务守护、交互桌面桥、进程管理、文件监听；保留 portable test doubles。

能力边界：Win32 代码只位于 `infrastructure/platform/windows`。

验收：Windows 冷启动恢复；服务态任务能请求交互桌面；核心测试在非 Windows test doubles 上仍通过。

失败红线：核心/扩展 SDK import Win32；服务把密钥放环境变量/命令行。

禁区：用 UI 自动化代替稳定协议接口；把交互桌面权限开放给所有扩展。

### F09.1 — Windows 安全凭据后端（DONE，最小授权范围独立验收通过）

2026-10-09 当前结论：凭据功能已先独立通过，阻断其阶段的F06.P2本次也通过完整独立复验，最小F09.1可解除BLOCKED并标DONE。独立全量1301 passed/130 skipped/9 warnings，PostgreSQL/真实Worker130 passed/1 warning，Ruff/Mypy232/依赖/差异/摘要/wheel通过。其他F09仍未授权实施，回到唯一后续F08.6（BLOCKED），补实际手机v15、丢回执重试、正式审批/缓存、Cloudflare独立中断及真实Push；当前部署/凭据配置并未在本轮改动。以下为实现与首轮门禁历史。

2026-10-08 用户明确授权的排期例外：先实现最小凭据后端，再回到 F08.6 验收。F08.6 继续 BLOCKED，F07/F08 整体 IN_PROGRESS；本授权取代前文历史记录中的“不推进 F09.1”。

范围：在 infrastructure/platform/windows 实现 SecretStorePort 的 Credential Manager 适配器；生产 PostgreSQL 组合根按平台选择；本机 CLI 只经隐藏终端输入保存并输出句柄。无迁移，无服务守护、自动邮件/填表、真实 Push 配置或外部动作。

本轮授权文件：Windows 平台实现、infrastructure/secrets 工厂、bootstrap.py、CLI 凭据命令及最小解析器接线，对应单元/Windows 集成测试，以及 README/TODO/契约/NEXT 与实现报告。Win32 不进入 core/SDK/扩展。原服务保留，不提交/推送/部署。

验收：先失败反例；句柄与 kind/namespace 绑定；同名不覆盖；Unicode/多行与字节上限；缺失/损坏/OS 错误拒绝且脱敏；隐藏输入不降级回显；合成凭据在真实 Windows 独立进程读取并精确删除；生产默认及注入接线。因凭据边界及组合根改动，运行全量及适用 PostgreSQL/Worker 门禁；记录实际结果，不复用历史数字。

#### F09.1 本轮结果与未通过门禁（2026-10-08）

已实现：Windows Generic Credential、UTF-8/2560字节上限、kind/namespace/wcm1_引用绑定、脱敏错误及精确删除；平台工厂、PostgreSQL默认/注入接线；隐藏CLI输入/删除，原CLI架构断言保留。无迁移/依赖新增；原服务未重启或换包。

最终定向命令：
```powershell
.\.venv-win\Scripts\python.exe -m pytest -p no:cacheprovider -o 'addopts=--strict-markers' -q --tb=short tests/unit/test_windows_credentials_f09.py tests/unit/test_secret_cli_f09.py tests/integration/test_windows_credentials_native_f09.py tests/contract/test_extension_independence.py tests/unit/test_cli_extension_flow.py tests/unit/test_bootstrap_f04.py
```
先红28 failed/1 passed，最终 **42 passed**，含真实Windows跨进程及仅解析合成VAPID的两项Native测试。

门禁命令：
```powershell
.\.venv-win\Scripts\python.exe -X utf8 var/verification/f091/run_gates.py full
.\.venv-win\Scripts\python.exe -X utf8 var/verification/f091/run_gates.py postgres
.\.venv-win\Scripts\python.exe -X utf8 var/verification/f091/run_gates.py full-failures
.\.venv-win\Scripts\python.exe -X utf8 var/verification/f091/run_gates.py postgres-failures
```
前两项调用既有scripts/test.ps1、scripts/test-postgres.ps1；只在子进程环境派生*_test DSN、输出脱敏，不测试原业务库。首轮全量 **9 failed/1284 passed/130 skipped/15 warnings**；PostgreSQL/Worker **3 failed/127 passed/2 warnings**。日志保留。

后两项串行复跑失败路径：full-failures 首次2 failed/19 passed/1 warning；修正PYTHONUTF8=1与Windows tasklist编码冲突（子进程保留系统locale、仅stdio UTF-8）后 **1 failed/20 passed**。postgres-failures 对邮件/ehall Worker完整文件 **6 passed**。

剩余失败是 test_extension_rpc_robustness.py::RpcRobustnessTests::test_rpc_call_timeout_includes_lock_wait 的elapsed<0.7s，两次串行0.703/1.641s。未放宽断言、未改RPC核心；F09.1收尾 **BLOCKED**，定向实现就绪待独立复验。未重跑第二遍完整门禁，不称全量全绿。

最终全仓Ruff、既有六包Mypy（232 source files）、pip check通过；差异检查通过。所有文件、命令与15项摘要见 [F09.1实现报告](F09_1_IMPLEMENTATION_REPORT_2026-10-08.md)。

风险：同身份跨进程已测，服务换身份/冷启动未测；不保证全堆清零或强杀清理；原临时DSN例外未迁移；真实Push尚未配置。先核对回归门禁并独立验收F09.1，再回F08.6补真机Push/正式审批等；F08.6仍BLOCKED，F07/F08整体IN_PROGRESS，其他F09功能不启动。未提交/推送/部署。

#### F09.1 初次独立验收与F06阻断（2026-10-09，历史；现已关闭）

用户独立检查通过凭据功能；阶段仍 **BLOCKED**。用户本次实际结果：F09.1定向 **42 passed**，RPC集合 **14 passed**，全量 **1292 passed、130 skipped、1 failed**（邮件TLS超时，单例复跑通过），PostgreSQL/真实Worker **130 passed**；Ruff、Mypy232文件、依赖、差异及15项交接摘要均通过。RPC本次未复现，不宣称根治；全量不能称全绿。以上为用户独立证据，未提供完整命令文本，实施者不冒充本轮重新运行。

新P2：`infrastructure/mail/smtp_client.py::_TrackedSmtp._get_socket` 在 `wrap_socket` 的自动TLS握手返回后才登记实际TLS socket。用户独立反例取消250ms后连接仍打开、线程仍运行，socket超时设2秒时约2.0秒后才回收；默认30秒可能明显拖延。反例零邮件发送，未证明凭据泄漏。

拟定最小修复：先 `wrap_socket(..., do_handshake_on_connect=False)` 创建TLS socket并登记，检查中止标记后显式握手；失败和取消关闭实际连接。改动文件限 `smtp_client.py`、`tests/integration/test_mail_protocol_f06.py`，如确认必须修改测试服务夹具则仅限对应辅助类，以及本轮状态/契约/报告。先写失败反例，增加有界取消测试，分别控制底层socket超时和宿主总时限分支，保留线程回收、连接关闭、零发送断言；随后串行全量及PostgreSQL/Worker完整复验，不重复实现凭据后端。

**待用户另行授权**：依据AGENTS每任务步骤3及用户本轮说明，F06跨范围修复尚未开始、尚未设为NEXT。F09.1/F08.6继续BLOCKED，F07/F08整体IN_PROGRESS，其他F09不推进。当前仅记录状态，无业务代码/测试改动、测试重跑或服务/外部账户操作。2026-10-08报告第8节15项摘要是当时交接快照；本次新增文档记录不改写该历史摘要。
### F10 — 部署、备份与双入口安全测试（IN_PROGRESS，本地实现已复验，生产演练待验收）

范围：Cloudflare Tunnel、Access、Tailscale health-only、6 小时恢复点、异地加密副本与恢复演练。

能力边界：常开主机与普通笔记本功能一致，只承诺可用性差异。

验收：黑盒证明 8010 只有 health；8001 仅回环；RPO <= 6h、RTO <= 2h 恢复演练；D2 受管制品纳入备份，D3 调试数据排除。

失败红线：Tailscale 可达任务/审批/管理 API；备份从未恢复验证；笔记本睡眠后自动重放发送/提交。

禁区：将 Git 当数据备份；把 cloudflared 指向 Admin API。

## 两条最终 E2E

- E2E-A：邮件通知 → 知识检索 → 缺失材料 → 手机补充 → ehall 填写 → Android 最终预览确认 → 可选低风险真实提交 → 归档。
- E2E-B：邮件 → 历史/知识 → 草稿 → Android 编辑 → 精确确认 → 受控真实 SMTP 发送 → 归档。

两条都必须通过；模拟外部系统的测试不能替代最后一次受控真实验收。


## 2026-10-13 集中交付状态（历史；当前见文首）

用户授权本批次完成全部后续开发，并提前接入真实模型；仅真实邮箱/通知/站点配置和联调延期。本地开发代码、运行手册和离线wheel已准备，本轮门禁已收尾；F09后续适配/F10运维实现待独立复验。当前唯一待办为 DELIVERY.2 修复与独立复验（BLOCKED），真实条件准备后回到F08.6；不启动新的开发设计。

新增持久运行、调度/事件去重、版本工作流、模型披露/严格提案、规则候选审核/Git回滚、PWA运行入口、本机Admin、Windows主机/进程控制、加密备份/空目标恢复和离线升级。新0012迁移未应用原库，0001–0011未改，原已验收F06三文件摘要保持。本轮没有提交、推送、部署、安装新业务扩展或真实外部动作。

用户已运行DeepSeek deepseek-flash的PUBLIC单次试调用，ok=true/104token；真实任务材料仍须单独披露许可。F07/F08整体IN_PROGRESS，F08.6 BLOCKED，F06.P2/最小F09.1 DONE保持不变。下方历史“其他F09未授权”等说明已由本次授权取代；历史测试数量不作本轮结果。

本轮精确命令、改动文件、门禁结果、未完成项和风险见 [集中开发报告](DEVELOPMENT_DELIVERY_2026-10-13.md)；实际运行方式见 [本地运行手册](RUNBOOK_LOCAL_BETA.md)。


### DELIVERY.2 — 集中开发本地独立复验（DONE，本地范围）

2026-10-20用户独立复验通过，本地验收阻断关闭；证据及限制见文首。本轮未重复全量或PostgreSQL/Worker。后续回到F08.6真实条件验收，不据此关闭F07/F08整体或宣称Beta已发布。

以下为2026-10-13实施时的历史证据：

2026-10-13 实施收尾：全量本地1373 passed/132 skipped/7 warnings，Ruff与Mypy263通过；临时PostgreSQL/Worker完整运行128 passed/4 failed（迁移枚举未含0012），补齐精确版本和checksum后受影响集合7 passed。没有再跑完整PostgreSQL门禁，不称全部门禁一次全绿。全部源/SDK/扩展/脚本Mypy269、Ruff、三份JS语法、pip check、diff check通过；最终wheel265条目、10web资源、12迁移逐字节核验，无业务扩展源码。

开发包实现就绪待独立复验。原库未迁移/原服务未重启，未提交/推送/部署；测试容器删除路径已执行但Docker收尾复查超时、剩余数未独立确认；pytest结束。真实配置与验收延期，F08.6继续BLOCKED、F07/F08整体IN_PROGRESS；原已验收F06.P2/F09.1 DONE不变。精确命令、日志、文件清单、历史失败与风险见集中报告。独立本地复验后，才根据用户安排进入API配置/真实环境验收，不提前宣布整体DONE。
