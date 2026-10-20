# F06 SMTP TLS取消修复报告（2026-10-09）

## 0. 当前结论（2026-10-09独立验收通过）

**F06.P2 DONE；最小授权F09.1 DONE**。原TLS取消及缺STARTTLS、454/EHLO拒绝时连接收尾均已独立通过，本次范围没有剩余阻断。F08.6仍BLOCKED，F07/F08整体IN_PROGRESS。第1至9节保留实现及待审历史，以第10节独立结论为准；第7/9节是当时已独立核对的历史摘要，第11节为状态文档更新后的摘要。本轮只更新文档，无源码/服务/提交推送部署操作。

## 1. 顺序与当前状态

用户明确要求“先写一下cm然后推送，推完再修F06”。已提交35文件当前快照并正常快进推送：

```powershell

git fetch origin main

git commit -F var/verification/f091/checkpoint-message.txt

git push origin main

```

提交：`9891102 feat: add Windows credentials and F08.6 PWA hardening`（父提交deeb9b2）。提交说明保留F09.1/F08.6 BLOCKED与SMTP已知P2。之后才新增F06测试并修改SMTP实现；本报告中的F06变更未再次提交/推送或部署。

F06.P2实现与实施者完整验证通过，保持IN_PROGRESS待独立复验。F09.1凭据功能已经用户独立通过，阶段仍BLOCKED等待本P2独立关闭；F08.6仍BLOCKED，F07/F08整体IN_PROGRESS。未重做凭据后端、开启自动发信/填表或推进其他F09。

实际仓库：`C:\Users\kunji\Desktop\homework\生成式软件工程\lab1`。

## 2. 原因及最小修复

原实现先注册raw socket，再默认wrap_socket自动握手，握手返回后才注册实际SSLSocket；wrap转移了socket所有权，取消期间关raw对象无法及时关闭真实连接。同一问题也存在标准库STARTTLS自动握手路径。

- 两种TLS模式共用：禁止创建时自动握手→登记实际SSLSocket→检查中止/guard→显式do_handshake→再检查。

- 所有握手错误关闭实际SSLSocket；STARTTLS错误还关闭SMTP连接。保留STARTTLS能力检查、220响应及RFC 3207的EHLO/ESMTP状态清除。

- FAILED/UNKNOWN分类、账号/租约guard、审批/Outbox、风险、canonicalization和迁移未改。

- 本机测试服务观察真实ClientHello与peer EOF/reset；不以服务端主动stop伪装客户端关闭。STARTTLS正常路径传输与请求完全一致的MIME，停滞反例均零AUTH/零发送。

## 3. 改动文件

| 文件 | 内容 |

| --- | --- |

| src/personal_assistant/infrastructure/mail/smtp_client.py | 实际TLS socket登记、显式握手与失败关闭；两种TLS模式 |

| tests/integration/test_mail_protocol_f06.py | 加强两个旧断言，增加独立socket/总时限及STARTTLS反例与通过路径 |

| tests/support/mail_servers.py | 观测peer关闭的停滞服务，最小STARTTLS模拟服务 |

| README.md、TODO.md、docs/NEXT_STEPS.md | 使用范围、排期与当前结果 |

| docs/CONTRACTS_AND_INTERFACES.md | 如实修正TLS生命周期说明及验证分支 |

| docs/F09_1_IMPLEMENTATION_REPORT_2026-10-08.md | 保留历史结果，追加本次修复链接 |

| 本报告 | 可复现实验、风险与摘要 |

无新增运行依赖、数据库迁移、业务扩展/API或PWA改动。运行器及日志在Git忽略的var/verification/f06_tls_cancel，保留历史f091日志；不含凭据。PostgreSQL仅从已授权本机配置在子进程派生*_test DSN，使用夹具临时库，日志脱敏，不测试原业务数据库。

## 4. 先失败，再实现

定向命令前清除子进程继承PA_*，保留Windows系统编码，只把stdio设UTF-8：

