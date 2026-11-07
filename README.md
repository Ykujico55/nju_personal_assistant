# Personal Assistant Framework

这是一个本地优先、扩展驱动的个人助理基架。FastAPI 只负责控制面；Agent、上下文、工具策略、审批与扩展生命周期都通过稳定接口组合。具体业务能力必须放在 `extensions/`，不能在核心里添加扩展 ID 分支。

完整设计见 [DESIGN_AND_DEVELOPMENT.md](./DESIGN_AND_DEVELOPMENT.md)，简要接力列表见 [TODO.md](./TODO.md)，后续实现顺序见 [docs/NEXT_STEPS.md](./docs/NEXT_STEPS.md)，稳定接口与持久化契约见 [docs/CONTRACTS_AND_INTERFACES.md](./docs/CONTRACTS_AND_INTERFACES.md)。

## 当前交付：通用本地文件扩展 FILES.1

**FILES.1 已验收并安装**：独立的 `personal.files@0.1.0` 已在原实例登记ENABLED，将现有知识库根配置为共享read_write，支持文本列举/读取、差异预览、版本核对后写入及逐层创建子目录。删除仅开放本机本人交互确认。原后台保持此前停止/冻结状态，没有自动执行任务；正常启动后从新对话/任务使用。当前两插件仍独立保存目录配置，不自动跟随知识库切换。下一唯一待办为WORKSPACE.1统一实例工作区，已获授权、尚未实施；FILES.BACKUP.1顺延。范围见[插件说明](extensions/personal_files/README.md)，证据见[验收安装记录](docs/FILES1_ACCEPTANCE_AND_INSTALL_2026-11-07.md)。

前项 **CORE.RECONCILE.1 本地实现完成，WAITING 正式验收/部署**：宿主以原动作绑定的持久证据确认结果后，任务转为等待用户明确继续，保留旧审批及 Outbox 历史，不重发原动作。既有专项 **37 passed**、隔离 PostgreSQL **5 passed**；后续关键回归 **643 passed**。本次收尾复用这些证据，未冒称完整套件、独立验收或原实例部署通过。旧完整门禁失败过程保留在[对账报告](docs/CORE_RECONCILIATION_2026-11-07.md)。

## 日常测试入口

默认 `scripts/test.ps1` 运行关键回归。最近一次FILES.1验收记录：插件专项 **72 passed**、独立反例 **7 passed**、关键回归 **643 passed、6 warnings，150.02秒**；Ruff、宿主Mypy290文件/插件7文件通过。按改动范围选择完整、数据库或插件专项，命令见[测试指南](docs/TESTING.md)，证据见[验收安装报告](docs/FILES1_ACCEPTANCE_AND_INSTALL_2026-11-07.md)。这些为2026-11-07既有运行，不冒称提交时再次执行；F08.6仍BLOCKED，F07/F08整体IN_PROGRESS。

以下日期章节保留历史实现、测试与部署记录；不代表所有候选都已安装或当前服务状态。当前排期以上文及接力清单顶部为准。

## 2026-11-07 索引状态修复（0.1.6 已部署并核验）

已按本人授权升级原实例至personal.knowledge 0.1.6并重启。现有目录一次完整扫描：复用40份有效索引、明确跳过2个空文本、无错误，状态READY；检索“数据结构”实际返回2条带正文和来源定位的CURRENT结果。后台在线、已恢复原先未冻结策略，原目录配置和15个任务保持。

当前全文检索可为RAG提供正文证据，语义向量模型仍未配置，本轮未调用大模型。TUI按F5刷新后建议新开对话使用，旧任务不自动更换工具快照。部署与验证证据见[当前接力记录](docs/NEXT_STEPS.md)。

## 2026-11-07 知识库文件正文（0.1.5 已部署）

知识插件新增 `knowledge.read` 和 `knowledge.document`，可直接读取已授权目录内的指定 TXT、Markdown 或受支持 PDF 的正文，不依赖索引。对话可说“阅读 数据结构.md 并总结”，或使用明确的 `knowledge://根标识/相对路径`；同名文件会要求选择，文件路径不会扩大目录权限。关键词搜索仍需要有效索引，待索引会明确说明，不能当作没有匹配或目录为空。已按本人授权升级到原实例0.1.5并重启，Worker在线、未冻结；原目录、索引和任务保留。验证与安装边界见[接力记录](docs/NEXT_STEPS.md)。

## 2026-11-07 日常使用修复已部署

本轮补齐多宿主扩展恢复协调、配置保存/应用分离、旧任务工具差异与携目标新建、脱敏诊断、受控restart，以及TUI接口兼容检查和参考对话展示。已按本人要求切换原实例、升级知识插件至0.1.4/ENABLED；Worker在线并保留原先未冻结策略，原目录/索引/任务不变。已有TUI需退出重开；旧索引缺绑定证明，首次完整reindex前显示待索引。未恢复旧任务或强制启用其他隔离扩展。验证证据和状态见[接力记录](docs/NEXT_STEPS.md)，操作方法见[工作台指南](docs/TUI_WORKBENCH.md)。

## 2026-11-06 对话与实例知识库

本机 TUI 的 automatic 对话现在按需读取实例共享的知识上下文，并把明确执行请求创建为真实 Agent 任务；目录配置一次即可跨对话/任务复用。回答保留流式与来源，任务显示编号和状态，最终外部动作仍需审批。本轮代码已按本人授权部署到原后台；已有 TUI 窗口需重新打开。详见[工作台指南](docs/TUI_WORKBENCH.md)。

`CORE.CONVERSATION.2` 已按本人授权升级原实例至知识插件 0.1.2，并重启后台：新增目录概览、明确模型失败提示及“放弃未完成回复”。原目录和索引保留；重开 TUI 后使用。启动隔离现象已通过正式启用恢复，根因另在 CORE.STARTUP.1 排查。

