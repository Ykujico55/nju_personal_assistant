# F08.6 环境准备指南

F08.6 仍为 `BLOCKED`：用户已完成 `yanshuheng.dpdns.org` 域名与单邮箱 Access 保护。发布门禁通过后，经明确授权启动原助手与独立 health、应用 0008–0011 迁移并接通命名 Tunnel；入口为 `https://yanshuheng.dpdns.org/ui/`。私有 Tailscale Serve 已只代理独立 health 8010，`AllowFunnel=false`。无认证本机/边缘及同节点私有 HTTPS 黑盒通过；Android首次登录/SSE、私有health三项，随后Chrome标识桌面入口无地址栏启动/空列表/事件连接及Wi-Fi切至移动数据后恢复均有本人反馈。断网明确提示、恢复联网，以及后台或锁屏约一分钟后的恢复也已有本人确认；手机任务/消息刷新恢复及电脑页面新增任务/消息自动同步获本人反馈，先前自检记录获原库只读核对；任务/消息场景CacheStorage四路径与任务GET的200/no-store已获手机证据；本人已对精确材料授权，example.echo@0.1.0安装启用成功，手机草稿来源未确定→用户输入、基准版本2及刷新恢复同一测试值已有反馈；随后手机截图确认冲突时保留本机输入并显示服务器3，显式保留后保存4/刷新恢复已有本人确认及原库只读核对；草稿GET的200/no-store与v12四静态缓存已有截图；随后新截图确认唯一缓存升级v13、仍仅四静态路径；手机PUT409/412、审批、其余缓存与跨设备路径仍待验收，真实Push尚缺生产秘密后端。F08.5 本地验收不变；F07/F08 整体仍 `IN_PROGRESS`。下文保留准备步骤与当时边界，实际部署证据由 `docs/NEXT_STEPS.md` 记录；不要重复注册 Tunnel、覆盖配置或无条件再次启动进程。

发布前快照（2026-10-04）：确认 `cloudflared 2026.9.3` 与 `Tailscale 1.102.4` 均已安装。本人完成 Cloudflare 官方授权，预注册未启用的 Tunnel 并通过静态 ingress 检查；当时没有 DNS 路由或 Tunnel 连接。Tailscale 后端为 `Running`，电脑及一个 Android 节点在线，Serve/Funnel 映射为空。Docker 引擎运行，原 `lab1-postgres-1` 健康且只发布 `127.0.0.1:5432`。用户明确选择沿用原助手数据库；仓库现成默认配置已从 Windows 宿主完成只读 `SELECT`，确认连接 `assistant` 库。随后用户明确授权临时本机 DSN 配置及口令更换：新随机口令仅保存在指定候选，权限仅本人/SYSTEM；新连接、旧口令拒绝及候选安全检查通过，数据库 OID 未变。当时未启动助手或迁移原库；当前已授权启动的状态以上段和文末为准。

## 1. 先由本人准备账号与固定域名

