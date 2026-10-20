# F08.6 ntfy 主机最小接线报告（2026-10-10）

## 1. 状态与授权

用户明确永久不使用 Google Play/GMS，并授权在 F08.6 增加 ntfy 无 Google 通知通道：先完成主机最小接线和本地测试，停在手机安装或外网入口配置之前。本轮不是 F08.6 整体验收；F08.6 保持 `BLOCKED`，F07/F08 整体保持 `IN_PROGRESS`，不启动其他 F09 项。开始时 main/HEAD 为 `9891102`；此前已独立通过的 F06.P2 源码、测试及报告仍未提交，本轮没有修改这三个邮件文件。

## 2. 已实现的最小范围

- `LocalNtfyNotifier` 实现现有 `PendingNotifier.notify_pending(task_id, risk)`；只接受固定的 `http://127.0.0.1:<端口>/<topic>` 或 IPv6 `::1` URL，不接受 DNS 名、远程地址、用户信息、查询、片段、嵌套路径或非法 topic。不是新业务扩展，不添加核心业务路由或数据库迁移。
- 消息仅为固定 JSON `kind=pending`、风险与非敏感任务 ID；标题固定 `Personal Assistant`，点击仅为当前 `PA_PUBLIC_ORIGIN` 的 `/ui/?task=<ID>`。不附 actions、邮件/表单正文、附件、目标或审批 nonce；通知与点击不能批准、发信或提交。
- 凭据按 `SecretHandle(kind="ntfy_publish_token")`、purpose=`notification_publish` 从现有秘密后端解析；配置只有句柄，没有明文 token。缺失、撤销或会导致 HTTP 头注入的凭据，返回需重新配置且不发送。
- 单次 POST，禁用环境代理、重定向、自动重试；总 HTTP 时限5秒、回执读取上限8192字节。只有200且 JSON 的 id/time/event/topic/message/title/click 完整对应本次通知才能计为 `accepted`；重复 JSON 字段、坏回执、重定向、拒绝、超时或丢响应均不确认。accepted 仅指通知服务接受，不指手机接收。
- PostgreSQL 与内存组合根都在显式配置 ntfy 时选择该通知器；没有配置时保留既有 Web Push 接线。两者同时配置时只通知 ntfy，没有自动 Web Push 兜底或双发；ntfy 关闭资源由 `Container.aclose()` 负责。
- 既有 Web Push 公共 API/PWA/Service Worker、审批与 Outbox 契约保持原行为；本轮没有给手机呈现“ntfy 已连接”或“已投递”的新状态，也没有把 Chrome 注册失败当成成功。

## 3. 配置与准备边界

默认两个字段均为空，ntfy 不启用。以下是说明用候选值，**没有写入现有运行配置**：

```dotenv
PA_NTFY_PUBLISH_URL=http://127.0.0.1:8090/assistant_<非秘密唯一topic>
PA_NTFY_SECRET_HANDLE=<ntfy_publish_token 的宿主句柄ID>
PA_PUBLIC_ORIGIN=https://yanshuheng.dpdns.org
```

真实服务准备时应默认拒绝匿名访问、让宿主仅能写固定 topic、让手机只读同一 topic；接收端设备凭据与宿主发布凭据分开。远程接收入口需独立受 Cloudflare Access 保护，其设备凭据不能访问原任务/审批/Admin。现有单邮箱策略与 health-only Tailscale 不作为通知客户端的放行捷径。通知服务缓存由宿主请求 `Cache: no` 关闭，因此本轮不提供离线通知补发保证；手机回来后仍从助手读取当前任务。