`CORE.OUTPUT.1` 已于2026-11-07获本人授权部署：知识插件0.1.3/ENABLED，后台已重启且恢复未冻结；默认目录/上下文/回答长度裁剪及1024输出token预算已取消，运行保护保留。原目录、索引和任务保留；已有TUI窗口需退出重开。相关目标验证和部署状态见 [实现接力清单](docs/NEXT_STEPS.md)。

## 当前能力与边界（2026-10-30）

| 范围 | 当前可用形态 | 尚未完成或需验证 |
|---|---|---|
| 手机与电脑 | 手机PWA任务交互；本机TUI问答、Agent任务、扩展管理和配置；同持久实例共享任务 | TUI问答已支持DeepSeek真实流式；手机纯聊天及跨重启聊天历史尚未实现 |
| 助手主体 | 真实模型规划、工具目录选择、工作流、持久检查点与只读调度；本轮补齐追问上下文和长期停机调度 | 外部动作 UNKNOWN 对账与显式继续已完成本地实现，等待正式验收/部署；普通继续仍禁止绕过未知结果 |
| 本地资料 | 知识库授权目录只读索引；简历插件从授权目录导入文件；开发插件可写自己的候选工作区 | FILES.1已验收安装，沿用知识库根共享读写；支持逐层子目录，统一工作区为下一项，之后补工作区备份 |
| 邮件与ehall | 邮件读写和受监督浏览器已接线，外部动作经过精确审批；真实只读收件与部分ehall试填有历史证据 | 当前版本受控真实发件、正式ehall完整材料与提交回执仍待验；本机直接交互不替代受监督验收 |
| 招聘与编码插件 | 独立招聘插件本地实现；开发插件已安装原实例并通过真实DeepSeek计算器候选试写 | BOSS实站/招聘插件原实例安装待验；计算器候选本身尚未安装 |
| 通知与移动端 | 无Google设备已通过ntfy订阅、锁屏接收、撤销恢复与点击；手机草稿回执、审批及缓存已补证 | F08.6仍因独立完整门禁中的F07创建超时记录保持BLOCKED；不据本轮小修宣告阶段完成 |
| 凭据与备份 | Windows Credential Manager已实现；数据库允许表和登记制品支持加密备份/隔离恢复 | 外部资料原件、插件开发工作区和独立CLI SQLite不在此备份包中；完整迁机需另行准备 |

这张表描述代码能力及已有证据，不代表原实例此刻正在运行或已部署本轮改动。TUI不自动启动、解冻或清空旧队列。DeepSeek真实试写记录见[说明](docs/EXT_DEV2_REAL_MODEL_TRIAL_2026-10-30.md)；本轮修补与下一批事项见[项目细节清单](docs/PROJECT_POLISH_2026-10-30.md)。本轮目标验证不替代完整发布门禁。

## 2026-11-06 队列与列表面板修复

本机TUI任务页新增持久排序与取消/移除入口；模型规划和材料预览期间也能响应取消，失败启动不会拖住后续作业。任务/扩展列表区域随终端大小扩展，刷新保留滚动位置；未决外部动作仍须对账，不删除历史。代码更新后需重启后台和TUI；本轮没有操作原实例。详见[工作台指南](docs/TUI_WORKBENCH.md)。

## 2026-11-06 真测交互修复

普通聊天和Agent模型处理可按本人配置采用 automatic 模式，不再逐次核对模型披露；仍记录实际字段/接收方绑定，SECRET及最终外部动作审批不变。TUI问答已支持DeepSeek/OpenAI兼容真实流式、原请求断流核对及实际Token统计；Agent结构化规划和手机消息尚未改为逐Token流。DISCOVERED明确表示未安装，界面提供安装引导，后台立即拒绝无效启用。当前原profile已按本人授权配置automatic，需要重启后台/TUI生效；未解冻、未安装新扩展。详见[工作台使用说明](docs/TUI_WORKBENCH.md)。

## 2026-10-30 本机 TUI 工作台

新增可选 Textual 工作台：带上下文的大模型问答、对话转Agent任务、已启用插件工具自动选择与执行状态、真实Token统计、扩展手工管理和模型参数设置。启动：

~~~powershell
.\scripts\assistant-tui.ps1 -Profile var/deployment/ext-dev2-2026-10-30/host.json
~~~
前提是对应持久实例的Local Admin已运行；入口不自动启动/解冻后台。手机当前支持同实例的任务式Agent交互，纯问答界面本轮只加入TUI。详见[使用指南](docs/TUI_WORKBENCH.md)和[交付记录](docs/TUI_IMPLEMENTATION_2026-10-30.md)。

## 2026-10-30 受监督插件开发 Agent

新增独立的 [assistant.plugin_developer](extensions/plugin_developer/README.md)：Agent 可在专用工作区创建和修改插件候选，查看差异、执行本人授权的本地测试、根据失败修复，再交接精确材料给本机安装确认。9 个标准工具，无宿主核心改动；安装/升级/启用和回滚复用现有 CLI。使用方式见[安装及配置](extensions/plugin_developer/INSTALL.md)。后续已在原实例安装启用，使用环境DeepSeek密钥完成受监督计算器试写，候选14项测试通过；计算器尚未安装，后台恢复冻结、未留常驻服务。见[真实试写记录](docs/EXT_DEV2_REAL_MODEL_TRIAL_2026-10-30.md)。证据和范围见[交付报告](extensions/plugin_developer/DELIVERY.md)。

## 2026-10-30 招聘插件及真实环境试用

独立招聘插件及通用审批接线的本地实现已收尾，尚未安装/部署到原实例，0.2.0已支持授权目录简历直接导入，BOSS实站兼容仍待验证。见[交付总结](extensions/career_apply/DELIVERY.md)、[安装说明](extensions/career_apply/INSTALL.md)和[全系统真实环境手工测试手册](docs/MANUAL_REAL_ENV_TEST_GUIDE_2026-10-30.md)。F08.6仍BLOCKED；下方为各日期的历史范围。

## 2026-10-13 开发交付

