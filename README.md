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
- smail 邮件扩展（F06 DONE，六轮独立审计及补充复验通过）：`extensions/nju_smail` 提供只读 IMAP 轮询（`smail.poll_inbox`、`smail.sync`）、线程上下文（`smail.thread_history`）、检索（`smail.search` READ）、版本化草稿（`smail.prepare_reply` INTERNAL_WRITE）与受控发送（`smail.send` EXTERNAL_WRITE）；通用宿主能力 `core/mail` + `infrastructure/mail`（真实 TLS IMAP 只读客户端、显式阶段 SMTP 客户端、唯一连接建立者 `MailTransportBroker`、审批快照校验执行器 `MailSendExecutor`）与 `host.artifact.*` 制品能力。消息去重以 `(账户, 文件夹, UIDVALIDITY, UID)` 为主、`Message-ID + 规范化内容哈希` 为辅；草稿每次编辑产生新版本与新的稳定动作 ID/Message-ID；发送必须经过 Tool Gateway、R2 审批与 SideEffect Outbox，实际发送的 MIME 字节/收件人/附件哈希与审批快照一致；部分拒收保存逐收件人明细；UNKNOWN 不自动重发并由 Sent 只读对账。客户端专用密码只经 `SecretHandle` 由宿主解析，扩展不建立 IMAP/SMTP 连接；读取与发送能力独立配置（`PA_MAIL_SEND_ENABLED` + `PA_MAIL_TEST_RECIPIENTS` 受控地址 allowlist）；宿主账户注册表持有端点与凭据句柄，扩展只提交 `account_id` 与宿主计算的账户 fingerprint；注册表 generation 删除后不复用，派发期间账户变更会经可失效 guard 在连接/认证/DATA 前中止。发送结果只由宿主权威账本决定，扩展没有写入工具（`smail.send_status` 仅把宿主状态投影到本地视图）。真实 smail 账号 E2E 未执行：缺少用户账号、凭据与受控地址，当前证据为协议级模拟服务器 + 真实 TLS/socket + 真实 PostgreSQL + 真实 Worker/组合根测试。
- ehall 受监督办事基架（F07 IN_PROGRESS，等待完整真实验收与独立复审）：核心 `BrowserSessionBroker`（会话状态机、origin/路径策略、永久 R3 分类、页面 fingerprint（含表单 action/method）、canonical 结构化预览（含提交目标绑定）、宿主驱动只读对账、过期会话释放）与 `infrastructure/browser` 的 Desktop Companion（唯一导入 Playwright 的 `driver.py`，强制 `headless=False`，256 bit 会话 capability、仅内存、TTL、可撤销且不经异常链泄漏；默认允许规范 HTTPS origin，可由用户在本机启动时切换为精确 origin 白名单模式；`fill` 后持久阻断全部写请求，提交临界区只放行一条精确匹配 method/origin/path 的写请求，延迟 autosave/二次 POST/动态改写 action 均被 abort 并计数；正文扫描不完整即安全暂停）以及宿主 `BrowserActionExecutor`（`browser.fill`/`browser.submit` 只能经 Tool Gateway + R2 审批 + Outbox；结果不明为 `UNKNOWN`，不自动重试，由宿主 `host.browser.reconcile` 只读对账）。扩展 `extensions/nju_ehall` 提供只读发现/检视/预览、声明式“进入事项”导航与受控填写，R3（退课/撤回/支付/选课变更/法律声明/未知事务/未知页面版本）在策略层永久阻断；适配器必须携带人工核验的页面指纹集合与提交目标，并按事务拆分（`proof.json` 在读证明 + `transcript.json` 成绩单打印）。宿主执行工具 `ehall.open_transaction`（INTERNAL_WRITE）只点击适配器声明、绑定 `transaction_id`/落地路径的导航动作：实时页面必须存在同 label 动作，落地路径与事务指纹双重校验，漂移即 `SAFETY_PAUSED`；提交仍必须 R2 审批。真实 ehall 验收仍在进行：受监督链路已完成真实门户/SSO 只读采集；本机 `assistantctl ehall` 已由用户反馈成功打开成绩查询，并对教室借用真实表单部分试填及程序化选择一个下拉选项。完整填写至权威预览、真实适配器指纹和提交回执尚未验收；本机入口不替代受监督链路。
- CLI、单元/契约测试以及面向后续模型的任务清单。
- 响应式 PWA 壳和 F08.1/F08.2 任务交互：通过有界公共 API 读取服务器任务状态，以 SSE 连接/断开显示连接状态并在重连后重读；手机宽度 PWA 可按服务器版本和幂等键补充任务消息，409 重读并保留草稿，离线或结果未确认时不冒称发送成功。F08.1 与 F08.2 均已通过定向独立验收；F08 整体仍为 `IN_PROGRESS`。Service Worker 只缓存固定静态资源。Web Push、版本化草稿与正式审批界面、Android 真机验收仍未完成。`assistantctl extension scaffold` 提供扩展生成器。

