# 当前系统真实环境手工测试手册

日期：2026-10-30。用于本人在 Windows 主机、现有手机、真实邮箱及 BOSS/ehall 账户上逐项试用。本文是计划，不代表真实测试已通过；本次仅写文档，没有安装、启动、解冻或对外操作。

## 1. 当前能测什么

系统形态为“本机持久后台 + 浏览器/手机控制面 + 独立业务扩展”。源码新能力需要切换到实际运行实例后才能使用；网页能访问不等于后台已更新、Worker 正在执行或发送已开放。

| 功能 | 当前入口 | 实测前的条件/限制 |
|---|---|---|
| 任务、消息、Schema 草稿、正式审批 | 固定地址 /ui/ | 本人 Access 登录，核对后台与脚本版本 |
| 模型规划 | 任务详情 → 继续任务 → Agent → 继续执行 | 已有 DeepSeek 公开自检；实际任务需单独核对披露 |
| 邮件同步/搜索 | smail 工具、Agent 或已登记轮询 | 真实账户、WCM 凭据、扩展配置、非冻结 Worker |
| 精确邮件发送 | assistant.mail_reply 工作流、正式审批 | 所需扩展启用、三层发送开关及受控收件地址 |
| 知识索引/检索 | personal.knowledge 工具 | 授权小目录、PostgreSQL、索引与扫描配置 |
| ehall 本机直接交互 | assistantctl ehall | 本人登录；导航和填写期间照常联网 |
| ehall 受监督流程 | nju.ehall + assistant.form_prepare/form_submit | 真实事务指纹/权威预览仍未完成验收 |
| BOSS 职位整理/计划 | 独立 CLI、只读采集器 | 可独立使用，不要求助手后台 |
| BOSS 消息/简历执行 | career.apply 工具、通用 R2 审批 | 新版宿主、安装配置、本人登录、实站适配 |
| 招聘邮件附件投递 | career.apply → 宿主邮件能力 | 真实邮箱配置、resume_roots授权目录 |
| 手机通知 | 已有 ntfy | 独立通知服务、后台权限与只读凭据有效 |
| 规则/调度/备份 | PWA 规则、本机 Admin/脚本 | 逐项开启，不一次释放旧队列或全部轮询 |

目前career.apply尚未安装到原运行实例。0.2.0已支持resume_roots授权目录读取：Agent列出文件并用hash导入宿主制品，无需PWA上传控件。仍无招聘专用工作流或任意工具控制台，不能把文件路径直接当artifact_id。

BOSS 选择器仅通过本地 DOM/浏览器验证，真实页可能不满足要求，应记“适配阻断”。F08.6 仍为 BLOCKED，F07/F08 整体状态不因本手册改变。开发与验证边界见[插件交付总结](../extensions/career_apply/DELIVERY.md)。

## 2. 第一次按这个顺序做

1. 核对运行实例、owner、冻结状态，准备可执行的持久运行配置。
2. 公开内容的模型任务。
3. 自己另一邮箱发来一封邮件，验证只读同步与搜索。
4. 助手向本人受控地址发送一封无附件邮件。
5. 本人登录 ehall，查一个确有需求的事项，停在最终提交前。
6. 导入少量真实 BOSS 职位、生成计划，再尝试已有会话的准确文字预览。
7. 配好resume_roots、确认本地导入并通过实站适配后，单独测试小简历发送。

不需要先重跑完整自动化套件。每个外部动作先只选一个目标；只在本人确实准备应聘且文字真实时联系招聘者，不把陌生招聘者作为测试收件人。

## 3. 先准备运行实例

### 3.1 核对现有环境

- 助手：https://yanshuheng.dpdns.org/ui/
- 私有 health：https://alchemymechine.tail98765a.ts.net/healthz
- 本机 Public/Admin/Health 预期仅回环监听 8000/8001/8010；ntfy 独立运行。
- 手机 Chrome 桌面入口、Access One-time PIN 与 ntfy 已有真实使用证据，无需重装或重做全部手机测试。

上次运行是冻结验收实例。历史 run-approval-host.py 是 frozen-only 临时 owner，去掉冻结可能使它停止子服务。**先切换 owner，再讨论 resume，不要直接把旧验收实例解冻。** 本次文档整理没有查询当前进程，不能凭历史记录断言此刻在线。

