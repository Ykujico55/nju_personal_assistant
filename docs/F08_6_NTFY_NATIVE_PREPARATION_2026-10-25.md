# F08.6：真实 Windows ntfy 本机启用记录（2026-10-25）

> 本文第1–5节为先前本机启用快照；2026-10-25后续通知公网入口已通过实际检查，当前进展见第6节，F08.6仍BLOCKED。

## 1. 本机启用时结论

F08.6 继续 **BLOCKED**；F07/F08 整体 **IN_PROGRESS**，DELIVERY.2 本地验收 **DONE**，最小 F09.1/F06.P2 **DONE**。缺口已不是凭据后端未实现，而是实际入口、运行版本、手机与完整流程证据。

本轮从桌面原仓库 main `38b192a4cfef658723b9f23d89d7aeb4dc53263c`（已推送）继续。Codex 的旧工作树仍为 `288f034`，本轮没有在旧工作树重复开发。所有下列命令的工作目录是 `C:/Users/kunji/Desktop/homework/生成式软件工程/lab1`。

先完成未启用候选及真实回环检查；随后用户明确回复“允许启用独立通知通道”，授权回环8090、独立通知域名/Service Auth及宿主只写、手机只读凭据。已完成**本机**部分，外网入口仍未发布。原助手版本切换、原库迁移未包含在本次通知启用中。

## 2. 已完成

- 下载官方 ntfy **2.33.1 Windows amd64**，只在 Git 忽略的 `var/tools/ntfy/2.33.1` 解出 ntfy.exe，没有改 PATH、注册服务或开机启动。ZIP SHA-256 与官方 GitHub Release 元数据一致：`b2dd70a74732151ed1f7945bc3146d4bd4d73a7c9e592ad7b3fbe59f7a24e6eb`；exe SHA-256：`987efb504b5c9e7561fede032b7c6e51a57b543f83c086c569eda63c0d4ca740`。没有额外声称比对 checksums.txt 的内容。
- `var/deployment/f086-ntfy-2026-10-24` 保存独立配置、运行记录和本机辅助脚本；目录 ACL 限本人/SYSTEM。日期为开始准备时命名，收尾在25日。模板保留未启用快照，`server.active.yml` 和运行记录表示最新本机状态。
- 正式本机 ntfy 只绑定 **127.0.0.1:8090**；独立 auth.db，默认 `deny-all`，两个不同普通用户分别只写/只读同一 `assistant_pending` topic。token 各有效720小时（30天），助手侧副本只由创建子进程返回本机脚本后保存为 **Windows Credential Manager** 句柄，不输出秘密；ntfy自身认证库独立保存其必要认证材料，受上述目录ACL保护。
- 消息缓存0s、无磁盘消息缓存、附件、Firebase或上游；关闭注册/登录界面，日志error，禁止debug/trace及HTTP头日志。API授权和用户权限仍生效。ntfy自身账户/授权表是独立本机安全材料，不是助手新增业务表或原PostgreSQL迁移。
- 独立新Python进程解析真实 Windows 发布/读取句柄，验证权限及适配器最小通知；宿主→回环服务兼容性已验证。这里只发送公开自检task_id/risk，不读取任务、邮箱、审批或表单。
- 生成独立 `notify.yanshuheng.dpdns.org` 接收入口草案：单独Access应用、Service Auth、单设备凭据、Tunnel在对应hostname条目验证新应用AUD。保留主站单邮箱OTP和Tailscale health-only边界。已另行登记独立named Tunnel `lab1-notify-f086`（`0906901b-296a-4f7a-af0c-0251ad1c2a84`），凭据只在受限本机目录；新应用AUD尚待本人提供，DNS未添加、连接器未启动。没有给主站Tunnel添加路由不同的副本。
- 已准备本人运行的 `store-cloudflare-token.py`：仅交互终端隐藏输入Client ID/Secret，保存Windows句柄；不把这些值放入聊天、CLI参数或文件。本人已确认隐藏录入完成，独立新进程已验证两个句柄均可解析且格式有效，未打印值或向外部发送；应用授权仍待配置。控制台最低有效期由本人反馈为1 year，已指导选择；该时限与本机ntfy720小时令牌分别记录，未独立核对Cloudflare过期时间。

