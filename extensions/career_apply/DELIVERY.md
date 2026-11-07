# Career Apply 本地开发交付总结

日期：2026-10-30。范围：EXT.CAREER.1，独立招聘插件及业务无关的宿主接线。

## 版本补记：0.2.0

本报告主体保留0.1.0历史范围和wheel摘要。0.2.0已补授权目录简历发现/导入，解决下文当时的文件导入缺口；新命令及实际证据见[本地简历增量报告](LOCAL_RESUME_2026-10-30.md)。尚未安装/升级原实例，BOSS实站兼容仍待验。

## 结果与当前可用程度

已完成独立职位/收件人导入、筛选、本地持久计划、浏览器只读采集器、SDK工具、BOSS受控适配器、招聘邮件材料准备及通用审批接线，并通过本轮定向验证。未安装到原运行实例、切换后台、解冻或真实发送。

当前可直接使用独立CLI做职位与投递计划。受控执行代码可通过宿主注册；**PWA没有招聘工作流/简历上传控件，真实材料导入及确定性调用入口仍待补齐。BOSS选择器只在合成本地页面验证，实站兼容未证明。** 因此不将其描述成现已可一键真实投递。进度保持IN_PROGRESS，等待入口与真实环境验证。

F08.6仍BLOCKED；F07/F08整体状态不变。此前独立全量的Companion超时失败不由本插件局部结果覆盖。

## 实现范围

| 位置 | 实现 |
|---|---|
| extensions/career_apply/src/career_apply | 归一化、筛选、SQLite计划/CAS/历史、CLI、11个SDK工具、BOSS与邮件业务 |
| extensions/career_apply/browser | 仅activeTab+scripting采集器；目标/消息/新上传简历适配脚本 |
| extension.toml、schemas、requirements.lock | 标准槽/配置、风险声明、固定Windows x64/Python3.12依赖 |
| infrastructure/tools/extension_actions.py、capability_router.py | 通用同版本READ预览、附件归属/实际字节核验；Gateway派发后坏回执UNKNOWN |
| bootstrap.py、runtime.py | 通用服务组合与接线，无招聘专用核心分支 |
| core/extensions/artifact_access.py、infrastructure/memory/mail.py | 宿主制品元数据查询，用于精确预览核验 |
| api/v1/approvals.py、schemas.py | 通用EXTENSION_ACTION材料展示与审批，不增加招聘专用路由 |
| web/pwa/app.js、service-worker.js | 通用纯文本材料核对，语义键序比较；源码缓存v17 |
| tests/unit/test_extension_action_*、tests/api/test_extension_action_approval.py | 材料漂移、审批、派发失败/坏回执/UNKNOWN回归 |
| tests/integration/test_extension_action_pwa.py、test_career_mail_host_wiring.py | 390px通用审批、真实插件MIME到宿主执行器组合验证 |

扩展只依赖公开SDK和自身依赖，不导入宿主私有core，不依赖nju.smail业务包。邮件只经宿主mail.send；不在插件内开SMTP。固定目标、完整材料、附件哈希/版本变化使旧审批失效；一次性5分钟审批与Outbox约束未弱化。

## 业务边界

- 职位/收件人输入2 MiB、1000条；BOSS URL归一化并去跟踪参数。CLI筛选limit不代表实际日发送配额。
- 本地规划简历8 MiB；真实resume.import最多512 KiB，支持PDF/DOCX/UTF-8 TXT。CLI不会发送，也不从模型补造经历。
- BOSS区分已沟通/简历已发；greet要求本人处理，不自动点未知默认招呼。准确公司/HR/职位缺失或页面漂移即暂停。
- 新简历必须核对宿主制品实际哈希、新上传标识/选中材料及相同标识的送达回执。同名远端文件不足以证明字节一致。
- BOSS适配存在合成页面属性，真实站点可能没有所需证据；缺失必须记兼容阻断，不能放宽成任意按钮。
- 邮件prepare需要带时区的prepared_at，同请求保持原值；使用原样send_arguments，复用宿主账户/权威MIME/审批/Outbox。
- UNKNOWN不自动重试。进程内BOSS guard只覆盖该Worker生命周期；持久权威由宿主Outbox负责，不以内存宣称跨重启防重完成。
- 没有career WorkflowProvider或公共任意工具API。Agent可尝试已注册工具，但模型规划非确定性；导入真实简历需要后续本机入口。

