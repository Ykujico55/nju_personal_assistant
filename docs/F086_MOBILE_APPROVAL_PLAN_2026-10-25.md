# F08.6 手机正式审批自检候选（2026-10-25）

最新状态（2026-10-27）：**手机实际v16/敏感缓存、正式单次批准、真机保存丢回执同键重试、受控独立主站中断与手动刷新恢复已取得证据**。F08.6仍 **BLOCKED**，待独立复验；当前结果见[真机交接报告](F08_6_REAL_DEVICE_REPORT_2026-10-27.md)。下方旧准备/剩余范围为各时间点历史快照，末节记录实际后续。这份计划仅补已有审批记录的手机材料核对与单次批准，不证明 Agent 自动创建审批、Outbox 消费或 SMTP 投递。

## 已核对的材料

当前已安装 `nju.smail@0.1.0` 没有只读 `smail.send.preview`；当前允许发件开关为 false，正向核对不能返回 nonce。候选为 `extensions/nju_smail` 的 **0.2.0**，工件摘要 `sha256:ba3692df64cbcae925e8de7132dec3c7e62672ed7f2a17b84e5ab9d2c360c68e`，Manifest SHA-256 `357f1dd43be4c3d7a88cfaf961329fc9721504e4ebbc5257965f855d1bbcd357`，lock SHA-256 `536cbb71862c9bbee4a87fdc9d0b8d6e16e7a5110488a437421a570cc6dea521`。没有锁定的第三方运行依赖，仅宿主 SDK；候选邮件 SQL 与已安装版本字节相同，摘要 `6320af7791bd2805e36c70f7fa731417935e7bfd6ad875b95a6d42d179469d8c`。本次仅静态读取与摘要，未 build/install/import/execute。

非秘密精确计划保存在 Git 忽略目录 `var/deployment/f086-ntfy-2026-10-24/mobile-approval-ui-plan.json`，SHA-256 **`33e54b47fb023f7df1595bc3178bc23c1a3cfc5330bd14dcb68e1c91de1a4e57`**。它绑定当前 profile 摘要和上述候选，不激活配置。真正执行前仍须取得正常生命周期的精确预览，重核同一工件；漂移时停止。

## 拟执行范围

1. 通过现成停止协议结束当前冻结宿主拥有者及其主站 Tunnel；独立 ntfy 不动。临时开启仅回环 Admin，按正常升级流程升级这一份邮件扩展，保留原扩展数据及其他扩展。原库现为 12 迁移，不可重跑仅接受旧 11 迁移的原切换 apply。
2. 仅增添独立合成账户 `f086-mobile-approval-20261025`；发件人/收件人/IMAP/SMTP 全部为 `example.invalid` 下的合成地址。使用 Windows 凭据库随机合成值，kind 为 `mail_client_password`，不需要真实邮箱密码。不能 replace_all 漏掉旧账户，不能回写旧文件使 generation 倒退。
3. 临时 profile 仅将邮件开关设 true、收件人 allowlist 限 `f086-recipient@example.invalid`。全程保持 HostControl 冻结，禁止作业领取及发送。按当前 schema12 和新材料启动冻结宿主，关闭 Admin，恢复原主站入口。
4. 真 Worker 为既有第二自检任务准备一封无附件、纯合成标题/正文的 MIME 草稿；原分类策略仍把制品标为 PERSONAL。调用 `MailSendExecutor.prepare_call` 实际核对 MIME、账户和完整空附件清单，用真实 descriptor/call 字段构造公开 `ApprovalBinding`，经 `ApprovalService.prepare` 持久保存 5 分钟审批。
5. 冻结状态下 Gateway 在创建审批前即阻断，故本次精确批准的宿主夹具只准备已有审批记录，不调用 Gateway、execute、Outbox 或 SMTP/IMAP。手机通过现有审批 ID 入口核对材料、批准一次；重复批准应返回 409 `APPROVAL_REVIEW_UNAVAILABLE`，不得改变原审批版本。另查审批 API no-store 和敏感缓存排除。
6. 用现成 reject 收尾为 CANCELLED，或真实到期后 EXPIRED；保留审批审计、合成草稿与制品，不能删除审计伪装无记录。确认无发送执行/job/Outbox/mail ledger 动作后，删除仅属于本夹具的合成账户与凭据，恢复发送禁用及原 allowlist，再按新版本的精确材料恢复冻结服务。