在 PowerShell 中：

~~~powershell
Set-Location -LiteralPath 'C:\Users\kunji\Desktop\homework\生成式软件工程\lab1'
$Py = Join-Path (Get-Location) '.venv-win\Scripts\python.exe'
$Ctl = Join-Path (Get-Location) '.venv-win\Scripts\assistantctl.exe'
$RealProfile = Read-Host '输入已核对的持久运行 profile 路径'
if (-not (Test-Path -LiteralPath $RealProfile -PathType Leaf)) {
    throw '配置不存在，停止；不要用模板覆盖原库配置'
}
.\scripts\assistant-host.ps1 -Action status -Profile $RealProfile
~~~

选定 profile 应保留原数据库句柄、扩展根目录和制品目录，使用 PostgreSQL。内存开发 profile 不适合验证持久化；不要覆盖原库配置或恢复默认口令。

首次切换由操作人员先完成：核对旧 owner 与端口；建立并验证加密恢复点；查看旧作业、未决审批和 UNKNOWN；准备含本轮通用接线的宿主及实际所需迁移；停止旧 owner 后用同一持久配置切换，初期禁发、冻结检查。

本次尚未代你完成这一切换。如果仍是旧 frozen-only owner，先停在此处，下一轮单独完成切换；独立招聘规划可先用。

### 3.2 临时 Admin 窗口

旧 owner 已退出、端口空闲、profile 已核对后，前台启动同一 guardian 并临时加入 Admin：

~~~powershell
& $Py -Xutf8 -B -m personal_assistant.host_entry serve --profile $RealProfile --admin
~~~

保持窗口运行，第二个 PowerShell 初始化同样的项目路径、$Py、$Ctl 后做管理。Admin 必须用同一数据库和数据根，仅回环，不能接 Tunnel。

旧作业、发送开关和当前任务均核对后，才在正常 owner 上操作：

~~~powershell
.\scripts\assistant-host.ps1 -Action freeze -Profile $RealProfile
# 确认上述前提后才解除冻结：
.\scripts\assistant-host.ps1 -Action resume -Profile $RealProfile
~~~

profile 启动会清理父进程继承的 PA_*，不能只在父 PowerShell 设置环境变量就认为新配置生效。收尾关闭临时 Admin，保持单一 guardian。运维细节见[本地运行手册](RUNBOOK_LOCAL_BETA.md)，其中历史示例路径/端口需与实际实例对应。

### 3.3 怎样执行下文的本机 HTTP 请求

下文 HTTP/JSON 块是请求说明，不直接粘贴进 PowerShell。可在已登录的本机 Admin 工具中提交，或把修改后的 JSON 保存到 Git 忽略的 var/manual-test 下，用如下 PowerShell 请求示例。开始前先 GET 查看原值，确认整体替换不会丢失其他配置。

~~~powershell
$AdminBase = 'http://127.0.0.1:8001'
# 只读示例：
Invoke-RestMethod -Method Get -Uri "$AdminBase/admin/v1/mail/accounts"

# 已编辑并核对的JSON文件，仅含非秘密设置与凭据句柄：
$BodyFile = Read-Host '输入本次已核对的 JSON 文件路径'
$JsonBody = Get-Content -LiteralPath $BodyFile -Raw -Encoding UTF8
$MutationKey = [guid]::NewGuid().ToString()
$MutationRequest = @{
    Method = 'Put'
    Uri = "$AdminBase/admin/v1/mail/accounts"
    ContentType = 'application/json'
    Headers = @{ 'Idempotency-Key' = $MutationKey }
    Body = [Text.Encoding]::UTF8.GetBytes($JsonBody)
}
# 核对Method/Uri/文件后才执行：
Invoke-RestMethod @MutationRequest
~~~

其他命令按下文设置Method、Uri和JSON。只有接口明确实现了命令回执时，结果未确认才可按约定重试原对象和原键，不重新生成命令。扩展配置PUT没有命令回执或CAS：结果不明先GET当前值核对，不能凭相同Idempotency-Key盲目重发；有并发修改时先保留并核对新值。不要把邮箱密码或模型密钥写进这些文件。

## 4. 安装简历投递插件

详见[安装说明](../extensions/career_apply/INSTALL.md)。Admin 就绪后：

