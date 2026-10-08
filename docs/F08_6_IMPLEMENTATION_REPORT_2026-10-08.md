# F08.6 实现与验收交接报告

日期：2026-10-08（Asia/Shanghai）
用途：交给独立审计 agent 复验当前工作区；本报告是实施者交接材料，不是独立验收结论。

**复验后更新**：新增P2的修复与本轮30例定向结果见第10节；第1–8节保留首次交接时的历史证据，第9节摘要已更新到修复后的当前文件。本阶段仍未通过整体验收。


## 1. 结论与快照

- F08.1–F08.5：沿用用户已给出的各自约定范围内的独立验收结论，DONE。
- F08.6：当前唯一工作项，BLOCKED；已有可用部署和部分真机证据，完整阶段条件尚未满足。
- F07、F08 整体：IN_PROGRESS。
- F09.1：仅提出最小凭据后端的排期建议。用户要求先报告并交独立审计，未授权实施，未修改为 NEXT。
- 本报告生成时没有执行新的 pytest、全量或 PostgreSQL/Worker 套件；没有升级扩展、创建审批、发邮件、操作真实 ehall 或改动运行服务。

实际工作区：`C:\Users\kunji\Desktop\homework\生成式软件工程\lab1`。
分支：main；HEAD：deeb9b2aa65481a3eeefdcbdb88cfeebef45d672。
交付包含未提交的修改及新增文件，**不能只审查 HEAD 或只看 git diff 的已跟踪文件**。Codex 的 ed17 worktree 是旧快照，不是本次 F08.6 实现目录。文末给出关键源码、测试和资源的 SHA-256。

### 是否形成进度死锁

如果要求“F08.6 全部 DONE 后才允许开始 F09”，当前真实 Push 验收会被卡住：生产组合根默认使用拒绝全部操作的 UnavailableSecretStore，而 VAPID 私钥和订阅加密需要可信秘密后端，路线图将该后端放在 F09。

这是**排期依赖造成的阻塞，不是程序运行死锁**。任务、消息、草稿及其他独立验收仍可使用或推进。解除该依赖需要用户明确允许先完成最小凭据后端，或另行批准并验收可信持久 SecretStorePort 实现；不需要先实现整个 F09。当前不采取这些例外，不把真实 Push 记为通过，也不把 F08 整体标 DONE。

## 2. 本阶段代码与材料

下表是相对上述 HEAD 的当前改动范围；大部分改动跨前几轮完成，本次报告不将其冒充新实现。

| 文件 | 实现或验证作用 |
| --- | --- |
| src/personal_assistant/health_app.py | 关闭斜杠重定向；仅精确 /healthz 可用，/healthz/ 不再 307。 |
| src/personal_assistant/api/middleware/security_headers.py | 在原 API no-store 分支加入 /ui/，固定名静态文件及 HEAD/304/查询参数请求明确禁用 HTTP 缓存。 |
| scripts/prepare_f08.py（新增） | 生成未激活候选、拒绝覆盖既有材料；检查固定 HTTPS、Access、PostgreSQL、回环端口、默认密码/DSN/继承环境冲突；不启动应用或访问数据库。 |
| web/pwa/index.html | Manifest 链接使用 crossorigin=use-credentials，以适配 Access 保护。 |
| web/pwa/manifest.webmanifest | 增加同源 192/512 PNG 图标；保持 /ui/ scope/start_url 和 standalone。 |
| web/pwa/icon-192.png、icon-512.png（新增） | 实际可解码 PNG 图标。 |
| pyproject.toml | wheel 静态材料包含上述两幅图标。 |
| web/pwa/app.js | 对精确 503/CLOUDFLARE_ACCESS_UNAVAILABLE 给中文说明；暂时草稿 GET 失败时保留已有有效 Schema 下的本机编辑、基准和在途/未确认请求。 |
| web/pwa/service-worker.js | 静态缓存从 v11 经 v12/v13 更新至磁盘当前 v14；白名单仍为四条固定路径。 |
| tests/api/test_boundaries.py | 精确健康路径反例。 |
| tests/api/test_pwa_installation_f08.py（新增） | Manifest、图标、打包、Access 拒绝路径及静态 GET/HEAD/304/no-store 反例。 |
| tests/unit/test_f08_environment_preparation.py（新增） | 非部署准备工具的拒绝路径和无启动副作用。 |
| tests/integration/test_health_boundary_f08.py（新增） | 真实回环 Uvicorn 进程的 health-only 黑盒。 |
| tests/integration/test_pwa_browser_f08.py | 390px 本地浏览器；实际旧 Worker 安装、缓存 Manifest、更新、PNG 解码及敏感内容不入缓存。 |
| tests/integration/test_pwa_access_recovery_browser_f086.py（新增） | 模拟 503 保留任务/未发送输入，恢复重读；其他错误不套用该说明。 |
| tests/integration/test_pwa_form_drafts_browser_f08.py | 新增在途/丢回执后 GET 失败、未保存输入断网保留与不支持 Schema 仍拒绝的反例。 |
| README.md、TODO.md、docs/CONTRACTS_AND_INTERFACES.md、docs/NEXT_STEPS.md、docs/F08_ENVIRONMENT_PREPARATION.md（新增） | 范围、状态、契约、授权和运行/真机证据记录。 |