1. 打开 [Cloudflare 官方注册页](https://dash.cloudflare.com/sign-up)，完成账号注册、邮箱验证与本人登录。
2. 选定愿意长期使用的域名及购买预算。域名名称、费用和购买确认由本人决定；已有域名可以接入 Cloudflare，无需为了本阶段另购域名。若需购买，可从 [Cloudflare Registrar](https://domains.cloudflare.com/) 核对可用性与价格；不以任何示例名称为已购买域名。
3. 在 Cloudflare Registrar 直接购买的域名自动使用 Cloudflare DNS；在其他注册商购买的域名则添加到 Cloudflare，并按页面要求修改 nameserver，等待活动状态。涉及既有 DNS 时先核对记录，避免影响已有服务。[官方域名接入指南](https://developers.cloudflare.com/dns/zone-setups/full-setup/setup/)
4. 选定固定主机名，例如直接使用 `yanshuheng.dpdns.org`，或另选 `assistant.<你的域名>`。Origin 只含 `https://<固定主机名>`；手机 PWA 打开同域名的 `/ui/`，该路径不进入 Origin 配置。

对于当前新空域名：在 Review DNS records 页面若要求先添加记录，可添加临时 `A @ 192.0.2.0`、Proxied（橙云）、TTL Auto，然后继续取得两条实际分配的 `*.ns.cloudflare.com`。这是 [Cloudflare 官方支持的无源站占位](https://developers.cloudflare.com/dns/manage-dns-records/how-to/create-dns-records/#originless-setups)，没有连接本机助手。到 [DigitalPlat 域名管理](https://github.com/DigitalPlatDev/FreeDomain/blob/main/documents/tutorial/platform/1.3-connect-nameservers.md) 将此域名的外部 NS 替换为这两条，保存后等待 Cloudflare Active；已有真实记录先保留。后续使用同名 Tunnel CNAME 时移除这条临时 A，避免同名冲突。

助手可核对安装、生成候选配置与执行本地检查。账号登录、付款、身份验证及手机权限由本人完成。不要把密码、Tunnel token、`cert.pem`、JWT、数据库密码或 Push 私钥发送到聊天中。

## 2. 先保护整个主机名，再准备 Tunnel 路由

在 [Cloudflare 控制台](https://dash.cloudflare.com/) 进入 Zero Trust，按当前页面建立团队。随后打开 `Access controls > Applications > Create new application`，选择 `Self-hosted and private`，添加上述完整公开主机名。

Access 应用必须覆盖整个主机名，不仅保护 `/pwa` 或某个 API 路径。Allow 策略只允许本人明确选择的身份，例如本人邮箱；不要使用 Everyone 或 Bypass。可选择已有身份提供商，或按 [官方一次性 PIN 指南](https://developers.cloudflare.com/cloudflare-one/integrations/identity-providers/one-time-pin/) 配置邮箱验证。[官方 Access 应用指南](https://developers.cloudflare.com/learning-paths/clientless-access/access-application/create-access-app/)

记录以下非秘密参数：

| 参数 | 例子/含义 |
| --- | --- |
| 固定 HTTPS Origin | `https://assistant.<你的域名>` |
| Access 团队域 | `<团队名>.cloudflareaccess.com` |
| 应用 AUD | 此 Access 应用的 audience tag |

当前用户提供的非秘密参数：Origin 为 `https://yanshuheng.dpdns.org`，团队域为 `falling-boat-ac78.cloudflareaccess.com`，AUD 为 `b0d0614b762aff31eb214d187a71d192baa1ce82c5e3a480248212fdc499d44e`。`var/deployment/f08-access-2026-10-04/.env.template` 已完整并通过本地检查，获授权后直接作为运行配置读取，没有另存为根 `.env`；团队域按仓库契约规范为带 `https://` 的 origin。

AUD 和团队域用于宿主验证 Access JWT；应用 token 本身是秘密，不需要发送。[官方应用 token 契约](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/application-token/)

准备阶段只生成非激活模板。真正部署前，先核验 Access 全主机名保护与宿主生产配置，再建立/运行命名 Tunnel：唯一公开目标是 `http://127.0.0.1:8000`，未匹配主机名返回 404。管理端口 `127.0.0.1:8001` 与健康端口 `127.0.0.1:8010` 不进入公开 Tunnel；不改为 LAN/公网监听，也不开放路由器端口。当前已按获准范围执行，不能重复建立路由或无条件启动第二组进程。[官方 Tunnel 指南](https://developers.cloudflare.com/tunnel/get-started/)

用户提供 `Email_policy` 截图：Action 为 Allow，Include 为一个指定邮箱，绑定 Self-hosted 应用 `yanshuheng.dpdns.org`；随后明确完整 Policies 列表仅这一条。文档只保存单一身份条件，不收录完整私人邮箱或截图副本。`Total rules 1` 仅表示这条策略的条件数；唯一策略结论来自用户的额外确认，控制台应用列表的自动读取仍超时，不能称已动态独立复验。后续有效签名 JWT 请求才会验证配置 AUD 与实际应用是否一致。

初次盘点没有运行的 `cloudflared` 进程，默认 `C:\Users\kunji\.cloudflared` 目录不存在。随后创建本机私有目录并启动官方 `cloudflared tunnel login`，本人完成授权后 CLI 退出 0，确认账号证书生成。证书及 Tunnel 凭据分别禁用权限继承，核对仅本人/SYSTEM 可访问，不读取或回显内容。账号证书及 Tunnel 凭据不得发到聊天或写入 Git。[官方本地 Tunnel 登录说明](https://developers.cloudflare.com/tunnel/features/locally-managed-tunnels/create-local-tunnel/)。

准备阶段预注册 `lab1-assistant-f086`，UUID 为 `675e44a7-276e-4d59-ab87-ceb7c5ad02ee`，当时复查为零活跃连接。`var/deployment/f08-access-2026-10-04/cloudflared.template.yml` 只引用私有凭据文件路径，固定主机名仅代理 `http://127.0.0.1:8000`，兜底 404；metrics 仅监听 `127.0.0.1:20241`。当时 CLI `ingress validate` 及主机名匹配/兜底测试通过，未运行 `route dns` 或 `run`。后续用户明确授权启动；DNS 同名冲突先停止，核对唯一临时占位 A 后另获精确替换许可才执行覆盖，当前 Tunnel `/ready` 为200。登录、注册和静态检查本身不证明助手上线；实际证据见 `NEXT_STEPS.md`。

## 3. 生成与核对本机候选配置

在仓库根目录运行：

```powershell
.\.venv-win\Scripts\python.exe scripts/prepare_f08.py prepare
```

默认输出为 Git 忽略的 `var/deployment/f08/`，包含 `.env.template`、`cloudflared.template.yml` 与 `START_HERE.md`。这些文件均未激活；已有文件会被保留，重复运行不会覆盖它们。缺少账号参数时保留空值/占位，检查失败属预期。已经有模板时直接检查并编辑该候选，不删除既有材料来强行重新生成。

参数准备齐后，可以在新的候选目录生成完整公开参数模板：

```powershell
.\.venv-win\Scripts\python.exe scripts/prepare_f08.py prepare --output-dir .\var\deployment\f08-candidate --public-origin https://assistant.example.com --team-domain your-team.cloudflareaccess.com --audience YOUR_APPLICATION_AUD
```

将例子替换为本人的实际非秘密参数。数据库 DSN 仅在本机候选文件中填写。先确认持久库、现有数据的归属和备份；此脚本不探测、启动、创建或迁移数据库，也不证明数据库连接有效。不要把现有运行库自动作为试验库，更不要填占位密码冒充已配置。

现成连接来源已核对：`Settings.from_env()` 以及 `scripts/mail_real_readonly.py` 都会在未设置 `PA_DATABASE_URL` 时使用仓库默认本机 `assistant` 库 DSN，Compose 的默认设置与之对应；历史脚本无需 `.env` 也可以连接。`scripts/run-public.ps1` 则要求活动 `.env`，且数据库配置之外还需生产与 Access 参数，不能把历史启动成功等同于新的手机链路已就绪。轮换前默认口令通过只读认证；轮换后默认连接串已失效，包括同角色在测试库的认证。后续启动或定向测试应从受限本机配置中读取连接串，不把新秘密复制到脚本或测试 fixture；不要重建 Compose 数据卷来恢复默认口令。

自动审批曾拒绝从 Docker 提取密码并写入候选文件，理由是沿用原库不明确授权复制、持久化凭据，以及 SecretHandle 边界；被拒命令没有执行。先前只读连接直接使用仓库已有默认配置，没有提取 Docker 凭据。用户随后明确授权更换原库口令并仅保存指定候选的临时本机 DSN 例外，已据此执行。使用现有 PostgreSQL `psql \password`，新口令仅经 stdin，客户端加密后传给服务器，不进入命令行参数或输出；只更换集群角色口令，不写业务表。[PostgreSQL 官方说明](https://www.postgresql.org/docs/17/app-psql.html)。当前数据库 adapter 仍只接收 DSN，没有数据库 SecretHandle 解析入口；正式 Secret Reference 方案属于 F09，不得把本次临时配置称为正式凭据后端完成。

在同一个干净 PowerShell 进程中核对并使用该候选文件。检查前确认没有会覆盖候选的继承 `PA_*` 配置；`PA_TEST_*` 测试参数不计入此项。以下命令只显示变量名，不显示值：

```powershell
Get-ChildItem Env: | Where-Object { $_.Name -like 'PA_*' -and $_.Name -notlike 'PA_TEST_*' } | Select-Object -ExpandProperty Name
.\.venv-win\Scripts\python.exe scripts/prepare_f08.py check --env-file .\var\deployment\f08\.env.template
```

若检查报告继承配置或缺少字段，先按报告处理；不要把检查输出解释为部署成功。仅新开一个子 shell 不保证清除了父进程环境。候选文件应写字面值，不能依赖 `${...}` 环境替换。通过本地检查后仍须独立核对 Access 策略、持久数据库连接与监听端口，才进入另行授权的部署步骤；此时不复制为活动 `.env`、不运行 Tunnel。

首次公共助手启动命令（已获授权执行；仅供记录，不要重复启动）：在实际 Desktop 仓库根目录、干净子进程直接读取唯一获准保存 DSN 的配置，不复制到根 `.env`：

```powershell
.\.venv-win\Scripts\python.exe -m uvicorn personal_assistant.app:app --env-file var/deployment/f08-access-2026-10-04/.env.template --host 127.0.0.1 --port 8000 --no-access-log
```

真正启动前须获得许可并完成仓库要求的发布验证。启动会连接原库、校验/应用待执行迁移、刷新已启用工具描述符及安全恢复过期执行台账；不是只读探测。保留原数据 owner 和原扩展/材料目录，不用 Access 邮箱/sub 替换 `PA_OWNER_ID`。当前契约是单用户，新增允许身份会共享同一个助手数据；不宣称每邮箱多租户隔离。本次无需启动 Admin，公共程序没有自动发送邮件或提交 ehall 的启动循环；读取 `/forms` 可按需启动此前已授权启用的表单 Worker。

发布前按 `AGENTS.md` 运行全套及适用 PostgreSQL/Worker 门禁。`scripts/test-postgres.ps1` 的旧默认口令已经失效，不能直接使用默认参数；只在本机受控进程中读取唯一候选并派生 `/assistant_test`，向干净子进程传入 `PA_TEST_DATABASE_URL`，不输出或另存密码。夹具创建/清理 UUID 临时库，不对原 `assistant` 业务库运行测试；这仍是集群写操作，不能称只读。结果与精确命令见 `NEXT_STEPS.md`。

## 4. 准备私有 Tailscale 健康探测

1. 当前 Windows 端已安装并核对版本；在托盘菜单选择 `Log in` 并由本人完成浏览器认证。[官方 Windows 安装指南](https://tailscale.com/docs/install/windows)
2. 打开 [Tailscale 官方注册/登录页](https://login.tailscale.com/start)，选择本人已有身份提供商，建立 tailnet。
3. 准备第二个节点。Android 手机可安装官方 Tailscale 应用、使用相同 tailnet 登录，并由本人确认 Android VPN 权限。[官方 Android 安装指南](https://tailscale.com/docs/install/android)
4. 核对 MagicDNS 与 HTTPS 条件、tailnet 的访问策略，以及已有 Serve/Funnel 映射。不要自动执行 `serve reset` 删除原配置。[官方 Serve 条件](https://tailscale.com/docs/use-cases/application-testing/share-local-dev-server-with-team)

当前步骤 1–4 已完成：本人分别登录 Windows 客户端和 Android，CLI 确认 `Running`、电脑在线、一个在线 Android peer。启用前 Serve 映射为空；本人完成账号功能启用后，获准的 `tailscale serve --bg --https=443 http://127.0.0.1:8010` 成功，TCP/Web 各一条，唯一 Proxy 是8010，`AllowFunnel=false`。本人误选 Funnel 账号功能开关不等于已配置公开转发；本次未发现实际 Funnel 映射，也未声称已撤销账号级资格。同节点 HTTPS 八项黑盒通过，本人随后从Android确认health正常、任务与管理路径Not Found，三项作为人工反馈单列；其余路径与端口的跨设备证据仍待补齐。节点在线本身不替代实际访问。

后续部署只允许私有 Serve 代理独立健康 sidecar `http://127.0.0.1:8010`。另一节点须核验精确 `/healthz`，并验证任务、审批、管理及其他路径返回 404；直接访问公共/管理端口不可达。Tailscale 不提供手机任务与审批入口，不启用 Funnel、subnet router 或指向 `8000/8001` 的转发。[官方 Serve 命令](https://tailscale.com/docs/reference/tailscale-cli/serve)

## 5. Android 与真实 Push 尚缺的条件

固定 HTTPS Origin 与 Access 部署完成后，再由本人用 Android Chrome 登录、安装 PWA，并按实际需要点击授予通知权限。随后验证 Wi-Fi/移动网络切换、切到后台与恢复、助手离线状态、草稿冲突与缓存边界。手机任务操作只与助手联系；邮件与 ehall 的业务联系由助手承担。

生产可信 `SecretStore` 后端仍是 F09 的硬缺口，默认实现保持 fail-closed。真实 VAPID 私钥及订阅加密密钥不能写入这些模板；安装工具与本地加密测试不能代替真实 Push 投递验收。此项尚未通过，也不在准备环境时擅自实现 F09。

发布前门禁与明确授权现已完成。当前仅以后台临时进程运行原助手、独立 health 与 Tunnel，原库 11 份迁移校验和一致，管理端未启动；不属于 F09 的服务守护/开机自启。唯一公开目标仍为 127.0.0.1:8000。本机配置仍只位于唯一获准候选路径，没有复制为根 `.env`；忽略目录中的 `runtime.json` 和受限日志记录本轮进程。电脑关机、睡眠或进程退出会影响可用性。

手机此前的 Access 截图仅提供 Cloudflare 账号登录并拒绝该身份；这没有证明请求已到宿主。本人已添加团队 One-time PIN，确认应用已保存，应用截图显示关闭 Accept all available identity providers、仅选择 One-time PIN，并关闭 Cloudflare One Client 自动认证；Email_policy 保持原来的单邮箱 Allow。用 Android Chrome 新开无痕窗口访问 `https://yanshuheng.dpdns.org/ui/`，使用该策略允许的邮箱接收并自行输入验证码，反馈任务列表与连接状态。不要把 OTP 登录方法新增为策略的单独 Include 条件，避免扩大允许集合；验证码/Cookie/JWT 不发送到聊天。[官方 OTP 指南](https://developers.cloudflare.com/cloudflare-one/integrations/identity-providers/one-time-pin/)

私有 Serve 已配置，下一步从 Android 保持 Tailscale VPN 在线，访问本机忽略配置 `runtime.json` 中 `health_private_origin` 的精确 `/healthz`，应为最小 `{"status":"ok"}`；`/healthz/`、`/api/v1/tasks`、`/api/v1/approvals`、`/admin/v1/extensions`、`/docs`、`/openapi.json`、根路径均应404，直接访问公共/管理端口不可达。私有节点主机名只保存在忽略目录，不写入Git。实际配置只有 private Serve，无 Funnel 转发；账号级 Funnel 勾选本次未修改。[官方 Serve 条件](https://tailscale.com/docs/features/tailscale-serve)

F09 秘密后端缺失不阻断任务/消息/草稿接口，但正式凭据配置仍未完成，真实 Push、邮件凭据及远程模型继续不可用；产品 Agent 自动创建邮件动作仍未接线。F08.6 等真实环境验收补齐后再更新阶段结论。

## 6. Chrome只提供快捷方式后的安装复验

本人首次反馈“只能创建快捷方式”，未按PWA安装通过记录。已确认当时Manifest没有192/512图标，受认证Manifest链接也未指定携带凭据；现已补齐两份同源PNG、`crossorigin="use-credentials"`和wheel清单，并将固定四路径缓存升为v12。旧v11受控页面升级、新图标原生解码及敏感内容不入缓存已有本地浏览器证据，Android安装仍待本人复验。[Manifest认证与图标要求](https://web.dev/articles/add-manifest)

在Android Chrome普通窗口关闭旧助手标签，重新打开 `https://yanshuheng.dpdns.org/ui/` 并登录，点击一次只读“刷新状态”，停留约30秒后刷新页面，让浏览器检查新Worker与安装条件。再打开Chrome菜单的“添加到主屏幕”，记录实际选项与桌面启动结果；系统包类型按实际证据另记，阶段9不额外要求特定WebAPK包装或应用列表登记。尚未产生可启动入口时继续诊断浏览器/设备条件，保持Access规则。[Chrome安装说明](https://support.google.com/chrome/answer/9658361?co=GENIE.Platform%3DAndroid&hl=zh-Hans)、[Google安装条件](https://web.dev/articles/install-criteria)

## 7. Pura 70 安装停滞的设备续查

2026-10-05 本人反馈现已能点击“安装”，但只显示“开始安装”后无下文；设备为华为 Pura 70，不能使用 Google Play。这仅确认安装选项出现，应用列表、桌面启动及安装终态仍未确认。Google 说明 Chrome 的 WebAPK 路径使用装有 GMS 的设备及 Play Services；安装服务不可用是当前候选原因，不能仅凭无法使用 Play 商店确认 Play Services 缺失或断定唯一原因。[Google Android 安装机制](https://web.dev/learn/pwa/installation#android_installation)

先在手机“设置 → 应用”搜索 `Personal Assistant` / `Assistant`。若没有应用记录，可在 Chrome 的助手页面选择“⋮ → 安装并创建快捷方式 → 创建快捷方式 → 添加”；版本不同可显示“添加到主屏幕”，若没有创建快捷方式选项则记录实际菜单。带 Chrome 标志的入口会在 Chrome 中打开，可用于继续手机通信验证；快捷方式与独立 WebAPK、真实通知投递分别记录。当前已请求本人反馈应用记录、菜单和桌面入口结果，尚待回复。[Google 快捷方式步骤](https://support.google.com/chrome/answer/15085120?co=GENIE.Platform%3DAndroid&hl=zh-Hans)

手机浏览器仍只与受 Access 保护的助手入口通信。网络切换、后台恢复、真机缓存及其余健康边界检查可以在网页中继续；正式安装和真实 Push 仍待补齐，F08.6 保持 `BLOCKED`。本次不变更产品代码或部署配置，不把已有本地测试改记为该手机安装成功。

同日本人续报应用列表没有助手，点击添加到桌面也无反应；Chrome 桌面入口尚未生成。已请求核对 HarmonyOS 版本，并在设置搜索“创建桌面快捷方式”；仅当实际列表存在 Chrome 且禁止时，允许该项后重试。华为官方权限路径文档针对 HarmonyOS 5.0 以下/EMUI 的应用助手，不据此断言所有 Pura 70 的 Chrome 都有相同入口或这就是故障原因。[权限路径的适用范围](https://consumer.huawei.com/cn/support/content/zh-cn00458276/)

可用华为自带浏览器作为下一条设备路径：先打开助手 `/ui/` 并完成原 Access 登录，确认正在显示助手；HarmonyOS 5.0 以下用“∷ → 添加至 → 桌面”，5.0 及以上用“∷ → 添加至桌面 → ✔”。该路径的生成入口、页面能力、后台恢复及 Push 必须分别真机验证，不能直接沿用 Chrome 的本地测试结论。[5.0 以下指南](https://consumer.huawei.com/cn/support/content/zh-cn00448896/)、[5.0 及以上指南](https://consumer.huawei.com/cn/support/content/zh-cn16032141/)

随后本人反馈“应用安装到桌面了”，桌面入口已按人工反馈记录。尚未确认使用的浏览器、是否为独立 WebAPK/快应用/快捷方式，以及从该图标启动后的页面状态；不能仅由图标出现推断包装类型。已请求从桌面打开，核对浏览器/地址栏、任务列表与连接状态，并在前台从 Wi-Fi 切到移动数据观察约30秒，反馈实际恢复情况。此观察时长只用于记录手机表现，不作为产品重连时限承诺。F08.6 整体仍 `BLOCKED`，继续补齐真机证据。

最新反馈确认：桌面图标带Chrome标识，启动后没有地址栏；任务列表显示暂无任务，事件连接已建立；Wi-Fi切到移动数据后恢复连接。桌面入口启动与这一方向的网络切换已记录为真机人工观察。系统包类型未核验，不额外要求WebAPK作为本阶段手机页面能力的门槛；此反馈没有非空任务/消息/草稿/审批证据。下一步仅观察断开Wi-Fi和移动数据后的明确离线提示、恢复移动数据后的自动重连，以及联网后台/锁屏约一分钟后恢复列表和连接。仍不需要创建外部事务或点击审批。

后台恢复问题已收到本人“是”的回复：联网时切后台或锁屏约一分钟，再从桌面入口返回，列表及连接正常恢复，未刷新整页。未分辨采用切后台还是锁屏，未验证后台长期存活或通知投递。断网提示与恢复网络的另一问题仍待反馈。

本人随后确认恢复联网会自动连接，并选择断网期间显示“设备离线或事件连接中断”。基本手机连接检查已有正向反馈；真机缓存、主机/Cloudflare中断、完整健康入口边界与真实Push仍待验收。已请求下一项：在手机创建标注“仅记录，不执行外部动作”的自检任务并发送非敏感补充消息，确认成功后刷新核对任务/消息各一条；失败先保留页面。当前Agent自动执行尚未接线，QUEUED为预期，本检查仅验证手机与助手的任务读写。

随后本人确认上述手机自检任务及首条消息成功后刷新各只有一条；原库只读聚合在后续新增操作前也确认匹配任务1条、首条消息1条。接着本人在电脑同身份助手页面核对手机新增任务及新增消息的自动同步，回复“我试了新增任务和新增消息，都可以”。这是人工跨设备页面反馈，不包含延迟测量、当前总条数或Agent自动执行证据；原库命令和快照时点见NEXT_STEPS.md。

## 8. 手机实际缓存的USB检查

本人已确认有数据USB线。手机保持解锁，在开发者选项开启USB调试；电脑Chrome打开 `chrome://inspect/#devices` 并勾选Discover USB devices，手机上的调试授权由本人确认。手机Chrome普通窗口打开已登录的助手，电脑应显示手机型号及对应助手页面，再选Inspect。不能以电脑本地页面的缓存替代手机结果。[Chrome远程调试](https://developer.chrome.com/docs/devtools/remote-debugging)

远程DevTools打开Application → Cache Storage，刷新列表，仅查看缓存名称和请求路径，不打开Cookie、令牌、响应正文或导出HAR。当前预期助手origin只有 `assistant-static-v13`，四条GET静态路径为 `/ui/`、`/ui/styles.css`、`/ui/app.js`、`/ui/manifest.webmanifest`，没有API、表单正文或其他路径。截图只需这一区域；历史任务/草稿场景v12及草稿GET响应头已有手机证据，随后v13升级/四路径截图也已取得，见文末；其他存储与未访问场景仍待核对。此检查限定当前origin的CacheStorage，不替代API no-store响应头或未访问表单/审批页面的检查。[Chrome缓存查看](https://developer.chrome.com/docs/devtools/storage/cache)

本人提供的电脑设备页截图显示Offline/Pending authentication；随后报告解锁重插及授权重置尝试后，仅有“USB调试已连接”通知，没有电脑密钥指纹授权弹框。本人确认HarmonyOS版本为4.2.0.207；这一版本按Android ADB路径续查，不套用HarmonyOS 5+的HDC分支。电脑只读PnP查询将ADB Interface报告为OK，仅说明接口枚举时未报告设备错误，不证明授权成功或缓存验收通过。已请求本人把USB模式切换为“传输照片”（PTP），保持解锁及USB调试，刷新电脑设备页；结果仍待收到。此为Chrome官方不出现授权弹框的排障选项，不能保证修复。没有安装驱动/ADB、结束进程或修改手机授权文件。[Chrome排障](https://developer.chrome.com/docs/devtools/remote-debugging)、[华为USB模式与版本说明](https://consumer.huawei.com/cn/support/content/zh-cn00407281/)

后续本人反馈PTP设备条目消失、传输文件仍Pending，因此PTP尝试没有解决问题。Windows仍枚举ADB Interface为OK；其PnP属性为Microsoft的winusb.inf、10.0.26100.8972、问题码0，仅为接口状态证据。未在PATH和常见SDK路径找到ADB，随后准备Google官方便携Platform Tools 37.0.1，按SDK仓库元数据核对Windows ZIP大小及SHA-1，未安装驱动或修改PATH。该工具位于Git忽略的诊断目录；首次设备查询可以启动本地服务、保存本机调试密钥，不能称完全无状态变化。[官方便携工具](https://developer.android.com/tools/releases/platform-tools)、[ADB机制](https://developer.android.com/tools/adb)

本人了解工具后回复“已取消勾选，可以查询”，暂时关闭Chrome USB发现并保持传输文件/解锁。官方ADB默认devices查询得到1台unauthorized；本人随后确认出现授权弹窗并已允许，再查询得到1台device。新ADB服务仅监听127.0.0.1:5037，本轮保留用于Inspect，不修改助手服务。仅输出设备数量/状态，未读取手机文件、Cookie、API正文或调试密钥内容，也未转发CDP端口。随后本人对Chrome重新启用USB发现、打开手机助手普通页面的检查回复“已出现助手页面及inspect”，对应手机页面入口已按人工反馈记录。已请求点击inspect并查看Application/Cache Storage名称与路径，实际缓存路径仍未取得，F08.6状态不变。命令与失败尝试见NEXT_STEPS.md。

本人随后提供手机远程DevTools缓存列表截图：origin为 `https://yanshuheng.dpdns.org`，未见路径过滤条件，所选缓存仅有 `/ui/`、`/ui/app.js`、`/ui/manifest.webmanifest`、`/ui/styles.css` 四条静态路径，没有API条目。截图未包含缓存名称或左侧树；本人随后确认左侧“只有这一条”，并抄出缓存名称 `assistant-static-v12`。四条静态路径按截图记录，节点总数1及缓存名称按本人文本反馈记录。手机GET任务列表的响应Cache-Control仍待核对；上述观察限定当前origin的CacheStorage，不构成HTTP缓存、LocalStorage/IndexedDB或尚未访问的Schema草稿/审批页面均无敏感内容的验收证据。

任务响应头检查已获本人远程DevTools截图：`GET /api/v1/tasks?limit=20` 为200 OK、Cache-Control为no-store、Cf-Cache-Status为DYNAMIC，Disable cache未勾选。结合上图四固定路径及本人反馈的唯一v12节点，当前任务/消息使用后的CacheStorage与任务GET响应头检查已记录；不扩充为浏览器HTTP缓存底层、LocalStorage/IndexedDB或尚未访问Schema草稿/审批场景的全面验收。下列步骤保留为复现入口：[Chrome查看响应头](https://developer.chrome.com/docs/devtools/network/reference#view-http-headers)

1. 在远程DevTools的Network选择 **All**，确认左上录制按钮为红点、正在录制，在 **Filter** 输入 `tasks`。
2. 在刚被Inspect的手机Chrome普通标签内，点击助手页面的只读“刷新状态”，保持远程DevTools打开。
3. 请求列表的Name通常显示 `tasks?limit=20`。选择对应GET `/api/v1/tasks`，在Headers → Response Headers查看；只反馈状态码及 `Cache-Control` 值。
4. 如果仍没有匹配行，只截Network请求列表及过滤/录制区域，暂不发送Headers或响应正文。

此检查不勾Disable cache、不修改或覆盖响应头、不导出HAR，不发送完整Headers、Cookie、JWT或正文。

本人随后在已有自检任务的Schema表单草稿区域反馈“尚无可用草稿。一个Schema不支持，已禁用”。2026-10-05只读查看电脑同身份助手页面，灰色项实际为 `nju.smail · smail.account_settings（Schema不支持）`；当前源定义使用数组及额外UI能力，超出既定扁平Schema渲染集合。当时Example Echo显示DISCOVERED，并非已安装/启用，编辑、来源与版本冲突真机检查尚未开始；不能自动安装/升级扩展或扩大Schema支持范围；后续若以现有example.echo PUBLIC字段作验收夹具，须先给出精确安装预览并按AGENTS.md在build/install/import/execute之前取得本人确认。不得以F07试验性预览替代权威审批。

### Access校验暂不可用时（2026-10-05续查）

`503 / CLOUDFLARE_ACCESS_UNAVAILABLE` 表示助手无法完成Access校验，不是已确认账号或登录令牌失效。请保持页面在前台并联网；现有30秒轮询、恢复联网、回到前台和SSE恢复只重读任务，不自动重发创建、消息、草稿或审批命令。保留未发送内容及结果未确认的原请求；不要因这条503退出重登。真正的401/Access登录页仍应按实际身份错误处理。

本人报告重新接验证码仍无效，随后回复手机与电脑“都已恢复”；电脑实际页面也显示事件连接已建立。宿主助手/health/Tunnel仍运行，仅监听回环；官方公钥端点的两个5秒限时、禁重定向、有界只读请求均200且解析出2个有效RSA公钥，检查时无法复现中断，不能确定此前是网络、超时、材料或轮换限速。控制台只读确认应用Session Duration为24 hours，唯一Email_policy为Same as application session timeout；全局会话页读取超时未确认，不据此保证有效会话必为24小时，未延长会话或修改访问策略。

本次仅将任务列表的精确503错误码映射为中文说明，现有只读恢复机制不变；Service Worker升 `assistant-static-v13` 以更新缓存脚本，仍仅缓存四条固定静态路径。定向本地API/390px浏览器20 passed、2 warnings，目标Ruff及Node语法通过，精确命令见NEXT_STEPS。此前手机截图与缓存名反馈属于v12；v13手机更新和实际中断复验尚未确认。更新页面前先保留未保存输入，不要求退出登录。F08.6继续BLOCKED，草稿/审批/剩余边界/真实Push验收未完成。

### 已授权的PUBLIC草稿夹具（2026-10-05）

本人已明确授权example.echo@0.1.0、材料sha256:1c3725193434fa8d027ce95b87b5992fec4bef2fa72f82da18525a241ec70725，用于手机草稿验收。正式预览核对同一哈希后安装/启用均SUCCEEDED，公共PWA已出现example.echo_settings；临时回环Admin和其两个直属Worker已优雅退出，原助手/health/Tunnel保留。单一prefix为PUBLIC测试文字，不扩大Schema支持范围。手机确认未确定→用户输入，草稿版本2及刷新恢复同一测试值；随后真实手机冲突截图和显式保留后保存4/刷新恢复获确认，原库只读核对一致；手机PUT409/412、响应丢失/重试和审批仍待检查；草稿使用后的v12四静态路径及草稿GET的200/no-store已获下方截图证据，v13更新仍待确认。所有操作及停止复核命令见NEXT_STEPS，F08.6仍BLOCKED。

### 草稿使用后的缓存复核（2026-10-05）

本人随后提供草稿使用后的远程DevTools截图：Cache Storage树仅一个assistant-static-v12节点，未设置路径过滤，列表仍只有四个固定静态路径；GET form-draft为200 OK、Cache-Control: no-store，Disable cache未勾选。本次仅确认该CacheStorage及草稿GET响应头，不扩大为浏览器HTTP缓存底层、LocalStorage/IndexedDB、其他敏感API或审批场景；手机仍为v12，v13更新尚待确认。已请求在Application → Service workers中仅点Update，然后刷新已保存页面及Cache Storage树，核对是否升级v13；不点Unregister或Clear storage，不退出登录。此为浏览器静态更新检查，不重发草稿命令。精确观察与边界见NEXT_STEPS，阶段状态不变。

### v13手机缓存升级已获截图（2026-10-05）

本人按Update/刷新步骤提供截图，Cache Storage展开且仅assistant-static-v13节点，旧v12节点不在树中，未设置路径过滤；列表仍是/ui/、/ui/app.js、/ui/manifest.webmanifest、/ui/styles.css四个静态路径，无API条目。该截图仅确认缓存名/路径，不推断所有Worker状态、HTTP缓存、其他本地存储或实际503恢复。本人随后反馈助手origin的Local storage与Session storage均为空，IndexedDB界面显示未检测到数据库。该项按本人文本反馈记录，未获得这三处的独立截图或存储导出；未读取值/Cookies，不推断其他origin或浏览器HTTP缓存底层。未清理数据。真实Push/审批及其他边界仍待验收，F08.6状态不变；历史v12证据保留。

当前继续同一F08.6手机私有入口检查：保持Tailscale在线，在本机忽略runtime配置的health_private_origin上读取/api/v1/approvals、/healthz/、/docs、/openapi.json及/，本人已对这五项均Not Found回复“是的”；与先前health/任务/管理三项合为手机八项页面观察。未取得新增五项Network状态码，不升级为独立404抓包证据。当前继续HTTP直接端口8000/8001检查，要求反馈实际连接错误/HTTP页面及HTTPS自动升级；精确私有IPv4仅在聊天使用，不入Git。手机直接端口的反馈见下段；审批/真实Push等未补齐，不启动后续编号。

### 手机直接端口反馈与当前范围（2026-10-05）

本人先反馈两项无法访问，后明确Chrome两个指定端口均ERR_CONNECTION_TIMED_OUT；Edge初次DNS_PROBE_FINISHED_NXDOMAIN，复试也为ERR_CONNECTION_TIMED_OUT。初次DNS错误不作为端口结果，保留重试历史；未修改DNS、代理或网络安全配置。此为手机浏览器在指定地址下的未成功直连观察，不独立归因于防火墙丢弃、未提供逐项最终URL截图或TCP抓包。当前私有HTTPS八项页面与两项直接端口的人工观察已补齐，不能把浏览器反馈当作全量安全测试或根因定位。剩余仍包括正式审批、响应不明/同键重试、手机PUT409/412、受控Cloudflare中断和助手主机离线，以及真实Push。后者缺生产SecretStore，不在F08.6擅自实现F09；原助手/health/Tunnel保留运行，不擅自停止。F08.6仍BLOCKED，F07/F08整体IN_PROGRESS。精确证据及官方错误定义见NEXT_STEPS。


### 电脑断网后恢复入口（2026-10-07）

本次只读复核确认原助手、独立health与Tunnel仍在运行：监听均在回环，health 8010返回最小200，Tunnel ready返回200；公网未登录请求仍302到既有Access团队。无需重复启动这些后台进程；恢复电脑网络后先保留手机页面并观察，必要时点“刷新状态”。公共8000的未认证health请求返回401，不能用它作为服务未启动的判断。当前是临时后台进程，并非F09的开机自启/服务守护。本次未重启进程或更改配置；本人随后反馈电脑断网期间手机提示事件连接中断、恢复电脑网络后任务列表自动恢复，但顶部提示需要手动刷新才恢复，并明确表示可接受。主机断网场景按这个实际范围认可，保留顶部提示滞留的体验限制，不宣称SSE或顶部提示自动恢复已通过；F08.6仍BLOCKED，其他验收项不变。实际诊断范围与结果见NEXT_STEPS.md。


### 真机草稿409的受控操作（2026-10-07；待执行）

沿用已授权的example.echo PUBLIC自检草稿及任务“第二个自检，不执行外部动作”，不重新安装扩展。已有只读同步冲突截图不能代替实际PUT409；本次须在Network核对真实请求方法及状态。先由本人回复手机基准版本与远程Inspect是否可用；没有回复前不发送新的保存命令。

1. 手机打开该任务，把prefix改为“手机冲突自检-20261007”，记录基准版本N，暂不保存、刷新或换任务；保留当前页面内存中的输入。
2. 在这个手机页面的远程DevTools Sources打开app.js。在saveFormDraft中保存请求发出前的 `const receipt = await jsonRequest(...)` 行设普通行断点：当前仓库未格式化源为628行，邻行必须是method PUT及version/values请求。以可见函数/语句定位为准，不盲用行号；不编辑脚本或执行Console代码，不展开包含认证材料的Scope/存储。
3. 本人点击手机“保存草稿”，确认暂停在该保存请求之前，先不要恢复。暂停使手机原版本/请求键已绑定，同时阻止本页只读轮询先更新冲突状态；若没有暂停，不能将随后场景当作确定的旧版本请求。
4. 手机保持暂停，由电脑公共PWA打开同一任务，填“电脑冲突自检-20261007”，保存一次。先核对电脑回执为N+1；若结果未确认，只保留原请求，不能换键盲发。成功后才继续手机。
5. 在远程DevTools只移除本次行断点，再Resume/F8。手机旧版PUT应由真实API返回409，随后GET新版本；Network只核对该PUT的方法/状态和必要版本，不导出HAR或完整Headers，不读取Cookie/JWT。页面应保留手机值/基准版本，展示服务器N+1与电脑值，并要求显式选择。当前CAS契约返回409；412为前端兼容分支，不改生产API制造412。
6. 收到实际409和页面反馈后再指导显式处理，不把断点暂停或预期结果写作通过。使用结束确保本次断点已移除且脚本恢复运行，不清站点数据或退出登录。原请求若显示结果未确认，保留页面，不刷新/换任务。

上述为待执行的真机步骤；当前没有真实409结果，也没有响应丢失/同键重放的新证据。行断点暂停在语句执行前、可从Breakpoints移除的机制按[Chrome官方断点说明](https://developer.chrome.com/docs/devtools/javascript/breakpoints)核对；状态码按[Network参考](https://developer.chrome.com/docs/devtools/network/reference)检查。响应丢失试验另行准备，不能用本次冲突或之前模拟测试替代。F08.6仍BLOCKED。


2026-10-08 本人已确认手机原基准5的保存请求暂停，电脑公共PWA对照保存后回复6；原库只读确认版本6、6份保存回执、匹配电脑PUBLIC自检值。现只需移除本次断点并Resume/F8，核对手机实际PUT409及输入/服务器6面板；尚未收到手机结果，不称409已通过。当前保留未保存手机输入，不再次保存或刷新；完整实际只读命令见NEXT_STEPS.md。


### 真实409结果及下一项回执丢失准备（2026-10-08）

本人已确认上述旧版手机请求是PUT409：本机基准5/未保存输入保留，服务器冲突面板显示6。显式选择“使用服务器草稿，放弃本机输入”后，页面版本6、值为电脑PUBLIC自检文字、冲突面板消失。原库只读复核仍为版本6、6份保存回执；本项证据已补齐，412仅为兼容分支，不人为修改生产响应。完整查询命令及证据来源见NEXT_STEPS.md。

下一项先只准备：同一手机草稿基准6，prefix改为“手机回执重试自检-20261008”，不保存。远程DevTools Network勾选Keep log，新增并选中“F08回执自检”配置：Download 1 Kbps、Upload 100000 Kbps、Latency 60000 ms，其余默认；确保此前断点已移除、脚本未暂停。本人确认后才指导一次保存及原库只读核对。只有数据库已提交而手机仍等待回执时才中断响应，未写入或已确认则不能计作该反例。恢复后使用页面保留的原请求手动重试，不能刷新/换任务/换键；键只由本人在本机比较是否相同，不向聊天发送键、完整Headers或HAR。所有限速/离线操作结束恢复No throttling，保留登录与站点数据。

这是待执行的测试步骤，不是通过结论；限速字段/离线操作参考[Chrome限速说明](https://developer.chrome.com/docs/devtools/settings/throttling)及[Network参考](https://developer.chrome.com/docs/devtools/network/reference)。不改应用脚本或生产API，不把延迟保存、未提交失败或另一次普通保存当作同键丢回执重放。F08.6整体仍BLOCKED。

2026-10-08 首次60000 ms限速保存已正常确认版本7，原库也为7/7；未中断响应、不计丢回执通过。下一次使用基准7及“手机回执丢失自检-20261008”，Latency改为300000 ms，点一次保存后约5秒、仍等待时直接切Offline，再只读核对是否已提交。没有提交或已确认成功均不能计作反例；实际中断/重放仍待反馈。结束必须恢复No throttling。


### 草稿读取失败的局部修复与当前停点（2026-10-08）

第二次手机切Offline时显示“无法编辑草稿：Failed to fetch”；原库已提交版本8、8份保存回执。恢复No throttling后本人确认值仍在、基准8、没有重试按钮或冲突面板。没有确认原PUT失败/结果未确认、没有手动同键重试，不计真机丢回执通过，暂不再制造保存。

本次修复loadFormDraft：暂时读取失败时保留已验证Schema下的本机输入、基准、在途/未确认请求和重试入口；在途请求保持保存按钮禁用。返回不支持Schema仍拒绝编辑，不缓存草稿或API响应。静态缓存升v14；本地定向21 passed（2个依赖警告），强化在途反例后草稿文件9 passed，与21有重复、不加总。手机尚未验证v14；继续使用前仅在已确认保存、没有未确认原请求和未保存输入时更新页面，并核对缓存版本，不清站点数据或退出登录。下一步为同一F08.6的手机局部修复复验；正式审批/其余敏感缓存/独立Cloudflare中断/真实Push等仍未完成，不启动F09或改用明文秘密后端。命令与证据见NEXT_STEPS.md。


### v14更新停点（2026-10-08）

本人刷新后仍见v13；当前.venv-win包路径/静态目录指向Desktop仓库、磁盘Worker为v14，但未证明手机实际下载新版。先在原手机页面远程DevTools Application → Service workers确认Offline关闭，点该注册Update一次，等待约10秒，反馈Status、红色错误和缓存版本。不要Unregister/Clear site data，也先不要开启Update on reload或Bypass for network。根据手机实际下载/安装结果再处理，不用强制清缓存隐藏正常更新问题；暂不继续功能复验、不制造第三次保存。官方操作见[Chrome PWA调试说明](https://developer.chrome.com/docs/devtools/progressive-web-apps)。

本人一次Update后仍反馈v13、Status为“#7 activated and is running”；#7不是缓存版本。下一步只读取Source首行及更新请求的响应状态/CF-Cache-Status/Age/Cache-Control，按实际证据区分下载旧文件与新版安装问题。不得由状态正常就宣称v14已生效，不凭推测清缓存或更改CDN策略。

本人已从运行Source首行确认v13，当前页面Network未捕获更新请求。下一步在已保存且无待确认输入的同一被Inspect手机标签页，分别直接打开稳定脚本URL和带PUBLIC诊断查询参数的同一路径，核对文件首行及四项最小响应信息；查询参数仅诊断、不改变Worker注册。实际网络交付结果待反馈，不能由磁盘v14推断手机已收到v14。