## 停止与回退条件

源材料、版本/账户/数据绑定变化、缺完整权威预览、意外外联/派发均停止。保持冻结，恢复发送禁用；升级失败仅走正常兼容回退，不能整体覆盖恢复原库或倒退账户 generation。当前原拥有者运行计划不得原地篡改或复用旧摘要。

AGENTS.md 明确要求第三方扩展 build/install/import/execute 前确认；此前启动批准只含两份 0.1.0 扩展及发送禁用。因此这份升级、临时自检配置、原库合成夹具和受控重启需本人新授权，尚未执行。

## 本次实际检查及未完成项

本人取消桌面 Chrome 的 Discover USB devices 后，官方 ADB `devices` 再查仍为 **0 个设备**；仅输出数量/状态，没有读取手机内容或输出序列号。手机脚本/缓存 v16 核验保留待验，不反复重置手机授权。既有本人反馈的新版任务列表/连接及真实通知点击通过保持有效。

本次只做静态接口核对、摘要和文档准备，无产品改动；未重复完整套件。此前同产品完整本地 1408 passed/137 skipped/7 warnings、完整临时 PostgreSQL/Worker 137 passed 的证据按原范围保留，不能算本计划已通过。手机审批、实际版本/缓存、真机丢回执、独立 Cloudflare 中断等仍待验收。


## 本人批准后的实际结果

本人明确回复“允许升级并进行该自检”，批准上述精确范围。未修改批准JSON及其摘要。只读预检确认原库12份迁移校验和及候选/原宿主材料匹配；私有运行脚本缺失/错误批准摘要两项负向检查均在创建运行journal前拒绝。

已通过原host_entry stop协议停止旧拥有者，旧journal为STOPPED_FROZEN且cleanup_errors=[]；其主Tunnel一起停止，独立ntfy保留。升级窗口只运行受拥有者监督的child-admin，不同时启动Agent：两者各自的恢复流程可能与升级冲突。Admin仅监听127.0.0.1:8001。

正常Admin inspect返回0.2.0/upgrade/replaces0.1.0及匹配工件/风险/能力，随后用精确plan_id/nonce/hash与固定幂等键批准升级。操作extop_05df69a9419a43bbb7fe1bf91ce0e511明确SUCCEEDED；活动记录0.2.0/ENABLED且工件哈希匹配，升级前后原配置摘要一致。没有新核心迁移。随后停止独立Admin及其Worker，cleanup_errors=[]。

在主站三个端口全关闭、HostControl保持冻结时，合成夹具只新增自己的WCM随机值及.invalid账户，保留旧账户；为Worker追加这一合成account_id引用，完整原配置与规范摘要保存在受限journal中，收尾按对比恢复。真0.2 Worker生成唯一版本1草稿draft_67eeb0cfbddd47e64171de1244851277，随后prepare_call核验完整MIME与空附件清单，MIME SHA-256为d8faa349fe9413d2b4fa0b545de005abfc4ea775bd272e09ee2c5dbe667add51。制品遵守原PERSONAL分类。helper自己的Worker已随容器关闭；未启动审批倒计时。

按新绑定schema12与临时受控profile恢复冻结guardian及主Tunnel，Admin关闭。六项实际HTTP检查通过：healthz200、health入口approvals404、主API无身份401/no-store、两个Tunnel的ready200、公开UI无身份302到本人团队Access。私有夹具inspect确认4个既有jobs总数不变、指定任务既有1个job不变、该任务run/execution均0；Outbox、邮件宿主账本、本夹具发送动作均0。不能把既有任务的1个job误报成夹具新增。

