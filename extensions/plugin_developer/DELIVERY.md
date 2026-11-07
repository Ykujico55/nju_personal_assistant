# EXT.DEV.1 交付报告：受监督插件开发 Agent

> 后续原实例安装、DeepSeek接入及计算器试写已实际进行，14项候选测试通过；状态和限制见[真实试写记录](../../docs/EXT_DEV2_REAL_MODEL_TRIAL_2026-10-30.md)。下文保留先前本地交付范围。


日期：2026-10-30。状态：**DONE（本地开发范围）**。真实实例安装与真实模型开发试用尚未执行，另列 EXT.DEV.2。F08.6 继续 BLOCKED，F07/F08 状态不变。

## 实现结果

新增独立 assistant.plugin_developer@0.1.0，用9个标准 ToolProvider 工具实现候选创建、文本读写/CAS、差异检查、获准验证、失败后迭代和安装材料交接。模型沿用现有Agent规划工具调用，无新核心分支、业务路由、数据库迁移或SDK修改。

1. workspace.py：预先配置的专用空工作区，候选/基线/控制/发布目录分离；ID及版本绑定、文本白名单、大小限制、拒绝路径穿越和链接/junction，旧SHA比对后写入。
2. validation.py：静态AST/TOML/JSON及依赖锁检查、精确快照、固定unittest discover；只接受本人已授权的摘要。环境清理、Windows Job及有界日志，超时/取消关闭进程树，清理未确认记UNKNOWN。
3. control.py：本机SQLite持久授权与验证回执；默认15分钟一次精确快照，可由本人显式选择30分钟最多5次迭代，固定Manifest/锁文件/pyproject。相同键不重复执行，坏回执/未知状态不当成功。
4. worker.py及schemas/extension.toml：9个工具适配公开SDK；模型获得有界失败位置与状态，不获得Admin确认nonce、run_token、绝对控制路径或原始进程日志。
5. cli.py及manage.py：本人交互式测试授权；只有当前摘要最新有效PASS才导出安装材料，经原宿主CLI再次确认安装/升级。只有安装明确成功才执行显式--enable；回滚复用宿主CLI。
6. README.md、INSTALL.md，以及根README、TODO、NEXT_STEPS、契约和全系统手测文档：用法、边界、证据与下一步。

生成候选有真正的SDK生命周期/调用unittest骨架。组合测试实际经历“错误实现 → 验证失败 → CAS修复 → 新摘要验证通过 → 冻结材料 → 本机安装CLI交接”。安装CLI子进程在此组合测试中被替身截住，未操作真实Admin；真实stdio握手、目录、候选操作和EOF退出则由实际Worker进程验证。

## 本轮发现并修复的边界

- 取消时验证器清理异常曾把取消转成普通失败；新增反例先失败，现等待清理、保存UNKNOWN并继续抛取消，避免误报已正常结束。重复取消不提前脱离验证线程。
- 测试配置上限曾可能超过宿主RPC期限；现在按实际UTC InvocationContext.deadline限制预算，预留10秒清理；过短/无效期限在授权消费及执行前拒绝。
- 另一快照RUNNING/UNKNOWN时，改回旧摘要曾可找到历史PASS；现在候选任何未确认记录都阻止发布，不能用编辑回旧代码绕过。
- 之前分工开发中的工作区身份、快照/CAS、进程回收、零测试/全跳过、旧通过失效等回归均保留。

## 实际验证

在仓库根目录运行：

~~~powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest -c extensions/plugin_developer/pyproject.toml extensions/plugin_developer/tests -o addopts=--strict-markers -q
.\.venv-win\Scripts\python.exe -Xutf8 -B -m ruff check extensions/plugin_developer/src extensions/plugin_developer/tests extensions/plugin_developer/manage.py
~~~

在 extensions/plugin_developer 目录运行：

~~~powershell
..\..\.venv-win\Scripts\python.exe -Xutf8 -B -m mypy --config-file pyproject.toml src
~~~

| 检查 | 最终结果 | 本地证据 |
|---|---|---|
| 新扩展完整定向测试 | **73 passed / 65.85秒** | [final-tests.log](verification/final-tests.log) |
| Ruff，新扩展源码/测试/入口 | 通过 | [ruff.log](verification/ruff.log) |
| 严格Mypy，6份源码 | 通过 | [mypy.log](verification/mypy.log) |
| 原宿主ManifestParser只读解析 | ID/0.1.0/9工具均通过 | [metadata-check.json](verification/metadata-check.json) |
| README/INSTALL中的PowerShell命令块 | 语法解析通过，未执行 | 本轮工具输出 |
| git diff --check及新文件尾空白 | 通过 | 本轮工具输出 |

早期65项集合、单例和先红后绿日志留在verification，不与最终73项相加。最终集合包含实际Windows验证子进程、子孙进程回收、真实SDK stdio Worker，以及组合开发循环；不是只有mock测试。

按用户要求避免全套过度验证，本次只涉及独立扩展与文档，**未运行完整宿主、PostgreSQL/Worker套件或真实模型/网站联调**；没有把此前全量结果当本次结果。本地SQLite仅保存本插件开发状态，不替代主项目的生产PostgreSQL。

## 未执行范围与使用限制

- 没有安装/启用/升级/回滚到原实例，没有迁移原库、重启/解冻服务、发送邮件、投递简历、调用真实模型、提交或推送。
- 代码验证以当前Windows用户权限执行信任代码；目录控制、环境清理、子进程和venv不是恶意代码沙箱，不能承诺阻止获准代码访问网络或同用户资源。本人确认仍必须早于候选执行/安装。
- 固定runner使用当前Python及公开SDK，不自动安装第三方依赖或运行任意shell；有额外依赖的候选需本人准备依赖环境并验证，不能用本轮无依赖夹具的通过代替。
- 生成源码与测试结果可通过已有模型披露机制进入模型上下文；候选和夹具不得包含真实凭据或私人材料。原始验证日志留本机，工具仅提供有界摘要。
- 断电/Worker被强杀可能留下持久RUNNING；必须本机检查后处理，工具不通过清键自动重跑。运行中的代码不能靠换一个候选名绕过人工信任要求。
- 不提供直接编辑已安装插件目录或宿主核心的工具；新版本在候选区准备，再经独立确认升级。生成代码还需业务正确性与真实环境验收。
- 宿主的3/5/8/24h熔断器、R2/UNKNOWN及安装确认机制保持原状。不能把开发扩展测试授权当成业务外部动作审批。

## 下一编号

**EXT.DEV.2（NEXT，待原实例安装确认）**：按[INSTALL.md](INSTALL.md)核对同一持久profile与本机Admin，确认并安装开发扩展、配置专用工作区，然后让真实模型完成一个公开、无外部副作用的小插件，验证本人确认安装后工具可发现/调用。该步骤本轮尚未开始，不自动启动其他功能或解除F08.6阻断。
