# TUI.1 本地实现报告

2026-10-30。用户要求参考coding agent交互，提供模型对话、根据对话选择插件执行与Token统计；本次完成唯一编号TUI.1。本地实现与有限验证完成，不等于原实例上线或整个项目验收。F08.6保持BLOCKED，F07/F08既有状态不变。

## 实现范围

- 可选Textual四页工作台：对话、任务、扩展、设置；键盘快捷键、80×24布局、异步读取、冻结/离线提示。
- 问答使用现有模型路由，支持最近12条历史，精确披露确认后调用且无工具。选择Agent任务时可确认携带对话上下文；宿主通过既有工具目录、Planner、Runtime、Gateway执行，不添加业务ID分支。
- Local Admin新增受回环peer/Host、专用header、无Origin约束的工作台路由。任务和模型披露复用既有服务；公开应用不挂载新路由。trace仅返回阶段与工具标识，不返回参数。
- 问答先持久化原命令派发权，模型I/O在事务外，CAS保存回执。同键并发、坏输出、取消、超时不会重复调用模型。未知状态只查原结果，不自动对账或重发。
- 扩展管理复用静态inspect与精确材料确认/operation。配置接口原本无幂等回执，故未知写入禁止重发；不通过固定客户端key伪称幂等。
- 模型设置仅更新明确profile/共享配置中的模型名、JSON/thinking选项，保留凭据引用与其他字段；摘要检测并发变更、文件锁、原子替换；无自动重启。
- Token来自实际provider usage：会话成功问答计数及全实例最近最多5000条model.completed计数，缺失明确标记；无估算账单或伪造零。
- 可选依赖textual>=8.2.8,<9；当前开发环境安装8.2.8。服务/API/Worker不依赖Textual，CLI按需加载。

