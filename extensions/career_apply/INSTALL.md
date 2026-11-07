# 将 Career Apply 安装到助手

日期：2026-10-30。插件 ID：career.apply；版本：0.2.0。本次尚未安装到原运行实例。0.2.0增加授权本地目录简历读取；已有0.1.0可在确认新材料后升级。

## 1. 准备同一实例的 Admin

宿主须包含本轮通用动作预览/审批接线。只安装插件到旧宿主不能获得完整能力。使用原实例相同的 PostgreSQL、扩展根目录和制品目录；不要随意另开不同数据根的 Admin。

先核对旧 owner 与端口。历史 run-approval-host.py 只允许冻结运行，不能直接 resume 变成日常服务。切换方案确定、旧 owner 已退出后，临时前台启动同一 guardian：

~~~powershell
Set-Location -LiteralPath 'C:\Users\kunji\Desktop\homework\生成式软件工程\lab1'
$Py = Join-Path (Get-Location) '.venv-win\Scripts\python.exe'
$RealProfile = Read-Host '输入已核对的持久运行 profile 路径'
& $Py -Xutf8 -B -m personal_assistant.host_entry serve --profile $RealProfile --admin
~~~

profile 必须是已有且核对过的实际配置，不直接使用生产模板。Admin 预期仅 127.0.0.1:8001，不接 Tunnel。只配置 BOSS 时继续保持宿主邮件禁发。

## 2. 生成干净安装目录

在第二个 PowerShell 中执行。安装器按目录内容暂存，不读取插件 .gitignore；不应将含个人计划的 .local 或测试日志当成安装材料。因此先白名单复制公开运行文件，不修改原目录：

~~~powershell
Set-Location -LiteralPath 'C:\Users\kunji\Desktop\homework\生成式软件工程\lab1'
$Ctl = Join-Path (Get-Location) '.venv-win\Scripts\assistantctl.exe'
$CareerSource = Join-Path (Get-Location) 'extensions\career_apply'
$CareerPackage = Join-Path (Get-Location) ('var\extension-packages\career-apply-0.2.0-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $CareerPackage -Force | Out-Null
foreach ($PackageItem in @('extension.toml', 'pyproject.toml', 'requirements.lock', 'run.py', 'src', 'schemas', 'browser')) {
    Copy-Item -LiteralPath (Join-Path $CareerSource $PackageItem) -Destination $CareerPackage -Recurse
}
~~~

$CareerPackage 是本次安装源，保留到安装操作明确结束。不要用 wheel 路径代替上述目录：当前独立 Python wheel 用于分发代码，宿主还需要根目录 Manifest/Schema/锁文件。

## 3. 检查并安装

~~~powershell
& $Ctl extension status career.apply
& $Ctl extension inspect $CareerPackage
& $Ctl extension install $CareerPackage
~~~

第一次 status 返回未找到是正常的。若已有安装记录，不执行上述 install：

- 已有0.2.0且材料相同：跳过安装/升级，核对配置与状态。
- 已有0.2.0但材料不同：停止，保留记录；需要发布新的版本材料，不能同版本覆盖。
- 只有候选版本严格高于当前版本时，才使用下面的upgrade命令（当前候选是0.2.0）：

~~~powershell
& $Ctl extension upgrade career.apply $CareerPackage
~~~

install/upgrade 会重新展示材料并要求本人确认，不加 --yes。核对 ID、版本、材料哈希、能力与依赖，再确认。首次安装未看到操作明确成功前不要 enable。

固定依赖：公开 SDK 与锁定的 Playwright/pyee/greenlet，当前锁针对 Windows x64 / CPython 3.12。依赖下载失败时保留失败信息，不删除锁/哈希。独立 venv 和 Worker 只隔离依赖与崩溃，扩展作为本人信任代码运行。

## 4. 配置后启用

先只配置 BOSS，邮箱未登记时不填写假的 account_id：

~~~powershell
$AdminBase = 'http://127.0.0.1:8001'
$ResumeFolder = Read-Host '输入本人授权的实际简历目录'
if (-not (Test-Path -LiteralPath $ResumeFolder -PathType Container)) {
    throw '简历目录不存在，停止'
}
$ConfigBody = @{
    config = @{
        boss_cdp_endpoint = 'http://127.0.0.1:9222'
        resume_roots = @(@{ key = 'resumes'; path = [IO.Path]::GetFullPath($ResumeFolder) })
    }
} | ConvertTo-Json -Depth 6
$ConfigKey = [guid]::NewGuid().ToString()
$ConfigRequest = @{
    Method = 'Put'
    Uri = "$AdminBase/admin/v1/extensions/career.apply/config"
    ContentType = 'application/json'
    Headers = @{ 'Idempotency-Key' = $ConfigKey }
    Body = [Text.Encoding]::UTF8.GetBytes($ConfigBody)
}
Invoke-RestMethod @ConfigRequest
& $Ctl extension enable career.apply
& $Ctl extension status career.apply
~~~

若配置PUT结果未确认，保留原请求用于对照，先GET同一路径读取当前配置，不能直接重发。此接口没有命令去重或CAS；携带相同Idempotency-Key也不能保证安全重试。当前值相符只说明现值相符；不同则先核对并合并需要保留的字段，再明确发起新的保存。返回restart_required=true表示下次enable/recover生效；已启用时先确认没有进行中的动作，再disable → enable。

后续加入已登记宿主邮箱的 account_id，与 boss_cdp_endpoint 一起保存。先 GET 同一路径的 /config 保留原字段，PUT 保存整份配置。密码只保存在宿主 Windows Credential Manager。

通过标准：安装明确成功、状态 ENABLED、Worker 健康、目录包含本版13个 career 工具；不代表真实 BOSS/邮件已通过。

## 5. 本人打开专用 BOSS 浏览器

下面使用已安装 Edge，路径不符时先查本机实际路径。可见窗口用于本人登录：

~~~powershell
$EdgeExe = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
if (-not (Test-Path -LiteralPath $EdgeExe -PathType Leaf)) {
    throw '请先确认本机 Edge 路径'
}
$BossProfile = Join-Path (Get-Location) 'var\manual-test\boss-browser'
Start-Process -FilePath $EdgeExe -WindowStyle Normal -ArgumentList @(
    '--remote-debugging-address=127.0.0.1',
    '--remote-debugging-port=9222',
    ('--user-data-dir="' + $BossProfile + '"'),
    'https://www.zhipin.com/'
)
~~~

本人登录，只保留一个 BOSS 页面。CDP 仅回环使用，不接公网/转发；插件不处理密码或验证码。选择器尚未实站验证，先只读核对岗位；页面不兼容就停止。

## 6. 安装后入口与限制

- 独立规划立即可用：[README](README.md)。
- 助手任务详情“继续任务”选择 Agent，给准确的只读准备要求；是否选工具看实际执行结果。
- 无需简历上传按钮。配置resume_roots后，Agent可调用career.resume.list和career.resume.import_file直接从授权目录导入；不自写artifact_id。当前仍无招聘专用工作流或任意工具控制台。
- 真实文件导入上限512 KiB；本地规划8 MiB上限不代表执行上限。
- 实际发送必须经过 Gateway、一次性审批、Outbox；UNKNOWN不自动重发。
- 临时Admin收尾关闭，正常实例保持单一guardian。不要为了安装直接解冻旧验收owner。

邮件配置和完整手测顺序见[真实环境手工测试手册](../../docs/MANUAL_REAL_ENV_TEST_GUIDE_2026-10-30.md)。无需为阅读或安装准备重复跑完整套件。
