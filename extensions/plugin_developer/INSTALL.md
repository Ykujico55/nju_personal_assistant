# 安装受监督插件开发扩展

assistant.plugin_developer@0.1.0，Python3.12，使用公开SDK，无额外第三方运行依赖。本文件是安装操作说明。2026-10-30后续已在原实例安装启用并进行真实模型试写；不要重复安装同版本。实际结果、后台状态及下一步见[试写记录](../../docs/EXT_DEV2_REAL_MODEL_TRIAL_2026-10-30.md)。

## 1. 准备同一持久实例的Admin

沿用现有数据库、扩展根目录和制品目录。先核对实际profile和旧owner，避免另起第二个guardian。历史F08.6 run-approval-host.py只支持冻结验收，不能直接resume作为日常运行器。

确认切换方案、旧owner已退出且端口空闲后，可临时前台启动同一实例：

~~~powershell
Set-Location -LiteralPath 'C:\Users\kunji\Desktop\homework\生成式软件工程\lab1'
$HostPy = (Resolve-Path -LiteralPath '.venv-win\Scripts\python.exe').Path
$RealProfile = Read-Host '输入已核对的实际持久profile完整路径'
& $HostPy -Xutf8 -B -m personal_assistant.host_entry serve --profile $RealProfile --admin
~~~

保持该窗口运行，在第二个PowerShell中执行后续步骤。不要用开发内存profile替换原持久实例，也不要覆盖原库凭据。Admin预期只在127.0.0.1:8001，不加入Cloudflare/Tailscale操作入口。

## 2. 生成干净的安装材料

安装器不读取.gitignore，故只复制本扩展运行需要的公开文件，不把verification、个人候选或控制记录混进安装包：