官方现已支持原生Windows服务，因此这次ntfy检查不需要Docker。[安装说明](https://docs.ntfy.sh/install/#windows)、[固定版本材料](https://github.com/binwiederhier/ntfy/releases/tag/v2.33.1)。Docker只读查询曾超时，不据此声称其容器状态已检查通过。

## 3. 本轮实际验证

### 3.1 原ntfy适配器定向回归

```powershell
.\.venv-win\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts=--strict-markers -q --tb=short tests/unit/test_ntfy_notifications_f08.py tests/integration/test_ntfy_local_f08.py
```

**13 passed，5.41s，exit0**。这一集合含HTTP替身，不能单独证明真实ntfy；真实服务证据另列。

### 3.2 真实二进制、隔离账户与撤销

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B var/verification/f086-ntfy-2026-10-24/check-native-ntfy.py
```

最终复跑 **9项检查通过，exit0**，结果为 `var/verification/f086-ntfy-2026-10-24/native-protocol-result.json`：

1. 原生服务健康就绪。
2. 匿名读取被拒绝。
3. 匿名发布被拒绝。
4. 发布者不能读取。
5. 读取者不能发布。
6. 其他topic被拒绝。
7. 真实 `LocalNtfyNotifier` 回执匹配，合成读取者收到准确kind/risk/task_id和同源Click，无动作/附件/邮件/nonce。
8. 发布后轮询为空，消息未缓存。
9. 发布token自撤销后不再被接受。

该脚本使用随机回环端口及**内存SecretStore测试替身**。最后关闭客户端、回收自身ntfy进程、验证端口不再监听并移除精确隔离auth.db及其附属文件；不操作正式8090服务。前期脚本在读取账户认证及预配用户撤销方法上失败；改成两个独立CLI token及当前token自撤销API后通过，没有修改产品代码或移除安全断言。

### 3.3 候选先失败，再生成

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B var/verification/f086-ntfy-2026-10-24/check-candidate.py
```

生成前 **exit1**：`missing candidate: server.template.yml`。生成五份非秘密候选后同命令 **exit0**，验证回环、deny-all、零缓存、独立权限及未创建认证材料。随后用户授权启用，真实auth.db已建立，因此这个**启用前**脚本现在会有意拒绝；保留断言，不把它改成永远绿色。当前运行使用下一条检查。

### 3.4 授权启用及真实Windows句柄

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B var/deployment/f086-ntfy-2026-10-24/activate-local.py
.\.venv-win\Scripts\python.exe -Xutf8 -B var/deployment/f086-ntfy-2026-10-24/check-local-active.py
```

首次启用 **exit1**，原因是ntfy账户CLI要求先启动服务初始化认证库；当时auth.db和凭据记录均未创建。调整为“启动默认拒绝的回环服务→健康检查→创建账户/权限/token”后，启用 **exit0**；该脚本有防覆盖断言，不应重复执行。

独立新进程运行第二条 **7项检查通过，exit0**：真实Windows句柄跨进程解析且发布/读取凭据不同；health正常；匿名读写拒绝；发布者只写/读取者只读；其他topic拒绝；真实适配器最小消息在本机接收；消息缓存为空。结果 `local-active-check.json`。正式token未因测试撤销。

已核对独立auth.db及新Tunnel凭据文件ACL均只有本人/SYSTEM允许访问。收尾端口检查只有 `127.0.0.1:8090`（本轮创建的ntfy）。8000/8001/8010当时无监听，不能把旧运行记录当成现在助手已在线；本轮未启动或迁移原助手。

## 4. 外网与手机的具体剩余步骤

1. 本人已创建通知专用Cloudflare Service Token并隐藏录入本机，已验证句柄可解析。待本人新增独立通知Access应用，仅Service Auth允许该token，提供新应用AUD用于Tunnel验证；主站OTP策略维持原范围。
2. 核对DNS无冲突后添加新通知子域及受限Ingress，完成未认证拒绝/设备只读黑盒；现未发布，不能声称外网或手机连通。
3. 本人安装官方 **F-Droid flavor**（候选Android1.25.2），配置通知服务器的Cloudflare自定义头和独立ntfy读取凭据。官方客户端支持自定义头及无Firebase常驻连接，但华为实际后台/锁屏行为仍须手机验证。[自定义头](https://docs.ntfy.sh/subscribe/phone/#custom-headers)、[即时接收](https://docs.ntfy.sh/subscribe/phone/#instant-delivery)、[Android固定材料](https://github.com/binwiederhier/ntfy-android/releases/tag/v1.25.2)。
4. 配置入口后，验证订阅、前台/后台/锁屏接收、内容最小化、点击只打开助手、撤销与网络恢复。`relay accepted`仍不等于设备送达；关闭缓存不提供离线补发承诺。
5. 原助手新版切换先准备持久profile、数据库句柄、备份/恢复及迁移清单；新版启动涉及0001–0012验证/迁移和独立Worker。不能把新增ntfy片段并入旧env后直接启动当作升级已验收。
6. F08.6其余缺口继续保留：手机实际脚本版本、真机草稿丢回执/同键重试、正式单次审批及缓存、独立Cloudflare中断。F07正式事务适配器/完整目标与材料后果绑定/实站Gateway与Outbox继续延期，不制造申请或最终提交。

Cloudflare控制台虽由本人确认登录，自动导航/读取仍连续超时；没有借此降低鉴权或宣称配置成功。本人已完成Service Token隐藏录入；当前已给出新增通知专用应用、Service Auth与提供新AUD的步骤。Service Auth使用两个指定头；不要从Chrome复制Cookie/JWT。[Cloudflare官方说明](https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/)。

## 5. 变更与验证边界

静态检查：五份本机准备/验证Python脚本compile通过，`python -m ruff check --no-cache --select F` 对这五份脚本通过；`git diff --check`通过。只做语法/未定义引用等检查，不宣称全规则Ruff/Mypy或整阶段门禁。

```powershell
.\.venv-win\Scripts\python.exe -m ruff check --no-cache --select F var/deployment/f086-ntfy-2026-10-24/activate-local.py var/deployment/f086-ntfy-2026-10-24/check-local-active.py var/deployment/f086-ntfy-2026-10-24/store-cloudflare-token.py var/verification/f086-ntfy-2026-10-24/check-native-ntfy.py var/verification/f086-ntfy-2026-10-24/check-candidate.py
git diff --check
```

两条均exit0。跟踪文档变更为TODO.md、docs/NEXT_STEPS.md及本报告；可执行文件、辅助脚本、配置、认证库和运行证据均在Git忽略的var目录，不加入提交。

产品源码、公共API、审批/Outbox、迁移历史均未改。本轮只新增Git忽略的本机工具/配置/辅助检查，更新TODO/NEXT和本报告。原库、模型、邮箱、ehall未访问或写入；没有最终提交或发件。

本轮为独立通知服务的本机配置，不是新版助手发布或F08.6阶段验收；只跑上述定向/真实回环检查及静态检查，没有重跑全量、PostgreSQL/Worker或真实手机。1405/135等均为此前已记录证据，不算本轮运行结果。没有提交或推送；本机ntfy按用户授权保留运行，隔离验证进程已退出。

## 6. 后续：通知AUD绑定及真实公网检查（2026-10-25）

### 授权与实际配置

用户已明确授权启用独立通知通道。因browser computer-use连续触发URL识别安全阻断，本轮没有继续自动点控制台；由本人逐步完成新应用：`assistant-notify`、通知hostname、策略 `notify-phone-only`，截图可见Action=Service Auth、唯一Include=Service Token `assistant-notify-phone`。本人随后提供的64位值已作为通知应用AUD，实际正向链路通过，确认不是误用主站AUD。官方控制台操作不是本轮脚本自动创建的。

独立Tunnel `0906901b-296a-4f7a-af0c-0251ad1c2a84` 使用独立凭据及 `cloudflared-notification.active.yml`。`originRequest.access.required=true`，团队短名 `falling-boat-ac78`，仅绑定通知新AUD。hostname精确匹配notify，回源127.0.0.1:8090，其余hostname兜底404；metrics仅127.0.0.1:20242。干净子进程环境移除了继承的TUNNEL_* / CF_TUNNEL_*覆盖，启动禁自动更新、error日志，无头部/秘密日志。原主站Tunnel未修改。

DNS命令添加notify指向该新Tunnel，未使用overwrite参数。连接器已启动，回环`/ready`返回200。本轮保留已授权的ntfy及独立cloudflared运行；未注册服务或开机启动。

### 本轮实际命令与结果

以下配置路径示例的`<目录>`为仓库内 `var/deployment/f086-ntfy-2026-10-24` 的绝对路径；原始运行证据与非秘密元数据均在该Git忽略目录。

```powershell
cloudflared tunnel --config "<目录>/cloudflared-notification.active.yml" ingress validate
cloudflared tunnel --config "<目录>/cloudflared-notification.active.yml" ingress rule https://notify.yanshuheng.dpdns.org/
cloudflared tunnel --config "<目录>/cloudflared-notification.active.yml" ingress rule https://yanshuheng.dpdns.org/ui/
```

**3项通过，均exit0**：语法OK、notify匹配规则0、主站匹配规则1且为404。`ingress-validation.json`仅证明本地配置匹配。

```powershell
cloudflared tunnel --origincert C:/Users/kunji/.cloudflared/cert.pem route dns 0906901b-296a-4f7a-af0c-0251ad1c2a84 notify.yanshuheng.dpdns.org
cloudflared --no-autoupdate tunnel --config "<目录>/cloudflared-notification.active.yml" run 0906901b-296a-4f7a-af0c-0251ad1c2a84
.\.venv-win\Scripts\python.exe -Xutf8 -B var/deployment/f086-ntfy-2026-10-24/check-external-entry.py
```

DNS命令exit0；run由隐藏后台进程执行，后续ready=200。最后一条 **14项检查通过，exit0（8.82s工具进程时间）**，结果 `external-entry-result.json`：

| 检查 | 实际结果 |
|---|---|
| 无凭据、仅ntfy凭据、伪造JWT、错误CF服务秘密 | 各403 |
| 正确CF认证但缺ntfy凭据 | 403 |
| 正确CF认证但错误ntfy凭据 | 401 |
| 正确CF认证+reader读取授权topic | 200 |
| reader发布、读取其他topic；publisher读取 | 各403 |
| 通知CF凭据访问主站/ui/及任务API | 各302到本团队Access登录 |
| 宿主真实LocalNtfyNotifier→回环ntfy→Cloudflare→受限公网测试读取者 | 收到准确PUBLIC自检kind/risk/task_id及Click，无actions/附件/正文/nonce |
| 随后轮询消息积压 | 200且为空，无缓存 |

每项请求使用新HTTP客户端，不带浏览器Cookie、不跟随重定向；凭据由Windows句柄在进程内解析，不打印秘密、完整头部或拒绝正文。真实消息仅发送一次公开自检标识，结果不明不自动重发。外部测试流结束即关闭，未建立手机订阅。该证据不是手机投递、锁屏显示或整个F08.6验收。

### 下一步与仍缺证据

官方GitHub API已核对固定Android `v1.25.2` 非草稿/非预发布、F-Droid flavor资产 `ntfy-1.25.2-fdroid-release.apk`（9,249,866字节；官方资产digest `sha256:8a1075c0963caa1e0dc404996531bc641cb2b982f92a4bc692d4999f9eb53f39`）。只核对官方元数据，未声称已下载校验APK或安装手机。已向本人提供官方无Google版安装链接，本人已回复“已打开”；这是安装并打开的人工反馈，没有查询设备包版本或验证实际APK字节。

下一步仍是F08.6：本人安装客户端，通过本机安全方式配置三个独立认证字段，验证手机前台/后台/锁屏、撤销和点击；再准备原助手新版持久运行切换及剩余草稿/审批/缓存/网络场景。助手原profile仍未绑定此通道，不能把本轮自检描述成真实任务自动提醒已上线；原主站正常会话可用性也未在本轮重新验证。

本轮只有本机配置与文档、Git忽略的验证脚本更新，没有产品源码、审批/Outbox或迁移变更；没有访问模型、邮箱或ehall，没有原库迁移/原助手重启。不是发布新版或阶段验收，因此未重跑全量、PostgreSQL/Worker；既有13/9/7均保留为前轮本机证据。本轮没有提交或推送。

### 手机配置当前停点

本人已安装打开ntfy，当前等待本人在 Settings → Advanced → Custom headers 逐条填写三个字段。已准备本机默认遮罩GUI `phone-config-viewer.py`，只从Windows凭据库解析CF两项及**ntfy reader**，不会解析publisher，不打印、序列化或自动复制秘密；窗口由本人在交互终端打开，10分钟自动关闭，不由Agent操控。

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B var/deployment/f086-ntfy-2026-10-24/phone-config-viewer.py --check
.\.venv-win\Scripts\python.exe -m ruff check --no-cache --select F var/deployment/f086-ntfy-2026-10-24/check-external-entry.py var/deployment/f086-ntfy-2026-10-24/phone-config-viewer.py
git diff --check
```

check模式exit0，确认三字段、仅reader、无秘密输出、未打开GUI；两份脚本语法compile通过，目标Ruff F规则与差异检查exit0。这不是GUI视觉/手机配置验收。

固定版源码确认：每条自定义头是Service URL / Name / Value三栏，baseURL精确匹配；三条均用 `https://notify.yanshuheng.dpdns.org`，不含topic或末尾斜杠。CF两头与 `Authorization: Bearer <reader>`可共存；所选方法保持该server的用户名/密码为空。原生备份格式不包括customHeaders，未提供一键导入或含凭据URL/二维码；用户不能把凭据编辑页截图发到聊天。下一步配置完成后再订阅topic、发送一次最小提醒验证手机接收。

### 手机订阅阻断的后续定位（2026-10-25）

本人确认三条标头已保存，遮罩列表截图可见三个预期名称；列表标题省略https是客户端正常显示，不能据此断言填错。新增订阅截图可见正确topic及通知域名，手机点击仍显示Login required，故未发送手机自检通知。

固定版本checkAuth使用GET `/assistant_pending/auth`；从宿主用原Windows句柄只读验证该接口200且success=true，health及poll也200（`mobile-auth-endpoint-check.json`）。另用一条正向请求校准cloudflared本机metrics：总请求及200计数各增1；随后请本人只点一次订阅，计数差值全部0（`phone-subscription-diagnostic.json`）。这只能说明本次未观察到请求到达本机连接器，不能直接判定哪个凭据值错误或将登录框改为成功。

当前保留原凭据，等待本人提供自定义头的完整Service URL（非秘密），核对客户端按原始baseUrl精确匹配的问题；不索取Value或明文编辑截图，不降低Access或ntfy权限。F08.6继续BLOCKED。

### 手机订阅恢复及首次实际接收（2026-10-25）

本人重新核对两条Cloudflare标头后反馈“成功了，刚才确实是输错了一位”。据此确认手机订阅成功，前述客户端登录阻断关闭；没有更改Access/ntfy权限或重建凭据。根因来自本人反馈，不是读取手机明文后作出的判定。

宿主经既有LocalNtfyNotifier发送一次PUBLIC自检 `f086-phone-check-20261025` / R1，回执accepted=1、unknown=0；发送前以独占新记录写入“结果未确认”，禁止中断后自动重发，记录 `phone-first-send.json`。没有创建实际任务、发送邮件或提交表单。

本人反馈“已收到”，随后说明当时在应用内，要求再测系统通知。因此当前只记**真机应用内接收通过**，不记为系统通知/锁屏通过。已请本人保持订阅、正常回桌面并锁屏，再由宿主等约一分钟后发送一条新的锁屏自检。F08.6整体继续BLOCKED，点击、撤销、网络恢复与原助手接线等仍待验证。

### 首次锁屏复验未通过（2026-10-25）

本人确认锁屏后，显式等待两次30秒，再单发 `f086-lockscreen-20261025` / R1。relay accepted=1、unknown=0（`phone-lockscreen-send.json`），本人反馈“这次都没有”。因此**系统/锁屏接收未通过**，不以服务回执替代手机证据，没有自动重发。

只读检查ntfy health及独立Tunnel ready均200，事后并发连接1、request_errors3（`phone-lockscreen-diagnostic.json`）；这些不能证明锁屏期间连接持续。固定Android1.25.2默认后台协议为JSON HTTP stream，两种协议均使用该baseUrl自定义头。真实公网以未知/未缓存since游标打开JSON流，返回200及open事件（`uncached-cursor-check.json`），只排除了这项服务端重连阻断，未证明手机后台正常。

当前根因未定。已依华为HarmonyOS4.x官方说明，请本人只核对ntfy应用启动管理的后台活动、电池优化例外及通知/锁屏开关；准备后再等约60秒，单发新编号最小提醒。没有关闭全局安全、放宽Access/ACL或打开消息缓存。原丢失提醒不承诺补达，F08.6继续BLOCKED。

### 华为应用设置后锁屏复验通过（2026-10-25，限此样本）

本人按前述ntfy应用专属后台/电池/通知项核对后回复“已设置并锁屏”。显式等待两次30秒后，宿主单发新的 `f086-lockscreen-retest-20261025` / R1，relay accepted=1、unknown=0。等待前与发出前的独立Tunnel并发连接均1、请求总计28、errors4，快照只证明两个时点，不冒充全程抓包。本人明确回复“锁屏已收到系统通知”，随后对显示内容反馈“啥都显示全了”。记录 `phone-lockscreen-retest1.json`。

这条测试实际载荷仅固定标题及kind/risk/合成task_id；客户端完整显示这些允许的最小元数据，未发送真实任务正文、邮件/表单材料、审批nonce或凭据。本样本的**锁屏系统接收及最小内容确认通过**。首轮失败记录保留；只知道应用设置调整发生在成功复验之前，不宣称已隔离出某个开关为唯一根因，也不外推为长时间深度休眠稳定性。

F08.6整体继续BLOCKED。后续仍需客户端取消/设备凭据撤销及恢复、实际助手入口就绪后的点击跳转与真实任务通知接线、其他既有真机/网络/审批缺口；本轮未以假task_id完成正式业务任务验收。


## 7. 设备读取凭据撤销与恢复（2026-10-25，恢复待真机确认）

### 为什么撤销时停止独立通知进程

固定ntfy v2.33.1对新请求重新认证，但已建立的JSON长流不会逐条重验token。只删除token不能宣称立即断开旧设备。因此此次先安全准备替代只读token，再删除旧token，强制停止精确匹配的独立ntfy进程并等实际退出，最后用同一配置/认证库重新启动；cloudflared连接器和原助手不操作。原进程的Windows HANDLE在核验exe和完整命令行期间保持持有，停止绑定该HANDLE。这里是强制停止，不称为优雅停机。

官方源码依据：[每次请求认证](https://github.com/binwiederhier/ntfy/blob/v2.33.1/server/server_auth.go)、[订阅流](https://github.com/binwiederhier/ntfy/blob/v2.33.1/server/server.go)、[删除token](https://github.com/binwiederhier/ntfy/blob/v2.33.1/server/server_account.go)、[CLI token签发](https://github.com/binwiederhier/ntfy/blob/v2.33.1/cmd/token.go)。保持error日志，避免删除处理在DEBUG下记录token。替代token继承phone_reader账户的单主题只读ACL，不描述成token自身携带权限限制。

### 先行隔离演练及实际执行

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B var/deployment/f086-ntfy-2026-10-24/revoke-phone.py check
.\.venv-win\Scripts\python.exe -Xutf8 -B var/verification/f086-ntfy-2026-10-24/check-reader-revocation.py
.\.venv-win\Scripts\python.exe -Xutf8 -B var/deployment/f086-ntfy-2026-10-24/revoke-phone.py apply
```

第一条只读预检exit0：精确进程身份、正式旧reader公网读取有效，无凭据改动/重启。第二条 **7项检查通过，exit0**：真实独立ntfy及临时认证库、实际helper的进程身份/HANDLE停止、删除旧token后的新请求401、删除本身仍允许已建立旧流收消息、停止确实关闭旧流、重启旧401/新200、替代只读ACL，以及测试进程/端口/目录回收；不加载正式凭据。该7项是协议检查数，不是7个pytest。

第三条在本人明确回复“已准备”后**仅执行一次**，exit0。私有脚本及凭据journal均在Git忽略目录；已有journal会阻止整个apply被自动重复，不能把此命令作为普通回归重跑。CLI新token stdout仅由父进程内存捕获并写Windows凭据库，不写终端或明文文件。先核验替代凭据能读授权主题、不能发布或读取其他主题；再DELETE旧token并只读核对401，原命令结果不明时不盲目重发DELETE。

正式结果 `phone-revocation.json`：旧公网401、新公网200，旧连接已断开，独立服务新PID记录为48668；publisher凭据不变、原助手不变、未发布任何通知。旧宿主凭据副本已删除；手机仍持旧值时，本人等待后反馈“连接错误”。此反馈加服务端拒绝证据确认撤销分支，不是对手机正文、Cookie或秘密的读取。

执行后只读复核指出helper在公网确认前先切活动句柄的失败收尾可改进；此次两项公网检查均成功，未发生失败错切。随后已调整为公网核验完成后才切活动句柄，并将坏JSON删除回执归为未确认、继续只读对账；没有重新执行正式apply或重启。最终helper语法/目标静态检查另行记录。

### 恢复停点与范围

本人仅需在本机私密配置窗查看替代Authorization，并在手机替换这一项，CF两头保持。当前等待手机反馈及一次新编号恢复提醒；尚未把新token公网200记为手机恢复通过，也没有点击合成任务链接。

原助手profile、原库、主站Tunnel、Access策略、publisher均未变。产品代码、审批/Outbox和迁移无改动；不是新版发布或阶段验收，仅针对此次独立服务配置运行隔离协议与目标静态检查，未重跑全量/PostgreSQL/Worker。F08.6继续BLOCKED，真实任务通知自动接线、实际入口点击、其余真机审批/缓存/网络证据仍待补齐。未提交、推送或调用真实模型/邮箱/ehall。

最终两份私有脚本compile语法检查、`python -m ruff check --no-cache --select F var/deployment/f086-ntfy-2026-10-24/revoke-phone.py var/verification/f086-ntfy-2026-10-24/check-reader-revocation.py` 与 `git diff --check` 均exit0。此处只声明所运行F规则，不宣称完整Ruff/Mypy或全量门禁。

### 手机恢复收到新提醒（2026-10-25）

本人反馈已完成Authorization替换后，宿主通过原publisher句柄和LocalNtfyNotifier仅发送一次 `f086-reader-restored-20261025` / R1。独占新文件在发出前先记结果未确认，禁止自动重复；`phone-reader-restored-send.json` 回执 accepted=1、unknown=0。本人随后明确反馈“已收到”。因此本次设备读取凭据撤销及更换恢复分支通过；不外推长时稳定性，也未点击合成ID链接。独立通知进程按授权保留运行。

下一步先核对原助手实际运行版本、持久profile和备份迁移准备，入口就绪后再做真实任务的通知点击。原库迁移/新版启动另有准备与切换步骤，本轮未执行。F08.6继续BLOCKED。


## 8. 原助手切换前的只读准备与新增阻断（2026-10-25）

通知撤销恢复通过后，为后续真实任务通知点击准备原助手入口。8000/8001/8010均未监听。第一次原库只读连接ConnectionRefusedError；Docker查询也超时。本人查看Docker Desktop后反馈Engine Running，随后只读inspect确认既有 `lab1-postgres-1` 处于running，Compose project/service为lab1/postgres，映射仅127.0.0.1:5432，原卷 `lab1_assistant-postgres` 挂载原数据目录。Agent未运行start/up、建库/建卷/改密码或迁移。

可复现只读检查：

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B var/deployment/f086-ntfy-2026-10-24/check-assistant-switch.py
```

最后一次exit0，`database_readonly_check=passed`。连接级default_transaction_read_only及事务readonly双重指定，只查询迁移清单和to_regclass；11份原迁移全部与仓库哈希一致，唯一pending为0012_runtime_records，runtime_records/runtime_commands均不存在。实际导入来源为本仓库src、发行名personal-assistant-framework 0.1.0，var/releases指针不存在；版本号本身不代替材料摘要。原库DSN仍在此前授权的本机临时配置，没有把值输出到聊天。

准备材料在Git忽略目录：`assistant-host.candidate.json` 只含非秘密参数/句柄占位，保留原owner默认owner、原扩展/制品目录及主站Access，加入已有ntfy发布句柄；配置语法load_profile通过。数据库和备份密钥句柄仍待配置，候选没有激活。`assistant-switch-plan.json` 明确先备份及隔离恢复、冻结启动核查、完整发布门禁；默认不开放发信/浏览器提交。`restore-migrations-0011` 只复制与原库逐字节匹配的11份SQL，没有修改迁移历史，也没有执行SQL。

兼容阻断：原库只有11份迁移；现成restore脚本默认迁至12后会按严格清单拒绝旧快照，因此准备在独立空目标使用匹配11迁移材料。进一步核对发现，即使目标匹配，`freeze_snapshot()` 第194行仍无条件增加空runtime_records项，随后restore_database会要求该表存在。没有修改备份迁移清单、补造未登记表或降低恢复保护来绕过。

纯合成反例（没有真实业务数据、数据库或秘密）：

```powershell
.\.venv-win\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts=--strict-markers -q --tb=short var/verification/f086-ntfy-2026-10-24/test_legacy_snapshot_schema.py
```

实际 **1 failed（0.27s）**：输入tables仅tasks，冻结后多出runtime_records。失败测试保留。真实旧备份恢复未执行；恢复缺表结论来自该动态变换反例及restore_database的确定检查路径，不冒充数据库恢复实测。

修复涉及F09的operations/recovery.py及回归测试，按AGENTS跨边界规则先记录并请求最小范围确认。当前未改产品代码；通知新凭据与独立服务继续有效，原助手未启动、原库未写、新迁移未执行、未提交或推送。F08.6继续BLOCKED，不启动其他F09功能。此前DELIVERY.2本地验收及F06.P2/F09.1的DONE结论按原验收范围保留。

### 旧快照兼容最小修复的后续（2026-10-25）

本人明确授权后，已仅增加runtime_records键存在性判断并补三单元/两真实PostgreSQL回归。先红后绿，单元及原反例11 passed、定向PG3 passed；完整本地1408 passed/137 skipped/7 warnings，Ruff/Mypy263通过；随后完整临时PostgreSQL/Worker137 passed。运行器及测试容器0，原库仍11迁移未写，新版未启动。详见[F08.6旧快照修复报告](F086_LEGACY_RECOVERY_FIX_2026-10-25.md)。兼容问题实现已就绪，下一步等待本人准备备份恢复密钥，再继续加密恢复点和隔离恢复准备；F08.6整体继续BLOCKED。


## 9. 本人生成密钥后的真实加密备份与恢复停点（2026-10-25）

本人反馈“已生成”后，已在进程内核验Windows凭据库backup_key为有效32字节密钥；没有读取本人离线副本。原已授权DSN仅经内存转为Windows凭据库database_dsn句柄，未轮换口令；候选profile填入两项句柄并通过load_profile，没有激活正式profile。原临时配置保留。

实际使用现成CLI创建一次恢复点，并认证读取：

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B scripts/assistant-backup.py create --profile var/deployment/f086-ntfy-2026-10-24/assistant-host.candidate.json
.\.venv-win\Scripts\python.exe -Xutf8 -B scripts/assistant-backup.py verify --profile var/deployment/f086-ntfy-2026-10-24/assistant-host.candidate.json --backup-id 20261025T053430_2b42ab498d23450fbfbf55c95cce234b.pa-backup
```

两次exit0。**create是本次已执行的一次性备份操作，不能当作只读回归命令重复执行。** 文件位于受限ACL的var/backups，创建时间2026-10-25T05:34:30.566194+00:00，密文SHA-256 `ab0e17bca451f808e8752e0dcf1187dd04457750e0ea89263774201cac437297`。认证包包含34张表、0制品；11份核心迁移均匹配。仅检查表名/数量及安装材料摘要，不输出邮件正文或备份解密明文。

备份包含13张邮件扩展自有表。静态绑定确认实际安装nju.smail@0.1.0工件摘要及0001_mail.sql摘要分别匹配备份安装记录和扩展账本；不能改用源码树0.2.0。宿主验证器静态检查通过，已复制同一份DDL并准备固定空账本及独立目标计划，尚未执行。详见[隔离恢复批准材料](F086_ISOLATED_RESTORE_PLAN_2026-10-25.md)，计划SHA-256 `883894771a00cd456096343d2d5baff587db579bf2f562cba54cde233571bdd0`。

**尚未创建恢复容器/目标库，尚未进行真实备份的隔离恢复**。AGENTS要求扩展执行前确认，因此等待本人批准该确切静态Schema-only材料；原库0012及新版启动另行确认。原库未迁移/改写，原助手未启动，未调用真实模型/邮箱/ehall。当前只做备份准备及静态核验，未重复全量；前一最小产品修复的完整门禁见第8节和专项报告。F08.6仍BLOCKED。


## 10. 本人批准后的真实隔离恢复通过（2026-10-25）

本人明确回复“允许”后，按已绑定摘要的静态nju.smail@0.1.0建表材料，在全新tmpfs PostgreSQL目标执行11迁移及Schema-only预建，再用现成恢复函数导入真实认证备份。34张表全部行在内存与冻结预期比较一致，5项序列只读检查通过，13张扩展表含原迁移账本；两个扩展保持DISABLED。没有启动Worker/Agent或连接模型/邮箱/ehall。

最终exit0；临时容器/临时凭据/空制品目录均清理，单独标签查询确认容器0，备份密文哈希未变。原库再次只读预检仍11迁移匹配、0012待执行，8000/8001/8010无监听。两次配置/就绪阶段失败及清理证据保留，未在原库试错。精确命令、摘要、失败与成功界限见[隔离恢复计划及结果](F086_ISOLATED_RESTORE_PLAN_2026-10-25.md)。

此为已批准恢复操作验证；未重复全量，本轮无新增产品代码。最小产品修复此前完整本地1408 passed/137 skipped/7 warnings、完整PostgreSQL/Worker137 passed。F08.6仍BLOCKED；下一步准备原库0012与新版冻结启动切换材料，执行前另行确认。未提交、推送或部署新版。


## 11. 新版手机访问及真实任务通知点击通过（2026-10-25）

原库0012和冻结新版启动后，本人普通刷新桌面入口，反馈“正常，没有冻结提示”。已核对当前app.js的loadSystem仅显示后台/模型/通知三项，本来没有渲染API的frozen字段；因此缺提示不能用来判断旧脚本或未冻结。宿主独立检查freeze=true、FROZEN_RUNNING及拥有者心跳更新。未修改页面或解除冻结。

只读以本人给出的精确自检标题定位唯一既有任务 task_f97a59148a5444a6b2223ed830b4683f，未新建任务。宿主正式LocalNtfyNotifier通过现有publisher句柄仅调用一次notify_pending(task_id=...,risk="R1")，回执accepted=1、unknown=0、reconfigure_required=0；写入前先独占记录结果未确认，不自动重复。载荷仍仅kind/risk/task_id，固定标题Personal Assistant、Cache:no，Click为固定助手域名/ui/?task=该ID。

随后请求本人点击最新系统提醒；本人回复“是”，确认进入yanshuheng.dpdns.org并自动显示“第二个自检，不执行外部动作”的任务详情。**真实通知收件及正确任务链接分支通过**；不把relay accepted单独当作真机证据。没有执行任务、保存、批准或发信。

手机实际运行静态版本及敏感缓存仍需核验。已用此前官方ADB只查询连接状态：exit0、device_count=0，没有输出序列号或读取手机内容；已请求本人连接USB，后续仅读取助手静态版本、缓存名称/路径，不读Cookie、正文或存储值。当前仓库/已绑定交付包的Service Worker为v16；尚不据此声称手机v16已验证。

正式单次审批仍未验收：当前安装nju.smail@0.1.0没有新版smail.send.preview声明，且mail_send_enabled=false。按已有review代码，正向材料核对不能返回nonce；不能靠seed一条approval或伪造预览跳过。保持当前授权边界，不升级扩展或开放发件。本结论来自静态接口/材料核对，不冒充真实审批测试。F08.6仍BLOCKED，等待手机缓存/版本及既有审批、丢回执/网络等剩余证据。


## 12. USB 查询停点与正式审批候选（2026-10-25）

本人取消桌面 Chrome 的 Discover USB devices 后，官方 ADB 再查仍 0 个设备；未读手机内容、未重置授权或驱动。实际手机版本/缓存继续待验。已静态核验只用合成 `.invalid` 账户、真 Worker/MIME 和现有 ApprovalService 的正向手机审批方案；它需要升级邮件扩展0.2.0、临时受控配置及受控重启，尚待本人批准，未实际操作。精确范围及材料见[手机审批自检候选](F086_MOBILE_APPROVAL_PLAN_2026-10-25.md)。F08.6继续BLOCKED。


## 13. 邮件扩展升级与合成审批准备通过（2026-10-25）

本人明确批准后，正常确认升级nju.smail0.2.0成功；独立Admin已关闭。已用合成.invalid账户/真实Worker生成版本1草稿并核验MIME，冻结主站与原Tunnel已恢复，独立ntfy保留。六项入口/健康/Access检查通过；既有4个jobs不变，Outbox/邮件账本及本夹具发送动作均0。审批尚未创建，等待手机准备后才开始5分钟TTL。完整操作与限制见[手机审批自检执行结果](F086_MOBILE_APPROVAL_PLAN_2026-10-25.md)。F08.6仍BLOCKED，手机缓存/正式批准/丢回执及独立中断等证据仍待验。


## 14. 手机首次批准及收尾（2026-10-26）

本人确认材料相符并显示已批准，真实仓储当时APPROVED版本1、未消费。重复批准检查前记录已到期，未执行该检查；不能报409通过。cleanup已CLEANED，审批EXPIRED版本2且保留原批准信息；自己的合成账户/凭据移除，原配置JSON恢复，草稿/MIME/审批审计保留，jobs4/Outbox0/邮件账本0。禁发restored原曾因私有OS探测超时停止，本轮修复其常驻监视（离线20 passed），保留旧失败journal，使用独立recovery-20261026恢复冻结主站/Tunnel。20项HTTP及一次完整身份/回环监听核验通过，Admin关闭，独立ntfy就绪。实际范围及未验项见[审批收尾](F086_MOBILE_APPROVAL_PLAN_2026-10-25.md)，F08.6仍BLOCKED。


## 15. 手机当前v16与存储目录核验通过（2026-10-27）

通过官方ADB及正式Target目录实际附着Chrome助手/ui/页面：主app.js与Worker源码摘要均匹配部署材料，页面控制器是该Worker，CACHE实际v16。唯一CacheStorage只有四条静态路径；LocalStorage/SessionStorage键名为空，IndexedDB数据库名为空。未读Cookie/正文/存储值，未刷新/更新Worker/清数据，已移除全部自建转发。此为当前Chrome助手origin快照，非HTTP缓存底层或全历史数据证明。完整摘要及证据见[手机版本/缓存核验](F086_MOBILE_APPROVAL_PLAN_2026-10-25.md)。本项通过，F08.6仍BLOCKED，接下来补真实丢回执及独立中断。

## 16. 2026-10-27 真机保存丢回执及同键重试

在本人实际Chrome上，精确截断固定公开草稿PUT 200响应前，原库已提交10；本人显式采用服务器稿并手动同请求重试后，手机确认10、原库版本/更新时间/10条命令/版本10唯一命令均不变。该分支通过；此前限速正常保存9不计丢回执。拦截/网络/自建转发均已收尾，冻结且无外部动作；完整命令、摘要与边界见[回执记录](F086_MOBILE_APPROVAL_PLAN_2026-10-25.md)。F08.6仍BLOCKED，独立Cloudflare中断等尚待验。

## 17. 2026-10-27 主站独立中断及最终收尾

只暂停主站Tunnel90秒并恢复同一进程，未操作ntfy。本人手机助手断连而私有health正常，恢复后下拉刷新恢复；19组health全200、宿主仍冻结、Admin关闭、无外部动作。最终手机v16/四静态缓存及空Local/Session/IndexedDB、任务/草稿/审批200/no-store确认。当前结果与剩余范围见[真机交接报告](F08_6_REAL_DEVICE_REPORT_2026-10-27.md)；F08.6保持BLOCKED，等待独立复验。