```powershell

$env:PYTHONUTF8 = '0'

$env:PYTHONIOENCODING = 'utf-8'

Get-ChildItem Env:PA_* | Remove-Item

.\.venv-win\Scripts\python.exe -m pytest -p no:cacheprovider -o 'addopts=--strict-markers' -q --tb=short tests/integration/test_mail_protocol_f06.py -k 'tls_handshake or starttls'

```

SMTP产品代码未修改时：**4 failed、3 passed、34 deselected**（15.31秒）。取消分别约2.015/2.578秒，两个总时限约2.126/2.156秒，超过保留的0.75/1.25秒上限。两个底层socket超时分支及正常STARTTLS通过。

实现后的首次同命令：**6 passed、1 failed**（5.53秒）；STARTTLS总时限1.5秒>1.25秒，伴随slow callback日志。未放宽断言，隔离原用例 **1 passed**（1.04秒）。另修正新增导入排序与类型标注（独立保存TLS主机名，不依赖typeshed未暴露的_host）。最终定向及完整门禁通过，不宣称历史计时波动已根治。

六项停滞反例均使用真实TCP/TLS socket，并明确区分：socket超时0.15秒/宿主30秒（abort=false）；宿主0.5秒/socket2秒（abort=true）；真实ClientHello后的取消<0.75秒。保留实际fileno=-1、线程为0、active为None、peer关闭、零发送断言。

## 5. 最终精确命令与结果

### 定向与相邻回归

```powershell

.\.venv-win\Scripts\python.exe -m pytest -p no:cacheprovider -o 'addopts=--strict-markers' -q --tb=short tests/integration/test_mail_protocol_f06.py tests/unit/test_mail_send_gateway_f06.py tests/contract/test_f06_contract_consistency.py

.\.venv-win\Scripts\python.exe -m ruff check src/personal_assistant/infrastructure/mail/smtp_client.py tests/integration/test_mail_protocol_f06.py tests/support/mail_servers.py

.\.venv-win\Scripts\python.exe -m mypy src/personal_assistant/infrastructure/mail/smtp_client.py

```

**86 passed，142.48秒**；目标Ruff通过，Mypy 1文件通过。

### 串行完整门禁

```powershell

.\.venv-win\Scripts\python.exe -X utf8 var/verification/f06_tls_cancel/run_gates.py full

.\.venv-win\Scripts\python.exe -X utf8 var/verification/f06_tls_cancel/run_gates.py postgres

```

- full实际调用scripts/test.ps1：**1298 passed、130 skipped、11 warnings**，1274.56秒；全仓Ruff通过、Mypy **232 source files**通过，退出0。

- postgres实际调用scripts/test-postgres.ps1：**130 passed、1 warning**，569.21秒，退出0。包括smail真实Worker/模拟TLS SMTP、ehall真实Worker及独立公共API的form.list链路；不是真实邮箱/ehall提交验收。

- 全量130项是既有未设置测试DSN时的跳过，随后真实PostgreSQL完整130项已执行；本轮没有新增skip或删除/弱化断言。

- 警告完整保留在full.log/postgres.log：依赖弃用、测试用短HMAC、Windows管道析构回收，以及pytest缓存目录权限。未隐藏警告或跨范围修改RPC/进程监督。

### 收尾静态检查

```powershell

.\.venv-win\Scripts\python.exe -m pip check

git diff --check

```

两项退出0：pip check 为 No broken requirements found，git diff --check 无输出通过。测试进程只读核对：本轮完整门禁期间记录的6项PID均已不在运行；未停止任何进程，不据此宣称全机没有其他测试进程。

文档更新后的单项契约复查：

```powershell

.\.venv-win\Scripts\python.exe -m pytest -p no:cacheprovider -o 'addopts=--strict-markers' -q tests/contract/test_f06_contract_consistency.py::MigrationHistoryContract::test_documentation_records_f06_as_done

```

**1 passed，0.05秒**。另逐行检查全部9项改动（含未跟踪的新报告）没有行尾空白；最后git diff --check通过。

