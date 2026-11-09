# CORE.CONVERSATION.DIAG.1 对话未确认诊断修复

日期：2026-11-09。状态：**本地实现与目标验证完成，已受控重启原实例加载**。WORKSPACE.1仍WAITING终端人工检查；本轮未启动FILES.BACKUP.1。

## 问题与证据边界

本人报告：对话先输出片段，随后显示CHAT_RESULT_UNKNOWN，关联编号为`req_10e9e94a616145f19f6026ce85c91ca4`。

只读核对发现最近原对话仍为STREAMING/v2，没有回答完成回执或answer的model.completed审计；路由模型已完成。旧实现不保存对话失败原因，也没有HTTP请求号到原对话命令的失败关联，因而**不能利用该旧编号确认本次究竟是模型超时、断流还是其他失败**。当前配置模型单次总预算60秒、对话路由/检索/回答共80秒、TUI流90秒；超时是候选原因，不能当作已证实根因。此前的“重试原请求”只查原结果，不再次调用模型。

本次获准范围为最小诊断修复。未改变思考模式、模型配置、输出长度、上述时间预算、原幂等键、审批或UNKNOWN语义。

## 实现

- 新增独立`workbench-conversation-diagnostics`记录桶，按原命令键保存首次确认提交的诊断；不增加原对话记录版本、不修改原回执CAS。
- 仅记录固定原因、阶段、原请求号、非负耗时和诊断是否确认保存。固定原因覆盖模型超时、连接/流读取失败、协议不完整、响应过大、总对话预算、回执保存失败、取消和固定内部错误；不保存异常原文、用户问题、资料正文、模型响应或凭据。
- 脱敏审计事件为`conversation.failed`；诊断写入与查询有独立有界预算。诊断自身失败仍保留UNKNOWN，不能冒称记录成功。提交成功但写回执丢失时，当前响应标记未确认保存，后续同键查询可读到已提交诊断。
- 原成功/明确失败回执优先于任何旧诊断。旧记录没有诊断时不猜测原因，同键重试不新增模型调用或任务。
- TUI校验白名单后显示固定原因、阶段、耗时；原故障编号与本次HTTP查询编号分开。诊断也显示在可滚动的未完成回复区，避免顶部两行提示裁切。未确认片段不进入聊天历史或Token合计。
- 统一取消诊断覆盖模型读取及回执写入阶段，取消继续传播；只读查询被取消不冒充原请求新故障。

### 定向测试发现并修复的相邻计时问题

原`bounded_stream`把`asyncio.timeout`上下文跨越`yield`。期限恰好在调用方处理心跳/片段时到达，会取消调用方，绕过生成器的总期限诊断，并可能错过正常清理。确定性反例在调用方sleep处收到CancelledError。

现改为单一monotonic截止时间，仅对生成器内部等待使用剩余预算；80秒数值不变。先标记总期限原因，再取消/回收读取协程，避免误记为用户取消。新增反例同时验证调用方不被注入取消、总期限仍生效和流被关闭。**这证明既有计时缺口已修复，不证明它就是用户这次旧请求的根因。**

## 修改范围

产品源码：

- `src/personal_assistant/core/conversation_diagnostics.py`
- `src/personal_assistant/api/workbench_conversation_diagnostics.py`
- `src/personal_assistant/api/workbench_conversation.py`
- `src/personal_assistant/cli/tui/service.py`
- `src/personal_assistant/cli/tui/app.py`

回归：

- `tests/integration/test_conversation_diagnostics_wire.py`
- `tests/unit/test_conversation_diagnostics.py`
- `tests/unit/test_tui_error_diagnostics.py`
- `tests/core-suite.txt`加入上述关键诊断文件。

同步更新协议、设计、TUI说明及当前状态文档。没有新增迁移、插件版本或运行依赖。

## 最终验证

**144 passed、2个既有依赖弃用警告，79.08秒。** 这是下列一次合并定向运行的结果，不与此前小集合相加。范围覆盖API、真实适配器的合成HTTP流、同键回放、诊断存储边界、取消/总期限、TUI客户端及真实Textual控件渲染。

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest tests/integration/test_conversation_diagnostics_wire.py tests/integration/test_conversation_model_failures.py tests/api/test_workbench_conversation.py tests/api/test_workbench_stream.py tests/unit/test_conversation_routing.py tests/unit/test_model_streaming.py tests/unit/test_conversation_diagnostics.py tests/unit/test_tui_error_diagnostics.py tests/unit/test_tui_stream.py tests/unit/test_tui_conversation_failures.py tests/unit/test_tui_service.py tests/unit/test_tui_conversation.py -o addopts= -q --tb=short -p no:cacheprovider
```

最终日志：`var/verification/conversation-diagnostics-2026-11-09/final-regression.log`。

Ruff通过；上述5个产品源文件Mypy通过；差异检查通过。按本人避免完整套件过度验证的要求，未重复全量、完整PostgreSQL/Worker或手机测试。没有真实模型调用、邮件或网页提交测试；生产RecordStore复用既有实现，本轮没有新增真实PG故障注入结果。

保留失败过程：最初9条新增反例8失败/1通过；取消发生在明确失败回执写入期间的反例1失败；总期限在调用方sleep期间取消的反例1失败；首次合并存在测试模块同名收集冲突，已将集成文件重命名为`test_conversation_diagnostics_wire.py`，未删断言。旧日志不覆盖、不计作通过。独立单测一次沙箱运行阻塞于Windows asyncio建立回环socket，尚未进入异步测试；回收后同一测试在适当权限下通过。

## 原实例加载

修复完成后先只读确认没有READY/LEASED作业或最近120秒新对话，再调用现有受控restart。重启前实例已是**未冻结**；本次保留该状态，没有重新冻结日常任务领取。

部署结果：

- guardian就绪，Worker在线，Admin可访问。
- 原15任务及作业状态前后相同；profile文件摘要不变。
- 统一工作区仍APPLIED；未升级或强制启用任何插件。
- 没有为验证诊断发送真实模型请求，也没有修改旧CHAT_RESULT_UNKNOWN记录。

本地辅助材料：`var/deployment/conversation-diagnostics-2026-11-09/restart.py`、`before.json`、`result.json`、`restart-final.log`。首次只读部署检查把tasks.status误写为state，未进入重启；修正列名后成功，历史`restart.log`保留。

实际部署命令（单次材料，未来不能当通用重启检查重复使用）：

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B var/deployment/conversation-diagnostics-2026-11-09/restart.py
```

## 当前旧对话怎样处理

旧请求无法补出此前未记录的原因；安装新代码不会重新生成旧回复。

1. 先在当前TUI保留问题文字。
2. 若仍是这次未完成的纯回复，可点“放弃未完成回复”。宿主只允许放弃STREAMING阶段，任务创建或未决外部动作不适用。
3. 退出并重新打开TUI加载新提示，再由本人决定是否发送新请求。已有窗口不会自动热更新客户端代码。

```powershell
& 'C:\Users\kunji\Desktop\homework\生成式软件工程\lab1\scripts\assistant-tui.ps1' -Profile 'var/deployment/ext-dev2-2026-10-30/host.json'
```

若新请求仍失败，现在可根据固定原因及原故障编号继续定位；本报告不宣称所有模型中断已经消除。未提交或推送。