可复验的无变更命令：

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B var/deployment/f086-ntfy-2026-10-24/run-approval-host.py preflight review
.\.venv-win\Scripts\python.exe -Xutf8 -B var/deployment/f086-ntfy-2026-10-24/mobile-approval-fixture.py check --profile var/deployment/f086-ntfy-2026-10-24/assistant-host.approval-review.json
```

stage/inspect/cleanup的精确参数与摘要保存在受限的mobile-approval-command-material.json；其中stage和cleanup是写操作，不能当作只读复验命令盲目重复。真实stage尚未执行，准备在本人手机就绪后开始5分钟审批；手机正式材料核对/单次批准/重复批准、相关缓存仍待验。结束后取消/过期审批，保留审计与草稿，移除仅本夹具账户/凭据，恢复原配置和发送禁用profile并受控重启。

本轮无产品源码变更，未重复完整本地或PostgreSQL/Worker套件；此前同产品完整门禁按原范围保留。私有新拥有者脚本Ruff通过；夹具文本编译/Ruff通过，静态check、真实draft及只读inspect均exit0。未连接SMTP/IMAP、调用模型或发送邮件。F08.6保持BLOCKED。


### 手机就绪后创建真实审批（2026-10-25）

本人反馈“已准备”后执行精确stage命令，exit0。真实Worker复核同一MIME后，ApprovalService.prepare创建唯一记录09575ebf-0b3d-4445-ad5f-3ed86733d169，WAITING_APPROVAL、version0，过期时间2026-10-25T07:59:21.517048+00:00（北京时间15:59:21）。未输出nonce，未执行发送。已要求本人经手机公共审批页面读取材料、相符后只批准一次；手机核对/批准结果及重复批准/缓存仍待验。F08.6继续BLOCKED。


## 2026-10-26 收尾：手机首次批准通过，F08.6整体仍BLOCKED

### 真实手机和数据库证据

本人反馈“内容相符，显示已批准”。当时真实仓储inspect确认同一审批为APPROVED、版本1、approved=true、consumed=false；既有4个jobs不变，Outbox/宿主邮件账本/本夹具发送动作均0。该结论覆盖真实手机经固定域名读取核对并完成首次批准，不包含SMTP发送、Agent自动生成审批或Outbox消费。

后续准备同记录重复批准检查前，真实5分钟已到期；脚本返回ALREADY_EXPIRED_BEFORE_REPLAY_CHECK，并没有调用approve handler。**不能记为重复批准409通过，也没有第二次手机HTTP POST证据**。没有回拨时间、改状态、延长TTL或重建另一条审批。

已执行精确cleanup，exit0、CLEANED：记录实际到期为EXPIRED版本2，保留approved_at与绑定指纹、从未消费；自己的合成账户及WCM句柄移除，原扩展配置JSON内容恢复，审批/合成草稿/MIME制品保留。配置恢复不宣称文件原排版/字节或原先缺失状态复原；账户文件保留单调revision墓碑。10月26日独立只读复查仍符合：EXPIRED/version2/previously_approved=true/consumed=false；原配置内容相等、合成账户不存在、合成句柄不可解析；jobs4/Outbox0/邮件账本0。此复查不运行迁移、Worker或发送。

### 禁发恢复及私有监视问题

临时review拥有者正常停止期间，重查询与guardian退出相撞，journal记GUARDIAN_IDENTITY_MISMATCH；cleanup_errors=[]。当时只读确认主站三个端口全闭、旧自有进程全部退出、stop标记存在且冻结保留后才启动restored，没有删除或改写失败journal。

原restored确曾运行，后在10月25日10:50UTC因常驻每5秒一次的全机PowerShell/CIM查询TimeoutExpired进入清理，停止了自有主站和主Tunnel；独立ntfy未受操作影响。10月26日查到stopped=true，因此没有将候选文件存在当作服务已恢复。

本次仅局部修复Git忽略目录的私有run-approval-host.py：restored常驻改用本次WPS拥有的进程句柄、冻结文件及已在启动完整祖先核验中确定的guardian PID/新鲜心跳；guardian PID不能误等于venv launcher PID。状态读取暂时失败有限重读，冻结撤销、已确认身份违规或owned进程退出仍拒绝。正常stop在验证前及await后优先识别；启动和收尾保留完整身份/监听检查，最终核验不明会记录cleanup错误，不假报clean。没有增加依赖、变更产品代码、审批规则或生产超时。

原失败journal保留；新增run-label recovery-20261026写独立journal，不重做upgrade/fixture/迁移。源材料与禁发profile重新核验，操作脚本摘要绑定为f0983687cb1511eac79e115bcb13e4c4c8a48a1103886ac9e667e28fe51cc56c。当前恢复拥有者保持FROZEN_RUNNING，Public/Health/Agent运行、Admin关闭，原Tunnel已恢复，独立通知通道就绪；不会自动解除冻结。

### 本次实际验证与命令

新增离线用例先20 failed（缺少新监视接口），最终20 passed；覆盖连续多轮禁止CIM、venv PID差异、短暂读失败与有界重试、状态/心跳/组件身份拒绝、owned进程死亡、解冻及正常停止。只用FakeWPS/临时文件，未用假测试声称长期真机运行通过。

```powershell
.\.venv-win\Scripts\python.exe -B -m pytest -q -o addopts= -p no:cacheprovider --tb=short var/verification/f086-approval-monitor-2026-10-26/test_monitor.py
.\.venv-win\Scripts\ruff.exe check var/deployment/f086-ntfy-2026-10-24/run-approval-host.py var/verification/f086-approval-monitor-2026-10-26/test_monitor.py
.\.venv-win\Scripts\python.exe -Xutf8 -B var/deployment/f086-ntfy-2026-10-24/run-approval-host.py preflight restored
```

以上均通过。恢复后20项真实HTTP检查通过：回环及本机Tailscale HTTPS的精确healthz200、各七项非health路径404；主API无身份401/no-store；主/通知两个连接器ready200；公网UI无身份302到原Access团队。完整进程祖先与监听另查通过，主站/health仅127.0.0.1、8001无监听。Tailscale结果是同节点，不冒充本轮手机复验。原主站此前临时监视故障不是本轮独立Cloudflare中断验收证据。

运行中的restored由独立拥有者持有Job Object，不能结束它来“清理后台”。正常停止命令：

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B -m personal_assistant.host_entry stop --profile var/deployment/f086-ntfy-2026-10-24/assistant-host.approval-restored.json
```