新增持续任务执行、声明式组合工作流、模型披露审核、规则候选审核、本机管理页、Windows主机控制、加密备份/隔离恢复及离线升级入口。用户已配置 DeepSeek `deepseek-flash` 并完成单次公开自检；真实任务编排和外部动作的独立验收仍待进行。启动方式见 [本地运行手册](docs/RUNBOOK_LOCAL_BETA.md)，本轮实际门禁和边界见 [集中交付报告](docs/DEVELOPMENT_DELIVERY_2026-10-13.md)。开发主机18100与原线上8000独立，不在本轮自行部署。

## 基架能力与阶段记录（历史快照，当前状态以上表为准）

2026-10-10：按用户无 Google Play 的固定约束，F08.6 新增默认关闭的本机 ntfy 通知适配器及最小接线。当前仅本地测试，真实通知服务与手机未接入；F08.6 仍 BLOCKED。范围与实际证据见 [主机接线报告](docs/F08_6_NTFY_LOCAL_REPORT_2026-10-10.md)。

- 可启动的用户 API、本机管理 API、独立 health-only API。
- 持久 Agent 状态机、工具风险门、精确审批、上下文与模型披露接口的基础实现。
- 数据库队列与 PostgreSQL Schema；开发环境提供内存适配器。
- 扩展 Manifest、Registry、生命周期/RPC 契约和 `example_echo` 示例扩展。
- Extension Supervisor：受控暂存与静态检查、精确确认屏障、每版本独立 venv、真实 Worker 子进程、原子 Registry Snapshot、enable/disable/upgrade/rollback/uninstall 与启动恢复（venv/Worker 只做依赖与崩溃隔离，不是恶意代码沙箱）。
- Cloudflare Access 边界：公共 API 在 `PA_TRUST_CLOUDFLARE_ACCESS=true` 时验证 Access JWT（`RS256`、`kid`、issuer、audience、expiry）后建立身份，JWKS 有界缓存与受控轮换，失败 fail closed（F03 DONE，三轮独立验收通过）。
- 模型适配器与披露许可（F04 DONE）：真实远端 OpenAI 兼容适配器与本地 Ollama 适配器（协议驱动 HTTP，无厂商 SDK，错误类型化、无静默重试、禁重定向、有界响应、总 deadline），持久化 `model_disclosure_consents` 许可（精确 provider/用途/字段摘要与不可复用接收端指纹绑定、到期/撤销、幂等重放）与 public API 的 preview/confirm/revoke 接口；SECRET 在所有模型路径硬阻断。
- 个人知识扩展（F05 DONE，独立审计与修复后通过）：`extensions/personal_knowledge` 注册 `knowledge.file_changes`、`knowledge.retrieve`、`knowledge.search`(READ)、`knowledge.reindex`(INTERNAL_WRITE)、`knowledge.reconcile`、`knowledge.roots` 与 `knowledge.index_schema`，实现授权目录扫描、Markdown/TXT/PDF 抽取与稳定 locator、owner/心跳租约绑定的 SHA-256 版本化分块与严格来源快照 CAS、扩展自有 `ext_*` Schema、embedding identity 隔离的 PostgreSQL FTS + pgvector 混合（RRF）检索、可验证引用与删除传播。扩展不接触数据库凭据：语句经通用宿主数据能力（`host.data.execute/transaction/migrate`）在扩展 Schema 内执行；配置经通用 `GET/PUT /admin/v1/extensions/{extension_id}/config` 通道注入并在启动时按当前版本 Schema 复核。默认 `embedding.provider=none` 时显式降级为仅 FTS。
- smail 邮件扩展（F06 DONE，六轮独立审计及补充复验通过）：`extensions/nju_smail` 提供只读 IMAP 轮询（`smail.poll_inbox`、`smail.sync`）、线程上下文（`smail.thread_history`）、检索（`smail.search` READ）、版本化草稿（`smail.prepare_reply` INTERNAL_WRITE）与受控发送（`smail.send` EXTERNAL_WRITE）；通用宿主能力 `core/mail` + `infrastructure/mail`（真实 TLS IMAP 只读客户端、显式阶段 SMTP 客户端、唯一连接建立者 `MailTransportBroker`、审批快照校验执行器 `MailSendExecutor`）与 `host.artifact.*` 制品能力。消息去重以 `(账户, 文件夹, UIDVALIDITY, UID)` 为主、`Message-ID + 规范化内容哈希` 为辅；草稿每次编辑产生新版本与新的稳定动作 ID/Message-ID；发送必须经过 Tool Gateway、R2 审批与 SideEffect Outbox，实际发送的 MIME 字节/收件人/附件哈希与审批快照一致；部分拒收保存逐收件人明细；UNKNOWN 不自动重发并由 Sent 只读对账。客户端专用密码只经 `SecretHandle` 由宿主解析，扩展不建立 IMAP/SMTP 连接；读取与发送能力独立配置（`PA_MAIL_SEND_ENABLED` + `PA_MAIL_TEST_RECIPIENTS` 受控地址 allowlist）；宿主账户注册表持有端点与凭据句柄，扩展只提交 `account_id` 与宿主计算的账户 fingerprint；注册表 generation 删除后不复用，派发期间账户变更会经可失效 guard 在连接/认证/DATA 前中止。发送结果只由宿主权威账本决定，扩展没有写入工具（`smail.send_status` 仅把宿主状态投影到本地视图）。真实 smail 账号 E2E 未执行：缺少用户账号、凭据与受控地址，当前证据为协议级模拟服务器 + 真实 TLS/socket + 真实 PostgreSQL + 真实 Worker/组合根测试。
- ehall 受监督办事基架（F07 IN_PROGRESS，等待完整真实验收与独立复审）：核心 `BrowserSessionBroker`（会话状态机、origin/路径策略、永久 R3 分类、页面 fingerprint（含表单 action/method）、canonical 结构化预览（含提交目标绑定）、宿主驱动只读对账、过期会话释放）与 `infrastructure/browser` 的 Desktop Companion（唯一导入 Playwright 的 `driver.py`，强制 `headless=False`，256 bit 会话 capability、仅内存、TTL、可撤销且不经异常链泄漏；默认允许规范 HTTPS origin，可由用户在本机启动时切换为精确 origin 白名单模式；`fill` 后持久阻断全部写请求，提交临界区只放行一条精确匹配 method/origin/path 的写请求，延迟 autosave/二次 POST/动态改写 action 均被 abort 并计数；正文扫描不完整即安全暂停）以及宿主 `BrowserActionExecutor`（`browser.fill`/`browser.submit` 只能经 Tool Gateway + R2 审批 + Outbox；结果不明为 `UNKNOWN`，不自动重试，由宿主 `host.browser.reconcile` 只读对账）。扩展 `extensions/nju_ehall` 提供只读发现/检视/预览、声明式“进入事项”导航与受控填写，R3（退课/撤回/支付/选课变更/法律声明/未知事务/未知页面版本）在策略层永久阻断；适配器必须携带人工核验的页面指纹集合与提交目标，并按事务拆分（`proof.json` 在读证明 + `transcript.json` 成绩单打印）。宿主执行工具 `ehall.open_transaction`（INTERNAL_WRITE）只点击适配器声明、绑定 `transaction_id`/落地路径的导航动作：实时页面必须存在同 label 动作，落地路径与事务指纹双重校验，漂移即 `SAFETY_PAUSED`；提交仍必须 R2 审批。真实 ehall 验收仍在进行：受监督链路已完成真实门户/SSO 只读采集；本机 `assistantctl ehall` 已由用户反馈成功打开成绩查询，并对教室借用真实表单部分试填及程序化选择一个下拉选项。完整填写至权威预览、真实适配器指纹和提交回执尚未验收；本机入口不替代受监督链路。
- CLI、单元/契约测试以及面向后续模型的任务清单。
- 响应式 PWA 的 F08.1–F08.5 已通过各自约定范围的独立验收：通过公共 API 与 SSE 显示任务/连接状态、发送版本化消息、编辑 Schema 草稿、核对并批准已有的权威邮件审批，以及管理 Web Push 订阅与最小元数据通知；F07 试填记录仍不可手机批准。F08.6 保持BLOCKED，是最小F09.1/F06.P2 DONE后的唯一后续验收项；此前经用户明确授权，原库迁移与助手/health 后台启动已完成，固定域名 Access + Tunnel 已接通，本机与无认证边缘黑盒通过。私有 Tailscale Serve 只代理 health 8010，`AllowFunnel=false`，同节点 HTTPS 黑盒通过。Android首次登录/SSE、私有健康三项已有本人反馈；随后本人确认Chrome标识的桌面入口无地址栏启动、空任务列表/事件连接正常，Wi-Fi切至移动数据后恢复连接。联网时切后台或锁屏约一分钟后返回，列表与连接自动恢复，也已获本人确认；后台与锁屏来自合并提问，未分别验证两个场景。本人又确认断网时出现“设备离线”或“事件连接中断”的提示，恢复联网后自动连接。手机本地自检任务及第一条消息创建成功，刷新后各一条，已获本人确认；后续新增操作之前的原库只读聚合快照确认匹配任务1条、消息1条。本人还确认电脑以同身份PWA新增任务及新增消息均自动同步至手机。手机任务/消息使用后，CacheStorage四条固定静态路径已有截图，唯一节点及名称 `assistant-static-v12` 已有本人文本反馈；任务GET的200、`Cache-Control: no-store`、`Cf-Cache-Status: DYNAMIC` 已有手机Network截图，检查时Disable cache未勾选。此确认限于上述CacheStorage及任务GET响应头；HTTP缓存底层、LocalStorage/IndexedDB、未访问的真机Schema草稿/审批、其余私有边界及真实Push仍待验收，F08.6保持 `BLOCKED`。F09.1 最小凭据后端已独立验收DONE，当前恢复的Windows公共进程已接线Credential Manager及VAPID配置，真实Push尚未验收；F07 与 F08 整体保持 `IN_PROGRESS`。Service Worker 只缓存固定静态资源。`assistantctl extension scaffold` 提供扩展生成器。