~~~powershell
# 先按安装说明生成白名单干净目录 $CareerPackage，避免把 .local 带入安装材料：
& $Ctl extension inspect $CareerPackage
& $Ctl extension install $CareerPackage
# 确认安装完成，按安装说明保存非秘密配置，再启用：
& $Ctl extension enable career.apply
& $Ctl extension status career.apply
~~~

install 会重新预览并要求确认，不加 --yes。已安装同 ID 时先查状态；同版本同材料跳过安装，同版本材料不同则停止，只有严格更新版本才能 upgrade，不重复 install。当前依赖锁针对 Windows x64 / CPython 3.12；不通过删除锁文件或放宽哈希解决依赖失败。

ENABLED 只表示工具可发现，BOSS 登录、真实邮箱和选择器兼容仍要单独核对。

## 5. 基础任务、模型与知识检索

### 5.1 模型任务

新建“手工验收-模型-日期”，输入：

> 只解释 IMAP 与 SMTP，使用公开知识，不调用工具或执行外部动作。说明哪些事项你没有验证。

任务详情 → 继续任务 → 执行方式选 Agent → 继续执行。创建任务或追加消息本身只保存，不保证执行。

出现“核对资料披露并允许模型处理”时，核对接收方、用途和字段，再确认并继续。任务文字及工具观察可能送往已配置的 DeepSeek。公开自检的许可不能代替真实邮件、资料和简历的许可；密码/验证码/API Key 不放进任务。

通过标准：后台实际执行，返回说明，其他终端能看到结果，没有外部动作。模型自述“已发信/已提交”不是回执。

### 5.2 知识小目录

确认 personal.knowledge 启用，只授权本人准备的手测目录，放 2–3 份 Markdown/TXT/PDF，其中一份含唯一短语。配置见[知识扩展 README](../extensions/personal_knowledge/README.md)，默认 embedding.provider=none 可测试仅 FTS。

任务示例：

> 只检索授权目录中的“唯一验收短语”，给出实际文件和定位引用。不要编造未知内容，不修改原文件。

通过标准：引用可回到原文；修改手测文件并重索引后引用更新；移走一份后不再引用旧内容。未启用索引或扫描时记配置缺口，不认定没有资料。

## 6. 邮件：先收，再发给自己

### 6.1 凭据与账户

交互式 PowerShell：

~~~powershell
& $Ctl secret put --name '手动验收邮箱' --kind mail_client_password
~~~

隐藏输入客户端专用密码，只记录返回的句柄 ID。不要把密码放命令、JSON、聊天或日志。

先 GET http://127.0.0.1:8001/admin/v1/mail/accounts，再向同一地址 PUT。所有管理写请求使用 Content-Type: application/json 与本次命令的唯一 Idempotency-Key（UUID）。同一结果未确认的命令保留原键，不另造新键。

PUT 结构示例，示意值必须先替换：

~~~json
{
  "accounts": [{
    "account_id": "nju-manual",
    "address": "本人真实邮箱",
    "imap_host": "服务商当前核验的IMAP域名",
    "imap_port": 993,
    "smtp_host": "服务商当前核验的SMTP域名",
    "smtp_port": 465,
    "secret_handle_id": "实际wcm1句柄ID",
    "display_name": "手动验收邮箱",
    "read_enabled": true,
    "send_enabled": false,
    "tls_mode": "implicit"
  }]
}
~~~

**PUT 整体替换全部账户**：保留原有账户，只传可写字段，不回传 GET 的 generation/fingerprint。账户变化会使旧审批失效。993/465 与 implicit 为结构示例，实际以服务商核验结果为准。

扩展配置：PUT /admin/v1/extensions/nju.smail/config，body 如下。先 GET 并合并需要保留的配置：

~~~json
{
  "config": {
    "accounts": [{"account_id": "nju-manual", "display_name": "手动验收邮箱"}],
    "folders": ["INBOX"],
    "poll_interval_seconds": 300,
    "max_messages_per_sync": 20,
    "max_message_bytes": 2097152
  }
}
~~~

返回 restart_required=true 后协调重载。不要用历史 scripts/mail_real_readonly.py 配置生产邮箱，它是旧验收器，会影响账户/Worker 状态。

### 6.2 收件

1. 从自己另一个邮箱发来“手工收件验收-日期-唯一编号”，正文仅公开测试说明。
2. Admin GET /admin/v1/declarations 核对当前版本。如需自动轮询，POST /admin/v1/schedules：

