# Career Apply：BOSS 职位整理与受控投递扩展

首批平台是 BOSS 直聘，其次是邮件；暂不适配各企业官网表单。职位、收件人、简历引用、匹配规则和投递计划均在本扩展内。助手只提供公开 SDK、一次性审批、Outbox、制品和通用宿主能力。

`PREPARED` 表示材料计划已生成。只有明确的投递回执才可表示消息或简历已发送；`UNKNOWN` 不会自动重发。BOSS 的“已打招呼”和“简历已发送”是两个状态。

## 0.2.0：直接使用授权目录中的简历

不需要上传页面或把文件base64交给模型。通过本机Admin给插件配置resume_roots；可指向知识库已经使用的同一目录，但两份扩展各自显式授权，不读取另一扩展的私有配置。

~~~json
{
  "config": {
    "boss_cdp_endpoint": "http://127.0.0.1:9222",
    "resume_roots": [
      {"key": "resumes", "path": "C:/Users/你的用户名/Documents/Resumes"}
    ]
  }
}
~~~

路径必须先替换为本人实际存在的绝对目录。首次保存后启用插件，已有Worker需在无进行中动作时重载。源目录只读；导入快照写入现有宿主制品目录，不改原简历。

助手内流程：

1. career.resume.list：列出授权目录中的PDF/DOCX/UTF-8 TXT，返回root_key、relative_path、名称、大小、格式和SHA-256；不返回绝对路径或文件正文。可选root_key，limit默认100、最多100。
2. career.resume.import_file：使用选定条目的root_key、relative_path和expected_sha256，导入精确字节并返回宿主artifact_id。
3. 把完整返回引用交给career.boss.prepare(action_kind=send_resume)或career.mail.prepare；后续仍须独立R2审批。

import_file参数示例（哈希取自本次list，不手写示意哈希）：

~~~json
{
  "root_key": "resumes",
  "relative_path": "软件开发简历.pdf",
  "expected_sha256": "使用list返回的64位SHA256"
}
~~~

可在任务中明确说：“列出resumes目录的简历；选择软件开发简历.pdf，按刚列出的哈希导入，只准备投递材料，先不发送。”Agent是否完成各工具调用看实际结果。文件字节留在本机到制品链路；元数据仍按既有模型披露规则处理。

执行导入上限512 KiB。超过上限、隐藏文件、越界/绝对路径/盘符/UNC/ADS、符号链接/junction均拒绝或在列表过滤。列表还有truncated/skipped计数；零条不代表目录为空。文件在列举后改变则要求重新列出/选择，不自动采用新内容。导入后使用固定快照，后来编辑源文件不会暗中替换待审批附件。

现有base64导入工具保留兼容用途，正常本地使用走import_file。BOSS实站选择器仍未验收，这次只补文件来源，不放宽投递判断。

## 立即运行本地规划器

从主项目根目录运行以下 PowerShell 命令。它们只使用合成示例和插件本地 SQLite，不要求登录 BOSS、配置邮箱或接入模型。

```powershell
$plannerPython = '.\.venv-win\Scripts\python.exe'
$plannerScript = 'extensions/career_apply/run.py'
$plannerDb = 'extensions/career_apply/.local/career.sqlite3'

& $plannerPython -Xutf8 -B $plannerScript --database $plannerDb jobs import extensions/career_apply/examples/jobs.json
& $plannerPython -Xutf8 -B $plannerScript --database $plannerDb jobs list --include Python --city 南京 --limit 5
& $plannerPython -Xutf8 -B $plannerScript --database $plannerDb plan boss fixture-python --action message --message '这是一段本地合成自检文字。' --resume extensions/career_apply/examples/resume.txt
& $plannerPython -Xutf8 -B $plannerScript --database $plannerDb plans list
```

也可先进入本目录再运行 `python run.py ...`，默认数据库是当前目录下 `.local/career.sqlite3`。从主项目根目录运行时，建议保留上述显式 `--database`，使本地数据位于插件的 Git 忽略目录。

本地输入支持：

- 职位：UTF-8 JSON 或 CSV；兼容 `url/job_url`、`title`、`company`、`recruiter`、`location` 等字段。BOSS URL 必须含明确的 `/job_detail/<id>.html`，跟踪参数不参与身份或去重。
- 收件人：UTF-8 JSON、CSV 或每行一个邮箱的 TXT；只接受单个邮箱，去重后保存于本地列表。
- 简历：PDF、DOCX、UTF-8 TXT，最多 8 MiB。规划器读取指定文件并保存名称、大小、格式和 SHA-256 元数据；不会自动解析经历或生成不存在的资历。

职位及收件人导入最多 2 MiB、1000 条。`jobs list --limit` 是本次筛选上限，不表示已在实站执行每日投递配额。

## 在 BOSS 页面只读采集职位

`browser/` 包含一个可单独载入 Chrome 或 Edge 的 Manifest V3 采集器。

1. 打开 `chrome://extensions` 或 `edge://extensions`，本人开启开发者模式，选择“加载已解压的扩展程序”，选择本目录下 `browser` 文件夹。
2. 在浏览器中本人登录 BOSS，进入职位列表。先加载需要整理的职位卡片。
3. 点扩展图标，再点“读取当前列表”。采集器只读取当前页面已加载、可见且身份明确的职位卡片。
4. 点“下载职位 JSON”，将 `boss-jobs.json` 保存到自己选择的位置。
5. 用本地规划器导入该文件。例如默认下载目录下的文件：

```powershell
& $plannerPython -Xutf8 -B $plannerScript --database $plannerDb jobs import "$env:USERPROFILE\Downloads\boss-jobs.json"
```

