# 测试使用与精简记录

日常改动从关键集合开始；按改动路径补专项。不要每修一个局部问题就重跑二十多分钟的完整套件。

## 命令

在仓库根目录的 PowerShell 执行：

```powershell
# 默认：关键回归 + Ruff + Mypy
.\scripts\test.ps1

# 指定改动路径（替代默认选择）
.\scripts\test.ps1 -TestPath tests/unit/test_extension_rpc_robustness.py

# 浏览器、进程、协议等集成专项
.\scripts\test.ps1 -Suite integration

# 三个独立插件，分别使用自己的 pytest 配置
.\scripts\test.ps1 -Suite plugins

# 发布/阶段验收：主项目全部测试 + 三个独立插件 + 静态检查
.\scripts\test.ps1 -Suite full

# 只检查收集，不执行用例、不跑静态检查
.\scripts\test.ps1 -Suite full -CollectOnly

# 真实 PostgreSQL / Worker 专项；仅使用隔离的 *_test 数据库
.\scripts\test-postgres.ps1 -DatabaseUrl '<隔离测试库连接串>'
```

`-TestPath` 接受数组，可指定多个文件或 `文件::类::方法`。不要同时把它当成追加到 `-Suite` 的列表：传入路径后只执行这些路径。所有执行模式在 pytest 成功后运行既有 Ruff/Mypy；仅收集模式不运行它们。

`full` 不自动创建 PostgreSQL、配置真实邮箱/模型/网站或执行手机验收。缺少依赖的 skip 必须单独记录，不能当作通过。数据库专项文件内保留原有临时目标校验，勿使用原助手数据库。

## 保留哪些测试

`tests/core-suite.txt` 明确列出日常关键范围：架构及迁移、审批和 UNKNOWN、队列与任务恢复、熔断、凭据与模型披露、扩展恢复/配置、知识库、邮件/浏览器动作边界、通知、API和TUI服务协议。保留原测试，不复制出另一套关键测试。

真实浏览器矩阵、安装/venv、数据库、协议取消、进程重启、TUI渲染仍保留，修改对应路径时专项运行，阶段验收再用完整入口。耗时长不构成删除理由。新的关键风险修复应加入清单；样式、文案和实现重排不必新增重复测试。

完整入口先收集 `tests/`，再分别用独立 pytest 进程及各自配置运行 `extensions/career_apply/tests`、`extensions/plugin_developer/tests` 和 `extensions/personal_files/tests`，避免独立插件的同名模块和配置相互污染。`-Suite plugins` 仅选择这三项插件集合。PostgreSQL入口从13个文件补齐为18个，涵盖恢复竞争、知识库元数据、对话、任务排序和对账。

测试脚本临时移除继承的 `PA_*` 配置和 `PYTHONPATH`，保留明确指定的 `PA_TEST_DATABASE_URL`，结束时恢复原值；不会读取、修改或重启已运行的助手。此隔离不替代每个测试自身的临时目录和外部服务边界。

## FILES.1 专项入口（2026-11-07）

只检查通用文件插件时，使用它自己的配置和合成临时目录：

```powershell
.\scripts\test.ps1 -TestPath '-c','extensions/personal_files/pyproject.toml','extensions/personal_files/tests'
```

`-Suite plugins` / `-Suite full` 已纳入第三个独立插件 `personal_files`。这是入口接线，不表示已执行或通过该集合；实际数字由 FILES.1 本轮报告记录。以下旧测试精简结果发生在新增插件之前，保留其当时的两个插件数量，不当作当前完整收集数。

## 历史：2026-11-07 删除和修正

本次仅整理测试、测试脚本和说明；不修改生产实现。

删除17项低价值测试，产品文档继续保留：

| 原文件 | 删除内容 | 保留的实际保障 |
|---|---|---|
| `test_f02_contract_consistency.py` | 全部4项：2项文档关键词、2项源码拼写/出现次数 | verifier取消回收、RPC单deadline和剩余预算的行为测试 |
| `test_f03_contract_consistency.py` | 2项文档路径/措辞 | 认证、架构、JWT、秘密扫描 |
| `test_f04_contract_consistency.py` | 1项披露契约文档查词 | 披露许可、目标绑定、模型路由 |
| `test_f05_contract_consistency.py` | 3项历史状态/文档查词，含旧“F06 NEXT” | 知识库行为和契约 |
| `test_f06_contract_consistency.py` | 1项历史“F06 DONE”查词 | 邮件协议、审批、账本 |
| `test_f07_contract_consistency.py` | 5项文档查词/旧“F08 TODO”；1项恒真断言加源码查词 | Gateway UNKNOWN及浏览器行为反例 |

关键替代节点包括：

- `tests/unit/test_extension_cancel_cleanup.py::InstallCancellationTests::test_cancelling_contract_verification_closes_the_worker`
- `tests/unit/test_extension_rpc_robustness.py::RpcRobustnessTests` 的call/drain deadline与清理用例
- `tests/unit/test_tool_gateway.py::ToolGatewayTests::test_unknown_external_result_is_not_retryable`

其他修正：

- RPC：从等锁/剩余预算验证中分离操作系统进程回收耗时，真实就绪信号代替固定等待；统一记录本文件client、所有代际进程和管道任务，成功或失败都收尾。测试拥有的管道transport在事件循环关闭前明确关闭，清理异常上报；不放宽生产超时，不通过删除失败节点求绿。
- 知识库元数据：删除过时的 `0.1.5` 预期字面值，改为核对当前 `EXTENSION_VERSION`；版本绑定断言仍在。
- 集成文件 `test_extension_recovery_concurrency.py` 重命名为 `test_extension_recovery_concurrency_postgres.py`，测试内容不变，解决与unit文件同名的收集错误；历史报告里的旧路径对应此文件。无需全局改import模式或临时加tests目录到PYTHONPATH。

## 验证记录与后续边界

证据目录：`var/verification/test-suite-trim-2026-11-07/`。完整收集成功：主项目2157项，career_apply119项，plugin_developer73项；数据库专项收集160项。收集成功不等于执行通过。

剩余契约回归90项通过；RPC定向14项通过，且受控注入“取锁后重置预算”和“超时不停止进程”都被测试拒绝。关键集合首轮642 passed/1 failed/8 warnings，97.72秒；失败为上述过期知识版本断言，原始日志 `core.log` 保留。修正后关键集合 **643 passed、6 warnings，97.20秒**，Ruff通过、Mypy **290文件**通过；日志 `core-final.log`。没有再出现Windows管道析构警告，剩余是依赖弃用及合成短HMAC密钥警告。失败日志仍保留。

脚本失败分支以不存在路径验证pytest退出码4原样返回，环境与工作目录恢复；数据库入口只收集160项并验证环境恢复。

没有运行本轮完整套件、真实PostgreSQL或真实网站。此次是测试维护；CORE.RECONCILE.1的阶段验收状态不自动改变。

盘点中保留两个邮件取消慢用例：IMAP在构造/CAPABILITY完成前未登记连接的边界值得另查；SMTP重复取消的约30秒耗时尚不能区分线程或TLS夹具收尾。它们没有被删掉、skip或缩短生产超时。Windows管道析构警告也不屏蔽，须按实际最终日志记录。没有把新的怀疑冒充已证实的产品根因。
