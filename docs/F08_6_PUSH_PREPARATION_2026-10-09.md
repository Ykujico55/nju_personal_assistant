# F08.6 本机 Push 准备交接（2026-10-09）

## 当前状态与暂停点

F06.P2、最小授权 F09.1 已独立通过并为 DONE；唯一后续验收项 F08.6 仍为 BLOCKED，F07/F08 整体仍 IN_PROGRESS。

用户先要求“开始下一步”，随后要求“先暂停在需要我配合的地方之前”。现已完成本机密钥、配置和后台恢复，停在手机开启通知之前。没有请求手机操作或投递真实通知，也未启动其他 F09 功能、真实发信或 ehall 操作。

## 本轮实际完成

- 原助手数据库只读连接成功；启动前 Push 订阅 0 条、审批 0 条。已安装且启用的目录为 example.echo@0.1.0、nju.smail@0.1.0，没有安装或升级扩展。第一次目录盘点误用不存在的 version/state 字段，返回 UndefinedColumnError；按实际迁移改读 active_version/lifecycle_state 后成功，未修改数据库。
- 通过既有 SecretStorePort 生成 P-256 VAPID 密钥并调用真实 Windows Credential Manager put。私钥没有写入配置、日志、命令参数、数据库、RPC 或模型输入。新公钥材料只含 public_origin、public_key、secret_handle、allowed_origins；未将原库 DSN 复制到新文件。
- 仅向原忽略配置 `.env.template` 追加 PA_PUSH_VAPID_PUBLIC_KEY、PA_PUSH_VAPID_SECRET_HANDLE、PA_PUSH_ALLOWED_ORIGINS，未改变数据库连接、Access 团队/AUD、发信或浏览器提交开关。当前精确白名单为 `https://fcm.googleapis.com`，依据 Chrome 使用 FCM 的[官方说明](https://developer.chrome.com/docs/extensions/how-to/integrate/web-push)及[官方端点示例](https://web.dev/articles/codelab-notifications-push-server?hl=en)。实际手机是否能创建订阅仍待验证，没有放宽到任意 origin。
- 在新的独立宿主进程经生产 PostgreSQL 组合根验证 configured_public_key 返回同一公钥，未调用数据库 startup 或发送网络通知；F06 三份已验收代码/测试摘要仍吻合。
- 启动前回环 8000/8001/8010/20241 均未连接成功。沿用既有明确启动/接通授权，隐藏窗口恢复助手与 health，再恢复既有 Tunnel；没有改 DNS、Access 策略、Tailscale 或注册服务守护。
- 当前 netstat 仅见 127.0.0.1:8000、127.0.0.1:8010、127.0.0.1:20241；8001 无监听。发信与浏览器提交为 false。

本机材料位于 Git 忽略的 `var/deployment/f08-access-2026-10-04/`：

| 材料 | 内容/边界 |
| --- | --- |
| .env.template | 原已授权临时 DSN；本轮仅追加公钥/句柄/白名单 |
| push-public.json | 公钥/句柄/精确 origin，无私钥或 DSN |
| runtime-push-2026-10-09.json | 本次 PID、日志路径、监听与暂停记录，无秘密 |
| public-push-*.log、health-push-*.log、tunnel-push-*.log | 本次恢复日志，保留旧日志 |

旧 runtime.json 的 PID 是历史记录；本次观察以新记录为准，不按旧 PID 关闭进程。

## 本轮实际命令与结果

工作目录为 `C:\Users\kunji\Desktop\homework\生成式软件工程\lab1`。

```powershell
.\.venv-win\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts=--strict-markers -q --tb=short tests/integration/test_windows_credentials_native_f09.py tests/unit/test_web_push_service_f08.py tests/unit/test_web_push_transport_f08.py tests/api/test_pwa_push_f08.py
```

实际为 **13 passed、2 warnings，3.22 秒**。包含真实 Windows 合成凭据跨进程/清理检查和 VAPID 接线；Push 发送替身不算真实投递。

实际后台启动使用 PowerShell Start-Process、WindowStyle Hidden、指定工作目录、清理继承的 PA_/PG/DATABASE_/POSTGRES_ 设置、UTF-8 和独立日志。子进程参数如下（省略外层可变日志路径，env-file 实际传绝对路径）：

```powershell
.venv-win\Scripts\python.exe -X utf8 -B -m uvicorn personal_assistant.app:app --env-file "var/deployment/f08-access-2026-10-04/.env.template" --host 127.0.0.1 --port 8000 --no-access-log
.venv-win\Scripts\python.exe -X utf8 -B -m uvicorn personal_assistant.health_app:app --host 127.0.0.1 --port 8010 --no-access-log
& 'C:\Program Files (x86)\cloudflared\cloudflared.exe' tunnel --config "var/deployment/f08-access-2026-10-04/cloudflared.template.yml" run 675e44a7-276e-4d59-ab87-ceb7c5ad02ee
```

两应用日志均有 Application startup complete，无 startup failed。只读探测结果：

| 入口/条件 | 实际结果 |
| --- | --- |
| public 8000，无 JWT：/ui/、/api/v1/tasks | 401、401 |
| health 8010：/healthz | 200，仅 {"status":"ok"} |
| health 8010：/api/v1/tasks、/api/v1/approvals、/admin/v1/extensions、/docs、/openapi.json、/、/healthz/ | 7 项均 404 |
| cloudflared metrics /ready | 200 |
| httpx 匿名、不跟随重定向：公网 /ui/、/api/v1/push/config | 均 302，Location 仅投影主机后确认是既有 Access 团队 |
| urllib 首次匿名探测同两路径 | 均 403；未定位客户端差异，不把它当作手机登录失败或 Access 策略已失效 |
| tailscale status --json / tailscale serve status --json | NoState、无在线自身/peer、无 tailnet；Serve {}，未恢复私有入口 |

没有读取 Cookie/JWT、跟随登录或绕过 Access。302 与 Tunnel ready 不代替已登录手机到真实 API 的证明。Tailscale 结果只描述当前 CLI 观察；未声称移除既有 tailnet Funnel 能力或完成本轮私有入口验收。

收尾实际执行：

```powershell
& 'D:\nodejs\node.exe' --check web/pwa/app.js
& 'D:\nodejs\node.exe' --check web/pwa/service-worker.js
.\.venv-win\Scripts\python.exe -m pip check
git diff --check
```

两份 JS 语法检查 exit 0，pip check 为 No broken requirements found，差异检查 exit 0（只有既有 LF/CRLF 提示）。新交接文件另做逐行尾部空白检查通过。

产品源码本轮没有新增改动；原 SMTP 修复保持已验收字节。本轮没有重跑全量或完整 PostgreSQL/Worker 套件，恢复基线采用此前用户独立通过的同一实现（1301 passed/130 skipped/9 warnings、PostgreSQL/真实 Worker 130 passed/1 warning），不冒充本轮结果或宣布阶段通过。

## 继续时的顺序与未完成项

1. 手机恢复后，从既有入口确认当前页面正常，再开启通知并由本人允许系统权限；实际订阅失败时保留真实错误，不能靠手动录入或模拟订阅宣称通过。
2. 有真实有效订阅后，再向本人发送最小元数据自检，核对接收、锁屏、点击、撤销；点击只能打开助手任务，不能批准或执行副作用。
3. 补手机实际 v15、真机丢回执同键重试、正式单次审批及相关敏感缓存、独立 Cloudflare 中断证据。原库没有审批记录，不制造真实申请或直接发送邮件。
4. 当前 Tailscale 尚需恢复本人设备登录/在线后复核 health-only；不以任意业务端口代理或 Funnel 替代。

Service Worker 磁盘为 v15，仍仅缓存四条固定 /ui/ 静态路径；未操作手机，不能记作手机 v15 已通过。华为设备无 Google Play 的既有反馈保留，不能据此猜测真实订阅一定成功或一定失败。原库临时 DSN 文件例外仍未迁移。

本轮修改 README.md、TODO.md、docs/NEXT_STEPS.md 和本交接文档，以及上述忽略的本机材料；不提交、不推送。下一编号仍为 F08.6，暂不启动其他 F09。

## 续接：原库与后台恢复、手机订阅待反馈（2026-10-09）

用户“继续”撤销此前停在手机配合前的暂停。本轮没有新增产品源码或 pytest/全量/完整 PostgreSQL/Worker 测试，前文 13 passed 保持此前结果。

- 当前探测先发现四个回环端口均不连接；VAPID宿主解析仍成功。首次恢复 public 的可跟踪运行会话 exit3，日志异常类型 ConnectionRefusedError；原库只读连接同样失败，docker ps 报 Linux engine pipe不存在。保留 public-push-resume-20261009.err.log，不归因于凭据失败、不要求重新录入密钥。
- 实际执行 docker desktop start --detach 返回 already running；随后 docker desktop status为running、docker context show为desktop-linux、docker ps -a显示原lab1-postgres-1已healthy。没有执行compose up、创建库/容器/卷、改密码或迁移历史。
- 用前文相同uvicorn与Tunnel参数恢复现有三个后台，此次直接在可跟踪运行会话中运行并重定向至新的resume日志，未注册服务守护。原库已授权的启动恢复沿用；没有安装/升级扩展或启用发信/ehall提交。当前netstat仅见127.0.0.1的8000、8010、20241。
- 实际httpx只读检查：health/healthz 200、Tunnel/ready 200、public/api/v1/push/config无JWT为401，匿名公网/ui/302且Location只投影主机后确认为既有Access团队。原库read_only=on，push_subscriptions总数0、指定PUBLIC自检任务匹配1。没有读取Cookie/JWT/订阅端点或输出正文。
- 当前重定向应用日志为空，不能记录新的Application startup complete标记；运行状态以当前HTTP与监听观察为证。前文两个成功标记是上一轮Start-Process的历史证据，没有拿空日志充当本轮通过。
- tailscale status现为Running、自身online、在线peer0；serve status显示唯一根路径代理http://127.0.0.1:8010且AllowFunnel=false。只是只读观察，未改Tailscale配置或宣称当前手机私有入口已复验。

实际续接命令包含：

```powershell
docker desktop start --detach
docker desktop status
docker context show
docker ps -a --format '{{.Names}}|{{.Status}}|{{.Ports}}'
```

恢复进程仍用前文列出的三条参数，在隔离继承配置的工作目录执行。最后以httpx（7秒超时、不跟随重定向、不信任环境代理）、asyncpg原库只读事务和netstat进行上述状态投影；这些是运维检查，不是测试套件。

已请求本人从桌面打开助手，普通刷新、重读订阅状态、只点一次开启通知并本人允许权限；反馈成功/错误/一分钟仍pending均按真实情况记录。已收到本人反馈：“通知权限未授予”。代码在Notification.requestPermission结果不是granted时即返回，本次尝试未进入浏览器subscribe或订阅POST；不能只凭该提示区分default/denied或认定设备缺少GMS。已核对Google Chrome与华为官方的权限设置说明，下一步由本人检查单站通知权限及Chrome系统通知开关。未发送自检通知，未新增测试结果。下一步只在确有真实有效订阅、本人准备好后继续最小通知投递/锁屏/点击/撤销；F08.6仍BLOCKED。

### 手机订阅结果未确认（2026-10-09 23:42，北京时间）

本人确认 Chrome 应用通知已允许，助手单站权限却显示“不允许”；按单站设置步骤继续后，实际反馈“订阅结果未确认，可手动重试同一浏览器订阅”。现有统一 catch 同时覆盖浏览器 subscribe 和 API 保存/回读，不能把该提示直接记为已创建浏览器订阅、API已收到请求或GMS失败。

实际运行 .venv-win\Scripts\python.exe -X utf8 -B -，从标准输入执行只读诊断：清理继承的 PA_/PG/DATABASE_/POSTGRES_ 设置、载入原忽略配置，不输出配置；宿主 configured_public_key 与配置公钥一致；asyncpg 事务 readonly=True 后 SHOW transaction_read_only 为 on，SELECT count(*) FROM push_subscriptions 与 SELECT count(*) FROM push_subscription_commands 均为0，max(created_at)为空。httpx 7秒超时、不跟随跳转、不信任环境代理：public无JWT /api/v1/push/config 为401，独立health /healthz为200，Tunnel /ready为200。只输出数量、状态码与错误类型，不读取/输出Cookie、JWT、订阅端点或浏览器密钥；没有调用notify_pending。

已请求保留当前手机页面，只再点击一次开启通知；已生成的原幂等键按现有逻辑保留，不刷新、撤销订阅或清站点数据。结果未确认继续保留，真实Push尚未通过；本次无产品代码/新测试/进程重启、提交或推送。F08.6保持BLOCKED。

本人保留页面手动重试一次后的实际反馈：短暂显示正在订阅，随后仍为订阅结果未确认。没有把重试或后台健康当作成功证据；下一步仅要求手机远程Network观察POST是否出现与状态码，不展开Payload、Cookies或完整Headers。此前23:42的原库0/0是该诊断时刻快照，重试后未再次查询，不冒充重试后的数据库结果。本次继续只读核对请求和中间件，不改业务代码、原键、白名单、认证或后台进程。

### USB 诊断续接（2026-10-09）

本人在手机远程Network定位前反馈Chrome再次显示Pending authentication。沿用此前已授权的官方工具 var/deployment/f08-access-2026-10-04/diagnostics/android-platform-tools-2026-10-05/platform-tools/adb.exe，实际执行 devices -l，原输出仅在宿主进程内解析、未输出设备序列号。结果ExitCode=0、DeviceCount=1、States=[device]；仅查询授权状态并在需要时启动原本机ADB服务，未读取手机内容或强制授予权限。下一步由本人让电脑Chrome重新读取Discover USB devices，保留手机订阅未确认的原页面。该结果只证明此ADB连接已获授权，不能证明Chrome当前Inspect已恢复、浏览器已建订阅或POST成功；F08.6仍BLOCKED。
### 手机Network反馈没有POST（2026-10-10）

本人在USB诊断续接后反馈没有POST，即此次未观察到 /api/v1/push/subscriptions 创建请求。该用户观察限定于本次点击，不把10-09的数据库0/0或后台健康值冒充10-10重新检查结果。失败发生于保存请求发出之前；现有代码也可能在getSubscription、状态读取、subscribe、材料检查或摘要计算失败，所以暂不能直接确定GMS或PushManager原因。

已核对Chrome官方异常断点说明，并请求本人在手机远程Sources启用Pause on caught exceptions后点击一次开启通知，仅读错误名称/提示和高亮代码，避免展开Scope、Cookies、请求材料或返回订阅对象。实际异常仍待反馈；此次无产品源码改动、新测试、后台重启、通知投递、提交或推送，F08.6保持BLOCKED。此前完整测试及定向数字不算本轮重跑。
### 实际浏览器注册错误与缺失GMS依赖（2026-10-10）

全局捕获异常暂停先停在app.js第36行的form draft not found；该事件属于没有草稿时的读取路径，不能当作Push失败原因。取消全局捕获后，把断点限定到通知专用catch里的结果未确认提示（当前磁盘源码1226行，手机按文字定位），本人在暂停帧读取错误名/提示，实际为：

```text
AbortError: Registration failed - push service error
```

结合本人此前Network没有POST的观察，订阅保存请求尚未发出，未记为服务器已收到或持久订阅成功。没有把未确认回执清空、制造订阅或发送测试通知。

实际ADB诊断仅投影状态和包是否存在，使用原官方工具；设备1台、状态device。针对GMS的初次pm path查询exit1/present=false，再以系统和Chrome作为对照检查：

```powershell
$taskAdbPath = Join-Path (Get-Location) 'var/deployment/f08-access-2026-10-04/diagnostics/android-platform-tools-2026-10-05/platform-tools/adb.exe'
& $taskAdbPath devices -l
& $taskAdbPath -d shell pm path android
& $taskAdbPath -d shell pm path com.android.chrome
& $taskAdbPath -d shell pm path com.google.android.gms
```

上述命令实际在宿主循环中执行，原输出在进程内解析，仅打印JSON投影；未输出设备序列号/安装路径或读取手机用户内容。android：exit0/present=true/输出1行；com.android.chrome：exit0/present=true/输出21行；com.google.android.gms：exit1/present=false/输出0行。对照证明包管理查询可用，并确认当前连接设备没有GMS包。没有枚举其他应用、安装GMS、修改手机权限或读取日志/Cookie/JWT。

核对的主来源：

- [Chromium错误映射](https://raw.githubusercontent.com/chromium/chromium/main/third_party/blink/renderer/modules/push_messaging/push_messaging_utils.cc)：该文字映射SERVICE_ERROR并产生Abort，属于通用注册服务错误，单独不能证明唯一底层成因。
- [Chromium Android注册实现](https://raw.githubusercontent.com/chromium/chromium/main/components/gcm_driver/android/java/src/org/chromium/components/gcm_driver/GoogleCloudMessagingV2.java)：Android注册检查com.google.android.gms，缺失时抛异常；[GCMDriver调用](https://raw.githubusercontent.com/chromium/chromium/main/components/gcm_driver/android/java/src/org/chromium/components/gcm_driver/GCMDriver.java)注册异常后返回失败。
- [Chrome官方Push说明](https://developer.chrome.com/docs/extensions/how-to/integrate/web-push)：Chrome使用FCM作为浏览器Push服务，由浏览器选择；当前PWA配置不能直接切换为任意手机厂商推送。

结论：真实浏览器注册已失败，当前设备缺少该Android Chrome链路的必需组件，是明确环境依赖阻断，且与实际错误吻合；没有取得原生内部日志，不宣称排除了全部服务错误原因。停止在相同配置上继续重试；请求本人移除此次通知断点并F8恢复原页面。若有可借用且Google Play服务正常的Android设备，可继续原授权Push验收；只有华为时需要核对支持该环境的浏览器/通知通道，未经范围确认不新增HMS/原生App或替换通信架构。既有任务/消息/草稿证据保留，真实Push接收/锁屏/点击/撤销未通过，F08.6仍BLOCKED、F07/F08整体IN_PROGRESS，其他F09不启动。

本次只更新状态文档并做上述只读设备/官方资料检查；无产品代码修改、新pytest/全量/PostgreSQL/Worker测试、助手后台重启、真实邮件/ehall、提交或推送。收尾git diff --check单列本次结果，不复用此前测试数字。

### 固定的无 Google 约束与候选通道（2026-10-10）

本人明确“没有，且永远不能使用google play”。此为固定产品约束；上节借用具备 Google 服务设备的建议已撤回，不再要求借机、安装 GMS 或继续反复点击 Chrome 注册。现有任务、消息与草稿的验收证据保留，F08.6 仍为 BLOCKED，F07/F08 整体仍为 IN_PROGRESS。

候选方案是 **ntfy 无 Firebase Android 客户端 + 助手主机上的通知服务**。这改变当前 Web Push 的交付方式，属于 AGENTS.md 操作顺序第3条要求先说明的范围调整，尚未授权实施，也不是新增 NEXT 或已通过的手机能力。

- 手机额外安装官方 F-Droid flavor，仅收存在待办、风险等级和非敏感任务 ID；不收正文、附件、审批 nonce 或凭据。点击仅打开固定 HTTPS 助手页面，身份验证和显式审批仍由原助手处理。邮件、ehall 和任务处理仍以助手为中心。
- 主机端候选实现只接现有 `PendingNotifier.notify_pending(task_id, risk)`（`core/tools/gateway.py:104–105`），新增通用通知适配器及必要配置，不把 ntfy 伪装成 Web Push 订阅。具体变更文件与测试先在授权后核对；不改变审批、Outbox、Schema 或外部执行契约。
- 自建服务仅监听回环；远程接收入口继续受 Cloudflare Access 保护。单独的设备凭据只允许读取通知，不能访问任务/审批/Admin 或发布通知；宿主凭据经现有 SecretHandle 后端。需要单独审查接收入口、撤销及最小元数据存储，不能把原站放行或将 Tailscale 改成业务入口。
- 无 Firebase 客户端通过前台服务维持连接；需要实测这台 HarmonyOS 手机的后台、锁屏、网络切换与撤销。收到文档支持不能替代真机验收。适配器先写失败反例；跨模块接线时依 AGENTS.md 运行所需门禁，之后才准备实际入口与安装步骤。

核对的主来源：[ntfy 手机说明](https://docs.ntfy.sh/subscribe/phone/#instant-delivery)明确 F-Droid flavor 不使用 Firebase、以常驻服务接收；[当前官方 v1.25.2 构建声明](https://raw.githubusercontent.com/binwiederhier/ntfy-android/v1.25.2/app/build.gradle)将 Firebase 依赖限制为 play flavor。[自定义请求头](https://docs.ntfy.sh/subscribe/phone/#custom-headers)支持认证代理，但待选 APK 仍须在准备安装时核对具体版本和能力；[Cloudflare Service Token](https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/)提供设备认证方式；[ntfy 访问控制](https://docs.ntfy.sh/config/#access-control)有只读权限，[点击行为](https://docs.ntfy.sh/publish/#click-action)可仅打开 URL。

本轮为方案与状态文档更新，未下载、安装或执行 ntfy，未创建令牌、修改 DNS/Access/Tunnel、发送通知或重启服务；未修改产品源码、运行新 pytest/全量/PostgreSQL/Worker 测试，未提交、推送或部署。此前测试数字不作为本轮结果。等待对上述最小通知通道范围的明确确认；实现与独立验收仍未完成。


### 最小 ntfy 主机接线已获授权（2026-10-10，本轮续接）

本人随后明确批准“允许最小接线，手机步骤前暂停”。上节“尚未授权”的候选状态属于批准前的历史记录；现已实现默认关闭的本机发布适配器、秘密句柄与组合根接线，并补充本机反例测试。新轮精确命令及范围见[主机接线报告](F08_6_NTFY_LOCAL_REPORT_2026-10-10.md)，不将此处旧 Web Push 或诊断数字当作新实现结果。真实 ntfy 未安装启动，手机及接收入口尚未接入；F08.6 仍 BLOCKED，停在需要本人安装应用或配置外网入口之前。