~~~powershell
Set-Location -LiteralPath 'C:\Users\kunji\Desktop\homework\生成式软件工程\lab1'
$HostPy = (Resolve-Path -LiteralPath '.venv-win\Scripts\python.exe').Path
$Ctl = Join-Path (Get-Location) '.venv-win\Scripts\assistantctl.exe'
$DeveloperSource = Join-Path (Get-Location) 'extensions\plugin_developer'
$DeveloperPackage = Join-Path (Get-Location) ('var\extension-packages\plugin-developer-0.1.0-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $DeveloperPackage -Force | Out-Null
foreach ($PackageItem in @('extension.toml', 'pyproject.toml', 'requirements.lock', 'manage.py', 'src', 'schemas')) {
    Copy-Item -LiteralPath (Join-Path $DeveloperSource $PackageItem) -Destination $DeveloperPackage -Recurse
}
& $Ctl extension status assistant.plugin_developer
& $Ctl extension inspect $DeveloperPackage
~~~

未安装时status返回未找到属预期。核对ID/版本、9个工具、风险、依赖与材料摘要。

首次安装：

~~~powershell
& $Ctl extension install $DeveloperPackage
~~~

由本人核对并确认，不加--yes。确认早于任何安装/build/import/execute。安装操作未明确成功前不启用。

若已有0.1.0且材料相同则跳过安装；同版本材料不同应停止，不能覆盖。只有准备了严格更高版本的材料时，才使用extension upgrade assistant.plugin_developer <该材料目录>。

## 3. 首次准备专用空目录

工作区不应是原项目根、现有知识库、简历目录或已安装插件目录。首次选一个专用空目录；示例位于Git忽略的var下：

~~~powershell
$WorkspaceRoot = Join-Path (Get-Location) 'var\plugin-development'
if (Test-Path -LiteralPath $WorkspaceRoot) {
    if (-not (Test-Path -LiteralPath $WorkspaceRoot -PathType Container)) {
        throw '工作区路径不是目录'
    }
    if (@(Get-ChildItem -LiteralPath $WorkspaceRoot -Force).Count -gt 0) {
        throw '首次配置请选择空目录；已有受管工作区按原配置恢复，不清空'
    }
} else {
    New-Item -ItemType Directory -Path $WorkspaceRoot | Out-Null
}
~~~

这段只适用于首次准备。使用过的工作区已有管理标记和持久记录，不运行清空操作；升级/重启时继续使用原workspace_root。

## 4. 保存配置，再启用

~~~powershell
$AdminBase = 'http://127.0.0.1:8001'
$ConfigBody = @{
    config = @{
        workspace_root = [IO.Path]::GetFullPath($WorkspaceRoot)
        validation_timeout_seconds = 30
    }
} | ConvertTo-Json -Depth 6
$ConfigKey = [guid]::NewGuid().ToString()
$ConfigRequest = @{
    Method = 'Put'
    Uri = "$AdminBase/admin/v1/extensions/assistant.plugin_developer/config"
    ContentType = 'application/json'
    Headers = @{ 'Idempotency-Key' = $ConfigKey }
    Body = [Text.Encoding]::UTF8.GetBytes($ConfigBody)
}
Invoke-RestMethod @ConfigRequest
& $Ctl extension enable assistant.plugin_developer
& $Ctl extension status assistant.plugin_developer
~~~

结果未确认时保留原$ConfigRequest用于对照，先GET同一路径/config核对当前值，不能重发原PUT。此接口没有幂等回执或CAS，保留$ConfigKey也不能防止覆盖另一客户端的新值。GET相符只证明当前值相符；不同则先合并需要保留的配置，再明确发起新的保存。

返回restart_required=true表示下次enable/recover生效。已启用且要改配置时，先确认没有进行中验证，再disable → enable。默认验证30秒，可设1–120秒，实际预算不超过宿主deadline减10秒清理余量；不自动安装新依赖。

通过标准：安装明确成功、状态ENABLED、Worker健康、9个developer工具可发现。启用不会立即执行候选或授予测试授权。

## 5. 开始第一次开发

按照[README](README.md)给助手一个小型公开测试需求。它先生成候选、编辑、review；本人再在本机运行manage.py的approve-tests，核对并输入YES；助手调用validate；最终本人通过manage.py install的原宿主确认安装，可显式--enable。

本机管理入口：

~~~powershell
$ManageScript = 'extensions/plugin_developer/manage.py'
$CandidateId = Read-Host '输入助手返回的candidate_id'
$Digest = Read-Host '输入review返回的candidate_hash'
& $HostPy -Xutf8 -B $ManageScript --workspace-root $WorkspaceRoot approve-tests $CandidateId --digest $Digest
~~~

默认15分钟/一次精确材料；--iterative改为30分钟/最多5次，Manifest、锁文件、pyproject变化需重新确认。详细安装、升级和回滚命令在README。

测试会以本人Windows权限执行信任代码。工作区边界、子进程、venv不构成恶意代码沙箱；不将真实凭据写入候选或夹具。现有紧急熔断、外部动作Gateway/审批/UNKNOWN规则继续适用。

本扩展不向模型开放Admin确认、原始进程日志或任意shell。临时Admin管理完成后关闭，正常运行保持单一guardian。此安装准备不代表F07/F08或真实站点验收已完成。

## 工作区另行备份

现有宿主备份只覆盖允许恢复的PostgreSQL表和登记制品，不包含workspace_root中的candidates、releases、records/workspace.sqlite3、control/control.sqlite3等文件。准备迁机或清理磁盘前，先确认没有验证/安装任务进行，停用开发插件及其他工作区写入者，再完整另存工作区到本人控制的加密备份位置；只复制候选代码会丢失基线与验证记录。

此副本是资料保存，不是可直接启用的恢复包。旧control记录包含执行授权，不能把复制的旧目录直接作为新机器已授权工作区启用。自动清理旧授权并恢复工作区的功能尚未实现；正式恢复需单独核对，并重新批准验证/安装。恢复密钥与真实凭据不写入候选或备份说明。详见[运行手册](../../docs/RUNBOOK_LOCAL_BETA.md)。