~~~json
{
  "schedule_id": "smail.poll_every_5m",
  "source_id": "smail.poll_inbox",
  "extension_id": "nju.smail",
  "extension_version": "0.2.0"
}
~~~

版本须与目录一致。已登记时不要换新键重复创建，非冻结 Worker 才会轮询。

3. 建只读 Agent 任务：“先 smail.sync 同步 nju-manual，再 smail.search 搜索唯一主题，只报告找到的邮件，不发送、删除或标记已读”。看实际工具结果；PWA 没有独立收件箱或公开 /tools/invoke。
4. 核对发件人、主题、正文与来源；再同步一次检查不重复，在网页邮箱核对原已读状态未变。

通过标准：真实邮件可检索，来源正确、不重复、邮箱状态未改变。配置成功不等于收件通过。

### 6.3 一封无附件发送

所需条件：

- nju.smail@0.2.0、personal.knowledge@0.1.0、assistant.workflows@0.1.0 已启用。
- 实际 profile 的 PA_MAIL_SEND_ENABLED=true。
- PA_MAIL_TEST_RECIPIENTS 仅列本次本人受控地址。
- 账户 send_enabled=true，Worker 非冻结。

新建独立任务，在“继续任务”选 assistant.mail_reply · 1，逐项填写 account_id、query、to、subject、body_text，最后 review_draft=true。to 只填一个本人受控邮箱。不选旧 smail.reply_flow，当前旧声明格式会禁用。

此工作流检索本地邮件与资料再准备手填草稿，不先同步，不自动生成正文或绑定原邮件回复头。出现正式审批后核对发件人、唯一收件人、主题、完整正文、附件为空、扩展版本，完全相符才批准一次。等待 Worker；过期重新准备审批，不能普通继续绕过未决动作。

通过标准：宿主权威发送状态与受控邮箱实际收到的一封内容一致，动作 ID/Message-ID 可对照。SMTP 接受不等于对方已读。刷新同一已完成动作不应再发；不要为测幂等创建新动作。

UNKNOWN 后停止，先只读核对 Sent/实际收件，再按原动作对账，禁止换键重发。无附件自发自收通过后才测试招聘附件。

## 7. ehall：先查真实事项，停在提交前

本人在交互会话执行：

~~~powershell
& $Py -Xutf8 -B -m personal_assistant.cli.main ehall '打开 成绩查询'
~~~

本人登录和输入验证码，核对页面。确有表单需求时可试：

~~~powershell
& $Py -Xutf8 -B -m personal_assistant.cli.main ehall --sample '打开 教室借用 > 临时借用（仅借1天）'
~~~

--sample 做可确定的试填并停在最终提交前，不保证任意事务可适配。没有真实需求不要虚构申请。

普通办理语法支持“办理 事务名；填写 字段名=值；提交按钮=页面按钮文字”。仅在真实需求、准确字段和后果核对后使用，最终点击前出现本机确认页。

**本机入口导航/观察/填写期间照常联网，页面可能自行 POST。** 本机确认页不绑定完整 payload/材料/后果，也不接模型、邮件或 PWA 任务，不能替代受监督安全验收。

助手可在任务文字中记录事项、截止时间和材料清单；这不是结构化截止时间或自动提醒。可尝试已安装的 assistant.form_prepare。受监督真实指纹/权威预览与提交仍待验，遇不可批准、漂移或 SAFETY_PAUSED 应记待适配，不改走绕过 Gateway 的自动提交。

第一次通过标准：正确登录/事项/字段/填写，明确停在提交前。真实提交单独记录回执；永久禁止事务不因测试放开。

## 8. 简历投递

### 8.1 可立即使用的本地规划

先合成示例，再替换少量真实文件。完整 CLI 见[插件 README](../extensions/career_apply/README.md)。

~~~powershell
$Planner = 'extensions/career_apply/run.py'
$CareerDb = 'extensions/career_apply/.local/career.sqlite3'
& $Py -Xutf8 -B $Planner --database $CareerDb jobs import extensions/career_apply/examples/jobs.json
& $Py -Xutf8 -B $Planner --database $CareerDb jobs list --include Python --city 南京 --limit 5
& $Py -Xutf8 -B $Planner --database $CareerDb recipients import extensions/career_apply/examples/recipients.csv
& $Py -Xutf8 -B $Planner --database $CareerDb plans list
~~~

