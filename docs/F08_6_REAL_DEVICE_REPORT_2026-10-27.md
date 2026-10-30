# F08.6 当前真机验收交接报告（2026-10-27）

## 1. 当前结论

本轮补齐了真实华为手机的实际脚本/敏感缓存、保存丢回执与手动同键重试、独立主站Tunnel中断与私有health隔离证据。此前真实ntfy、已有邮件审批的手机核对和单次批准证据继续按原范围有效。

**F08.6保持BLOCKED，等待独立复验；F07/F08整体IN_PROGRESS。** 本报告是实施交接，子agent只核对了部分文件证据，不等于独立整体验收。没有因通过局部分支而启动其他编号。

实际仓库：`C:\Users\kunji\Desktop\homework\生成式软件工程\lab1`。main HEAD `38b192a4cfef658723b9f23d89d7aeb4dc53263c`；工作区仍含此前已授权的旧快照恢复修复与文档，未提交/推送。本轮没有产品源码变更、迁移、邮件/网页提交、任务执行或模型调用。

## 2. 本轮结果

| 分支 | 实际结果 | 证据来源 |
|---|---|---|
| 实际手机版本 | Chrome153.0.8010.53，app.js及Worker源码SHA匹配，Worker控制当前页面，v16 | 官方ADB + 当前browser Target/CDP，非旧target目录 |
| 当前敏感存储 | 唯一assistant-static-v16、恰四个/ui/静态路径；LocalStorage/SessionStorage键名空、IndexedDB库名空 | 真机只读目录快照 |
| 敏感API缓存策略 | 任务列表、固定Schema草稿、已有正式审批GET均200/Cache-Control:no-store | 实际手机只选响应状态与缓存头，未读正文/认证信息 |
| 丢回执 | 基准9的一次保存提交10后，精确截断唯一PUT 200响应；手机保留原基准/同请求重试 | 真机CDP Fetch Response阶段故障注入 + 原库只读版本/命令计数 |
| 同键重试 | 本人采用服务器草稿后手动重试，手机确认10；服务器版本、时间、命令数均不变 | 本人反馈 + 原库及手机元数据复核 |
| 主站独立中断 | 精确主站Tunnel暂停90秒；本人手机助手断连，私有health正常 | 本人反馈 + 19组回环/私有health200 |
| 恢复 | 同一Tunnel已恢复；本人“下拉刷新后恢复” | 工具成功/持有进程身份 + 本人反馈；自动恢复未单验 |
| 收尾 | 冻结宿主/进程拥有关系一致；8000/8010/8090仅回环，Admin8001关闭；jobs4/Outbox0/邮件动作0 | 原库及Windows监听/父子关系只读核对 |

app.js SHA：`4c99cb084288f8048c26855e048b02edc064b561f5ea69acf0603e971b1ad761`；Worker SHA：`42637945292a450c594d2723ab7dd582038305fc80fea2771fde568e458cf03d`。缓存路径：`/ui/`、`/ui/app.js`、`/ui/manifest.webmanifest`、`/ui/styles.css`。

## 3. 丢回执的完整过程与无效尝试

固定任务为“第二个自检，不执行外部动作”，ID `task_f97a59148a5444a6b2223ed830b4683f`。首次前置状态不符即停止，未限速/保存；后一次基准8的限速仍正常保存9，记INVALID_EARLY_RECEIPT，不计丢回执通过。

本人随后在基准9准备公开文字“手机回执自检-20261027-2”。仅对当前助手页固定草稿URL开启Fetch响应阶段事件，GET不修改直接继续；唯一PUT200已暂停且原库确认提交10/命令+1后，中断该响应，再关闭Fetch并恢复网络。协议事件只选择requestId、URL、method、status；不访问或输出headers/Cookie/postData/正文，不读取应用内部幂等键。没有Network/Runtime/Console事件订阅，辅助脚本未自动保存、重试或直接写数据库；草稿写入来自本人单次保存。

CDP当时读到UNKNOWN/基准9/RETRY_SAME_REQUEST；本人随后见SSE服务器10冲突提示。本人显式采用服务器草稿，再点一次同请求重试，反馈“已确认，是10”。后查手机SAVED/基准10；数据库仍10、updated_at `2026-10-26T16:47:12.480270+00:00`、该任务命令总数10、版本10命令1条。没有清键绕过、增加版本或新增命令。这是受控网络响应故障注入，不冒充物理断网。

## 4. 中断范围与恢复

微软官方PsSuspend1.09文件已验签Valid/Microsoft并绑定SHA。只选当前owner31760对应主站Tunnel42388；持有查询句柄，校验创建时间、父进程、映像/配置/启动参数及冻结心跳；最长90秒，finally恢复同一仍存活进程。未以进程名选择，未改DNS/Access/Tailscale，未停主站后台或独立通知Tunnel。工具首次本机运行接受Sysinternals EULA，未提权/安装服务。