## 6. 未验收及限制

- 本轮为本机Windows真实socket/证书/主机名验证、模拟服务器及真实PostgreSQL/Worker；未发送真实邮件、访问真实ehall、部署、重启服务或进行新的手机/Cloudflare/Push验收。

- 耗时断言通过本次串行测试，不是任意OS负载下的硬实时保证；保留首次修复计时失败供独立复核。

- 原有系统DNS解析线程无法强杀的限制未改变；本项修的是已创建TLS连接的取消/超时回收。

- F06.P2待独立复验关闭，然后再确认F09.1阶段状态，回到F08.6补实际v15、丢回执重试、正式审批/缓存、独立Cloudflare中断及真实Push；不启动其他F09。

- 原临时DSN例外未迁移，真实账户/VAPID没有配置。

## 7. 第一轮交接历史摘要（已独立核对）

以下8项为本轮补修前、已由用户独立核对一致的历史SHA-256；当前文件已更新，不能用此表核验当前内容。当前摘要在第9节；本报告不做自引用摘要。

| 文件 | SHA-256 |

| --- | --- |

| src/personal_assistant/infrastructure/mail/smtp_client.py | `8a7b07782e15c3ceb205385a4e6cf1d9832ddbffcbfabba65c54193c137d407d` |

| tests/integration/test_mail_protocol_f06.py | `b29a1d77be2e8cd9c4fa618b423cd52ee7aa828e9421166e78a60a78d9e7fd1a` |

| tests/support/mail_servers.py | `f7d2b386c8bd32e6ccf44330f303f2ca3ed2ab21b405f24db907e3827b6f82ef` |

| README.md | `f5d67663224947074ecf002a7f6edf6e538c4c7becabfc16ca37853375b40232` |

| TODO.md | `c452ea93df95332724b0d8b44c6e505f08d5790d8dbccf9888649dfb028a9c91` |

| docs/NEXT_STEPS.md | `a4b3e84fb43ba20a070748e7465e44860981b216900a8979aa8835a03f8f35d6` |

| docs/CONTRACTS_AND_INTERFACES.md | `cd5f2b9d79fcec8df8093f46b0f48f65b73e0a69c959e8013fc9fa7d398acda5` |

| docs/F09_1_IMPLEMENTATION_REPORT_2026-10-08.md | `b590cc22f35ff21517e9a04cc830d1936f33eb1badf25796ab670d02992b0e6a` |

## 8. STARTTLS准备失败的局部补修

### 独立反馈与原因

用户独立复跑上一轮邮件86 passed、取消及连接交接6项通过，Ruff/Mypy/依赖/差异和8项摘要一致；本轮不把这些数字计为自身执行。两个新增独立反例未过：未声明STARTTLS及454拒绝升级时发送FAILED、线程退出，TCP仍打开；无认证或发信。

_connect返回前准备失败，_send_sync还未拿到connection，无法在finally关闭；此前只保护了实际TLS握手。现把初次EHLO、能力检查/STARTTLS升级、socket交接、升级后EHLO统一放入try；任何异常先close，然后bare raise，清理异常不会替换原错误。既有TLS握手登记顺序、FAILED/UNKNOWN、审批/Outbox及迁移均未修改。

本轮在同3项代码/测试文件内增量修改：smtp_client.py的_connect异常保护；协议测试新增不支持/454/EHLO拒绝三个实际TCP反例；测试服务可声明缺能力、返回454或550，记录命令动词及peer EOF/reset，停止时等待所属handler任务。服务不记录AUTH载荷或正文；反例检查FAILED或既有PROTOCOL_ERROR、线程0、实际fd=-1、客户端关闭的peer证据及零AUTH/MAIL/RCPT/DATA，不以server.stop代替客户端回收；finally只为红测试兜底清理。

### 先失败，再实现