2026-09-30 用户调整实施顺序：F07 仍为 `IN_PROGRESS` 时，允许先完成不依赖其剩余真实站点工作的 F08 子任务；F08.1–F08.5 已按各自范围验收，F08.6 的完整真机与网络验收仍在补齐证据。F07 的一次登录自动路由与七字段试填烟测已通过，未点击最终提交；正式真实事务 Adapter、绑定实际提交目标及完整材料/后果的受监督权威预览、正式 Gateway/Outbox 实站链路仍未验收。真实完整填报因暂无确有需要的事务延期，不制造申请。F08 当前阻塞项与本轮测试证据见 [NEXT_STEPS](./docs/NEXT_STEPS.md)。

F08.6 的非部署准备工具 `scripts/prepare_f08.py` 可生成不会覆盖已有文件的候选模板，并检查候选配置；它不启动应用、连接数据库或发布外网入口。用户已取得 `yanshuheng.dpdns.org`、提供 Access 团队域和 AUD；未登录请求被边缘重定向至 Access，用户截图及确认表明应用仅有一条单邮箱 Allow 策略。经用户授权，原库口令已轮换并只保存到受限、Git 忽略的本机配置；历史默认连接串现已失效，正式 Secret Reference 方案仍待 F09。当前工作区发布前全量及 PostgreSQL/Worker 门禁通过后，用户明确授权启动与接通入口；原库 0008–0011 迁移完成，11 份迁移校验和一致。核对唯一临时占位 DNS 的截图并取得精确覆盖许可后，Tunnel 已连接，只代理回环 8000；助手与 health 后台临时运行，Admin 未启动。手机入口是 `https://yanshuheng.dpdns.org/ui/`，Android 浏览器首次登录及 SSE 已由本人反馈和截图确认；Chrome标识桌面入口无地址栏启动及空列表/连接正常也已确认，最新网络与前台恢复反馈见下段。Windows/Android Tailscale 节点在线，私有 Serve 已启用且只代理 8010；同节点 HTTPS 的 `/healthz` 最小 200 与七条禁止路径 404 已验证，本人随后从 Android 确认 health 正常、任务与管理路径均 Not Found；其余跨设备路径仍待补齐。手机此前的 Access 页只提供 Cloudflare 账号登录且拒绝该身份；团队 One-time PIN 已添加，应用仅选该方式的截图及手机首次连通已确认，完整真机验收仍待补齐。操作步骤见 [环境准备指南](./docs/F08_ENVIRONMENT_PREPARATION.md)。