没有新增数据库迁移，没有修改审批 canonicalization、风险等级、Outbox、CAS 或幂等指纹规则。扩展 UI 仍只能提供 JSON Schema/UI Schema。原 F08.1–F08.5 业务实现不是本阶段新交付。

### v14 的局部修复

暂时草稿读取失败（网络错误、408、5xx）且当前已有有效 Schema、本机修改/在途保存/冲突时，编辑器不被清空：

- 保存仍在途：保留值、原版本和请求，按钮继续禁用，不冒称成功。
- 保存结果未确认：保持原请求绑定和同键手动重试入口。
- 只有未保存编辑：保持输入可见，明确表示未保存，恢复后重读服务端版本。
- 成功读取但 Schema 不支持：仍拒绝编辑，不用旧 Schema 隐藏错误。
- 状态只留当前页面内存，不把草稿正文存入浏览器持久存储。

Service Worker 的 CacheStorage 白名单仍只有：

```text
/ui/
/ui/styles.css
/ui/app.js
/ui/manifest.webmanifest
```

图标、service-worker.js 本身及 API 不加入该白名单。HTTP no-store 与显式 CacheStorage 是不同机制；本地浏览器缓存测试已覆盖原四路径。新 HTTP 响应头不等于既有 CDN 副本已被清除。

## 3. 已授权部署和当前运行事实

### 本阶段已经完成的部署操作

这些是历史操作记录，不是让审计 agent 再启动一组进程：

1. 用户明确授权沿用原助手 PostgreSQL 库；默认口令轮换为随机口令，仅存于受限、Git 忽略的本机配置。此为临时 DSN 例外，不代表 F09 Secret Reference 已实现。
2. 首次启动前只读核对原库 7 份迁移，明确待执行 0008–0011；经用户授权启动后记录 11 份迁移一致。该一致性是首次部署时证据，本次报告没有重新做迁移账本检查。
3. 固定入口 https://yanshuheng.dpdns.org/ui/；完整主机名由 Cloudflare Access 保护。单一 Email_policy 来自用户截图和完整策略列表确认，未声称独立控制台全量读取。
4. 用户完成 cloudflared 官方授权。核对唯一 A→192.0.2.0 占位后，用户明确批准精确替换为 Tunnel CNAME；Tunnel 只代理回环公共 API 8000。
5. 应用改为 One-time PIN，用户自行处理验证码；已有截图和首次 Android 连通反馈。
6. 私有 Tailscale Serve 只代理独立 health 8010。用户曾启用账户的 Funnel 能力；当前没有 Funnel 映射，不能把这说成账户能力已撤销。
7. example.echo@0.1.0 仅在用户明确批准精确材料后安装启用，用于 PUBLIC prefix 草稿验收；材料 SHA-256 为 1c3725193434fa8d027ce95b87b5992fec4bef2fa72f82da18525a241ec70725。临时 Admin 及其直属 Worker 当轮已关闭。

配置、证书和日志位于本机受限的 Git 忽略目录；报告不包含 DSN、私钥、JWT、Cookie、用户邮箱或私有 Tailscale 节点名/IP。之前的 ACL 检查见 NEXT_STEPS；本次报告未重查 ACL。

### 2026-10-08 静态缓存修复后的重新加载

先确认独立公共控制台和进程树。Ctrl+C 没有让旧进程退出，20 秒等待失败，未启动替代；Ctrl+Break 使旧 Worker 退出并释放 8000，但辅助进程也退出非零，两个公共 Python 仍残留。再次核验模块/端口/配置后清理这两个残留进程，确认旧 Worker 均退出，再沿用原配置启动公共助手。

实际 Uvicorn 参数：