采集器仅申请 `activeTab` 和 `scripting`。它不保存登录信息、不读取 Cookie/Storage/聊天历史、不调用隐藏 API，也不会点击“立即沟通”或其它职位操作。网页文字始终只作为数据。

当前选择器经过本地合成 DOM 测试，尚未核验真实 BOSS 页面的所有版本。页面结构不匹配时可能返回零条；不要据此认定没有匹配岗位。可直接编辑 CSV/JSON 导入，或在后续实站核验中更新本扩展的适配器。

## 本地邮件收件人与计划

```powershell
& $plannerPython -Xutf8 -B $plannerScript --database $plannerDb recipients import extensions/career_apply/examples/recipients.csv
& $plannerPython -Xutf8 -B $plannerScript --database $plannerDb recipients list
& $plannerPython -Xutf8 -B $plannerScript --database $plannerDb plan email --subject '应聘${position}' --body '您好${name}，这是本地合成材料自检。' --resume extensions/career_apply/examples/resume.txt
& $plannerPython -Xutf8 -B $plannerScript --database $plannerDb plans export extensions/career_apply/.local/plans.json
```

模板仅支持 `${name}`、`${email}`、`${company}`、`${position}`，没有代码执行或任意属性访问。每个收件人生成独立计划；缺简历时显示 `NEEDS_USER/RESUME_REQUIRED`。以上命令不会发送邮件。

重复生成相同计划会返回原版本；不同正文、目标或简历材料要求显式处理版本冲突。查看 `plans list` 中的 ID 后，可用 `plans show ID --history` 核对历史。更新 BOSS 计划时传 `--expected-version N`。更新已有邮件计划时建议先用 `--recipient 邮箱` 选择单项，再传对应版本。多个计划的更新不宣称为一个原子批量事务。

`plans export` 不覆盖已有文件，重复导出请换文件名。本地 `.local/` 含收件人和申请材料，请保留在 Git 忽略目录。

## 接入助手

插件通过 `extension.toml`、公开 `personal_assistant_sdk` 和标准槽注册。业务模块不导入助手私有 core，也不依赖 smail 扩展的业务包。先按[安装说明](INSTALL.md)白名单复制干净安装目录，避免将本地计划或测试日志暂存；然后检查该目录材料：

```powershell
.\.venv-win\Scripts\assistantctl.exe extension inspect $CareerPackage
```

该命令需要按主项目运行手册准备本机 Admin。先核对检查结果中的材料摘要、能力、依赖和版本，再由本人确认安装/启用；本轮开发不会自动安装到原服务或重启后台。

非秘密配置示意：

```json
{
  "account_id": "job-mail",
  "boss_cdp_endpoint": "http://127.0.0.1:9222",
  "resume_roots": [{"key": "resumes", "path": "C:/Users/你的用户名/Documents/Resumes"}]
}
```

`account_id` 是宿主已有邮件账户的 ID，密码仍由宿主安全凭据后端管理。`boss_cdp_endpoint` 只接受带明确端口的字面回环 HTTP 地址；本人在隔离的可见浏览器中登录，插件不会自动处理密码或验证码。BOSS 执行端需要 Playwright，具体固定版本及安装材料以本版本锁文件和宿主交接报告为准；缺依赖或缺浏览器配置时应返回配置需求。

助手内工具流程：

| 通道 | 流程 | 对外动作条件 |
|---|---|---|
| BOSS | `career.resume.list` → `career.resume.import_file`（简历动作） → `career.jobs.filter` → `career.boss.prepare` → `career.boss.execute` | 准备阶段核对岗位、公司、HR、完整文字和页面快照；逐项 R2 批准后才执行 |
| 邮件 | `career.resume.list` → `career.resume.import_file` → `career.mail.prepare` → `career.mail.send` | 当前简历制品、收件人、正文及 MIME/附件哈希核对后，走宿主邮件审批和 Outbox |

本地 CLI 计划与助手内可执行动作分别标明能力。BOSS “立即沟通”可能直接发送账号默认话术，无法预先核对时会要求本人处理。已有简历无法从页面核对实际内容哈希时，不会仅凭同名文件推断可发送。受控 BOSS 执行包含明确文字与新上传简历路径；简历可从resume_roots用career.resume.import_file直接导入宿主制品，执行导入上限512 KiB，message 与 send_resume 分别审批。真实页面缺新上传标识/对应送达凭据时暂停，不能仅凭远端同名文件认定可发送。通知点击只打开任务，不批准或发送。

## 开发验证与边界

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest -c extensions/career_apply/pyproject.toml extensions/career_apply/tests -o addopts=--strict-markers -q
node --test extensions/career_apply/tests/test_boss_dom.mjs
node --check extensions/career_apply/browser/popup.js
```

本地合成测试覆盖 URL/邮箱校验、稳定去重、筛选、计划版本、UNKNOWN 保留、简历大小/格式、模板边界和只读 DOM。完整接线门禁与最终数字由本轮交付报告记录。本地测试不等于真实 BOSS 投递或真实邮箱发送验收。

参考来源见 [RESEARCH.md](RESEARCH.md)，本扩展进度见 [NEXT_STEPS.md](NEXT_STEPS.md)。

## 当前安装与真实使用

安装步骤见[INSTALL.md](INSTALL.md)，本轮实际结果见[DELIVERY.md](DELIVERY.md)，全系统手测见[手工测试手册](../../docs/MANUAL_REAL_ENV_TEST_GUIDE_2026-10-30.md)。当前PWA没有招聘专用工作流；本地简历已可经resume_roots/list/import_file读取，不依赖上传控件，BOSS实站兼容仍待验证。career.mail.prepare需要带时区的prepared_at，同请求重试保留原值及原send_arguments。插件本轮未安装到原运行实例或真实投递。