示例是合成材料，不会投递。独立 browser 目录可作为 Chrome/Edge 已解压扩展加载；本人登录 BOSS 后读取可见职位并导出 JSON。零条可能是页面不兼容。

导入上限 2 MiB、1000 条；规划简历 8 MiB。筛选 limit 不是实际日投递额度。数据放 .local，不提交真实名单和简历。

### 8.2 一个真实 BOSS 目标

按[安装说明](../extensions/career_apply/INSTALL.md)启动独立可见回环 CDP 浏览器，本人登录，只保留一个 BOSS 页面。插件不自动登录、不绕验证码，CDP 不经 Tunnel/转发。

准备准确 job_id/URL、公司和 HR。工具职位对象只用 url/job_id/title/company/recruiter/location/salary_text/description；采集导出额外的 communication_state 不要原样传入。

先只读任务：

> 仅核对这一职位的公司和招聘者，尝试 career.boss.prepare 的 message 预览，使用我给出的准确文字。不兼容就停止，不使用 greet，不点击立即沟通，没有独立审批不发送。

Agent 能看到已启用工具，但选工具非确定性；只有解释、没有实际 prepare/审批，不算执行。PWA 没有 career 专用按钮/工作流。

确实要联系且预览准确时，再允许 execute 进入正式 R2 审批，核对职位、公司、HR、完整文字和后果，仅批准一次。当前 greet 返回需本人处理，“立即沟通”可能直接发送默认话术；文字实际不超过 2000 字。

通过标准：准确目标的一条准确消息与可验证引用。多窗口、已有编辑草稿、缺唯一控件/稳定回执时停止；点击后不明为 UNKNOWN，不新建动作重发。

### 8.3 简历文件：从授权本地目录直接读取

按[安装说明](../extensions/career_apply/INSTALL.md)配置resume_roots，可与知识库使用同一目录；插件分别显式授权，不自动继承其他扩展私有配置。

~~~text
career.resume.list
→ 选择root_key / relative_path / sha256
→ career.resume.import_file(expected_sha256=所选哈希)
→ 宿主制品 ID / SHA-256 / 大小
→ career.boss.prepare(action_kind=send_resume, resume=完整引用)
→ career.boss.execute → 独立 R2 审批 → 新上传材料及送达回执核验
~~~

在助手新任务中明确要求：“列出resumes目录，选择我指定的简历，按列出的哈希导入；先只准备、不发送。”不把真实简历base64贴给模型。工具只返回文件元数据与制品引用，字节在本机读取/制品链路内传递；元数据与后续观察仍受既有披露许可约束。

上限512 KiB，支持PDF/DOCX/UTF-8 TXT。目录需实际存在、为本人显式授权；源文件保持只读。列表最多100条、扫描有界，truncated/skipped提示过滤或未完整枚举，零条不能直接判为空目录。

越界路径、链接/junction/隐藏文件被拒绝或过滤。列表后文件变更导致旧hash导入失败，应重新列出/选择；导入后附件固定为该快照，不随源文件后来修改而变化。message与send_resume分别审批，不自写artifact_id，不用同名远端文件冒充已核验字节。

真实BOSS若缺新upload-id、大小、完成状态或对应送达凭据，仍记实站适配阻断，不放宽为任意发送按钮。通过须准确文件、准确HR和可验证回执一致。

### 8.4 邮件投递简历

本地名单可保存为 JSON/CSV/TXT，模板仅支持 name/email/company/position，生成每位收件人的独立计划，不会自动发送。

真实流程是 career.resume.list → career.resume.import_file → career.mail.prepare → career.mail.send，复用第6节邮箱配置和审批。prepare 必须给一个 to、subject、body、完整 resume 引用及带时区的 prepared_at；同请求重试沿用原时间和返回的 send_arguments。名单导入不是批量发送授权。

使用上节的本地目录导入引用，先发到本人受控邮箱，下载小附件并核对 SHA-256，再考虑一个真实应聘地址：

~~~powershell
Get-FileHash -Algorithm SHA256 -LiteralPath '本人实际下载的附件路径'
~~~

## 9. 其他功能用小样本覆盖