2026-10-05 手机反馈 Chrome 只能创建快捷方式后，F08.6 补齐 Manifest 的 192/512 PNG 图标、携带凭据的 Manifest 链接与 wheel 静态材料，并把固定四路径缓存升为 v12。API/契约定向78 passed，390px浏览器17 passed，真实v11缓存升级及图标解码通过。随后本人确认桌面Chrome标识入口能无地址栏启动、空任务列表/事件连接正常及Wi-Fi切到移动数据后恢复。本人对“联网切后台或锁屏约一分钟后返回，列表与连接是否自动恢复”的合并问题答是，未分别验证后台与锁屏两个场景；本人又确认断网时显示“设备离线”或“事件连接中断”，恢复联网后自动连接。手机创建本地自检任务及第一条消息后刷新仍各一条，已获本人确认；一次原库只读事务按精确自检文字聚合得到任务1条、消息1条，这是后续新增操作之前的快照。随后本人反馈“我试了新增任务和新增消息，都可以”，确认电脑同身份PWA的两类新增操作自动同步至手机。使用上述任务/消息后，手机远程DevTools截图显示所选CacheStorage只有四条固定静态路径，本人文本确认助手origin只有一个节点、名称为 `assistant-static-v12`；手机Network截图又确认GET `/api/v1/tasks?limit=20` 返回200 OK、`Cache-Control: no-store` 与 `Cf-Cache-Status: DYNAMIC`，Disable cache未勾选。这些截图、手机人工反馈与本地测试、原库只读快照分别记录；系统包装类型不另设为验收门槛。本次缓存确认仅覆盖上述CacheStorage和任务GET响应头，HTTP缓存底层、LocalStorage/IndexedDB、未访问的真机Schema草稿/审批、其余私有边界及真实Push仍待验收。F08.6仍为 `BLOCKED`，F07/F08整体为 `IN_PROGRESS`。当前完整命令和未完成项见 `docs/NEXT_STEPS.md`。

扩展安装与生命周期只经本机 Admin API（`127.0.0.1:8001`），例如：

```powershell
.venv-win\Scripts\assistantctl.exe extension inspect extensions\example_echo
.venv-win\Scripts\assistantctl.exe extension install extensions\example_echo
```

扩展的非秘密配置经同一 Admin API 的通用通道设置（例如个人知识扩展的授权目录），保存只更新配置草稿；在本机 TUI 的扩展页点“应用配置”，才会同步到运行中的各个 Worker。请核对“已保存、待生效、已生效”和索引状态，保存不代表目录已经切换或完成索引：

```powershell
$body = '{"config":{"roots":[{"path":"C:/Users/me/Documents/notes","key":"notes"}],"embedding":{"provider":"none"}}}'
Invoke-RestMethod -Method Put -Uri http://127.0.0.1:8001/admin/v1/extensions/personal.knowledge/config `
  -Headers @{ "Idempotency-Key" = "knowledge-config-1" } -ContentType "application/json" -Body $body
```

个人知识扩展的索引数据位于 PostgreSQL `ext_personal_2e_knowledge` Schema，不写入核心表；源目录始终只读。

Cloudflare Access JWT 验证已实现并通过三轮独立验收（F03 DONE）；F08.6 已经用户授权手动临时接通 Tunnel 与私有 health Serve，服务守护、开机自启及完整部署编排仍待 F09/F10。真实 ehall 方面，**门户/SSO 只读采集已运行，真实教室借用已部分试填，完整填写至权威预览尚未验收；真实适配器指纹和提交回执未验收**。Windows Credential Manager 的最小 F09.1 后端及相邻 F06.P2 修复已于 2026-10-09 通过独立验收，均为 DONE；本日恢复的 Windows PostgreSQL 公共进程已加载该后端并配置 VAPID 公钥/句柄，手机真实 Push 尚未开始。Web Push 的 F08.5 本地范围已通过独立复验，F08.6 已运行本机、无认证公网边缘及同节点私有 HTTPS 检查，真实 Android/Push 服务验收尚未完成，不能用模拟结果替代真实投递。smail 已完成一次**受控真实只读同步**（2026-09-19）：经本机回环页面录入客户端专用密码（仅进程内存），真实 `imap.exmail.qq.com:993` 只读同步入库 15 封邮件/15 个事件（INBOX 13 + Sent Messages 2），第二次同步 0 新、0 发送动作；真实 SMTP 发送及实际邮件凭据配置仍未完成。F07 的受监督浏览器基架与 `nju.ehall` 扩展已实现；审计前快照的自动化套件曾全绿，随后独立复验有 1 项锁等待计时失败（单项复跑通过），当前快照的完整结果见 `docs/NEXT_STEPS.md`；真实门户/SSO 只读采集已由用户亲自完成（证据 `docs/evidence/real_ehall_2026-09-21.md`），真实完整填写、权威预览和提交回执仍需用户实时参与，F07 保持 `IN_PROGRESS`。当前恢复的 Windows PostgreSQL 组合根使用 Credential Manager，VAPID 私钥只存该后端；远程模型与实际邮件凭据尚未配置，原库 DSN 仍沿用已授权的临时本机文件例外；非 Windows 平台继续使用拒绝操作的 `UnavailableSecretStore`，不会静默使用内存明文；协议行为只能通过注入 HTTP transport 或模拟服务测试，尚未完成真实厂商/邮箱/Push 服务端到端验证。

早期基架曾只入队而未启用Agent Worker；当前已实现持久Worker与真实模型工具调用。是否执行由实例的Worker、冻结状态、模型配置和披露许可共同决定。开发内存容器不跨进程共享任务，手机/桌面共用须连接同一个PostgreSQL持久实例。

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

生产环境必须设置 `PA_ENVIRONMENT=production`、`PA_STORAGE_BACKEND=postgres`，并配置已实现的 PostgreSQL 适配器；启动检查会拒绝生产环境使用内存存储。

远程访问必须设置 `PA_TRUST_CLOUDFLARE_ACCESS=true`，并提供 `PA_CF_ACCESS_TEAM_DOMAIN`、`PA_CF_ACCESS_AUD` 和 `PA_PUBLIC_ORIGIN`。team domain 只接受 `<team>`、`<team>.cloudflareaccess.com` 或 `https://<team>.cloudflareaccess.com`；签名密钥只从 `https://<team>.cloudflareaccess.com/cdn-cgi/access/certs` 获取。配置缺失或非法时启动失败，不会降级为无认证模式。