```powershell
.\.venv-win\Scripts\python.exe -m uvicorn personal_assistant.app:app --host 127.0.0.1 --port 8000 --no-access-log --timeout-graceful-shutdown 10 --env-file var/deployment/f08-access-2026-10-04/.env.template
```

通过 Start-Process -WindowStyle Hidden 启动；stdout/stderr位于忽略目录。10 秒是本次临时进程的关停等待上限，不是 F09 服务守护实现。health 和 Tunnel 没有重启；没有再次修改 DNS、Access、数据库凭据或业务数据。

### 报告生成本轮的只读复核

| 项目 | 实际结果 |
| --- | --- |
| 公共 wrapper/listener PID | 38220 / 30748 |
| health wrapper/listener PID | 23548 / 14976 |
| Tunnel PID | 8928 |
| 8000 / 8010 / 20241 监听 | 均为 127.0.0.1；8001没有监听 |
| 无 JWT GET /ui/service-worker.js（回环） | 401，Cache-Control: no-store |
| 无 JWT GET /api/v1/tasks（回环） | 401，Cache-Control: no-store |
| 独立 8010 GET /healthz | 200，仅 status:ok |
| Tunnel 回环 /ready | 200 |
| tailscale serve status --json 脱敏读取 | 唯一代理 http://127.0.0.1:8010；AllowFunnel 的值均不启用 |

本轮只读命令 exit0，5.14秒；上述值分别检查，不用“进程存在”替代 HTTP 可用性。PID 只是此快照，不是长期重启参数。公开入口的已认证 v14 脚本内容仍没有取得；回环401不能证明手机收到的脚本版本。

紧邻本报告前的原库只读事务（exit0，1.25秒）确认：

- example.echo@0.1.0 和 nju.smail@0.1.0 均 ENABLED。
- 审批按状态聚合为空，即当前没有审批记录。
- VAPID 公钥和秘密句柄都未配置。
- mail_send_enabled、browser_submit_enabled 都是 false。
- nju.smail 源码 Manifest 为0.2.0，部署仍为0.1.0；未自动升级。
- 事务为 repeatable-read、readonly=True，并检查 transaction_read_only=on。没有读取审批载荷、nonce或邮件正文；精确命令在 NEXT_STEPS 的“下一实际依赖盘点”段。

## 4. 真机证据矩阵

手机：用户反馈 Huawei Pura 70，HarmonyOS 4.2.0.207，无 Google Play。以下文字反馈、用户截图与本地自动化结果分别记录；没有远程独立接管手机后做全套验收。

| 条件 | 已取得证据 | 限制 |
| --- | --- | --- |
| Access 登录与公共连通 | One-time PIN 配置截图；手机列表/已连接反馈及页面截图 | 不从截图推断全部账户级设置；全局会话覆盖未确认。 |
| 桌面入口 | Chrome标识图标，无地址栏启动，列表/连接正常 | 不证明具体 WebAPK 系统包类型；该类型不是额外验收门槛。 |
| Wi-Fi→移动数据 | 用户确认切换后恢复连接 | 没有分别测试所有校园/家庭网络。 |
| 后台或锁屏约1分钟 | 用户对合并问题确认返回后自动恢复 | 未将后台和锁屏两个场景分别验证，也未验证长期后台。 |
| 手机断网/恢复 | “设备离线或事件连接中断”；恢复联网后恢复 | 不等于真实 Push 在后台收到。 |
| 任务和消息 | 创建自检任务/消息、刷新后各一条；电脑与手机新增同步 | 原库早期聚合是当时快照；任务QUEUED不等于 Agent 执行业务。 |
| 草稿来源/恢复 | prefix来源未确定→用户输入；保存版本2并刷新恢复 | PUBLIC 测试字段，不是完整业务填报或证据自动播种。 |
| 只读同步冲突 | 手机截图：本机基准2/未保存值保留、服务器3；显式保留并保存4、刷新恢复 | 不用该截图代替保存 PUT 的409证据。 |
| 实际 PUT409 | 手机在PUT前断点暂停；电脑保存6；恢复后用户确认PUT409、本机值/基准5保留、服务器面板6 | 来自用户方法/状态反馈；没有409网络截图。没有真实412。 |
| 显式采用服务器 | 用户确认采用后变6、电脑值恢复、冲突面板消失 | 不宣称做了新的保存。 |
| 主机断网恢复 | 手机中断提示；电脑恢复后列表自动恢复 | 顶部提示需手动刷新，用户明确接受；不称顶部/SSE完全自动恢复。 |
| CacheStorage | 用户截图/反馈：唯一v12，后来唯一v13；均只有四条静态路径 | 当前磁盘v14，但手机最后确认运行源仍v13；普通刷新正常不能证明v14交付。 |
| 敏感API缓存策略 | 手机任务GET截图200/no-store、CF DYNAMIC；草稿GET截图200/no-store | 没有覆盖未访问的邮件/正式审批/全部敏感API场景。 |
| 其他本地存储 | 用户反馈 Local/Session storage空、IndexedDB未检测到 | 无这三处独立截图；不声称检查Cookies或浏览器HTTP缓存底层。 |
| 私有健康边界 | 手机 health正常，任务/管理/审批、/healthz/、/docs、/openapi.json、/ 均Not Found | 后几条仅用户页面反馈，没有逐项HTTP状态抓包。 |
| 私有IP直连8000/8001 | Chrome两项ERR_CONNECTION_TIMED_OUT；Edge复试也是 | Edge第一次DNS错误不算端口证据；不归因防火墙，不声称TCP抓包。 |
| 最近公共进程重载 | 用户普通刷新后“列表和连接正常” | 正常使用恢复的证据，不能替代脚本逐字版本或阶段整体验收。 |