**此命令会停止正在运行的主站和主Tunnel，不是只读验收命令。** 重启不可重跑旧0011切换apply，亦不可覆盖已有journal冒充首次；应核验当前材料并使用新的restored run-label。没有注册开机启动。

当前源码仍与此前已完整验收的产品材料一致；本次只改私有运行辅助脚本、离线用例及文档，未重复完整本地/PostgreSQL/Worker。此前1408 passed/137 skipped和完整PG/Worker137 passed仍只是原范围证据。未连接真实邮箱、发送邮件、调用模型、提交、推送或新增部署入口。

### 剩余范围

F08.6仍BLOCKED；F07/F08整体仍IN_PROGRESS。手机实际v16及使用邮件审批后的敏感缓存检查、真机丢回执同键重试、重复批准的额外真机/HTTP证据、独立Cloudflare中断与其余未覆盖网络场景仍待补齐。当前推荐继续唯一NEXT F08.6，不启动其他阶段。


## 手机实际脚本及当前敏感缓存核验通过（2026-10-27）

本人继续并保持USB授权后，官方ADB识别1台device。电脑原生UI控制曾因无法确认浏览器URL自动停止，没有继续操作该界面；后续经本人要求，改用官方ADB与浏览器正式DevTools Target目录做只读诊断。最初返回Edge通道或旧Android/json/list中的根页目标，读取超时不能计为通过；正式Target.getTargets中找到精确助手/ui/页及其现存Worker后才取得以下实测。