模型端点必须显式配置，半配置会拒绝启动：

- 远端：`PA_MODEL_REMOTE_BASE_URL`（必须 https）+ `PA_MODEL_REMOTE_MODEL` + `PA_MODEL_REMOTE_SECRET_HANDLE`（仅句柄 ID，不是密钥原文）。
- 本地：`PA_MODEL_LOCAL_BASE_URL`（必须 loopback）+ `PA_MODEL_LOCAL_MODEL`。
- 显式本地回退：`PA_MODEL_LOCAL_FALLBACK_PROVIDER_ID`，必须等于已配置的本地 provider ID。

敏感/个人字段发往远端模型前必须存在持久的、精确绑定 provider、用途和字段摘要的披露许可；`SECRET` 在本地、远端和回退路径全部拒绝。Windows Credential Manager已实现；远程凭据按SecretHandle解析。其他平台未提供受支持后端时仍拒绝解析，不降级为内存明文。

smail 邮件扩展的读取与发送独立配置：`PA_MAIL_SEND_ENABLED=true` 时才开放 `mail.send` 能力，且必须提供 `PA_MAIL_TEST_RECIPIENTS`（逗号分隔的受控测试地址，未登记的收件人在建立连接前被拒绝）；`PA_MAIL_MAX_MESSAGE_BYTES` 限制单封大小。账户端点、端口与 `SecretHandle` 只保存在宿主所有的邮件账户注册表（Local Admin `GET/PUT /admin/v1/mail/accounts`，仅回环）；扩展配置只能引用 `account_id`，扩展无法提交服务器地址或凭据句柄，客户端专用密码进入宿主凭据后端，绝不写入配置文件或数据库。

### ehall 受监督浏览器（F07，IN_PROGRESS）

`assistantctl ehall` 是本机**手工验收入口**，用于验证事务页加载、观察和试填；它不监听邮件或任务消息，也没有接入 LLM。命令启动正常联网的可见 Chromium，不拦截页面在导航、观察、填写期间自行发起的 POST，也不要求 origin 白名单、页面指纹或预先登记事务。用户仍需自行完成统一认证。普通模式准备点击用户指定的最终提交按钮时会打开独立的本机点击前确认页，列出本次脚本记录的已填字段和指定按钮；用户点击“确认提交到 ehall”后才由脚本点击该按钮，点击“取消”则不由脚本点击。该页不展示服务器实际完整 payload、材料及后果，不保证确认前没有服务器写入，也不判断其他页面动作是否完成正式办理。`--sample` 不进入最终提交分支。

```powershell
.venv-win\Scripts\python.exe -m personal_assistant.cli.main ehall "打开 成绩查询"
```

2026-09-27 用户实测：完成 SSO 后，“成绩查询”事务在新标签页正常显示，没有“网络错误”；真实教室借用已部分试填，至少一个自定义下拉选项可程序化选中。完整填写至权威预览尚未验收，真实适配器指纹和提交回执也未验收；F07 保持 `IN_PROGRESS`。

2026-09-28 第二课堂“志愿服务经历（申请）”真实受监督试填：用户亲自登录和进入表单后，验收脚本接管嵌套 frame，经 `BrowserSessionBroker` 填写七项合成测试值；用户确认页面七项正常且无报错，Broker 返回 `PREVIEW_READY`，没有点击最终“提交”。这份试验性预览尚未绑定真实提交目标或核实完整材料和后果，也未进入正式 `nju_ehall` Adapter 与 Tool Gateway/Outbox 路径；F07 仍为 `IN_PROGRESS`。脱敏证据与复现命令见 [真实试填记录](./docs/evidence/real_ehall_volunteer_supervised_2026-09-28.md)。

要在有表单的事务中试填少量固定值，可运行：

```powershell
.venv-win\Scripts\python.exe -m personal_assistant.cli.main ehall --sample "打开 教室借用 > 临时借用（仅借1天）"
```

第二课堂成绩单的在线办理页由“第二课堂”入口自动打开；要在表格中定位“志愿服务经历”所在行的“申请”，并只采集表单结构，可运行：

```powershell
.venv-win\Scripts\python.exe -m personal_assistant.cli.main ehall --capture-out "$env:TEMP\ehall_volunteer_capture.json" "打开 第二课堂 > 志愿服务经历（申请）"
```

括号中的“申请”表示在同名项目所在行点击该按钮；若匹配多行则停止。`--capture-out` 不填值也不点击最终提交，保存页面和 iframe 的结构指纹、字段形状及动作标签，省略当前字段值、选项文字和 URL 动态查询参数。该文件是本机交互入口的观察记录，仍需在受监督链路中核验页面版本、完整材料和权威提交前预览。

