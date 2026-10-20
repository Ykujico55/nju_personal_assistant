# F09.1 Windows 安全凭据后端实现报告（2026-10-08）

## 0. 当前结论（2026-10-09独立验收通过）

**最小授权F09.1 DONE，F06.P2 DONE**。凭据功能先前已独立通过，SMTP全部收尾问题本次也通过独立复验，阶段阻断已解除。第1至11节保留实施和待审历史，最新证据见第12节；其中旧BLOCKED/待授权表述不再是当前状态。F08.6仍BLOCKED，回到其验收缺口，不实施其他F09。

## 1. 排期与状态

用户明确授权：“允许提前推进最小 F09.1 凭据后端，完成后回到 F08.6 验收；暂不启动其他 F09 功能。”

本轮只实施凭据后端、句柄和必要宿主/CLI 接线。F09.1 实现就绪、定向验证通过，必需回归门禁仍失败，状态 BLOCKED；F09 其余范围尚未实施。F08.6 继续 BLOCKED，F07/F08 整体 IN_PROGRESS。F08.6 旧回执 P2 已由用户独立复验关闭，本轮未改 PWA。

实际仓库：`C:\Users\kunji\Desktop\homework\生成式软件工程\lab1`。保留原有未提交的 F08.6 变更；本轮没有提交、推送、部署、重启原服务或启用真实账户。

## 2. 实现

- `infrastructure/platform/windows/credential_manager.py`：stdlib ctypes 的 Advapi32 Generic Credential 适配器；异步宿主端口在等待线程完成后返回，真实 OS 存取/删除，不是内存持久化替身。
- 引用为 `wcm1_<32位小写十六进制>`，TargetName 为 `PersonalAssistant/personal-assistant/v1/<kind>/<id>`。随机新 ID、同名不覆盖，kind/namespace 绑定，拒绝裸密钥或任意 OS 目标。
- Blob 为 UTF-8，非空且不超过 2560 字节，支持 Unicode/多行。Generic Type=1、Persist=2，读回核对这些元数据与类型、目标；缺失/损坏/编码/OS 错误转换为常量类型化错误，不返回成功，不携带原异常链。
- 可控 bytearray/native 缓冲清零，CredReadW 的分配块经 CredFree 释放。取消先等开始的写入结束，再尝试精确删除本次新引用。
- `infrastructure/secrets/factory.py` 与 `bootstrap.build_secret_store()`：Windows 的 PostgreSQL 组合根按平台选择；非 Windows 拒绝操作，禁止自动退回明文/内存。保留显式注入与 development/test 内存替身，模型/邮件/Push 共用选定端口。
- `assistantctl secret put --name ... --kind ...`：只读隐藏交互终端单行输入；无参数值、文件、环境、管道、读取/导出能力。GetPassWarning 转失败，不降级回显；输出只含句柄。删除命令只接受 ID/kind，缺失引用幂等。未知参数与异常不回显敏感文本。
- CLI 经组合根独立构造端口，不导入基础设施或调用数据库容器，原架构断言保留。
- 未增加第三方依赖或数据库迁移；现有 0010/0011 及审批/Outbox canonicalization 未修改。

## 3. 改动文件

| 文件 | 用途 |
| --- | --- |
| src/personal_assistant/infrastructure/platform/windows/__init__.py | Windows 后端导出，导入不加载 DLL |
| src/personal_assistant/infrastructure/platform/windows/credential_manager.py | Native Credential Manager 与脱敏边界 |
| src/personal_assistant/infrastructure/secrets/factory.py | 平台工厂 |
| src/personal_assistant/infrastructure/secrets/__init__.py | 工厂导出 |
| src/personal_assistant/infrastructure/secrets/unavailable.py | 不支持平台的拒绝操作说明 |
| src/personal_assistant/bootstrap.py | 独立凭据装配及 PostgreSQL 默认接线 |
| src/personal_assistant/cli/secret_commands.py | 本机隐藏输入/删除及安全错误 |
| src/personal_assistant/cli/main.py | 命令解析与分派 |
| tests/unit/test_windows_credentials_f09.py | 便携替身反例及接线验证 |
| tests/unit/test_secret_cli_f09.py | 无回显/无网络/无参数输入验证 |
| tests/integration/test_windows_credentials_native_f09.py | 真实 Windows 独立进程与 VAPID 解析 |
| README.md、TODO.md、docs/NEXT_STEPS.md、docs/CONTRACTS_AND_INTERFACES.md | 使用方式、排期、契约与证据 |
| 本报告 | 本轮证据和边界 |