[ntfy 官方手机说明](https://docs.ntfy.sh/subscribe/phone/#instant-delivery)说明无 Firebase flavor 通过常驻连接接收；[发布协议](https://docs.ntfy.sh/publish/#click-action)定义 Click，[服务访问控制](https://docs.ntfy.sh/config/#access-control)支持固定 topic 的只读/只写。文档支持不等于这台华为手机实际锁屏投递通过。

## 4. 先失败再实现及本轮实际命令

工作目录均为 `C:/Users/kunji/Desktop/homework/生成式软件工程/lab1`。

```powershell
.\.venv-win\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts=--strict-markers -q --tb=short tests/unit/test_ntfy_notifications_f08.py
```

实现前 **9 failed**（配置字段与通知器尚不存在，19.26s）；最小实现后同命令 **9 passed**（6.37s）。随后新增重复 JSON 回执反例：

```powershell
.\.venv-win\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts=--strict-markers -q --tb=short tests/unit/test_ntfy_notifications_f08.py::NtfyNotificationTests::test_duplicate_receipt_fields_cannot_confirm
```

修复前 **1 failed**（旧解析将重复字段回执算 accepted，5.72s）；修复后包含于下面的定向集合。

```powershell
.\.venv-win\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts=--strict-markers -q --tb=short tests/unit/test_ntfy_notifications_f08.py tests/integration/test_ntfy_local_f08.py tests/unit/test_web_push_service_f08.py tests/unit/test_web_push_transport_f08.py tests/unit/test_web_push_gateway_f08.py tests/api/test_pwa_push_f08.py tests/integration/test_pwa_push_worker_f08.py tests/unit/test_settings.py
```

**57 passed、2 warnings（24.05s）**。新单元测试11项、实际本机 TCP 协议夹具2项；剩余为原 Web Push/配置相邻回归。协议夹具模拟 ntfy，明确不是实际 ntfy 服务或手机投递。

```powershell
.\.venv-win\Scripts\python.exe -m ruff check src/personal_assistant/infrastructure/notifications src/personal_assistant/settings.py src/personal_assistant/bootstrap.py tests/unit/test_ntfy_notifications_f08.py tests/integration/test_ntfy_local_f08.py
.\.venv-win\Scripts\python.exe -m mypy src/personal_assistant/infrastructure/notifications src/personal_assistant/settings.py src/personal_assistant/bootstrap.py
```

最终目标 Ruff exit0；Mypy exit0，4 source files。初次新测试的格式/闭包绑定问题已修正，未减少安全断言。

## 5. 全量与收尾门禁

配置与组合根跨模块，因此依 AGENTS.md 运行全量门禁。以下是本轮实际结果，不能称全量全绿：

```powershell
$env:PATH='D:\nodejs;'+$env:PATH
& ./scripts/test.ps1
```

**2 failed、1312 passed、130 skipped、8 warnings，1808.91s（30:08），exit1**。完整输出已保存到 [full.log](../var/verification/f086_ntfy_local/full.log)。两项失败为：

- `tests/integration/test_browser_companion_real_f07.py::BrowserCompanionRealTests::test_operator_can_capture_a_user_opened_popup_without_writes`：测试 `asyncSetUp` 的 Companion 未在宿主时限内输出握手。
- `tests/integration/test_mail_protocol_f06.py::MailProtocolTests::test_socket_timeout_during_tls_handshake_closes_and_reaps`：准备断言 `sender.running_threads == 1` 时实际为0。

这两个文件均非本轮接线改动；本轮不修改 F06/F07 实现或测试，不删除/放宽断言。隔离复跑结果另列，不覆盖上述完整失败记录。`scripts/test.ps1` 在 pytest exit1 后没有继续 Ruff/Mypy，因此另行实际运行：

```powershell
.\.venv-win\Scripts\python.exe -m ruff check src extension_sdk extensions tests
.\.venv-win\Scripts\python.exe -m mypy src\personal_assistant extension_sdk\src\personal_assistant_sdk extensions\example_echo\src\example_echo extensions\personal_knowledge\src\personal_knowledge extensions\nju_smail\src\nju_smail extensions\nju_ehall\src\nju_ehall
```

全项目 Ruff exit0；Mypy exit0，234 source files。

```powershell
.\.venv-win\Scripts\python.exe -m pip check
& 'D:\nodejs\node.exe' --check web/pwa/app.js
& 'D:\nodejs\node.exe' --check web/pwa/service-worker.js
git diff --check
```

依赖无冲突，两份未改动 PWA 脚本语法通过，当前差异检查通过。Git 对 TODO/NEXT_STEPS 提示工作区 LF 将按既有配置转换为 CRLF，不是差异错误。新增未跟踪源文件与测试的尾部空白也已单独核对通过。

### 两项失败隔离复跑及 PostgreSQL/Worker

```powershell
$env:PATH='D:\nodejs;'+$env:PATH
.\.venv-win\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts=--strict-markers -q --tb=short tests/integration/test_browser_companion_real_f07.py::BrowserCompanionRealTests::test_operator_can_capture_a_user_opened_popup_without_writes tests/integration/test_mail_protocol_f06.py::MailProtocolTests::test_socket_timeout_during_tls_handshake_closes_and_reaps
```

**1 failed、1 passed，29.77s，exit1**；[隔离日志](../var/verification/f086_ntfy_local/full_failures_isolated.log)。F07 单例通过；F06 同一 `running_threads` 断言仍失败。不得把隔离结果写成全量全绿，也不能据此声称 F06 当前线程状态测试的原因已根治。本轮没有修改这些失败用例；F06/F09.1 既有独立验收记录保留，新增失败现象单列待定位。

本轮**未继续运行完整 PostgreSQL/真实 Worker 脚本**：完整 pytest 门禁已有上述尚未关闭失败，保持在当前接线范围收尾，不扩大验证或邮件层修复。本轮没有数据库迁移、ntfy 数据库表或原库写入；新单元测试只确认 PostgreSQL 组合根可构造并选择 ntfy，不证明真实数据库中的通知流程。旧轮130 passed等数字不是本轮执行结果。真实 ntfy 服务、生产投递与手机仍未验证。

### 收尾资源与原运行实例

本轮 pytest、全量和隔离复跑会话均已结束；收尾按启动时间核对，未发现14:31:40之后仍在运行的 python/node/chrome测试进程。新本机 HTTP 夹具的线程退出与客户端关闭有测试断言。本轮未重启原助手：原 public 16928/health 14376 仍仅监听127.0.0.1的8000/8010，8001无监听；原 public无JWT的Push配置401、health /healthz 200、Tunnel本机 /ready 200。该检查仅确认原实例仍运行，不证明它已加载本轮新代码。

## 6. 未完成与下一步

尚未下载/安装/启动真实 ntfy、生成真实发布/接收凭据、修改既有 `.env.template`、重启原服务、增加 DNS/Access/Tunnel 入口或安装手机客户端。没有真实手机订阅、锁屏、点击、撤销或网络切换的 ntfy 证据；实际 ntfy 回执兼容性仍须在真实服务配置后复验。

F08.6 既有缺口继续保留：手机实际使用 v15、真机草稿丢回执与同键重试、正式单次审批及相关缓存、独立 Cloudflare 中断和通知完整流程。其既有真实手机任务/消息/草稿与冲突证据、F08.1–F08.5 的定向独立验收结论不变。本轮完成不等于整个阶段完成。

下一步仍为 F08.6：审核本报告后，准备受限的真实通知服务与独立 Access 接收入口，再由用户安装无 Firebase 客户端完成真机验证；按本次要求先暂停在安装手机应用/配置外网入口之前。不提交、推送或部署。


## 7. 本轮改动文件

| 文件 | 本轮作用 |
|---|---|
| src/personal_assistant/infrastructure/notifications/__init__.py | 宿主通知适配器包 |
| src/personal_assistant/infrastructure/notifications/ntfy.py | 本机 ntfy 发布、最小内容、回执核对与资源释放 |
| src/personal_assistant/settings.py | 默认关闭的 URL/秘密句柄配置与校验 |
| src/personal_assistant/bootstrap.py | 内存/PostgreSQL 组合根选择通知器及关闭资源 |
| tests/unit/test_ntfy_notifications_f08.py | 配置、注入、回执、撤销、单次通知、审批先持久化和组合根反例 |
| tests/integration/test_ntfy_local_f08.py | 本机 TCP 协议夹具、环境代理绕开、已接收后丢回执无重发 |
| README.md、DESIGN_AND_DEVELOPMENT.md | 无 Google 范围例外、当前可用范围 |
| docs/CONTRACTS_AND_INTERFACES.md | ntfy 最小宿主契约及真实投递边界 |
| TODO.md、docs/NEXT_STEPS.md | 授权、BLOCKED 状态与当前证据 |
| docs/F08_6_PUSH_PREPARATION_2026-10-09.md | 将此前候选记录衔接到本轮授权与实现 |
| docs/F08_6_NTFY_LOCAL_REPORT_2026-10-10.md | 当前交接报告 |

工作区原有 F06.P2/F09.1 修改不属于本轮改动；本轮没有提交、推送、生产迁移或部署。


## 8. 当前接线源码与测试摘要（SHA-256）

| 文件 | SHA-256 |
|---|---|
| src/personal_assistant/infrastructure/notifications/__init__.py | d0394cb1a480f3f621acc388c386ecde1e809f05d41b2da6f2a0d41b858cc298 |
| src/personal_assistant/infrastructure/notifications/ntfy.py | 65fc4a6b29dc2622fdd5f961fb0ed1e344aaee7d5cccca478457c56b6521d799 |
| src/personal_assistant/settings.py | 4ada1bb1c56596498a97b5561000a1de3b9d296f65e8c707db7798b814967872 |
| src/personal_assistant/bootstrap.py | e19b68b44eb04722b498707d18d0ab32bdb9cd15366fe2e03642eb2dbf5b8258 |
| tests/unit/test_ntfy_notifications_f08.py | cc6321ebc345fd41c456705759b655e128c46476b1ba7e4f4e1796bbd02a55e9 |
| tests/integration/test_ntfy_local_f08.py | 657bf5655fe2a72e07e17502bf7f44c09172100ff9d09546f051408e600c3463 |

已只读核对原 F06 的 smtp_client.py、test_mail_protocol_f06.py 和 mail_servers.py，三个摘要均与此前验收记录一致；此项不是本轮重新验收 F06。