指令中的 `>` 表示依次点击：先在大厅点“教室借用”，再在进入的页面点“临时借用（仅借1天）”，最后才读取表单。`--sample` 等待异步表单加载后打印当前可见字段候选数、可直接填写数、字段名称和类型；只读控件、ARIA 自定义控件及仅有标签的字段也会列出。在页面手动选择前置字段后，终端输入 `r` 会重扫字段和下拉选项。试填只作用于空白且标签明确的控件：事由/用途/说明/备注/主题填“测试填写（未提交）”，人数字段填 `10`，教室容量填 `30`，教室借用数量填 `1`；这些数字控件即使是 HTML `text` 类型也能识别。已有值、身份与联系方式、日期、下拉/单选/复选/附件和无法唯一匹配的控件保持原状；自定义下拉会列出可见选项，但没有指定时 `--sample` 不替用户选择。可以在 `--sample` 指令后加 `；校区=页面实际选项文字` 等字段，让脚本试填下拉或其他字段，同时仍停在真实页面且绝不点击最终提交按钮；多个显式字段按指令中出现的顺序填写，可先填前置字段再填依赖字段。字段数和下拉选项是当时可见页面的快照，真实站点的自定义控件仍需逐项核对；后续 LLM 可以决定字段含义、值和填写顺序，页面观察与控件操作由浏览器层完成。

指令格式为 `办理 事务名；填写 字段名=值；字段名=值`，最后一项 `提交按钮=页面上的按钮文字` 可省略，默认“提交”。也可以不传指令，按终端提示输入；只输入事务名时，打开页面后会列出可填写字段供继续输入。浏览器中的表单值仅供本次操作，不写入报告。当前自动化回归使用本地模拟站点，真实 ehall 事务仍需在登录后逐项验证；页面使用非标准控件或多步提交时，命令会停在无法匹配的步骤并给出字段或按钮错误，不会声称提交成功。

登录后若大厅首页没有该事务，命令会点击“全部服务”并尝试站内搜索；查找期间每 10 秒在终端显示进度。也可在可见浏览器中手动展开分类，命令会继续在已打开的标签页和页面框架中查找该事务卡片。旧 `scripts/ehall_real_acceptance.py capture` 仍会拦截非 GET 请求，不适合验证事务页能否正常加载。

最终目标是文档中的综合办事流：smail 邮件事件和 Android PWA 任务消息进入持久任务，模型提议事项与字段值，Agent 通过注册工具打开事务并填表，在 PWA 等待用户确认。邮件正文只作为任务证据。按开发路线，F07/阶段 8 先验收真实浏览器与一个低风险事务；PWA 确认界面属于 F08/阶段 9；邮件、模型与 ehall 的自动组合属于阶段 10。当前不把后两项计入 F07 的真实站点验收。

下面的 Desktop Companion 和 `scripts/ehall_real_acceptance.py` 是原有受监督验收链路，其读写拦截与适配器约束仍适用于该链路；直接办理命令使用上述独立交互入口。

受监督链路的桌面交互发生在当前登录用户的 Desktop Companion 进程里。使用该链路时，安装可选浏览器依赖并启动 Companion：

```powershell
.venv-win\Scripts\python.exe -m pip install "playwright>=1.62,<2"   # 可选 extra：browser
.venv-win\Scripts\python.exe -m playwright install chromium          # 已缓存时跳过
# 在用户桌面会话中启动；stdout 第一行是含一次性 capability 的握手 JSON，只能交给宿主启动器，不要写日志
.venv-win\Scripts\python.exe -m personal_assistant.infrastructure.browser.companion_entry
```

宿主配置（半配置 fail closed）：

- `PA_BROWSER_COMPANION_URL`：必须为 `http://127.0.0.1:<port>`；设置时必须同时提供进程内存环境变量 `PA_BROWSER_COMPANION_CAPABILITY`（来自 Companion 握手）。
- `PA_BROWSER_ALLOWED_ORIGINS`：逗号分隔的规范化 HTTPS origin 白名单；仅在 `PA_BROWSER_ORIGIN_MODE=allowlist` 时用于拦截。白名单模式下默认空会拒绝打开会话，扩展 adapter 的 origin 必须是它的子集，真实 SSO 的认证 origin 也需加入。
- `PA_BROWSER_ORIGIN_MODE`：受监督 Companion 启动默认 `open`，允许任意规范 HTTPS origin，不校验网页 origin 白名单。手动设为 `allowlist` 可恢复精确白名单；URL 结构校验、写请求阻断、R3 与 R2 审批仍执行。此 `open` 只放宽 origin 校验，不是上述 `assistantctl ehall` 本机直接交互入口。切换需重启宿主/Companion，新会话才使用新模式；真实采集脚本默认为 `open`，可用 `--origin-mode allowlist` 切换。
- `PA_BROWSER_SUBMIT_ENABLED`：默认 `false`。即使开启，最终提交仍必须经 Tool Gateway + 一次性 R2 审批；结果不明为 `UNKNOWN`，只允许只读流程跟踪对账。

受监督 Companion 浏览器安全边界：headed、仅回环、可手动切换的 HTTPS origin 模式（默认 `open`；`allowlist` 精确白名单）、不允许 `javascript:`/`data:`/`file:`、不导出 cookie/storage state、不截图、不使用 `page.evaluate`；默认阻断全部非 GET（唯一例外是用户亲自完成 SSO 的登录挑战窗口，且只放行与冻结的真实登录表单 action 精确匹配的一次主框架提交）；`fill` 后即使再遇登录页也不再放行，提交临界区只放行一条绑定 method/origin/path **且无 query** 的写请求，并在浏览器进程内校验真实 `request.post_data` 的 canonical 哈希（获批字段必须等于审批 payload，完整 body 必须等于点击时模板，multipart fail closed；正文与隐藏/密码控件值都只留在浏览器进程内，隐藏输入以 name/type 进入页面指纹而值不导出），成功必须以该写请求确实放行为证据（仅 DOM 文本变化一律 UNKNOWN）；交易页版本必须命中适配器人工核验的 fingerprint 集合，否则 `UNKNOWN_PAGE_VERSION`/R3 安全暂停；正文读取失败或截断即 `PAGE_TEXT_SCAN_INCOMPLETE` 安全暂停；对账基线经只读临时标签页从真实跟踪页采集并只存引用哈希，基线与对账都要求最终 URL 精确等于声明的 tracking 目标（origin/path#hash 全等且不允许附加查询参数，重定向一律 `RECONCILE_UNSAFE`），基线缺失/截断时对账保持 UNKNOWN（`RECONCILE_UNSAFE`）；UNKNOWN 为未决状态，TTL 不自动释放并占用任务槽位。Cookie、密码、验证码、二维码与 storage state 不进入扩展、RPC、模型、数据库或日志。