### 两次丢回执尝试：没有通过证据

1. 基准6，限速下保存测试值，用户在准备断网前已报告保存成功到7；这是普通确认成功，不是丢回执。
2. 基准7，另一测试值保存后切 Offline，出现“无法编辑草稿：Failed to fetch”。原库只读核对已到8；恢复 No throttling 后用户报告基准8、没有重试按钮或冲突面板。没有确认原 PUT 失败、原请求进入“结果未确认”或发生同键手动重放。
3. 第二次现象暴露草稿 GET 失败会清空编辑器，随后做了 v14 本地修复；不能将该修复的模拟浏览器通过结果替代真机丢回执验收。
4. 用户认为实验过重；已停止反复 DevTools、限速、断网或制造第三次保存。该项留为未验收。

## 5. 测试证据：时间与范围分开

### 5.1 最近一次产品修复：静态 HTTP 缓存策略

本节命令在报告前已经实际执行；生成报告本轮没有重跑。

```powershell
# 实现前：1 failed / 4 deselected / 2依赖警告。
# 实现后同命令：1 passed / 4 deselected / 2警告。
.\.venv-win\Scripts\python.exe -X utf8 -m pytest -p no:cacheprovider -o 'addopts=--strict-markers' -q tests/api/test_pwa_installation_f08.py -k fixed_ui_files

# exit0：29 passed、2依赖警告，23.18秒；包含上面1例，不另行加总。
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

反例覆盖实际静态 GET/HEAD、ETag 条件请求304、查询参数及安装资源；实现前响应头为None。原断言没有跳过或删除。对应修改只加HTTP缓存策略，没有更换公共身份或审批逻辑。

### 5.2 v14草稿输入保留：此前的定向结果

```powershell
# 正确收集后先红：2 failed；最小实现后同命令2 passed。
.\.venv-win\Scripts\python.exe -X utf8 -m pytest -p no:cacheprovider tests/integration/test_pwa_form_drafts_browser_f08.py -q -k 'failed_draft_reread or failed_network_read'

# exit0：21 passed、2依赖警告，55.26秒。
.\.venv-win\Scripts\python.exe -X utf8 -m pytest -p no:cacheprovider -o 'addopts=--strict-markers' -q tests/integration/test_pwa_form_drafts_browser_f08.py tests/integration/test_pwa_browser_f08.py tests/api/test_pwa_installation_f08.py

# 强化在途PUT反例后，exit0：9 passed，19.66秒。
.\.venv-win\Scripts\python.exe -X utf8 -m pytest -p no:cacheprovider -o 'addopts=--strict-markers' -q tests/integration/test_pwa_form_drafts_browser_f08.py