```powershell
$env:PYTHONUTF8 = '0'
$env:PYTHONIOENCODING = 'utf-8'
Get-ChildItem Env:PA_* | Remove-Item
.\.venv-win\Scripts\python.exe -m pytest -p no:cacheprovider -o 'addopts=--strict-markers' -q --tb=short tests/integration/test_mail_protocol_f06.py -k 'starttls_unsupported or starttls_454 or starttls_ehlo_refusal'
```

未改产品时 **3 failed、41 deselected，1.16秒**（实际fd分别1004、920、1108仍打开）；修复后同命令 **3 passed、41 deselected，0.88秒**。原有断言均保留；日志分别在var/verification/f06_tls_cancel/starttls_refusal/red.log及green.log。

### 相邻定向回归与静态检查

```powershell
.\.venv-win\Scripts\python.exe -m pytest -p no:cacheprovider -o 'addopts=--strict-markers' -q --tb=short tests/integration/test_mail_protocol_f06.py tests/unit/test_mail_send_gateway_f06.py tests/contract/test_f06_contract_consistency.py
.\.venv-win\Scripts\python.exe -m ruff check src/personal_assistant/infrastructure/mail/smtp_client.py tests/integration/test_mail_protocol_f06.py tests/support/mail_servers.py
.\.venv-win\Scripts\python.exe -m mypy src/personal_assistant/infrastructure/mail/smtp_client.py
.\.venv-win\Scripts\python.exe -m pip check
git diff --check
```

**89 passed，134.30秒**；Ruff三文件通过，Mypy 1源文件通过，pip check无损坏依赖；git diff --check及全部9项改动（含未跟踪报告）的行尾空白检查通过。回归日志在同目录regression.log；上一轮full.log/postgres.log不改写。

### 验证范围与后续

产品增量仅SMTP连接交接前异常清理；没有改共享状态、认证、审批/Outbox、SQL或Worker接口，依据AGENTS局部改动规则只做SMTP协议、Gateway与契约定向复验。本轮不是阶段验收或发布，不重跑全量、PostgreSQL/Worker，也不把第5节的1298/130数字当作当前快照的完整门禁结论。没有测试真实邮箱/ehall/手机/Push或重启原服务。

F06.P2等待独立复验，包括这次不支持STARTTLS、454和EHLO拒绝及此前握手取消/超时；通过后再关闭F09.1阶段阻断并返回F08.6，不重做凭据后端，不启动其他F09。此前OS负载下的计时波动、DNS解析线程限制与完整门禁管道警告仍保留，不宣称根治。工作区累计9项改动文件，提交9891102之后没有再次提交或推送。

## 9. 局部补修交接历史摘要（用户独立核对8项通过）

以下是本次验收前8项文件的历史SHA-256，用户已独立核对通过；状态文档随后更新，不能用本表核验新文档。当前摘要在第11节，本报告不做自引用摘要。

| 文件 | SHA-256 |
| --- | --- |
| src/personal_assistant/infrastructure/mail/smtp_client.py | `a0988ea29f0c84c8ac806e4beb739db1ccbe6d331d8ce62eba22f1f80622a2a8` |
| tests/integration/test_mail_protocol_f06.py | `8df10ff679e777b9be2f6e7576632d0fb4ffc2e8b1c10d410074c3fed3818bf5` |
| tests/support/mail_servers.py | `394fdffd719fd00b94952b688c57afbee8a02a0071b6d73f9ff57e16aaa3b4c4` |
| README.md | `970f887c11b3999154b306fc3df579f11134ee39cf222b9846209bd4f5f1e7f6` |
| TODO.md | `c5550653398c2ac7feb308cc74b40fb522d912cc99a732fc58ba23522cb908a1` |
| docs/NEXT_STEPS.md | `808203a97bbe50395919b79df288a4999c21ad425f63dd7f69c96a685a197d2e` |
| docs/CONTRACTS_AND_INTERFACES.md | `3d1f2d14678326d43970eb0b5994743b396157542491b02ce1a6e3f6a0089c8a` |
| docs/F09_1_IMPLEMENTATION_REPORT_2026-10-08.md | `2c08150b95c1db6837fe63fffab82290448479dbbad52267d4927b532a8a41f8` |