另外两个安全边界必须单独启动：

```powershell
.venv-win\Scripts\python.exe -m uvicorn personal_assistant.admin_app:app --host 127.0.0.1 --port 8001
.venv-win\Scripts\python.exe -m uvicorn personal_assistant.health_app:app --host 127.0.0.1 --port 8010
```

Tailscale 只能映射 `8010`。Cloudflare Tunnel 只能映射用户 API `8000`，不得映射 `8001`。

## 验证

日常默认只运行关键回归及静态检查；完整与集成测试显式选择。删除清单、覆盖边界及精确命令见[测试指南](docs/TESTING.md)。

```powershell
./scripts/test.ps1                          # 日常关键回归
./scripts/test.ps1 -TestPath tests/unit/test_extension_rpc_robustness.py
./scripts/test.ps1 -Suite integration       # 按需集成
./scripts/test.ps1 -Suite full              # 阶段验收/发布，含两个独立插件
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

1. 受监督宿主工具调用必须经过统一 Tool Gateway；R3 永远拒绝，R2 只执行用户确认过的精确 payload。本机 `assistantctl ehall` 的页面直接交互按上文单独契约运行：仅在点击用户指定的最终提交按钮前本机确认，不保证确认前没有服务器写入。
2. 结果为 `UNKNOWN` 的外部副作用不得自动重试，必须进入对账状态。
3. 新业务通过 SDK 注册扩展槽；不得修改核心路由、核心表或写 `if extension_id == ...`。


## Windows 安全凭据（F09.1）

Windows 的 PostgreSQL 组合根现在按平台使用 Credential Manager；非 Windows 保持拒绝操作，development/test 的内存容器仍是测试替身。部署为不同 Windows 用户或服务账号时，须由该身份重新配置凭据。

在本机交互终端保存单行凭据：

```powershell
.\.venv-win\Scripts\assistantctl.exe secret put --name "模型测试账号" --kind model_api_key
```

命令隐藏输入，只输出 `id`、`kind` 与 `secret://...` 引用。模型配置 `PA_MODEL_REMOTE_SECRET_HANDLE`、Push 配置 `PA_PUSH_VAPID_SECRET_HANDLE` 只填写返回的 `wcm1_<32位小写十六进制>` ID，禁止填写密钥原文或完整 URI。用途类别分别为 `model_api_key`、`push_vapid_private`；邮件由宿主账号注册表引用 `mail_client_password`。同名再次保存生成新句柄，不自动轮换引用或覆盖旧值。

删除一个引用：

```powershell
.\.venv-win\Scripts\assistantctl.exe secret delete --id "wcm1_<返回的32位十六进制>" --kind model_api_key
```

缺失的同格式引用可重复删除；类型或命名空间不匹配不能读写其他记录。无读取/导出命令，无 `--value`、文件或管道输入；隐藏终端不可用时拒绝。宿主端口可保存 Unicode/多行文本，UTF-8 上限 2560 字节；CLI 提示仅适用于单行值。VAPID 宿主期望 32 字节标量的未填充 base64url；本轮只用生成的合成密钥验证解析，尚未配置真实 Push。

最小F09.1排期例外已独立验收通过（DONE），原TLS取消及STARTTLS准备失败连接收尾F06.P2也已DONE。实现及历史门禁见 [F09.1报告](docs/F09_1_IMPLEMENTATION_REPORT_2026-10-08.md)，最新验收见 [F06报告第10节](docs/F06_TLS_CANCELLATION_REPAIR_2026-10-09.md)。F08.6继续BLOCKED，F07/F08整体IN_PROGRESS；回到真机/真实Push等缺口，不启动其他F09。此次仅记录验收，无源码/服务/部署或新提交推送操作。

### F06 SMTP握手取消修复（2026-10-09，以下两段为实施历史）

用户要求先提交推送，快照 `9891102` 已推送 `origin/main`，随后才修复F06。隐式TLS和STARTTLS现在先登记实际TLS socket再握手，失败/取消关闭连接并等待线程结束。定向86 passed；完整门禁1298 passed、130 skipped、11 warnings，Ruff/Mypy232通过；PostgreSQL/真实Worker130 passed、1 warning。未连接真实邮箱、未部署或再次提交推送。F06.P2待独立复验，F09.1/F08.6仍BLOCKED；详见 [修复报告](docs/F06_TLS_CANCELLATION_REPAIR_2026-10-09.md)。

2026-10-09 独立复验确认原TLS取消已修好；新检出STARTTLS缺能力或454拒绝仍遗留TCP。已按局部范围补统一准备异常清理，新增缺能力/454/EHLO拒绝3 failed→3 passed，相邻回归**89 passed，134.30秒**。本轮未重跑全量/PostgreSQL，以上完整门禁为补修前历史结果；F06.P2仍待独立复验，F09.1/F08.6仍BLOCKED，未再次提交/推送/部署。当前证据见修复报告第8/9节。

### 最新独立验收（2026-10-09）

F06.P2与最小授权F09.1均DONE。用户独立邮件回归89 passed及额外10项通过，全量1301 passed/130 skipped/9 warnings，PostgreSQL/真实Worker130 passed/1 warning；Ruff、Mypy232、依赖/差异/摘要/wheel通过，记录25个测试进程均退出。既有警告保留，未连接真实邮箱。实施者本轮仅核对日志摘要并更新文档，不重跑上述测试。F08.6仍BLOCKED，唯一下一步是补其真机与真实Push等证据；未部署或启用其他F09。