# exit0；两份JS语法和diff当轮也通过。
.\.venv-win\Scripts\python.exe -X utf8 -m ruff check --no-cache tests/integration/test_pwa_form_drafts_browser_f08.py
```

21、9与最新29有重叠，不能加总成新的独立用例数量。浏览器使用本地模拟服务：

- PUT写入后暂扣/丢弃回执；保存在途和未知状态遇到GET503都保留输入、基准、键与按钮状态；手动重试仍一条回执、一个新版本。
- 实际fetch中断保留未保存编辑；恢复后保存成功；之后返回不支持Schema仍拒绝编辑。
- 初次新测试有缩进导致收集失败，修正后才得到有效红测；Ruff曾报告新增长行，diff曾报告多余EOF空行，均修复后通过，未弱化断言。

### 5.3 更早的 F08.6 证据（历史，不代表当前快照全量通过）

| 时点/范围 | 实际记录 | 阅读位置 |
| --- | --- | --- |
| health边界初期 | /healthz/ 先红；相关API/设置73 passed，真实回环health1 passed等 | NEXT_STEPS“F08.6 本机预检” |
| 非部署准备工具 | 多个先红反例；准备/边界/health目标16 passed、2 warnings | NEXT_STEPS“环境准备协助” |
| 第一次授权发布前门禁 | scripts/test.ps1：1248 passed、130 skipped、7 warnings，751.26秒；摘要后另有Windows管道unraisable警告；Ruff/Mypy通过 | NEXT_STEPS“当前工作区发布门禁” |
| 同期数据库门禁 | scripts/test-postgres.ps1：130 passed、1 warning，348.69秒；临时*_test库、真实PostgreSQL/Worker，清理后未遗留新增库 | 同段loader与结果 |
| Manifest/安装资源 | 相关API/契约78 passed、5 warnings；390px浏览器17 passed | NEXT_STEPS安装资源修复 |
| Access 503说明 | 浏览器先红1 failed/1 passed；相关集合20 passed、2 warnings | NEXT_STEPS“Access不可用提示修复” |

历史全量使用干净子进程调用 `scripts/test.ps1`；数据库脚本通过受保护配置派生专用 *_test DSN并脱敏，没有把原assistant库作为测试目标。精确loader在 NEXT_STEPS。这些门禁早于后续安装资源、503说明、v14及HTTP缓存修复，**不能称当前未提交快照“全量全绿”**。

最近两轮修复按用户小范围要求只跑目标和相关回归，未重新运行完整全量或数据库/Worker脚本。真实TLS SMTP/Worker的先前F08.4独立通过属于前一阶段，不代表本次连接了真实邮箱。

## 6. 剩余项与风险

| 项目 | 当前状态/下一条件 |
| --- | --- |
| 手机实际使用v14 | 未确认。磁盘v14、已重载源站no-store、手机普通使用正常；既有CDN/浏览器旧副本根因与消除仍未证实。 |
| 真机响应丢失/同键手动重试 | 未验收；本地反例通过。停止重复真人实验，是否再测由审计/用户安排。 |
| 正式手机审批 | 未验收；当前库没有审批记录。nju.smail部署0.1.0与源码0.2.0不同，升级需另作精确材料确认；不能制造未经核验的权威动作。 |
| 审批/邮件等未访问场景的手机缓存 | 未验收；已有任务和PUBLIC草稿场景不能代替它们。 |
| 独立Cloudflare中断 | 未完成受控独立中断；已有Access取钥503模拟和主机断网反馈不等同于该条件。 |
| 真实Push订阅/投递/撤销/锁屏最小元数据/点击 | BLOCKED：VAPID两项未配置、生产秘密后端拒绝操作。Huawei无Google Play的实际浏览器Push能力也尚未测，不能提前保证。 |
| 主机恢复顶部提示 | 列表自动恢复，顶部需刷新；用户接受，保留体验限制，不扩展此次修复。 |
| 常驻与恢复 | 当前是临时后台进程；无开机自启、服务守护或备份恢复验收，这些不计入F08.6完成。 |

其他范围边界：

- PWA只与助手通信；邮件/ehall不直接接到手机。
- Agent自动路由、自动填表和自动发起邮件审批尚未接线，属于后续阶段，不因任务入队或F08.4审批界面可用而宣称已实现。
- F07仍IN_PROGRESS：正式真实事务Adapter、绑定实际提交目标和完整材料/后果的权威预览、正式Gateway/Outbox实站链路尚未验收。完整真实填报因没有确需办理的事务延期；没有制造申请或点击真实最终提交。
- F07试填预览不成为手机可批准的权威预览；本阶段没有绕过R2/R3、UNKNOWN或幂等规则。
- 未改迁移历史，未提交/推送；先前启动、DNS切换及此次公共重载均按记录中的用户授权实施。

## 7. 给独立审计 agent 的复验入口

1. 使用上述Desktop工作区核对HEAD、未跟踪文件和文末哈希；不要在旧worktree里审查。
2. 先对照AGENTS、阶段9、契约F08章节和本报告，分别判断“代码局部通过”与“F08.6完整条件通过”。
3. 下方为可选的当前相关定向命令，**报告没有执行此合并命令，不预测其通过数量**：

```powershell
.\.venv-win\Scripts\python.exe -X utf8 -m pytest -p no:cacheprovider -o 'addopts=--strict-markers' -q tests/unit/test_f08_environment_preparation.py tests/api/test_pwa_installation_f08.py tests/api/test_boundaries.py tests/integration/test_health_boundary_f08.py tests/integration/test_pwa_access_recovery_browser_f086.py tests/integration/test_pwa_form_drafts_browser_f08.py tests/integration/test_pwa_browser_f08.py
```

4. 当前原服务正在运行。默认只读检查；不要重复启动进程、写入原库、清理用户浏览器数据、修改Access/DNS或升级扩展。审计中需要实际材料、变更或新外部行为时，按已有确认边界处理。
5. 审计可关闭已修复局部问题，但剩余真机与Push条件缺失时不要把F08.6或F08整体标DONE。可建议拆开局部验收与依赖阻塞；实施顺序例外由用户决定。
6. `docs/NEXT_STEPS.md` 保留逐轮实际命令、失败尝试与用户反馈；旧条目按当时范围理解，当前摘要以本报告及最新条目为准。

## 8. 本次只读运行复核的精确命令

以下是报告生成本轮已运行的命令；只读HTTP/进程配置检查不是pytest或真机测试：

```powershell
@'
import json, subprocess
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