`var/verification/f091/run_gates.py` 与日志属于 Git 忽略的本机门禁辅助产物，未存密钥或 DSN。运行器清除继承的 PA_，PostgreSQL 门禁仅在子进程环境中使用派生的 *_test DSN，日志再脱敏；不使用原业务数据库作为测试库。

## 4. 先红后绿

先新增测试后运行：原测试模块同名造成一次 collection error，重命名集成测试文件后，正式首轮为 **28 failed、1 passed**（后端/工厂/CLI 尚不存在），不是把 collection error 当功能反例。

随后实现：初始 **29 passed**；补充字节边界、取消清理及真实 OS VAPID 解析后 **32 passed**。目标加相邻 CLI/组合根/架构回归为 **42 passed**。

反例包括：错误 kind/namespace、同名值不覆盖、原始密钥冒充引用、空值/控制字符/UTF-8 超限、非法 UTF-8 和元数据、OS 异常携带合成秘密、隐藏输入失败、未知 --value、取消时写入清理。未删除、跳过或弱化断言。

## 5. 精确命令与实际结果

以下命令均在本报告第 1 节的实际仓库根目录执行。

目标：

```powershell
.\.venv-win\Scripts\python.exe -X utf8 -m pytest -p no:cacheprovider -o 'addopts=--strict-markers' -q --tb=short tests/unit/test_windows_credentials_f09.py tests/unit/test_secret_cli_f09.py tests/integration/test_windows_credentials_native_f09.py
```

结果：32 passed。Windows 原生测试不因 API 不可用而跳过；仅非 Windows 跳过两项 Native 用例。真实 OS 测试使用现场生成的合成值与独立命名空间：父进程写入，另一个 Python 进程只接收 namespace、ID、kind、预期 SHA-256，验证读回并删除，父进程确认已不可读取，finally 精确清理。VAPID 用例生成临时 P-256 标量，仅验证宿主解析，断言 sender 没有发送。

相邻回归：

```powershell
.\.venv-win\Scripts\python.exe -X utf8 -m pytest -p no:cacheprovider -o 'addopts=--strict-markers' -q --tb=short tests/unit/test_windows_credentials_f09.py tests/unit/test_secret_cli_f09.py tests/integration/test_windows_credentials_native_f09.py tests/contract/test_extension_independence.py tests/unit/test_cli_extension_flow.py tests/unit/test_bootstrap_f04.py
```

结果：42 passed（包括上述 32 项，不相加）。

门禁：

```powershell
.\.venv-win\Scripts\python.exe -X utf8 var/verification/f091/run_gates.py full
.\.venv-win\Scripts\python.exe -X utf8 var/verification/f091/run_gates.py postgres
```

分别调用仓库 `scripts/test.ps1` 和 `scripts/test-postgres.ps1`。首轮（两个门禁并行、子进程强制 UTF-8）实际结果：

- 全量：**9 failed、1284 passed、130 skipped、15 warnings**，4254.66 秒。脚本因 pytest 失败未继续其 Ruff/Mypy。CLI 架构失败已通过组合根接线修正，原断言未改；该运行期间源码有修正，不能当最终快照通过证据。
- PostgreSQL/Worker：**3 failed、127 passed、2 warnings**，3303.28 秒；失败是 smail tool.invoke RPC 超时及两项 ehall WORKER_TIMEOUT，真实 form.list 通过。
- 日志保留为 `var/verification/f091/full-first.log` 和 `postgres-first.log`。

失败路径串行命令：

```powershell
.\.venv-win\Scripts\python.exe -X utf8 var/verification/f091/run_gates.py full-failures
.\.venv-win\Scripts\python.exe -X utf8 var/verification/f091/run_gates.py postgres-failures
```

full-failures 覆盖原 CLI 架构文件、F07 浏览器进程回收、三项失败 PWA 浏览器用例及完整 RPC robustness 文件。先为 **2 failed、19 passed、1 warning**；确认 PYTHONUTF8=1 与 Windows tasklist 系统编码冲突，产生 UnicodeDecodeError、stdout=None。运行器改为子进程 PYTHONUTF8=0、仅 PYTHONIOENCODING=utf-8，pytest 子进程不带 -X utf8 后，结果 **1 failed、20 passed**。未改仓库测试或断言。