本人中断反馈“出现断连，health是{status:ok}”，恢复反馈“下拉刷新后恢复”。19组health样本（含基线和恢复后）全部200；最后无认证公网/ui/仍302，回环health200。该证据限主站连接器暂停故障，不覆盖Cloudflare平台/Access/DNS全部故障情形。

## 5. 先前已取得的手机证据

- 本人固定域名One-time PIN登录、SSE/任务消息同步、Wi-Fi→蜂窝恢复、断网提示/联网恢复、桌面Chrome标识无地址栏入口已记录。后台或锁屏此前为合并提问，不拆写为两个独立测试。
- 本人已授权ntfy无Firebase替代：真实订阅、前台收取、华为后台/电池设置后的锁屏系统通知、最小任务ID/风险内容、旧读取凭据撤销/长流断开/新凭据恢复、真实任务链接点击均已有证据。通知只打开任务，不执行审批或发送。该设备原生Web Push未通过，不强装Google服务。
- 本人已确认真实手机完整邮件材料相符并只批准一次。审批 `09575ebf-0b3d-4445-ad5f-3ed86733d169` 当时APPROVED/version1/未消费；现CLEANED/EXPIRED/version2保留批准来源，合成.invalid账户/凭据已移除，原配置恢复、发件禁用。真实Worker/MIME验证不等于SMTP发送。重复批准检查前已到期，没有第二次approve HTTP证据。

详细时间点与原始证据见[通知及环境记录](F08_6_NTFY_NATIVE_PREPARATION_2026-10-25.md)、[正式审批/回执/中断记录](F086_MOBILE_APPROVAL_PLAN_2026-10-25.md)。历史章节的“待验”按当时时点保留，当前状态以本报告为准。

## 6. 本轮实际验证与复现

- 私有三个辅助脚本编译/Ruff及默认静态check通过；默认check不访问手机或原库，不启动故障注入。
- 实际真机执行：静态/存储核验、一次有效丢回执故障注入、本人手动同键重试、一次90秒主站Tunnel暂停/恢复、三项只读敏感API缓存头，以及最终进程/监听/数据库只读复核。另保留未满足前置条件及限速未丢回执的无效记录。
- `git diff --check`通过（Windows既有LF/CRLF提示保留）。
- **本轮未重跑完整本地或完整PostgreSQL/Worker。** 本轮没有产品变更；上一轮同产品旧快照修复的完整本地1408 passed/137 skipped/7 warnings和PostgreSQL/真实Worker137 passed，属于2026-10-25实施证据，不计作本轮执行结果。见[原修复门禁](F086_LEGACY_RECOVERY_FIX_2026-10-25.md)。本报告不据此签署整阶段DONE。

只读手机版本/存储命令（需本人已连接授权手机，查询只限助手origin）：

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B var/deployment/f086-ntfy-2026-10-24/inspect-phone-static.py
```

静态check：

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B var/deployment/f086-ntfy-2026-10-24/phone-draft-receipt-cut.py check
.\.venv-win\Scripts\python.exe -Xutf8 -B var/deployment/f086-ntfy-2026-10-24/pause-main-tunnel-check.py check
```

故障注入的精确历史run参数、文件SHA与只读复核SQL范围见[执行记录](F086_MOBILE_APPROVAL_PLAN_2026-10-25.md)。不要重放基准9保存命令、旧PID暂停或已清理的审批夹具。证据/摘要清单位于Git忽略的 `var/verification/f086-mobile-2026-10-27/manifest.json`；不含秘密值，独立审计应先读后核验。

## 7. 保留事项与下一步

1. 唯一NEXT仍F08.6：按本报告及固定材料独立复验，决定哪些未覆盖分支仍是整阶段门槛；重复批准额外真实HTTP分支、Cloudflare中断后的自动恢复、分开后台/锁屏及其他网络场景未新增独立证据。
2. 当前浏览器存储目录与响应头已核验；没有读取HTTP磁盘缓存底层或全部历史内容，不宣称这些取证已完成。
3. 未重复完整门禁、未进行独立整体验收，不把本实施报告当DONE依据；若审计需要完整门禁，应串行运行现有脚本，使用隔离测试资源，保留原服务。
4. 原助手仍冻结，邮件/浏览器提交禁用，保留既有4个jobs。没有为验收恢复自动任务或真实发件。解除冻结及真实业务模型/外部动作试运行需按后续编号与已授权边界准备。
5. F07完整受监督真实办事事务、真实SMTP发送及其他F09/F10实站运行不在本轮通过范围。F07/F08整体维持IN_PROGRESS。

本轮手机配合已结束；不再追加手机操作。后续建议先交独立审计复核这份当前报告。
