# F08.6 切换前的旧快照恢复兼容修复（2026-10-25）

## 范围与授权

用户已明确允许最小修复F09恢复模块：快照原本含runtime_records时才执行原hosts过滤，补回归及必要门禁后继续备份准备。原库迁移、新版启动另行确认。本任务是F08.6的切换前置；F08.6保持BLOCKED，F07/F08整体IN_PROGRESS，此前DELIVERY.2、F06.P2/最小F09.1按原范围保留验收结论。

## 修改

- `src/personal_assistant/infrastructure/operations/recovery.py`：仅给原过滤增加键存在性判断，不为11迁移快照增加不存在的runtime_records表项。显式空表仍保留；当前快照仍移除hosts并保留冻结、UNKNOWN、审批失效及凭据重绑规则。
- `tests/unit/test_recovery_delivery.py`：新增旧快照表集合/原对象不变、显式空列表保留、现有runtime过滤和执行控制三项。
- `tests/integration/test_recovery_remediation_delivery.py`：新增同字节11迁移临时库，经加密认证往返后11→11恢复、任务暂停及版本递增、再次恢复拒绝非空、后续独立0012升级；另核对哈希和版本不匹配继续拒绝、源库仍11迁移/原状态。

没有修改SQL迁移历史或restore_database拒绝条件。测试只接收专用*_test参数，建立随机独立源库/恢复库；建库前注册清理，跟踪连接池和已建库，finally关闭/DROP，临时容器按标签回收。

## 定向证据

```powershell
.\.venv-win\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts=--strict-markers -q --tb=short tests/unit/test_recovery_delivery.py
.\.venv-win\Scripts\python.exe -Xutf8 -B scripts/test-delivery-postgres.py --tests tests/integration/test_recovery_remediation_delivery.py --log-dir var/verification/f086-legacy-recovery-2026-10-25/red
```

实现前：单元 **1 failed、9 passed**；真实临时PostgreSQL **1 failed、2 passed（22.16s）**。数据库红例实际命中restore_database的缺表拒绝，补足此前只有纯变换反例的证据。

```powershell
.\.venv-win\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts=--strict-markers -q --tb=short tests/unit/test_recovery_delivery.py var/verification/f086-ntfy-2026-10-24/test_legacy_snapshot_schema.py
.\.venv-win\Scripts\python.exe -Xutf8 -B scripts/test-delivery-postgres.py --tests tests/integration/test_recovery_remediation_delivery.py --log-dir var/verification/f086-legacy-recovery-2026-10-25/green
```

实现后：单元及原合成反例 **11 passed（0.76s）**；相同真实临时PostgreSQL文件 **3 passed（22.46s）**。改动三文件Ruff通过。脚本日志在上述Git忽略目录red/green；这些均为本轮执行，未以原库进行恢复实验。

## 完整门禁与当前停点

本轮 `scripts/test.ps1` exit0：**1408 passed、137 skipped、7 warnings（975.34s，16:15）**，随后Ruff全目标通过、Mypy263源文件通过；总门禁982.33s。日志 `var/verification/f086-legacy-recovery-2026-10-25/full.log`，退出状态和PID记录full-run.json。警告保留：依赖/测试弃用及合成HMAC向量警告；另有pytest缓存路径写入被拒绝的PytestCacheWarning，没有跳过测试或改断言来清除它。

随后串行运行 `python -Xutf8 -B scripts/test-delivery-postgres.py --all --log-dir var/verification/f086-legacy-recovery-2026-10-25/postgres`，完整临时PostgreSQL/真实Worker **137 passed（646.46s，10:46）**，exit0；含原执行中审批恢复及新增两项旧快照回归。运行器总654.0s。日志与退出状态分别为postgres/postgres-full.log、postgres-runner.log、postgres-run.json。运行环境清除了继承PA_*生产配置，独立测试容器不挂原数据卷。

原库未写/迁移，原助手未启动，通知独立进程保留。未提交、推送或部署；未调用真实模型、邮箱或ehall。候选profile仍未激活，数据库/备份密钥句柄尚待配置。门禁通过后继续加密备份及隔离恢复准备，F08.6真实任务点击、正式审批等既有证据缺口继续保留。

## 收尾核对与下一步

本轮登记的两个门禁wrapper/gate共4个PID查询均已退出，Docker pa.delivery-test标签容器查询成功且0，差异检查通过。原库只读复查仍0001–0011哈希一致、0012待执行，runtime两表不存在；8000/8001/8010均未启动。原SQL迁移历史没有改动，通知独立进程保留。

最小兼容修复已通过本轮定向及两套完整本地门禁，材料可供独立复验；不将实现者门禁称为用户独立验收。下一步是本人本机生成并离线保管备份恢复密钥，然后准备原库加密恢复点和空隔离目标的恢复核验；尚未实际备份/恢复原库，也未迁移或启动新版。F08.6整体仍BLOCKED，未提交、推送或部署。

### 三份改动材料SHA-256

| 文件 | SHA-256 |
|---|---|
| `src/personal_assistant/infrastructure/operations/recovery.py` | `e8dfe2fb5d7fb94fe85d06e74edc83f3b5c5edf3db24d6aa418259bbe9808a7f` |
| `tests/unit/test_recovery_delivery.py` | `c8b3d48e16cbb9dbbf25611f6304289adff6acbee97febe9ed8bd70d4fa2b1d9` |
| `tests/integration/test_recovery_remediation_delivery.py` | `635f6e7a4aa02f309a561c1f6049e216fb0816de67dc93967b392ae2681b6e79` |
