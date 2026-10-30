# F08.6 测试隔离修复与阶段门禁（2026-10-30）

## 当前结论

本轮修正测试读取宿主运行目录的 P3。真机补证已获用户独立认可，不增加手机动作。**F08.6 保持 BLOCKED，等待最终独立复验；F07/F08 整体 IN_PROGRESS。** 本报告属于实施交接，不替代独立签收；子agent仅只读核对差异和报告数字，未另跑独立门禁。

## 修改范围与原因

只修改四份测试的目录夹具：

- `tests/unit/test_ntfy_notifications_f08.py`：三个容器统一使用临时 Settings。
- `tests/api/test_delivery_api.py`：三个容器构造隔离临时目录。
- `tests/api/test_delivery_remediation.py`：隔离工作流回归的 setUp。
- `tests/unit/test_runtime_delivery.py`：隔离被多个 Runtime 测试类复用的 asyncSetUp。

每个夹具通过 `enterContext(TemporaryDirectory())` 创建独立根目录，再设置 `extension_root=<临时根>/extensions`、`artifact_root=<临时根>/artifacts`。宿主冻结探针自然读取 `<临时根>/control`；没有替换、mock 或删除生产冻结判断。已登记的容器/客户端清理先于临时目录回收。

四文件的原断言调用 AST 分别为 44、22、40、34 条，修改前后完全相同。生产源码、API、审批、Outbox、迁移和通知逻辑本轮未修改；此前未提交的旧快照恢复修复原样保留。

## 失败复现与定向回归

证据目录：`var/verification/f086-test-isolation-2026-10-30/`。

- `red.log`：原 ntfy Gateway 用例 **1 failed**，期望 APPROVAL_REQUIRED，实际 FAILED；Gateway 正确拒绝被宿主冻结的 R2。
- `green.log`：仅改目录后，ntfy 文件 **11 passed**。
- `full-initial.log` / `full-initial-run.json`：首轮完整运行早期观察到 8 项同类失败后主动中断。没有完整结果，不计作一次完成的全量。
- `adjacent-red.log`：Embedded Agent、工作流、Runtime 三个最小用例 **3 failed**；默认目录下分别不能领取队列或进入 PAUSED_SAFETY。
- `directed.log`：统一隔离四文件后 **37 passed、2 warnings**。
- `assertions.json`：原断言及修改后文件摘要。

可复现定向命令：

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest -o cache_dir=var/verification/f086-test-isolation-2026-10-30/pytest-cache -o addopts=--strict-markers -q tests/unit/test_ntfy_notifications_f08.py tests/api/test_delivery_api.py tests/api/test_delivery_remediation.py tests/unit/test_runtime_delivery.py
```

## 完整门禁

本地与 PostgreSQL/真实 Worker 串行运行。私有 `run-gates.py` 清除继承的 `PA_*`、`PG*`、`DATABASE_*`、`POSTGRES_*`，设置独立 pytest cache，记录 PID、退出码、耗时及原冻结文件摘要。

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B var/verification/f086-test-isolation-2026-10-30/run-gates.py full
.\.venv-win\Scripts\python.exe -Xutf8 -B var/verification/f086-test-isolation-2026-10-30/run-gates.py postgres
```

full 调用标准 `scripts/test.ps1`（全部 pytest、Ruff、源代码 Mypy）；postgres 调用 `scripts/test-delivery-postgres.py --all`，从正式脚本枚举全部 13 个 PostgreSQL/Worker 文件，创建唯一标签、随机回环端口、无原卷的临时 pgvector 容器，finally 只删除本次容器。

| 门禁 | 本轮实际结果 |
|---|---|
| 完整本地 `scripts/test.ps1` | **1408 passed、137 skipped、6 warnings**；Ruff通过、Mypy **263源文件**；exit 0 |
| 完整临时 PostgreSQL/真实 Worker | **137 passed**；exit 0 |
| 三份 JavaScript | `node --check web/pwa/app.js`、`web/pwa/service-worker.js`、`web/admin/app.js` 全通过 |
| 依赖与差异 | `python -m pip check`、`git diff --check` 通过；状态文档更新后相关契约 **38 passed** |
| 离线框架 wheel | 265条目；258项源码/静态资源/迁移逐字节匹配；7项PWA资源、12份迁移齐全，无扩展源码 |

完整本地 pytest 用时1169.10秒，连同静态检查1173.53秒；PostgreSQL pytest 657.51秒，包装器665.77秒。日志为 `full.log`、`postgres/postgres-full.log`；退出码与环境/冻结记录为 `full-run.json`、`postgres-run.json`。所有用例按原脚本运行，未增加跳过或删减断言。