| 范围 | 操作 | 通过标准 |
|---|---|---|
| 任务/消息 | 手机建任务，电脑查看，追加一条不同消息 | 不重复，另一端可见 |
| 草稿 | 保存测试字段，再做双端版本冲突 | 版本/来源正确；保留未保存输入，显式处理 |
| 通知 | 后台/锁屏等一条任务通知，再点链接 | 最小任务标识/风险，进入准确任务；无正文/简历 |
| 边界 | 私有 /healthz 正常，私有 tasks/admin 路径 Not Found | health-only；8000/8001 数字IP直连不可达 |
| scoped 规则 | 窄作用域候选，核对正反例再启用/停用 | 不立即生效，不改变审批/风险 |
| 调度 | 登记单一只读 schedule，观察后停用 | 按时执行，事件不重复，停用后不再领取 |
| 网络 | 无待发送动作时短暂断网恢复 | 如实记录自动或手动刷新恢复 |
| 重启持久化 | 无未决写动作时有序停/启同一实例 | 任务/草稿保留，不重放旧 R2 |
| 备份 | 选定 profile create/list/verify | 可认证读取，密钥本人离线保管 |
| 隔离恢复 | 另设空 *_restore / *_test 库和新制品目录 | 不覆盖原库；恢复结果可核对 |

精确备份命令见[本地运行手册](RUNBOOK_LOCAL_BETA.md)。恢复/升级单独安排，不与第一次发信混做。测试结束用 Admin GET /admin/v1/schedules 取实际版本，POST /admin/v1/schedules/smail.poll_every_5m，body 为 {"version":实际版本,"enabled":false}，只停本次开启的调度。

### 9.1 Agent 编写和安装新插件

先按[开发扩展安装说明](../extensions/plugin_developer/INSTALL.md)在同一持久实例安装并配置 assistant.plugin_developer；该步骤尚未代为执行。模型凭据继续沿用现有安全句柄，不为此复制密钥。

1. 给助手一个公开、无外部副作用的小需求，例如“生成一个纯文本字数统计插件，包含正确性和空输入测试”。让它使用 developer 工具生成候选，回报 candidate_id、代码差异、依赖和 candidate_hash。
2. 本人按[使用说明](../extensions/plugin_developer/README.md)运行 manage.py approve-tests，核对后输入 YES。首次建议只批准精确快照；若需要自主修复，显式选择有时限、有次数上限的 --iterative。
3. 继续同一 Agent 任务，让它运行 developer.validate。检查测试确实覆盖需求，不把初始 ping 测试当业务通过。失败后修复应产生新摘要；新依赖/权限需要再次本人确认。
4. 有当前摘要 PASSED 后让助手 request release；本人运行 manage.py install --digest ...，在原宿主确认界面核对实际材料。需要启用时明确 --enable，安装不成功不得继续。
5. 新开任务调用新工具，核对准确输出、原任务/插件不受影响。需要回滚则本人执行 manage.py rollback；保留原记录。

通过标准：模型实际生成满足需求的代码，真实本地验证通过，本人确认后完成宿主安装/启用，任务可发现并调用新工具。当前开发测试只证明本地循环和 CLI 边界，**不把这五步的真实联调预记为通过**。测试代码有当前 Windows 用户权限，首轮仅使用公开夹具，不读取本人材料或发信。

## 10. 记录与收尾

每项只保留必要脱敏证据，不导出完整 HAR/Cookie/凭据或真实材料正文：

~~~text
用例：
实际实例 / profile / 宿主与扩展版本：
前置条件与操作时间：
任务 ID / 动作 ID / Message-ID / 回执引用：
预期：
实际：
结论：PASS / FAIL / BLOCKED / 未执行
必要证据路径：
是否发生外部动作、是否需只读对账：
收尾：Admin、调试浏览器、调度、发送开关状态
~~~

UNKNOWN、材料与审批不符、目标漂移、验证码/账号异常、界面成功却无回执时，停止该动作，保留原请求，不清幂等键、不换新动作重发。

**下一步：先准备正常持久实例的受控切换与 Admin 窗口，安装 career.apply 并配置只读 BOSS 连接；按“模型 → 收件 → 自发自收 → ehall 提交前 → BOSS 计划/预览”试用。** 配置授权简历目录并验证实站兼容后，再做实际简历发送。
