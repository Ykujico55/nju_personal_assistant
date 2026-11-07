# 本地开发交付运行手册

2026-10-13。本手册描述已实现的启动入口。真实邮箱、ehall、手机通知、Cloudflare/Tailscale 联调与独立 Beta 验收仍待进行；F08.6 保持 BLOCKED。执行前在仓库根目录打开 PowerShell。

## 1. 本机开始使用

```powershell
.\scripts\assistant-host.ps1 -Action start -Profile deployment/host-development.json
.\scripts\assistant-host.ps1 -Action status -Profile deployment/host-development.json
```

访问 `http://127.0.0.1:18100/ui/`。开发模板使用内存、共享容器中的嵌入 Worker，重启会丢任务；仅用于本机体验，不能当生产持久化。health 在回环 18110。普通启动不启用 Admin。

需要本机管理时，先停止以上进程，再显式前台启动：

```powershell
.\scripts\assistant-host.ps1 -Action stop -Profile deployment/host-development.json
.\.venv-win\Scripts\python.exe -Xutf8 -m personal_assistant.host_entry serve --profile deployment/host-development.json --admin
```

Admin 页面为 `http://127.0.0.1:18101/admin/ui/`，只允许本机。内存模式的 Admin 和公共进程不共享扩展状态；安装/管理扩展应使用下面的 PostgreSQL 模板。公共入口不发布管理路由。

1. 新建任务，在详情查看真实执行状态；缺配置或材料时显示等待用户，不假装完成。
2. 选择已启用扩展发布的工作流，填写声明输入；结构能力不支持时拒绝执行。
3. 使用远端模型前预览并确认本次精确披露请求。请求或接收端改变需重新确认；SECRET 永远禁止发送。
4. 外部动作单独审核权威目标、正文/载荷、附件和版本。批准一次后，Worker 恢复同一精确动作。
5. UNKNOWN 只进入对账，不能点普通重试重新发送。未保存输入或结果未确认时保留页面，使用原请求重试；发生版本冲突时显式选取版本。

## 2. 持久主机配置

复制 `deployment/host-production.template.json` 为 Git 忽略的 `var/config/host.json`，填写现有 Access 域名/AUD、公开 origin 和 Windows Credential Manager 的 **database_dsn 句柄**。模板只含非秘密参数，不能把 DSN 口令、API Key 或 Token 写入配置。

数据库首次启动按顺序验证/应用 0001–0012；本次开发没有迁移原助手数据库。生产主机公共/管理/health 仍分别只监听回环 8000/8001/8010。独立 Worker 使用 PostgreSQL 队列和记录，不能使用独立内存进程冒充持久运行。

模型配置脚本由本人在本地交互终端执行：

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 scripts/configure-model.py
```

当前用户已成功配置 `https://api.deepseek.com` / `deepseek-flash`，公开自检一次共104 token；这是模型连通证据。脚本隐藏输入，将密钥保留在 Credential Manager，`var/config/model.json` 只含句柄和公开模型选项；主机自动读取它。启动新进程才加载新配置，原线上服务本轮没有重启。运行任务所需的披露许可另行精确确认。

`assistant_workflows@0.1.0` 是可审阅的组合扩展。安装必须先 inspect 静态材料，再确认精确哈希/警告；本批次未自动安装或执行它。邮件工作流声明检索→材料→草稿→人工确认→受监督发送→归档；表单工作流声明证据→材料→预览/填写。正式 F07 目标/payload/材料权威绑定不齐时，宿主会拒绝 R2 浏览器准备，试验预览仍不可批准。

## 3. 停止、冻结与登录启动

```powershell
.\scripts\assistant-host.ps1 -Action freeze -Profile var/config/host.json
.\scripts\assistant-host.ps1 -Action stop -Profile var/config/host.json
.\scripts\assistant-host.ps1 -Action status -Profile var/config/host.json
```

冻结在 Tool Gateway 派发前复核，Worker 监测任务取消及 kill flag。守护器只管理自己创建的子进程，有限次数重启，磁盘不足或备份失败会冻结。Windows Job Object 在守护器退出时回收其后代；不把可信扩展进程称为恶意代码沙箱。元数据日志在对应 `var/.../logs`，不记录正文或凭据。

用户选择启用登录启动时调用 `-Action install-startup`；撤销为 `uninstall-startup`。只注册当前用户的 InteractiveToken 登录任务，不覆盖同名任务，不在 Session 0 启动可见浏览器。本轮未注册任务。排查后显式 `-Action resume`；UNKNOWN 不因 resume 自动重发。

### 正常重启（CORE.HOST.1，已部署）

~~~powershell
.\scripts\assistant-host.ps1 -Action restart -Profile var/config/host.json
~~~

`restart` 保留原来的 Admin 模式，先冻结领取、正常停止旧守护器及其子进程，等锁释放后隐藏启动新守护器，并核对本次启动身份和全部组件就绪。原本冻结的实例继续冻结；原本未冻结的实例仅在就绪且期间没有新的人工/故障冻结时恢复领取。失败返回明确错误码和关联编号，保持冻结；不会按陈旧 PID 结束不明进程。

重复 `host_entry serve` 返回 `HOST_ALREADY_RUNNING`，不冻结现有服务。重启进行中再次请求返回 `HOST_RESTART_IN_PROGRESS`。直接 `stop` 再 `serve` 沿用原来“停止后保持冻结”的行为，不等同于保留领取策略的 `restart`。