runtime = json.loads(Path('var/deployment/f08-access-2026-10-04/runtime.json').read_text(encoding='utf-8-sig'))
print(json.dumps({'public_pid': runtime.get('public_pid'), 'public_listener_pid': runtime.get('public_listener_pid'), 'health_pid': runtime.get('health_pid'), 'health_listener_pid': runtime.get('health_listener_pid'), 'tunnel_pid': runtime.get('tunnel_pid')}))
for name, url in (
    ('public_worker_without_jwt', 'http://127.0.0.1:8000/ui/service-worker.js'),
    ('public_tasks_without_jwt', 'http://127.0.0.1:8000/api/v1/tasks'),
    ('minimal_health', 'http://127.0.0.1:8010/healthz'),
    ('tunnel_ready', 'http://127.0.0.1:20241/ready'),
):
    try:
        try:
            response = urlopen(url, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            result = {'name': name, 'status': response.code, 'cache_control': response.headers.get('Cache-Control')}
            if name == 'minimal_health' and response.code == 200:
                result['minimal_ok'] = json.loads(response.read(200)) == {'status': 'ok'}
            print(json.dumps(result))
    except Exception as error:
        print(json.dumps({'name': name, 'error_type': type(error).__name__}))
result = subprocess.run([r'C:\Program Files\Tailscale\tailscale.exe', 'serve', 'status', '--json'], capture_output=True, text=True, timeout=8)
if result.returncode == 0:
    config = json.loads(result.stdout)
    backends = []
    for server in config.get('Web', {}).values():
        for handler in server.get('Handlers', {}).values():
            if 'Proxy' in handler:
                backends.append(handler['Proxy'])
    print(json.dumps({'private_serve_backends': backends, 'funnel_any_enabled': any(config.get('AllowFunnel', {}).values())}))
else:
    print(json.dumps({'serve_status_exit': result.returncode}))
'@ | .\.venv-win\Scripts\python.exe -X utf8 -B -
Get-NetTCPConnection -State Listen -LocalPort 8000,8001,8010,20241 -ErrorAction SilentlyContinue | Select-Object LocalAddress,LocalPort,OwningProcess | ConvertTo-Json -Compress
```

同轮还执行git branch --show-current、git rev-parse HEAD、git status --short、git diff --stat以及目标代码差异读取；报告写入后执行git diff --check。没有把这些命令计入通过用例数。

## 9. 关键工作区文件 SHA-256

以下17项摘要已在第10节P2修复后更新并复核；不包含含秘密的忽略配置，不对后续修改自动保持有效。


| 文件 | SHA-256 |
| --- | --- |
| pyproject.toml | 89667a70af3cfe56c185ece40e647aced512a88dfe640c5accbeb42a2390dfaf |
| src/personal_assistant/api/middleware/security_headers.py | 0204af562c12329185a38011643736eab896dada83d93ef02ef7fa05d76941fc |
| src/personal_assistant/health_app.py | f3b92746b484b7b2a8d63df22637e3d55f5484fbad351ba298548649e6b75501 |
| scripts/prepare_f08.py | 514a17f5e02ad446fa7beaaff9fca153cddba4214ddde44f70f83d61b1402246 |
| tests/api/test_boundaries.py | c5296b5c233d5b71056fd58246c6f4bbaf280a906d7c716e6fc9ad821b546060 |
| tests/api/test_pwa_installation_f08.py | 59cbf4f57bf03d4a66a5bd97c78b8066a226bbc4f344fce978774e7ec2394fa0 |
| tests/unit/test_f08_environment_preparation.py | 1bd3350e106c62c7c33cfcf783346a7af8c4b3b7da9c5a94325aa7a53d4f57a9 |
| tests/integration/test_health_boundary_f08.py | 9b15f5b78fc4b30a7da03a76b09af374ef0596d38db8035d6e3ad28e0df5563f |
| tests/integration/test_pwa_access_recovery_browser_f086.py | 33f300b02dd2966d061fe48b4f7e61bc2fba9534d0847f1df64639cd915aeb9d |
| tests/integration/test_pwa_form_drafts_browser_f08.py | 586e5418e1bf80fcb23777100c3b3c5bade783f9e53048c5a90e3f1a9ac5d702 |
| tests/integration/test_pwa_browser_f08.py | 97f721b343a7a1c461f59276b3a637be9844feda02b54bcc746a7ee7689e948c |
| web/pwa/app.js | 02d1870a6e948603ace0db4b301af4e29b8c6a60f7f3879fef4ee586516843ae |
| web/pwa/index.html | f09d1ee5ae5a18897acd54b65d5affaa9182bacd21050aed9392f2d27ec3e748 |
| web/pwa/manifest.webmanifest | 6905c66ab56b2c91226c67cb129b949f296c5345dd1a773ea77be3fa1873c34c |
| web/pwa/service-worker.js | 5400750f4c678d4a29db606404e7931d7d508c4f314eff768a79544b171e8b0e |
| web/pwa/icon-192.png | d9678ecb0cdb41364b5beb47b579f3e62754b1bb8900f4cbf488e8cd735773f8 |
| web/pwa/icon-512.png | 2d825fdbc5c2191e7ea557678dbb993a38a40f9448281f4513e9dc8ab7ff3d27 |

本次报告收尾：报告已写入，17 个源码/测试/资源 SHA-256 已逐项复核一致；报告生成后的 git diff --check 实际 exit0（仅已有 LF/CRLF 提示）。本轮未运行测试集合，不将只读或文档检查计入 passed 数量。

## 10. 独立复验后：旧回执覆盖新草稿的 P2 修复

### 10.1 独立复验输入

用户报告原合并定向命令 40 passed、2 warnings，新增组合反例 1 failed：原保存已到2但回执丢失，另一设备保存3，手机显式采用3后再同键重试，页面退回2。服务端3未覆盖，也未重复入库。用户还报告原17份摘要吻合、wheel 228条目（7份PWA资源/11份迁移、不含扩展源码）及认证/回环/health/Tailscale等检查通过；全量启动后中断，无完整结果，完整PostgreSQL/Worker脚本未复跑。

以上为**用户提供的独立审计证据**，本修复轮没有重跑该40例合并命令、wheel检查、全量或真实数据库/Worker；不能把30例定向结果与40例相加，也不能据此称阶段通过。

### 10.2 本轮改动与验收条件

产品修改仅限 web/pwa/app.js 与 web/pwa/service-worker.js：

- 同键重试继续发送原版本、原值和原键；用户显式采用服务器草稿时不清除未确认请求。
- 完整回执仍按原请求验证。回执只证明原命令完成，先比较页面已知的当前基准/冲突服务器版本。
- 已知版本高于回执版本时，保留当前基准、字段值、dirty与冲突选择，提示“原保存请求已确认”；不将旧回执称为当前草稿已保存。
- 没有更高已知版本时才采用回执；可以清除与该回执对应的冲突状态。
- 渲染失败恢复回执处理前的编辑状态与原请求，继续进入结果未确认，不提前丢弃键。
- 确认旧命令后，显式保留的本机输入仍未保存；再次由用户点击保存才生成绑定新版的新命令。采用服务器值后无脏输入，再点保存不会写入。
- 磁盘Worker升到v15，仍只有原四条静态缓存路径，无API/正文缓存、后台自动重试或外部副作用。未重启或重新配置原服务，手机是否收到v15尚未验收。

新增测试位于 tests/integration/test_pwa_form_drafts_browser_f08.py，全部使用390px真实Chromium＋本地模拟助手HTTP服务：

1. 丢回执→第二客户端实际PUT到3→采用服务器3→同键重放2；页面保持3与第二客户端值，刷新仍为3。
2. 原PUT回执暂扣→第二客户端PUT到3→采用3→原回执迟到；页面保持3。
3. 丢回执后显式保留本机输入、以3为基准→重放原2；仍保持基准3和未保存输入，下一次手动保存才用新键、版本3写到4。

测试同时断言原重试请求完整一致、服务端不回退、命令回执数不增加；不能用删除原键或禁止重试使反例变绿。新增测试在实现前均失败于“页面实际为版本2、预期3”，没有跳过或删除断言。

### 10.3 本轮精确命令与实际结果

工作目录为报告开头的Desktop仓库，HEAD仍为 deeb9b2aa65481a3eeefdcbdb88cfeebef45d672；保留之前所有未提交文件。

```powershell
# 实现前：exit1，3 failed、9 deselected，23.71秒。
# 最小实现后同命令：exit0，3 passed、9 deselected，8.77秒。
.\.venv-win\Scripts\python.exe -X utf8 -m pytest -p no:cacheprovider -o 'addopts=--strict-markers' -q tests/integration/test_pwa_form_drafts_browser_f08.py -k 'keeps_adopted_newer_server_draft or keeps_local_choice_on_newer_draft_version'

# exit0：30 passed、2依赖弃用警告，68.22秒；包含上述3例。
.\.venv-win\Scripts\python.exe -X utf8 -m pytest -p no:cacheprovider -o 'addopts=--strict-markers' -q tests/api/test_pwa_form_drafts_f08.py tests/api/test_pwa_installation_f08.py tests/integration/test_pwa_form_drafts_browser_f08.py tests/integration/test_pwa_browser_f08.py

# exit0：All checks passed。
.\.venv-win\Scripts\python.exe -X utf8 -m ruff check --no-cache tests/integration/test_pwa_form_drafts_browser_f08.py
# 两项均exit0。
& 'D:\nodejs\node.exe' --check web/pwa/app.js
& 'D:\nodejs\node.exe' --check web/pwa/service-worker.js
# exit0，仅已有LF/CRLF提示；文档更新后再检查。
git diff --check
```

30例警告为Starlette/httpx和anyio别名弃用。该改动只处理前端回执/显示状态，没有改服务端共享状态、CAS、数据库、审批、安全身份边界或迁移，因此遵守局部修复验证范围，没有重新做阶段发布门禁或全量/真实环境回归。

本轮修改文件：web/pwa/app.js、web/pwa/service-worker.js、tests/integration/test_pwa_form_drafts_browser_f08.py、TODO.md、docs/NEXT_STEPS.md、docs/CONTRACTS_AND_INTERFACES.md，以及本报告。第9节仍列17个关键文件，已更新此次3个源码/测试文件的摘要，其余14项保持不变；首次审计验证的原摘要结论仅适用于修复前快照。

### 10.4 当前结论与下一步

本次P2已通过用户提供的局部独立复验并关闭（见第10.5节），**F08.6继续BLOCKED，F08与F07整体继续IN_PROGRESS**。下一步仍补齐F08.6剩余验收证据，不开始F09.1或后续编号。

尚缺手机运行当前v15（此前v14亦未确认）、真机丢回执/同键重试、正式单次审批与相关敏感缓存、独立Cloudflare中断、真实Push完整流程。既有临时部署与顶部提示需手动刷新的已接受限制继续保留。本轮没有真实发件、ehall操作、配置/迁移变更、提交或推送。

### 10.5 P2 局部独立复验通过（2026-10-08）

用户明确确认本次P2局部修复可关闭，本轮没有发现新的局部阻断问题。以下均为**用户提供的独立复验结果**，本轮实施者只作记录，未重跑：

- 报告第10.3节的定向回归：30 passed、2 warnings。
- 额外组合反例：3 passed，覆盖原问题、坏回执与渲染失败后的同键重试，以及迟到回执保留未处理冲突。
- Ruff、两份JavaScript语法、差异检查通过；17项文件摘要全部吻合。
- 测试浏览器已回收，原有服务未受影响；用户未改业务代码、重启服务、提交或推送。

局部结论：旧回执仅确认原请求，不覆盖已选择的新草稿；保留的本机输入仍须用户再次明确保存。P2关闭，F08.6整体验收状态仍为BLOCKED，F08与F07整体仍为IN_PROGRESS。

缺口仍是手机实际运行v15、真机丢回执/同键重试、正式单次审批及相关缓存、独立Cloudflare中断和真实Push完整流程。独立复验没有运行全量、PostgreSQL/Worker或真实站点；本次记录也未新增这些测试证据，不合并/复用旧数字宣称全量通过，不推进F09.1。