手机Chrome/153.0.8010.53当前/ui/页面：控制器为/ui/service-worker.js；实际V8主脚本/ui/app.js的SHA-256为4c99cb084288f8048c26855e048b02edc064b561f5ea69acf0603e971b1ad761，与部署源码逐摘要一致。现存Worker实际源码/ui/service-worker.js的SHA-256为42637945292a450c594d2723ab7dd582038305fc80fea2771fde568e458cf03d，同样匹配部署材料；其CACHE常量实测assistant-static-v16。不是仅据源站文件或缓存名称推断手机版本。

Chrome同一助手origin的CacheStorage仅assistant-static-v16一项，恰四条同源路径：/ui/、/ui/app.js、/ui/manifest.webmanifest、/ui/styles.css。没有API、草稿或审批路径。页面LocalStorage/SessionStorage键名数组均为空，Worker侧同origin IndexedDB数据库名数组为空。该页面此前已由本人使用正式审批；这是当前存储目录/键名快照，不证明HTTP缓存底层或全部历史内容。未打开Cookie、业务响应正文、存储值或其他站点内容。

只向当前精确助手Target附着，未启用Runtime/Console/Network事件域；Debugger只取白名单静态文件源码并在内存计算摘要，Runtime只返回上述元数据。没有刷新、更新/注销Worker或清站点数据。已detach并移除所有本轮创建的回环ADB转发。

成功证据：var/deployment/f086-ntfy-2026-10-24/phone-static-cache-current-targets-2026-10-27.json，SHA-256为2a7a21bdc7a1a6447b2d1a696efe7d99533e60f23178d19f6263ed414b4439d0。先前超时结果与Worker部分通过结果分别保留，不算页面通过。原成功调用为内联Python；已将同一正式Target流程保存为可重复运行的只读辅助脚本，Ruff通过：

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B var/deployment/f086-ntfy-2026-10-24/inspect-phone-static.py
```

脚本只对当前唯一助手/ui/页及对应Worker读取静态版本/存储名称；每次输出单独时间戳证据，且finally清理转发，不覆盖本次成功记录。保存后没有为刷次数而再次运行。无产品代码变化，未运行完整本地/PostgreSQL/Worker。本项当前手机静态版本和上述敏感缓存检查通过；F08.6仍BLOCKED，继续真实丢回执/同键重试及独立Cloudflare中断等剩余范围。已请求本人准备一段公开prefix自检输入并反馈草稿基准版本，尚未限速或保存。

## 2026-10-27 真机保存丢回执及同键重试

使用既有第二自检任务 `task_f97a59148a5444a6b2223ed830b4683f` 和公开prefix，不执行任务或外部动作。首先核对手机基准8；首次未检测到未保存输入即停止，无限速/保存。本人重新准备后，限速方案仍正常保存到9，即使随后短暂offline仍收到回执；记录为 **INVALID_EARLY_RECEIPT，不能计丢回执通过**。

随后本人在基准9准备公开文字 `手机回执自检-20261027-2`。独立私有脚本只对当前助手页面、固定草稿URL开启CDP `Fetch.enable` 的Response阶段，GET不改请求直接继续，认证挑战拦截关闭。事件仅选择requestId、URL、method、responseStatusCode；不读取/输出headers、Cookie、postData或响应正文，不调用getResponseBody，也不订阅Network/Runtime/Console事件。此方案**确实开启Fetch事件域**，不能沿用之前“全部事件域关闭”的描述。方法依据[Chromium官方协议](https://raw.githubusercontent.com/ChromeDevTools/devtools-protocol/master/pdl/domains/Fetch.pdl)：响应阶段暂停发生在响应正文接收之前，用failRequest中断该请求。

本人只点击一次保存后：2026-10-26T16:47:12Z，PostgreSQL只读查询确认草稿10、公开prefix匹配、该任务命令数9→10、版本10命令恰1；已收到PUT 200的响应阶段暂停，随后 `Fetch.failRequest(Aborted)`，而后禁用拦截。手机固定DOM元数据当时为UNKNOWN、基准9、RETRY_SAME_REQUEST。本人稍后反馈事件重读已提示服务器新版本/本机输入未保存冲突，按钮仍为“重试同一保存请求”。按显式选择规则，本人采用服务器草稿，再只点一次原请求重试，反馈“已确认，是10”。没有清除或更换原幂等键。

随后真实只读复核通过：手机SAVED/基准10/普通保存按钮；服务器仍版本10、updated_at仍 `2026-10-26T16:47:12.480270+00:00`、命令总数10、版本10唯一命令1。jobs仍4、Outbox0、邮件动作0、宿主freeze=true。Fetch关闭、No throttling恢复、附加会话分离、全部自建ADB转发删除。本项是**真实手机/原库上的受控响应丢失故障注入**，不是物理断网，也不宣称Cloudflare独立中断已通过。

### 证据与可复现入口

文件位于Git忽略的 `var/deployment/f086-ntfy-2026-10-24/`，不包含请求头或业务正文：

| 材料 | SHA-256 | 结果 |
|---|---|---|
| `phone-draft-receipt-20261027-v8.json` | `855b2617eca14ff2605363ae13b6d3d7b3bf0694810ff24faa03dacc084355a6` | 未满足未保存输入前置条件，停止 |
| `phone-draft-receipt-20261026T163539.235525Z-v8.json` | `5c7c27a68c92cd04d21d51063810b4093772639cb39d03ea717478c9c4a8bea5` | 正常保存9，不计丢回执 |
| `phone-draft-receipt-cut-20261026T164606.298676Z.json` | `e09026a01c631945389eeeece74bddb7d46214f6934252e111a343dc6516e119` | 回执中断及恢复，等待本人重试 |
| `phone-draft-receipt-retry-postcheck-20261026T165022.011374Z.json` | `9fdcfe73b7cb64839a300d12668f20cd74cf969023077b9d31592e14e923c761` | 本人重试后真实只读复核，accepted=true |

私有响应脚本SHA `74fd9e8c02e0110a6867b23136422a078546f7fc2c08e3eccb00b88e94d8c28d`；绑定基础脚本SHA `e47fe50df1a728c36fdf7889f52769ce13f50808075c28159b04abffb1c60841`；profile SHA `b1ce9366125b388e77aaa1fd8c11d0cc476dd5bbc142719c2f740803498f77fd`。静态编译/Ruff/check通过。静态入口不连接手机/数据库：

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B var/deployment/f086-ntfy-2026-10-24/phone-draft-receipt-cut.py check
```