仍运行旧版守护器、缺少启动身份记录时，返回 `HOST_RESTART_UNSUPPORTED`，不猜测或直接强杀。首次加载本次代码需要正常 `stop`，确认旧进程退出，再使用原 profile 和原 Admin 模式 `serve` 一次；以后即可使用 `restart`。2026-11-07已按本人要求完成原实例首轮stop/serve和后续restart核验；现在可使用上述restart命令。

## 4. 加密备份与空目标恢复

本人运行 `scripts/configure-backup.py` 生成/录入独立恢复密钥，并将句柄加入 host profile；需要恢复时应保留本人离线副本。不要将密钥交给聊天或日志。定期备份默认每六小时一次，数据库全快照、制品按内容哈希增量存储，AES-GCM 认证加密；本机保留7日/4周/6月代表点。离线期间只补最近一次。可选异地目录复制的文件仍加密，异地保留需用户单独管理。

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 scripts/assistant-backup.py create --profile var/config/host.json
.\.venv-win\Scripts\python.exe -Xutf8 scripts/assistant-backup.py list --profile var/config/host.json
.\.venv-win\Scripts\python.exe -Xutf8 scripts/assistant-backup.py verify --profile var/config/host.json --backup-id <备份文件名>
```

备份覆盖的是实现允许恢复的PostgreSQL表及artifacts中登记的制品，不能当作整台电脑或整个var目录镜像。以下内容需另行保管：知识库/简历授权目录的原文件；开发插件workspace_root（候选/发布源码、基线、SQLite及控制记录）；招聘独立CLI的.local/career.sqlite3；扩展包/环境与主机配置。Windows凭据、浏览器会话和授权材料不应混入普通源码副本。

独立工作区副本应在对应插件停用、验证/安装等写入者退出后完整保存到本人控制的加密位置，并核对文件清单/摘要。旧控制记录不是新机器的执行许可；目前没有开发工作区自动安全恢复，不能复制旧control后直接启用。正式恢复应另行核对材料并重新批准验证/安装。

恢复只接受新的 `*_restore` 或 `*_test` 数据库名和新的制品目录，先只读检查空目标，再迁移和事务导入；不同凭据句柄指向原库也会拒绝。备份迁移版本/哈希必须与恢复代码一致。扩展 Schema 必须先经批准安装后才能恢复其业务数据，备份不执行包中的 SQL/代码。

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 scripts/assistant-backup.py restore --profile var/config/host.json --backup-id <备份文件名> --target-database-handle <新库句柄> --target-artifacts var/restore/new-artifacts
```

恢复后的任务/运行暂停、旧作业 DEAD_LETTER、审批过期、未决外部动作 UNKNOWN、扩展/调度停用。凭据、浏览器会话、Push订阅和模型披露许可不恢复；先重新绑定并只读核查，再逐项启用。不恢复到原库，不自动继续外部动作。

## 5. 离线升级与回滚

准备包含核心和所有依赖的固定 wheelhouse；逐项 preview 的 SHA-256/hash 是用户确认材料。先冻结并停止主机。

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 scripts/assistant-upgrade.py preview --profile var/config/host.json --wheelhouse var/wheelhouse
.\.venv-win\Scripts\python.exe -Xutf8 scripts/assistant-upgrade.py apply --profile var/config/host.json --wheelhouse var/wheelhouse --accept-hash <预览hash>
```

apply 先生成加密恢复点、固定复核材料副本、建立新独立环境、离线安装与依赖/导入检查，再原子切换解释器指针；失败保留原指针并冻结。没有备份不切换，不自动恢复运行。完成后必须核对候选 health、迁移、队列和只读恢复状态再显式 resume。

`rollback-code` 只切回保留解释器，数据库必须按对应版本的备份恢复到隔离库、核对后再改句柄；不会偷偷降级或覆盖原数据。本轮没有安装升级或登记启动任务。

## 6. 本轮未完成的真实验收

截至2026-10-30：手机v16脚本/缓存、丢回执同键重试、正式审批、独立Cloudflare中断及ntfy订阅/锁屏/撤销恢复/点击已有真机补证；Cloudflare恢复后本人手动刷新恢复，不冒称自动恢复。F08.6仍因独立完整门禁的F07创建超时记录保持BLOCKED，见最新NEXT_STEPS。F07正式真实事务完整材料与Gateway/Outbox实站链路、当前版本受控真实发件、BOSS实站、长期运行和完整迁机/升级仍待验证。此前加密备份隔离恢复通过仅对应当时固定材料，不涵盖新增开发工作区。

## 管理请求断连后的核对

CLI已收到operation_id时，用assistantctl extension operation <operation_id>单次读取原操作，PENDING/RUNNING不是完成；不要重新执行install/upgrade。若POST丢回执且没有ID，只能先读取扩展状态核对，不能凭空恢复ID或用新键盲目重发。

本机Admin安装/升级依据静态预览的mode，确认后绑定原材料；页内刷新保留未知命令。配置PUT未提供CAS/命令回执，页面先读取当前配置；保存丢回执后只能GET原扩展核对，当前值不同由本人明确选择，选择本身不写入。整页退出会提醒但不持久化原请求，重新打开后须先只读核对；不能把它当作跨重启去重能力。
