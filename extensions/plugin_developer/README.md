# 受监督插件开发扩展

assistant.plugin_developer@0.1.0 将“创建候选 → 编辑代码 → 审核 → 获准验证 → 修复 → 本人确认安装”接入现有助手。业务在独立扩展中，只用公开SDK，不修改宿主核心，也不向Agent开放本机Admin或任意shell。

首次交付时尚未安装；2026-10-30已获准安装到原实例，并由真实DeepSeek完成计算器候选试写。候选本身尚未安装，详见[真实试写记录](../../docs/EXT_DEV2_REAL_MODEL_TRIAL_2026-10-30.md)。新实例按[INSTALL.md](INSTALL.md)安装配置；开发工作区须另行备份，宿主数据库备份不包含它。

## 能做什么

| 工具 | 风险 | 作用 |
|---|---|---|
| developer.workspace.create | INTERNAL_WRITE | 为指定扩展ID/版本创建新候选，返回candidate_id |
| developer.candidates.list | READ | 查看本工作区的候选 |
| developer.files.list | READ | 列出候选文件、SHA-256与大小 |
| developer.files.read | READ | 分页读取候选文本，返回整份文件哈希 |
| developer.files.write | INTERNAL_WRITE | 新建文件或以旧哈希CAS替换完整文本 |
| developer.review | READ | 展示相对初始骨架的diff、Manifest、静态检查与候选摘要 |
| developer.validate | INTERNAL_WRITE | 仅在本机本人授权有效时运行固定验证 |
| developer.validation.status | READ | 查看授权剩余次数和有界验证结果 |
| developer.release.request | INTERNAL_WRITE | 为已通过验证的精确摘要准备安装快照与本机交接标识 |

最后一个工具不安装、不启用。Agent不能通过这些工具签发自己的测试授权或获得安装确认nonce；本人在本机终端核对后，才走现有宿主CLI的精确材料确认。

## 1. 配置专用工作区

本机Admin配置示意，路径须换成本人实际预先创建的空目录：

~~~json
{
  "config": {
    "workspace_root": "C:/Users/kunji/Desktop/homework/生成式软件工程/lab1/var/plugin-development",
    "validation_timeout_seconds": 30
  }
}
~~~

workspace_root必须为已存在的绝对目录。首次只能为空，避免把主项目、简历或资料库当作开发目录；使用过后凭本扩展管理标记重新打开。不要手工清除标记、控制记录或候选基线。

候选放在candidates下，初始骨架基线/CAS记录及验证授权在候选树外持久保存。Agent工具只能编辑自己生成候选中的允许文本，不能修改原助手源码、records、control或已导出的发布目录。

工作区单文件最多512 KiB、每候选总计2 MiB/100文件；只接受.py/.toml/.json/.md/.txt/.lock。相对路径拒绝穿越、隐藏控制路径、盘符/UNC/ADS、符号链接/junction及大小写别名。它是一项编辑策略，不是执行恶意代码的沙箱。

## 2. 给助手一个明确开发任务

在任务详情“继续任务”选择Agent，示例：

> 创建一个独立fixture.notes扩展，版本0.1.0。先生成公开SDK骨架，按我的需求修改，并写能验证真实行为的unittest。只在开发工作区操作。先给出文件差异、依赖、权限和摘要，等我在本机授权验证；不要自行安装或发送任何外部请求。

助手应依次使用create、files.list/read/write、review。新骨架提供READ ping工具及真实的SDK生命周期/调用unittest，不是恒真占位测试。仅有骨架和测试通过不代表业务需求已实现，仍须检查实际变更与用例。

新文件写入时省略expected_sha256；替换已有文件时使用files.read/list返回的完整旧文件SHA-256。每次write替换整份文本，不能把一段分页内容误当完整文件。单次工具content最多131072字符；files.read默认16000字符，可用next_offset继续，最多32000字符一页。

extension.toml的ID/版本与create时绑定，不能在该候选中偷偷更换；新版本另建候选。修改冲突应重新读取，不覆盖别人已更新的代码。

模型接收候选代码、diff和有界失败摘要时仍按现有披露许可处理；不要把真实密码、令牌、私人邮箱正文或简历放进候选/测试夹具。

## 3. 本人在本机授权验证

以下命令在交互式PowerShell中执行，不通过管道、后台任务或Agent工具代为输入YES。manage.py只加入本扩展的源码路径；$HostPy是具有公开SDK的本机宿主Python。

