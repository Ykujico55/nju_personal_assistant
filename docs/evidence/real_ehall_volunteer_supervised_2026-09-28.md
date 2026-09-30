# 第二课堂志愿服务经历：真实提交前试填证据（2026-09-28）

## 范围与复现

目标路线是“第二课堂 → 第二课堂成绩单（在线办理） → 志愿服务经历（申请）”。用户在可见 Chromium 中自行完成统一认证并进入申请表；验收脚本接管新标签页中的嵌套表单 frame，经 `BrowserSessionBroker` 试填合成测试值，`submit_enabled=False`，没有点击最终“提交”。本次没有经过生产 `nju_ehall` 扩展的 Tool Gateway/Outbox 提交路径。

在项目根目录运行以下命令可重做同一类试填；运行时需人工登录、进入申请表，并按脚本提示核对页面指纹。校园页面变化时应暂停重新核验，不能复用旧指纹：

```powershell
.venv-win\Scripts\python.exe scripts\ehall_real_acceptance.py preview-current-volunteer --out "$env:TEMP\ehall_volunteer_preview.json"
```

## 本次观察

- 初始脱敏结构：`real_ehall_volunteer_supervised_final_preview_2026-09-28.observation.json`，SHA-256 `3f45d12ea5071ca56a60398ebd601abee9868cf5a3ab39c53ca554360f746f25`；新标签页已接管，选中 `/tw/xssq/xssq/create` frame，页面指纹 `410e527ff0f87f6a0701676c3b38ae379ef0427417ec0324933dffcd9ada508e`。页面有 9 个底层控件、7 个业务字段和一个“提交”按钮；表单 DOM 的 `action` 为空、`method` 为 `get`，不能据此推断实际 JavaScript 提交目标。
- 从上到下填写前六项，包括审核学院“文学院”后，审核人选项数由 64 变为 8；变化后的脱敏结构为 `real_ehall_volunteer_supervised_final_preview_2026-09-28.after-college.observation.json`，SHA-256 `97409a447acf12e95ed583083534998cf74b4523caeb8d2cfcc0bf0865ff5ebb`，新指纹 `04878a711f3ff6757e5f9b65afd83f70535cd44dc15240b937c49488189d63c7`。操作端复核新指纹后才继续选择审核人。
- Broker 返回 `PREVIEW_READY`，字段 7 项、`missing_fields=[]`、`attachments=[]`、风险 `EXTERNAL_WRITE`，canonical payload hash 为 `d7c3fe1d608f63aafc679c627c16196de387aa7648132efb13bcac514ed4c981`。最终脱敏报告为 `real_ehall_volunteer_supervised_final_preview_2026-09-28.json`，SHA-256 `aab5c15c6a65eb1ec6ec2085aa52019bc529705f631f7c146744bbb463a8ba02`；审核人选项值已遮盖，报告不能单独重算该 hash。
- 用户在真实页面目视确认“七项正常”：名称、开始时间、结束时间、团队职务、服务时长、审核学院“文学院”与审核人均显示测试值，页面无报错。脚本结束后关闭浏览器；没有点击最终“提交”。填写期间网站可能自行发送请求，本证据不声称服务器绝无写入。

本轮修复了隐藏原生 `select` 与可见控件不同步、下级选项依赖上级选择、动态选项改变页面指纹和结束时间被页面规范化造成的校验失败。回归采用有头 Chromium 模拟站点、Broker/Companion 嵌套 frame 反例与单元/契约测试。最终验证命令及结果：

```text
.venv-win\Scripts\python.exe -m pytest tests/unit/test_ehall_real_acceptance_script_f07.py tests/unit/test_browser_driver_route_f07.py tests/contract/test_f07_contract_consistency.py -q
  63 passed
.venv-win\Scripts\python.exe -m pytest tests/integration/test_ehall_interactive_browser_f07.py -q
  23 passed
.venv-win\Scripts\python.exe -m pytest tests/integration/test_browser_companion_real_f07.py -q -k "companion_selects_nested_transaction_frame or nested_frame_reaches_broker_preview_without_submit"
  2 passed
.venv-win\Scripts\python.exe -m ruff check scripts/ehall_real_acceptance.py src/personal_assistant/infrastructure/browser/driver.py tests/unit/test_ehall_real_acceptance_script_f07.py tests/integration/test_ehall_interactive_browser_f07.py
  All checks passed!
.venv-win\Scripts\python.exe -m mypy src/personal_assistant
  Success: no issues found in 185 source files
```

## 验收边界

这次证明真实表单的 7 项测试值可经受监督 Broker 填入并生成结构化预览，且停在最终提交前。当前报告明确 `target_bound=false`、`acceptance_complete=false`：没有核实真实提交目标，也没有来自真实页面的完整材料清单与提交后果说明；临时 Adapter 只在验收脚本的内存存储中注册，尚未成为 `extensions/nju_ehall` 的真实版本适配器。没有真实 Tool Gateway → Outbox 路径或最终提交回执的证据。阶段 8 的完整权威预览验收仍未满足，F07 保持 `IN_PROGRESS`，不推进 F08。