## 10. 独立验收通过及状态关闭（2026-10-09）

用户明确判定原TLS取消，以及缺STARTTLS、454、EHLO拒绝时的连接收尾均已修复，本次范围无剩余阻断：F06.P2标DONE，最小授权F09.1解除BLOCKED并标DONE。F08.6仍BLOCKED，下一步回到其真机/真实Push等证据缺口，F07/F08整体仍IN_PROGRESS。

| 验证 | 独立实际结果 | 证据 |
| --- | --- | --- |
| 邮件相邻回归 | 89 passed | 用户独立反馈；可复现实施命令见第8节 |
| 额外独立检查 | 10项通过 | 用户反馈；完整命令文本未提供 |
| 全量 | 1301 passed、130 skipped、9 warnings；1019.06秒 | var/verification/f06_tls_cancel/full.log，摘要只读核对一致 |
| PostgreSQL/真实Worker | 130 passed、1 warning；580.50秒 | var/verification/f06_tls_cancel/postgres.log，摘要只读核对一致 |
| 其余 | Ruff、Mypy232文件、依赖、差异、8项摘要及wheel通过；记录25进程退出 | 用户独立反馈；Mypy232及Ruff成功亦见full.log |

以上测试和25个进程回收均为用户独立执行证据；本轮实施者只核对日志摘要及三份代码/测试摘要，没有重跑这些测试或自行进行全机进程检查。本机同名full.log/postgres.log现为最新独立门禁日志，不能再把它们当作第5节1298/130旧运行的原日志；历史数字与命令保留，未改写为最新结果。独立命令全文未提供，不伪造；既有复现命令在第5/8节。

本轮仅更新6份状态/契约/报告文档。此前SMTP代码和两份测试文件保持已验收字节，未提交、推送、部署、重启服务或连接真实邮箱/ehall。既有警告及DNS/负载限制仍保留；通过范围不延伸至真实发信或完整F08/F09。

唯一后续验收项F08.6仍BLOCKED：手机实际v15、真机丢回执/同键重试、正式审批及敏感缓存、独立Cloudflare中断、真实Push订阅/接收/撤销/锁屏/点击均待补齐。凭据后端已DONE不等于实际VAPID配置或部署接线已验收；不启动其他F09，原临时DSN例外仍须后续处理。

## 11. 验收记录更新后的文件摘要

以下8项为本轮记录独立验收后文件的SHA-256；代码/测试3项仍与独立验收一致，5项文档已更新。本表不是又一次独立审计。本报告不做自引用摘要。

| 文件 | SHA-256 |
| --- | --- |
| src/personal_assistant/infrastructure/mail/smtp_client.py | `a0988ea29f0c84c8ac806e4beb739db1ccbe6d331d8ce62eba22f1f80622a2a8` |
| tests/integration/test_mail_protocol_f06.py | `8df10ff679e777b9be2f6e7576632d0fb4ffc2e8b1c10d410074c3fed3818bf5` |
| tests/support/mail_servers.py | `394fdffd719fd00b94952b688c57afbee8a02a0071b6d73f9ff57e16aaa3b4c4` |
| README.md | `4fbdfaaba40ea06ba759bd184ad1d5c88bdbfa747d182593f3aa0d875354082c` |
| TODO.md | `94cdc9cd0de063ff7ad513624a893d7ced5b1c545fb82a959e302ede7a671d5a` |
| docs/NEXT_STEPS.md | `7013a66b2da8ccea716123aa537372a07fa6f0f7a02b4e33f4ead4c3f2e27715` |
| docs/CONTRACTS_AND_INTERFACES.md | `201d31a933f6c4a2fe1b13e50749db93aa178c27ed8594b5413b5d3fd867792e` |
| docs/F09_1_IMPLEMENTATION_REPORT_2026-10-08.md | `f5644c64cdee5f681c1361eaaab07ea17d1a870f179ddf495f158d59e4650562` |