剩余失败：`tests/unit/test_extension_rpc_robustness.py::RpcRobustnessTests::test_rpc_call_timeout_includes_lock_wait` 原断言 elapsed < 0.7 秒，两次串行分别 0.703/1.641 秒；有 event-loop slow callback 日志。未认定该项已排除，未放宽时限或改 RPC 核心（超出本轮文件范围）。

postgres-failures 跑完整 smail/ehall Worker 文件：**6 passed**，697.57 秒；临时库、真实 Worker、模拟外部服务器，未连接真实邮箱/ehall。

最后对最终快照执行（无 -X utf8）：

```powershell
.\.venv-win\Scripts\python.exe -m pytest -p no:cacheprovider -o 'addopts=--strict-markers' -q --tb=short tests/unit/test_windows_credentials_f09.py tests/unit/test_secret_cli_f09.py tests/integration/test_windows_credentials_native_f09.py tests/contract/test_extension_independence.py tests/unit/test_cli_extension_flow.py tests/unit/test_bootstrap_f04.py
```

**42 passed，28.94 秒**，包括两项真实 Windows Native 测试。最终全仓 Ruff、Mypy 232 files 与 pip check 再次通过。

**实现定向验证通过，必需回归门禁未全通过。F09.1 保持 BLOCKED，不宣布 DONE；F08.6 仍 BLOCKED。** 未重跑第二遍完整全量/完整 PostgreSQL 脚本，局部复跑不替代完整门禁。

静态：

```powershell
.\.venv-win\Scripts\python.exe -m mypy src/personal_assistant extension_sdk/src/personal_assistant_sdk extensions/example_echo/src/example_echo extensions/personal_knowledge/src/personal_knowledge extensions/nju_smail/src/nju_smail extensions/nju_ehall/src/nju_ehall
.\.venv-win\Scripts\python.exe -m ruff check src extension_sdk extensions tests
.\.venv-win\Scripts\python.exe -m pip check
git diff --check
```

结果：Mypy 232 个源文件无问题；Ruff 通过；无损坏依赖；差异检查通过，仅有既存 LF/CRLF 提示。初次检查发现新 CLI 泛型标注不匹配，修正后复跑通过。

## 6. 未实现及风险