改动分布：cli/main.py与cli/tui/*、api/workbench*.py与admin_app.py挂载、core/audit/usage.py与两份审计适配器、pyproject.toml、启动脚本、六份目标测试，以及README/TODO/NEXT/CONTRACTS和使用指南。既有招聘/开发插件及其他脏文件未作为本轮新实现申报。没有新增迁移，没有修改Gateway/风险分类/审批/Outbox的生产行为。

## 实际验证

先记录缺模块/CLI未注册的失败测试，再实现；追加聊天上下文测试先失败后通过。最终联合定向 **95 passed、2个既有依赖弃用警告**，日志在 var/verification/tui1/targeted.log。

~~~powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/api/test_workbench_api.py tests/unit/test_tui_service.py tests/unit/test_tui_app.py tests/unit/test_tui_cli.py tests/unit/test_tui_model_settings.py tests/unit/test_tui_usage.py tests/api/test_delivery_api.py tests/unit/test_runtime_remediation_delivery.py tests/unit/test_runtime_crosscheck_delivery.py tests/unit/test_cli_extension_flow.py tests/unit/test_model_router_f04.py -q -p no:cacheprovider -W error::RuntimeWarning
~~~

包含18项Textual Pilot交互测试；12项工作台API测试；真实Runtime/Planner/Gateway处理合成READ工具，provider与工具executor使用合成替身，实际执行一次并回传结果；冻结分支模型/Gateway/executor均零调用。不将其称为真实LLM或真实业务插件E2E。

~~~powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B -m ruff check src/personal_assistant/cli/main.py src/personal_assistant/cli/tui src/personal_assistant/api/workbench.py src/personal_assistant/api/workbench_usage.py src/personal_assistant/core/audit/usage.py src/personal_assistant/infrastructure/database/audit_writer.py src/personal_assistant/infrastructure/memory/audit.py src/personal_assistant/admin_app.py tests/api/test_workbench_api.py tests/unit/test_tui_app.py tests/unit/test_tui_cli.py tests/unit/test_tui_model_settings.py tests/unit/test_tui_service.py tests/unit/test_tui_usage.py
.\.venv-win\Scripts\python.exe -Xutf8 -B -m mypy src/personal_assistant/cli/main.py src/personal_assistant/cli/tui src/personal_assistant/api/workbench.py src/personal_assistant/api/workbench_usage.py src/personal_assistant/core/audit/usage.py src/personal_assistant/infrastructure/database/audit_writer.py src/personal_assistant/infrastructure/memory/audit.py src/personal_assistant/admin_app.py --cache-dir var/verification/tui1/mypy
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pip check
git -c core.excludesfile=NUL diff --check
.\.venv-win\Scripts\python.exe -Xutf8 -B -m personal_assistant.cli.main tui --help
~~~

全部通过；Mypy覆盖12个源文件。PowerShell启动脚本解析通过。80×24与110×36的离线测试夹具SVG已生成；内置Chromium启动EFTYPE后，改用隔离无头Edge渲染并关闭，未访问真实助手。SVG导出中文textLength按单字符宽度导致预览挤字，已仅对预览产物按终端cell宽度修正并视觉核对，产品布局不因此改写。产物在var/verification/tui-ui；预览中冻结/连接均为夹具数据。

按用户“避免完整套件过度验证”，未跑全量、完整PostgreSQL/Worker、生产wheel发布门禁或真实模型调用。本轮生产PostgreSQL事务路径复用既有RecordStore，但没有新增真实PG运行证据；定向使用隔离内存容器，不宣称内存等于生产持久化。未安装/升级业务扩展，未更改原profile/原库、启动/解冻服务、联网发信/投递或提交推送。

## 使用与限制

见 [TUI使用指南](TUI_WORKBENCH.md)。需要已启动的持久实例Local Admin；TUI本身不是守护器。原实例冻结与旧队列需要另行核对，不能自动解冻。

手机现有PWA能以任务方式和同一持久Agent交互；补充消息需继续、终态任务另建。新的纯问答页面本轮只提供TUI。问答暂不流式输出，不持久化可恢复的TUI聊天会话；响应回执仍保留于宿主。R2审批仍在现有权威PWA页面完成。待工具trace不是永久完整工具流水，可能漏看轮询间隔内的短步骤。全实例usage不是单任务用量或供应商账单。永久UNKNOWN不自动重发；重开后新提问属于新计费请求。

下一建议编号 **TUI.2**：连接运行实例，完成真实问答和已批准的简单READ工具执行小型联调；此前的F08.6整阶段门禁仍单独保留，不由TUI结果替代。

## 本轮材料摘要

以下摘要针对本轮接线/新增模块与目标测试最终文件；不包含报告自身或历史脏文件的归属判断。

| 文件 | SHA-256 |
|---|---|
| pyproject.toml | e5c451cbbeb827a69027fd28e66e5aa3eb52a24474ca9fc309dc0bb2e0c70f2e |
| scripts/assistant-tui.ps1 | a5742b64025598d0f3ff09d051a3e4256ae2637538e4ea9226af1404b02076ba |
| src/personal_assistant/cli/main.py | 6e1564032c871ca8260e0f74e7b10f8f847a0c1c9a167512775830cdf4509794 |
| src/personal_assistant/admin_app.py | 86dd44bbe3e972d222c0945bd2a44fe98963a2f66f03c1b4344c083bc8220c26 |
| src/personal_assistant/api/workbench.py | f11d98d70ba8309f71832774903b6263c7af9ebcdc459f4541c8e42dd49d4bcf |
| src/personal_assistant/api/workbench_usage.py | f1f56d404c2e3de86a31aec4081138f2d65ff5047ae2afe26eb511bb760c361c |
| src/personal_assistant/cli/tui/__init__.py | 60ff75992172959cbe4c290651040216024229216d24950e24b778cfc36942ce |
| src/personal_assistant/cli/tui/app.py | 9de8ebf95f1bc3eaca3b2a1b733f0de69fcc9d42ca88048eb6c9d5648b24931e |
| src/personal_assistant/cli/tui/service.py | 54e51f29778ded7c4a5e2103261a71e0335659091108e400b7b59d432ac0b1c9 |
| src/personal_assistant/cli/tui/model_settings.py | bb3c8d4f3efa7834e08f351e7391b5ba8c8cf8d83869f498990bc2a7ebc27268 |
| src/personal_assistant/cli/tui/launch.py | ae2c5265fae51da784e4bed69efc40f644440cbb331dbb0c8cbb9a3c8f13b19c |
| src/personal_assistant/core/audit/usage.py | dd71dfd062fb2900abd740a9f64d9632f580bffd14792d7a198f8228cd6a4dea |
| src/personal_assistant/infrastructure/database/audit_writer.py | 2a8fd4d05290162b256cca18404cbe9662bd59bc1557b5a415b64d320c35cfbb |
| src/personal_assistant/infrastructure/memory/audit.py | fdde1c1012510124cb30f28a28cff53a71d3ff81ba573e39c482311c0c39c049 |
| tests/api/test_workbench_api.py | 320c88341f1d88036984d344f9fa8a4491eaefe079250f83949086ded4a77ff4 |
| tests/unit/test_tui_app.py | eb33edd5c46243369fb0b9ed7fdd0ba9bf04ceb8a60c63099994e70ba753d4f1 |
| tests/unit/test_tui_cli.py | 778b4272b5fa310bf120599107b94ceb0e452cd344486e12aa6333bf69aac8ee |
| tests/unit/test_tui_model_settings.py | 2c768bab98deb0c12af06a95450f627e4319ca1e053f041e8852892ec89cb79f |
| tests/unit/test_tui_service.py | 80d38c91b48f8074e8f6e448cf69efe059aa435cece06ad534b5690ca9803817 |
| tests/unit/test_tui_usage.py | d05454d9ccc23bbbb97d9eea04a621f7d8be88bb5f9521b02240a67dd072af17 |
| docs/TUI_WORKBENCH.md | e680cf1b64bf81b6f6992db102382e7c45c0760dcb029033e30b6250cad72457 |