wheel SHA-256：`5906b5c812723f0d523d59135c3726daf478d3eb098f23be9a2dee29246c4d83`。构建命令：

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pip wheel . --no-deps --no-build-isolation --no-index --wheel-dir var/verification/f086-test-isolation-2026-10-30/wheel
```

额外直接对 ntfy 单元测试文件运行 Mypy 得到 11 条诊断，与未修改 HEAD 完全相同（`mypy-optional-comparison.json`）。此检查不属于标准源代码 Mypy 门禁，本报告不宣称该测试文件 Mypy 通过，也未为修目录扩大到历史类型清理。

## 材料与收尾

开始修改状态文档前，上一轮 36 项真机材料全部匹配（`previous-phone-manifest-check.json`）。本轮不重复手机、真实 Push 或 Cloudflare 中断操作。产品源码/扩展/迁移/静态材料340项收尾摘要全部未变。原冻结文件在两轮门禁前后始终为 `ef2293f9fbdeba20ad32347a8987cc19b01dc326834aa6a7b17dd549bf4be2bc`。本轮测试容器及登记运行器均为0，归属已核验的1个临时匿名卷单独回收、剩余0；查询均成功。见 `cleanup-and-material.json` 与 `test-process-cleanup.json`。

Docker 起初未运行，本轮仅隐藏启动已安装的 Docker Desktop 以提供隔离测试 Engine；未重置、重建原数据库或调整其凭据。没有操作原库、解冻/重启助手、调用真实模型、发送邮件/通知或提交表单，未提交或推送。

当前材料清单：`var/verification/f086-test-isolation-2026-10-30/manifest.json`；对当前文件重新计算摘要，旧36项清单保留不改。TODO/NEXT本轮状态文档更新与已获认可的手机证据分别记录。

下一编号仍为 F08.6：仅独立复验本轮测试隔离及最终门禁后决定是否签DONE，不新增手机动作，不推进其他编号。

## 独立复验与提交前补记（2026-10-30）

上述数字属于实施者门禁。本节另记随后完成的独立检查，不用单例复跑或实施结果替代独立完整运行：

- 四文件定向回归 **37 passed、2 warnings**；相对HEAD的140条原断言及测试名均不变。
- 79项交接摘要在本次文档补记前全部匹配，340项产品材料不变；wheel的258项源码/静态/迁移逐字节匹配，另两项配置也匹配。临时目录中的真实Gateway冻结前/冻结中/恢复后分别为APPROVAL_REQUIRED/FAILED/APPROVAL_REQUIRED，未关闭生产冻结探针。
- 独立标准 `scripts/test.ps1`：**1407 passed、137 skipped、1 failed、6 warnings**，pytest耗时1806.73秒，运行器exit 1。失败为 `tests/integration/test_browser_companion_real_f07.py::BrowserCompanionRealTests::test_open_transaction_rejects_a_mismatched_landing_path`，在 `reach_authenticated -> create_session -> POST /v1/sessions` 阶段抛出 `BrowserUnavailableError: the desktop companion timed out`，尚未进入路径不匹配断言。脚本因此未继续执行本次Ruff/Mypy；前文实施者静态门禁证据不变。
- 独立日志：`var/verification/f086-independent-audit-2026-10-30-sfyso7ng/full.log`、`full-run.json`。原冻结文件前后摘要一致；测试运行器随后退出。未启动独立PostgreSQL/Worker，不宣称该门禁独立通过。
- 按本人要求仅重跑失败用例：**1 passed / 20.95秒，exit 0**。未改代码、断言或超时；该结果只证明此次单例跑通，不能把原全量改写为全绿，也不能证明超时根因已消除。

单例复现命令（清除继承的业务/数据库环境设置后，使用既有虚拟环境）：

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest -p no:cacheprovider -p no:faulthandler -o addopts=--strict-markers -q --tb=short tests/integration/test_browser_companion_real_f07.py::BrowserCompanionRealTests::test_open_transaction_rejects_a_mismatched_landing_path
```

F08.6仍为 **BLOCKED**，已获认可的手机补证结论不变，不增加手机动作。下一步仅定位F07会话创建超时及补齐独立门禁，不推进其他编号。此次文档补记会改变本报告及TODO/NEXT的摘要；原79项清单作为实施时快照保留，不覆盖历史摘要或日志。本次仅准备本地提交，不推送、不部署，不操作原库或解冻/重启助手。