实际执行参数（历史验收命令，当前草稿已10，**不能为刷次数重跑基准9命令**）：

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B var/deployment/f086-ntfy-2026-10-24/phone-draft-receipt-cut.py run --baseline-version 9 --task-id task_f97a59148a5444a6b2223ed830b4683f --prefix '手机回执自检-20261027-2' --accept-script-sha256 74fd9e8c02e0110a6867b23136422a078546f7fc2c08e3eccb00b88e94d8c28d --accept-profile-sha256 b1ce9366125b388e77aaa1fd8c11d0cc476dd5bbc142719c2f740803498f77fd
```

重试后的SQL只读证据由该脚本 `metadata()` 同形查询取得：`task_form_drafts`只返回version/updated_at/固定公开prefix相等布尔；`task_form_draft_commands`只返回该task命令count及receipt.version=10的count。不选择幂等键或receipt正文。手机只返回固定状态/按钮类别及基准版本，未读取字段值。

本轮没有产品代码变更、服务重启、原库迁移、模型调用、邮件发送或网页提交；未重复完整本地/PG套件。F08.6继续BLOCKED；下一项独立Cloudflare中断、重复批准额外证据及其余未覆盖网络场景仍待验。

## 2026-10-27 独立主站Tunnel中断、手机刷新恢复及最终收尾

本人手机私有health先显示status ok。核验微软官方PsSuspend 1.09的Authenticode为Valid/Microsoft及固定SHA后，私有有界脚本只暂停当前owner31760持有的主站cloudflared PID42388，90秒后对同一仍存活身份恢复。进程名不作选择器；保留查询句柄/创建时间，核对父进程、映像、精确绑定启动参数、冻结状态及owner心跳。未结束/重启助手、修改DNS/Access/Tailscale或操作独立ntfy。

本人在中断窗口反馈：“出现断连，health是{status:ok}”。本机与私有HTTPS health共19组样本（含基线及恢复后）全部200；owner/guardian身份保持。暂停和恢复工具exit0，真实恢复同一主站Tunnel。本人恢复后反馈**“下拉刷新后恢复”**，因此本项按“受控独立主站中断及本人手动刷新恢复”记录，自动恢复未单独证实。它不证明Cloudflare平台故障、Access服务故障或DNS中断的全部情形。工具运行参数包含首次本机接受Sysinternals EULA；未提权或安装服务。

随后收尾通过：无认证公网/ui/仍302；回环health200；原库草稿仍10、更新时间未变、命令10且版本10命令1；jobs4/Outbox0/邮件动作0。实际监听8000/8010及独立8090均127.0.0.1，8001关闭；通过进程父子关系复核真实监听进程属于当前拥有者（Windows venv启动器与真正监听子进程PID可不同）。宿主FROZEN_RUNNING/freeze=true，原通知监听未变。

本人下拉刷新后的实际Chrome再次核验：app.js/SW摘要仍匹配、唯一assistant-static-v16四静态路径、LocalStorage/SessionStorage空、IndexedDB库名空。再通过同一已登录手机的固定只读GET核对任务列表/当前Schema草稿/已有审批三项响应，均200且Cache-Control:no-store；只选择状态码和Cache-Control，取消正文流，不读取Cookie、请求头或正文，不订阅Network事件。所有自建ADB转发已删除。

### 本次证据摘要

材料位于Git忽略的var/deployment/f086-ntfy-2026-10-24：

| 文件 | SHA-256 |
|---|---|
| `pause-main-tunnel-20261026T170211.889047Z.json` | `0aa72a696c1b5d1a76b5fa045b6cc2db0e5a00c58979b7a6c8396455b0b1391a` |
| `cloudflare-outage-phone-postcheck-20261026T170650.495789Z.json` | `c8431ed0a3c222cf0725af4bf3233200e2310b4374087922e1b93d2ad050be99` |
| `phone-static-cache-current-targets-20261026T170553.227769Z.json` | `31371162762edb00c6603fe349909411b73d7d052274ffae7713de0c90bc6563` |
| `phone-sensitive-api-cache-headers-20261026T170833.282221Z.json` | `0f5e210c105a154a70cd7a360238ec0d4d18e2d3ba97c888d372180304d5764d` |
| `final-owned-process-boundaries-20261026T171106.024177Z.json` | `514bc012f79011d747a1b07b24b4a6a14bb54c7d98d10cc5910eaa958fcab281` |

有界主站中断脚本SHA：`916d951c0903768251ac1c186eb95c86065e3d4be93f9d2f55f3426363ba99f8`；微软工具SHA：`ecbad759fa863f98f4a7de2aabe5a3006d7b77dc42c10d5cb5439b12d16c2f1a`。工具来自[微软官方PsSuspend](https://learn.microsoft.com/en-us/sysinternals/downloads/pssuspend)，ZIP仅提取pssuspend64.exe。脚本默认check为静态检查；实际暂停命令为历史证据，后续不能复用旧PID盲目运行：

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B var/deployment/f086-ntfy-2026-10-24/pause-main-tunnel-check.py run --accept-script-sha256 916d951c0903768251ac1c186eb95c86065e3d4be93f9d2f55f3426363ba99f8 --expected-main-pid 42388 --expected-owner-pid 31760 --max-seconds 90
```

新证据收束见[当前真机交接报告](F08_6_REAL_DEVICE_REPORT_2026-10-27.md)。F08.6仍BLOCKED等待独立复验与剩余范围裁定；重复批准额外分支未完成、自动恢复未单独证明、HTTP磁盘缓存未取证，不以这些实际未做事项宣称通过。原生Web Push在该无Google设备不可用，真实通知证据属于本人已批准的ntfy替代通道。F07/F08整体维持IN_PROGRESS。