- 本轮证明同一 Windows 身份下的真实跨进程持久化，不是重新登录/电脑重启/服务账号冷启动验收。LOCAL_MACHINE 只在同机同用户下跨登录保存，不给其他用户共享、不企业漫游；未来换服务身份须重新配置。含义依据 [Microsoft CREDENTIALW](https://learn.microsoft.com/en-us/windows/win32/api/wincred/ns-wincred-credentialw)。
- 同用户可信代码仍可能访问 Generic Credentials，不宣称恶意代码沙箱；不提供逐用途 OS ACL。
- Python 字符串/OS 内部副本不保证全堆清零；强杀/断电或 OS 拒绝清理时可能留下孤立记录。同名创建不是自动轮换；引用更新与旧引用撤销需要宿主显式处理。
- 原用户授权的临时本机 DSN 例外仍保留，未迁移到 Credential Manager；不得宣称系统所有既有凭据已安全迁移。没有录入用户邮件/模型密码或真实 VAPID。
- 原正在运行的助手未重启、未换包，尚未加载本轮后端。收尾只读监听确认8000/8010/20241均为127.0.0.1，8001无监听；原PID分别为30748/14976/8928。真实 Push 的配置/白名单、手机订阅/接收/撤销/锁屏内容/点击仍需回到 F08.6。
- 不实现服务守护、Job Object、交互桌面桥、文件监听、自动发信/填表、LLM 路由，其他 F09 功能未启动。

## 7. 返回 F08.6 的下一步

先核对未通过的RPC门禁并完成F09.1独立复验，未过门禁不标DONE；之后按已授权排期回到 F08.6，在已授权接入边界内配置真实 VAPID 引用并补手机证据。F08.6 仍缺手机实际 v15、真机丢回执重试、正式单次审批及其缓存、独立 Cloudflare 中断、真实 Push 完整流程，继续 BLOCKED。

F07 真实事务 Adapter、绑定实际目标及完整材料/后果的权威预览、正式 Gateway/Outbox 实站链路与完整真实填报仍未验收；没有制造申请、连接真实 ehall 或点击最终提交。

## 8. 2026-10-08 交接文件摘要（历史快照）

基准 HEAD：`deeb9b2aa65481a3eeefdcbdb88cfeebef45d672`。下列15项是本轮代码、测试及状态文档的最终SHA-256；不包含本报告自身，也不改写此前F08.6报告的历史摘要。

| 文件 | SHA-256 |
| --- | --- |
| src/personal_assistant/infrastructure/platform/windows/__init__.py | `6e30a7ab12e6ccbadfcfa104f77e545772617a3710f691a6c40e81982425b3a1` |
| src/personal_assistant/infrastructure/platform/windows/credential_manager.py | `adaf0f4d7d40f4f44eba7ec63d86022bc40c55e012d08d85a4151ee60366ca99` |
| src/personal_assistant/infrastructure/secrets/factory.py | `a0f643a1494160c08b98d4434f20aa85e8abd018c5f73e7313e2075c8706a89a` |
| src/personal_assistant/infrastructure/secrets/__init__.py | `9c5cbbc4ef9f7e9eb4eb0621443ad04eb1b23d152298eb63a9c3696d80d56bea` |
| src/personal_assistant/infrastructure/secrets/unavailable.py | `b049a30efb41c8313d1047b1a4fc81c989c6a46c9e31db9a421ff07be6834a5a` |
| src/personal_assistant/bootstrap.py | `70daf1faa6288e531bc68609ebc0c65b140a0afaaea22a63f99f286690b51f7d` |
| src/personal_assistant/cli/secret_commands.py | `ddcb331e783b4bce934ba3f7c4efc197d20873ade56a533fbd9b46e2b0d3a49c` |
| src/personal_assistant/cli/main.py | `a1ac9b0af5f25ff2a54d0a4491b276b28925b4afa8eb1b3c215375553a23608f` |
| tests/unit/test_windows_credentials_f09.py | `60157b0fe7520a3563dc3be7b4655a247918d53e9e4d27851c3eb46137ad43d2` |
| tests/unit/test_secret_cli_f09.py | `44ee97c76f2f88b49b8a6dc724e12f1db095a04eb9c783857d236161ed32bff6` |
| tests/integration/test_windows_credentials_native_f09.py | `b54d65d70f3981c9a8fc72c307fc95df7f1ff299adfbf96793449563c820bfa5` |
| README.md | `e0be2759caa02f0243cbace3fa4c308a1b09625b61a0359a932e1a1428b985b5` |
| TODO.md | `be852f75f7cac661eed1564363451593a765a2f8a41b1dd86b4877aa83cb05ff` |
| docs/NEXT_STEPS.md | `93b1e0d72cf04172c2f72eb8c7f410bc215bafd6a19abfc47f34f048e63bf0a3` |
| docs/CONTRACTS_AND_INTERFACES.md | `a3345eae6ac6abcff536844a3eef9a3f2924de2f67ce2a52e301968d8a896c05` |

## 9. 独立验收反馈与剩余 SMTP P2（2026-10-09）

本节保留修复前独立验收结论；当前已授权修复及结果见第10节。第1至8节保留2026-10-08实施快照和原门禁结果，不把历史失败改写成通过。第8节15项摘要已由用户独立核对吻合，随后本节及状态文档更新，因此摘要不是2026-10-09更新文档的当前哈希。

用户独立验收：**F09.1凭据功能通过，整阶段仍BLOCKED**。实际运行结果为：定向42 passed（含真实Windows跨进程）；RPC14 passed（本次未复现，不表示根治）；全量1292 passed、130 skipped、1 failed（邮件TLS超时用例，单例复跑通过不等于全量全绿）；PostgreSQL/真实Worker130 passed。Ruff、Mypy232文件、依赖、差异及15项摘要通过。完整命令文本未在反馈中提供，实施者本轮没有重新运行上述测试，也不将这些数字归为自身执行证据。

新P2在既有F06 SMTP连接生命周期：`smtp_client.py` 第86/87行的 `wrap_socket` 默认自动握手，实际TLS socket直到握手返回后才登记。用户独立反例取消250ms后连接仍开着、线程仍运行，设置2秒socket超时后到约2.0秒才回收；默认30秒会造成明显取消拖延。反例没有发送邮件，阻断不是凭据泄漏。

拟只改SMTP生命周期及现有TLS协议测试（必要时局部测试服务夹具）：禁用创建时自动握手，先登记TLS socket，再检查取消并显式握手；失败和取消关闭实际连接。先补失败反例，约束取消耗时，分别控制底层socket超时与宿主总时限；保留线程回收、连接关闭、零发送及既有FAILED/UNKNOWN分类断言。修复后串行完整复验。

**F06跨范围修复等待另行授权**：AGENTS每任务步骤3及用户本轮明确要求。未获得授权前不修改产品或测试、不将F06设为NEXT；不重做凭据后端。F09.1与F08.6保持BLOCKED，F07/F08整体IN_PROGRESS；其他F09不启动。授权修复并通过完整复验后再独立验收F09.1，随后回到F08.6。当前只更新README、TODO、NEXT、契约及本报告，原服务保留，未部署、重启、提交或推送。

## 10. 已授权F06修复与完整门禁（2026-10-09）

用户要求先写提交说明并推送，再修F06；已先提交/推送9891102，然后才实施SMTP修复。凭据后端未重做；隐式TLS和STARTTLS实际socket登记/显式握手及失败关闭已修复，先红4 failed/3 passed，最终邮件定向86 passed。完整scripts/test.ps1为1298 passed、130 skipped、11 warnings，Ruff/Mypy232通过；随后串行完整PostgreSQL/真实Worker130 passed、1 warning。首修复复跑有1项1.5秒计时失败，隔离及后续完整回归通过，未放宽断言；历史失败和告警均保留。

[F06修复报告](F06_TLS_CANCELLATION_REPAIR_2026-10-09.md)提供当前文件、精确命令与摘要。F06.P2实施就绪待独立复验，F09.1/F08.6仍BLOCKED，不自行宣布阶段完成或启动其他F09。F06修改未再次提交/推送，未部署、重启服务或连接真实邮箱/ehall。第8节摘要继续作为当时已独立核对的历史快照保留。

## 11. SMTP准备失败回收的局部补修（2026-10-09）

用户独立确认原TLS取消及交接6项通过、邮件86 passed，但STARTTLS缺能力或454拒绝遗留TCP，F06.P2尚不能关闭。已补缺能力/454/EHLO拒绝的实际TCP反例3 failed→3 passed，_connect统一关闭准备失败连接并原样抛出；相邻回归**89 passed，134.30秒**，Ruff/Mypy1/依赖通过。按局部规则未重跑全量或PostgreSQL/Worker，第10节完整门禁为补修前快照。F06.P2等待独立复验，F09.1/F08.6仍BLOCKED；凭据后端未改、未重启服务或再次提交/推送/部署。当前摘要与精确命令见[F06报告](F06_TLS_CANCELLATION_REPAIR_2026-10-09.md)第8/9节。

## 12. 最小F09.1独立验收关闭（2026-10-09）

用户明确判定F06.P2 DONE、最小授权F09.1 DONE：原TLS取消及缺STARTTLS、454/EHLO拒绝连接收尾均已修复，本次范围无剩余阻断。实际独立结果为邮件89 passed及额外10项；全量1301 passed/130 skipped/9 warnings，PostgreSQL/真实Worker130 passed/1 warning；Ruff、Mypy232文件、依赖、差异、8项摘要和wheel检查通过，记录25个测试进程已退出。既有警告保留，不宣称历史计时波动或其他阶段全部问题已根治。

本轮实施者只读核对本机full.log/postgres.log上述摘要及三份已验收代码/测试摘要一致，更新状态文档；没有重新运行用户测试、修改业务代码、连接真实邮箱、重启服务或提交/推送/部署。完整独立命令文本未提供，不杜撰；历史可复现实验命令继续保留。

F08.6仍BLOCKED，是唯一后续验收项；安全凭据端口已经实现，实际VAPID配置与部署接线/真实Push流程尚未验收。补手机v15、真机回执丢失/同键重试、正式审批/敏感缓存、Cloudflare独立中断以及Push订阅/接收/撤销/锁屏/点击。其他F09功能不启动，原临时DSN例外不因本次DONE自动消失。[F06最新验收](F06_TLS_CANCELLATION_REPAIR_2026-10-09.md)第10节记录日志与验证边界。