## 实际验证与可复现命令

以下数字来自本轮已保留日志，本次文档整理没有重新执行测试。均在项目根目录运行，PowerShell：

~~~powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest -c extensions/career_apply/pyproject.toml extensions/career_apply/tests -o addopts=--strict-markers -q
node --test extensions/career_apply/tests/test_boss_dom.mjs extensions/career_apply/tests/test_boss_action.mjs
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/contract/test_f03_contract_consistency.py::F03ContractConsistencyTests::test_api_layer_does_not_import_infrastructure tests/unit/test_extension_action_preview.py tests/unit/test_extension_action_gateway.py tests/api/test_extension_action_approval.py tests/integration/test_extension_action_pwa.py tests/integration/test_career_mail_host_wiring.py -q
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/unit/test_tool_gateway.py tests/unit/test_mail_send_gateway_f06.py tests/api/test_pwa_approvals_f08.py -q
~~~

| 范围 | 实际结果 | 日志 |
|---|---|---|
| 插件Python | 93 passed | var/verification/career-plugin-2026-10-30/plugin-final.log |
| DOM/动作JavaScript | 33 passed，0 skipped | browser-js-final.log |
| 新宿主接线/审批/PWA/邮件组合 | 18 passed，2 warnings | host-directed.log |
| 原Gateway/邮件/审批相邻回归 | 37 passed，2 warnings | adjacent-regression.log |

插件93项含本机浏览器合成上传/发送与丢回执场景，邮件组合使用合成传输，不代表真实BOSS或SMTP。SDK已验证Dispatcher与真实stdio握手/目录/调用/EOF退出；不宣称system.shutdown退出竞态已修复。

Ruff、JS语法、pip check、差异检查已通过；宿主src Mypy231文件、插件Mypy10文件通过（前序较宽检查264源文件通过，不能与最终231口径相加）。

完整本地套件按用户“避免完整套件过度验证”要求中止，full-run.json为interrupted，**没有完整通过结果**。中止前观察到的API跨层导入已修正为Container组合，并在上述18项包含的契约用例复验通过；不能据此把全套记绿。本批未运行完整PostgreSQL/Worker。不重复开全量或实站测试。

## 打包与部署状态

独立Python wheel（不是直接可装的完整宿主扩展包）：

- var/verification/career-plugin-2026-10-30/dist/personal_assistant_career_apply-0.1.0-py3-none-any.whl
- SHA-256：875d16beb9e2ca300172490e803599460a2ef705f9c15de14ee542321d6a9bf7
- 包含Python模块和两份浏览器适配JS；宿主安装仍须根Manifest/Schema/锁文件。

[安装说明](INSTALL.md)采用白名单复制干净源目录，避免安装器把Git忽略的个人.local数据/验证日志一起暂存。确认必须先于第三方build/install/execute。

原宿主冻结文件在本轮门禁前后摘要相同；未操作原库、解冻、重启、发信、招聘投递、提交Git或推送。源码缓存v17不代表手机实际已经升级到v17。测试浏览器已由夹具关闭，不关闭用户浏览器。

## 待办及推荐下一步

1. 准备正常持久实例的受控切换、同实例Admin和精确材料安装。
2. 提供经过runtime/Gateway/审批的本机简历导入与确定性工具入口；不把真实简历发给模型。
3. 本人登录BOSS后只读核对实站目标/控件/回执，适配不足保留阻断。
4. 先自发自收邮件，再安排一个确实要联系的招聘目标，逐项审批。
5. 保留F08.6现有门禁待办，不冒称真实交付全绿。

完整操作计划：[真实环境手工测试手册](../../docs/MANUAL_REAL_ENV_TEST_GUIDE_2026-10-30.md)。