2026-09-30 用户调整实施顺序：F07 仍为 `IN_PROGRESS` 时，允许先完成不依赖其剩余真实站点工作的 F08 子任务；F08.1 已经用户定向验收，随后授权推进 F08.2。F07 的一次登录自动路由与七字段试填烟测已通过，未点击最终提交；正式真实事务 Adapter、绑定实际提交目标及完整材料/后果的受监督权威预览、正式 Gateway/Outbox 实站链路仍未验收。真实完整填报因暂无确有需要的事务延期，不制造申请。F08 的下一个子任务与本轮测试证据见 [NEXT_STEPS](./docs/NEXT_STEPS.md)。

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

Cloudflare Access JWT 验证已实现并通过三轮独立验收（F03 DONE），但 Tunnel/Tailscale 部署编排尚未执行；真实 ehall 方面，**门户/SSO 只读采集已运行，真实教室借用已部分试填，完整填写至权威预览尚未验收；真实适配器指纹和提交回执未验收**；Windows Credential Manager、Web Push 也尚未实现。它们必须作为适配器或业务扩展完成，不能用假成功替代。smail 已完成一次**受控真实只读同步**（2026-09-19）：经本机回环页面录入客户端专用密码（仅进程内存），真实 `imap.exmail.qq.com:993` 只读同步入库 15 封邮件/15 个事件（INBOX 13 + Sent Messages 2），第二次同步 0 新、0 发送动作；真实 SMTP 发送与凭据持久化仍需 F09。F07 的受监督浏览器基架与 `nju.ehall` 扩展已实现；审计前快照的自动化套件曾全绿，随后独立复验有 1 项锁等待计时失败（单项复跑通过），当前快照的完整结果见 `docs/NEXT_STEPS.md`；真实门户/SSO 只读采集已由用户亲自完成（证据 `docs/evidence/real_ehall_2026-09-21.md`），真实完整填写、权威预览和提交回执仍需用户实时参与，F07 保持 `IN_PROGRESS`。Windows Credential Manager（F09）完成前，生产环境的远程模型凭据与邮件客户端密码后端都是 fail-closed 占位（`UnavailableSecretStore`），不会静默使用内存明文；协议行为只能通过注入 HTTP transport 或模拟邮件服务器测试，尚未完成真实厂商/邮箱端到端验证。

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

1. 受监督宿主工具调用必须经过统一 Tool Gateway；R3 永远拒绝，R2 只执行用户确认过的精确 payload。本机 `assistantctl ehall` 的页面直接交互按上文单独契约运行：仅在点击用户指定的最终提交按钮前本机确认，不保证确认前没有服务器写入。
2. 结果为 `UNKNOWN` 的外部副作用不得自动重试，必须进入对账状态。
3. 新业务通过 SDK 注册扩展槽；不得修改核心路由、核心表或写 `if extension_id == ...`。
