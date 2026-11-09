# WORKSPACE.1 安装与工作区切换记录

日期：2026-11-09。原实例 profile：`var/deployment/ext-dev2-2026-10-30/host.json`。

**安装和自动化集成检查通过，WORKSPACE.1 保持 NEXT / WAITING，仅待实际终端显示、输入和滚动检查。** 用户已明确授权本次升级、启动新版宿主、绑定原读写目录并自动索引。此前 A01–A04 独立复验结论见[领取取消复验报告](WORKSPACE1_CLAIM_CANCEL_REAUDIT_2026-11-09.md)，本轮未重新开发产品。

## 已安装材料

| 扩展 | 安装前 → 安装后 | 安装材料 SHA-256 | 正式升级操作 |
| --- | --- | --- | --- |
| personal.files | 0.1.0 → 0.1.1，ENABLED | `8622e7f7bb9fbe56258907c123fc404cb5d3e7ae10de9c3b2d74ad55e9fd6c87` | `extop_5de187fa0fd944dc909e41648b2b268c` |
| personal.knowledge | 0.1.6 → 0.1.7，ENABLED | `bbf686b2e2e894bcd97b04021f26536b4e8e399508a5ede636bb95297b648112` | `extop_b37d0d7527fc49f092dec33805838eb2` |

正式 inspect、绑定摘要/nonce 的确认、升级、安装载荷摘要复核均成功。files 无必需宿主能力，knowledge 仍仅需 `extension.data.sql`；无新增第三方运行依赖或迁移。两个扩展仍是本机用户信任代码，Worker/venv 不构成恶意代码沙箱。

原实例沿用现有 `personal` 根及已授权的 `read_write`。宿主统一工作区是目录授权来源，两插件已将独立 `roots` 改为 `workspace_binding: "instance"`，业务参数及文件计划状态目录保留。后续只需在设置中编辑、保存、应用共享工作区；只保存不会切换运行目录。

## 原实例结果

- 新版 Public、Admin、Agent、health 已启动，启动就绪；Workbench 接口能力匹配。手动冻结仍为 true，不领取任务，不自动继续原任务。
- 聚合工作区和两个插件配置均为 APPLIED。在线 Admin/Public 两侧各有对应插件运行副本：每插件共 2 个 RUNNING，generation 与 loaded_digest 均匹配当前应用版本。
- 配置应用后自动索引达到 READY，索引表计数为 40 来源、40 版本、747 分块。工作区概览列出 95 个可见文件，源目录完整性核对覆盖 100 个文件；三者统计范围不同，不能把 95 或 100 当作已建立索引的文档数。
- 原 15 个任务的 ID、状态、版本，以及作业状态计数（FAILED 4 / SUCCEEDED 127）前后相同。
- 原目录文件哈希、手动冻结文件哈希及其他扩展登记相同。没有写入原资料文件，也未触发旧任务或真实模型。
- `nju.ehall`、`nju.smail` 安装前已有 QUARANTINED，本轮保持；本报告不宣称所有插件正常。

运行代码来自当前项目的可编辑安装及 `.venv-win`，没有切换不存在的 release pointer，也未提交或推送 Git。

证据位于 `var/deployment/workspace1-2026-11-09/`：`operations.json`、`after-upgrade.json`、`workspace-applied.json`、`index-evidence.json`、`host-started.json`、`live-verification.json`、`live-workspace.json`、`live-system.json`、`live-capabilities.json`。含目录或运行配置的文件只保存在 Git 忽略的本地目录，不复制到报告或聊天。

## 安装前备份

原实例此前停止且手动冻结。变更前已保存任务、作业、扩展、已保存/已应用配置、工作区状态、冻结状态和源文件摘要基线，并使用现有 Windows 凭据库中的备份密钥句柄生成：

| 本地备份 | 内容 | SHA-256 |
| --- | --- | --- |
| `database-before.enc` | 一致性只读数据库快照，43 表，密文 10,336,344 字节 | `8ec1339cd3f7b8dee461984e78278018505ad29b1613cf7b7bda3e5d5a208eaa` |
| `local-state-before.enc` | 62 项本机配置/控制状态/文件计划 SQLite 等快照 | `42a6e68facb847c37844369a4b32ff5e50b981ccbd692125af397d3e5a587cce` |

加密后解密回读与原快照一致，证据为 `backup-evidence.json`。**这不是隔离恢复演练，也不是 FILES.BACKUP.1 的工作区资料备份完成证明。** 本次未备份或修改用户源文件内容；只记录完整性摘要。

回退必须配套恢复旧插件理解的 legacy roots 配置，不能仅降级版本却保留 workspace_binding。版本回退也不会自动还原知识索引或文件计划数据库；如需恢复，应基于本轮加密快照另行执行并核验。

## 已安装插件的隔离 TUI 集成检查