~~~powershell
Set-Location -LiteralPath 'C:\Users\kunji\Desktop\homework\生成式软件工程\lab1'
$HostPy = (Resolve-Path -LiteralPath '.venv-win\Scripts\python.exe').Path
$ManageScript = 'extensions/plugin_developer/manage.py'
$WorkspaceRoot = Read-Host '输入Admin中已配置的workspace_root完整路径'
$CandidateId = Read-Host '输入助手返回的candidate_id'
$Digest = Read-Host '输入developer.review返回的candidate_hash'
& $HostPy -Xutf8 -B $ManageScript --workspace-root $WorkspaceRoot approve-tests $CandidateId --digest $Digest
~~~

终端展示完整review和不执行候选的静态检查；核对代码、依赖、Manifest权限、风险后，本人输入YES。默认授权为**精确摘要、15分钟、一次运行**；代码改变后需要新的审核授权。

如果本人愿意让助手在固定元数据下迭代修复，可改用：

~~~powershell
& $HostPy -Xutf8 -B $ManageScript --workspace-root $WorkspaceRoot approve-tests $CandidateId --digest $Digest --iterative
~~~

该授权为**30分钟、最多5次运行**。允许源码/测试修复，但extension.toml、requirements.lock或pyproject.toml任一变化都会要求重新授权。它不是永久执行权限。

授权意味着本人信任候选代码以当前Windows用户运行。验证子进程、工作区及venv均不构成恶意代码沙箱；代码仍须遵守Gateway副作用契约，测试使用合成材料，不在验证过程中真实发信或提交网页。

## 4. 助手运行验证并修复

回到助手，明确要求：“对刚核对的候选摘要调用developer.validate；根据真实失败修复，再review。未经新授权不扩大依赖或权限。”

验证器只运行固定unittest discover，不接受shell命令，不自动安装依赖。当前环境缺依赖时明确失败；不能靠测试运行成功推断候选的依赖锁已实际安装验证。额外依赖必须精确锁版本/哈希，并由本机安装流程处理。

validation_timeout_seconds默认30秒，可配置1–120秒；实际测试预算还受宿主传入的UTC deadline限制，预留10秒清理时间。剩余不足1秒测试预算或deadline无效时不启动，也不消耗授权。超时、取消或无效回执不记通过；零测试、全跳过、测试失败不产生可安装通过凭据。原始stdout/stderr日志留在本机，Agent仅取得有界状态/失败名称等摘要。

同一验证请求重放读取已记录结果，不静默重跑。同一候选任意快照结果UNKNOWN或仍RUNNING时先本人核对，不能换键或改回旧代码绕过；授权到期/用尽、元数据变化时回到第3步。已有Agent的熔断器保持不变。

可在本机查看记录：

~~~powershell
& $HostPy -Xutf8 -B $ManageScript --workspace-root $WorkspaceRoot status $CandidateId
~~~

## 5. 本人确认安装与启用

当当前精确摘要有PASSED回执后，让助手调用developer.release.request。它导出同摘要的干净快照，返回READY_FOR_LOCAL_INSTALL；不会把本机绝对安装路径或确认nonce交给模型。

先确认本机Admin已按[安装说明](INSTALL.md)为同一持久实例启动，然后执行：

~~~powershell
$Digest = Read-Host '输入本次已通过验证并准备发布的candidate_hash'
& $HostPy -Xutf8 -B $ManageScript --workspace-root $WorkspaceRoot install $CandidateId --digest $Digest --host-python $HostPy --admin-url 'http://127.0.0.1:8001' --enable
~~~

本人在原宿主CLI核对精确包摘要、ID、版本、依赖、能力并确认。命令不添加--yes，也不截取确认nonce；安装成功后--enable才启用。只想安装不启用，省略--enable。

已有版本升级时，候选版本须严格高于已安装版本：

~~~powershell
& $HostPy -Xutf8 -B $ManageScript --workspace-root $WorkspaceRoot install $CandidateId --digest $Digest --upgrade --host-python $HostPy --admin-url 'http://127.0.0.1:8001' --enable
~~~

同版本同材料无需重复安装；同版本材料不同应停止并准备新版本。任何代码变化都要求当前摘要自己的通过回执，旧通过不能证明新材料。

需要回滚时本人执行并确认：

~~~powershell
& $HostPy -Xutf8 -B $ManageScript --workspace-root $WorkspaceRoot rollback fixture.notes --host-python $HostPy --admin-url 'http://127.0.0.1:8001'
~~~

回滚复用宿主现有机制，不撤销已经发送的邮件或提交的事务。Admin保持回环，不向公网发布；完成管理后关闭临时Admin窗口。

## 当前边界

- 可开发候选和进行受监督验证，安装/升级/启用仍经本人本机确认。
- 不提供任意终端、自动依赖安装、修改宿主核心或修改既有已安装插件目录的工具。
- 生成插件不代表业务完成；真实站点、真实邮件和真实模型开发效果仍需各自测试。
- 本轮没有向原服务安装本扩展，没有替本人授予真实候选执行权限，也没有提交或推送。