使用独立 PostgreSQL、隔离实例和合成 A/B 目录，正式安装同一候选，然后运行真实 WorkbenchApp/Textual 控件事件，通过回环 HTTP 调用 Admin 与实际安装的 Worker；没有模拟插件、SQL 或工作区 API。

| 操作 | 实际结果 |
| --- | --- |
| TUI 保存并应用 A | 自动索引 READY；检索 Alphaquartz 返回正文 |
| 只保存 B | 显示 PENDING；仍使用 A，scan_id 不变 |
| 应用 B | 检索转为 Betazircon，A 不再命中；A 旧写计划以 CONFIGURATION_CHANGED 拒绝，未生成旧计划文件 |
| 停止并恢复已安装 Worker | 同一 READY 索引被复用，scan_id 不变 |
| 撤销所有 roots 并应用 | 两插件拒访；副本如实显示 FAILED，未误报可以继续访问 |
| 副作用与清理 | 任务 0、模型请求 0、冻结保持；临时容器、匿名卷及测试/Worker 进程已回收 |

最终 `run3.log` exit 0，`attempt3/result.json` 为 `ok=true`、`workers_stopped=true`、`server_task_done=true`。五张 SVG 的 XML 解析通过。证据与说明见[隔离检查摘要](../var/verification/workspace1-install-2026-11-09/tui-isolated/SUMMARY.md)及同目录的 `resources.json`、`evidence.sha256.json`。

本检查是 **headless TUI 集成检查**，不是实际 Windows 终端人工视觉/键盘验收。computer-use [技能](C:/Users/kunji/.codex/plugins/cache/openai-bundled/computer-use/26.1103.32239/skills/computer-use/SKILL.md)引用的[操作指引](C:/Users/kunji/.codex/plugins/cache/openai-bundled/computer-use/26.1103.32239/docs/guidance.md)明确规定：“Do not automate terminal applications such as Windows Terminal, Command Prompt, or Windows PowerShell.” 因此不以 Windows 终端自动操作代替本人最后检查。

## 实际执行命令与检查范围

以下命令均在项目根运行，日志保留。安装/隔离 helper 是本轮单次材料，包含基线与重复运行保护；资源已回收后不可盲目重跑。需要再执行时应重新建立隔离资源和基线。

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B var/deployment/workspace1-2026-11-09/deploy.py prepare
.\.venv-win\Scripts\python.exe -Xutf8 -B var/verification/workspace1-install-2026-11-09/tui-isolated/run.py
.\.venv-win\Scripts\python.exe -Xutf8 -B var/deployment/workspace1-2026-11-09/deploy.py upgrade
.\.venv-win\Scripts\python.exe -Xutf8 -B var/deployment/workspace1-2026-11-09/start.py
.\.venv-win\Scripts\python.exe -Xutf8 -B var/deployment/workspace1-2026-11-09/verify-live.py
git diff --check
```

`verify-live.py`核对的任务/作业等基线只适用于本次冻结安装窗口，未来正常使用产生状态变化后不能继续将相同断言当作日常健康检查。

准备 helper 最初两次因 jobs 字段名、备份句柄取值方式错误失败；均发生在升级前，修正后备份成功。历史错误保留为 `prepare-error-history.json`。隔离脚本前两次因控件标签 ID 和 reload 动作名错误失败，修正脚本后新隔离实例第三次通过；原 `run.log`、`run2.log` 保留，产品代码未改。

本轮只进行安装检查、已安装材料的隔离 TUI 专项及原实例只读核验；按用户避免重复完整套件的要求，没有重跑完整本地或完整 PostgreSQL/Worker 门禁，也没有将此前独立复验的数量计作本轮运行。未进行手机、真实模型、邮件发送或网页提交测试。

## 最后一项人工检查

在电脑 PowerShell 中执行：

```powershell
& 'C:\Users\kunji\Desktop\homework\生成式软件工程\lab1\scripts\assistant-tui.ps1' -Profile 'var/deployment/ext-dev2-2026-10-30/host.json'
```

1. 设置 → 实例共享工作区 → 读取状态，确认显示已生效，两个消费者均 APPLIED。
2. 扩展页确认 personal.files 0.1.1、personal.knowledge 0.1.7 为 ENABLED。
3. 检查窗口布局、滚动和输入焦点正常。可在聊天框输入测试文字后清空，不发送、不执行任务。

这些只读检查完成并反馈后，才关闭 WORKSPACE.1。当前后台冻结是本轮约定保留的手动状态，不是本次升级失败。日常任务执行前另行按正常管理操作解除冻结。

**后继仍为 FILES.BACKUP.1（WAITING，尚未开工）**；CORE.RECONCILE.1 WAITING 正式验收/部署、F08.6 BLOCKED、F07/F08 IN_PROGRESS 不因本次安装自动改变。
