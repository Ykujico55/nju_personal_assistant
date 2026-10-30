# F08.6 原库加密备份的隔离恢复计划与执行结果

当前状态：**本人已批准，隔离恢复与收尾核对通过；原库未迁移，新版未启动。F08.6 BLOCKED。**

下方“请求批准的唯一操作”保留执行前的精确范围，实际结果见最后一节。

## 已核验材料

- 恢复点：`20261025T053430_2b42ab498d23450fbfbf55c95cce234b.pa-backup`；认证解密通过。
- 加密文件 SHA-256：`ab0e17bca451f808e8752e0dcf1187dd04457750e0ea89263774201cac437297`。
- 34张表、0个制品；11份核心迁移与原库和静态副本一致。
- 已安装邮件扩展：`nju.smail@0.1.0`，namespace `ext_nju_2e_smail`。
- 工件摘要：`sha256:b05775055c0daa7932ec924286d0d1f7f95055f3c58a02e9c990c7a80c58353a`，与认证备份中的安装记录一致。
- 安装包原 SQL SHA-256：`6320af7791bd2805e36c70f7fa731417935e7bfd6ad875b95a6d42d179469d8c`，与认证备份的扩展迁移账本一致。
- SQL只创建12张邮件表、5个索引；另外用宿主固定定义创建空迁移账本。未执行扩展Python、Worker或正常会写账本的迁移入口。
- 机器可读计划 SHA-256：`883894771a00cd456096343d2d5baff587db579bf2f562cba54cde233571bdd0`，存于本机Git忽略目录 `var/deployment/f086-ntfy-2026-10-24/restore-approval/plan.json`。SQL副本同目录，逐字节不变。

## 请求批准的唯一操作

在全新临时容器 `pa-f086-restore-20261025` 中使用本机已存在的同一PostgreSQL/pgvector镜像，建立 `assistant_f086_20261025_restore`。端口只绑定回环随机端口，不使用原卷；制品目标为独立新目录。先运行原目标身份及空目标检查，再仅应用匹配0001–0011，执行上述静态建表材料和固定空账本，保留全部非空/迁移/列/依赖检查后调用现有恢复函数。

恢复后仅在内存比对数据及冻结状态，输出数量与布尔结果，关闭并删除本次临时目标，保留加密备份及不含正文的证据。邮件正文不输出、不另存明文文件；扩展和Agent均不启动，不连接邮箱、模型或ehall。

原数据库不迁移或改写，原助手不启动。本次批准不包含原库0012或新版切换。`AGENTS.md` 要求确认先于扩展材料执行，因此在此停下等待批准。


## 已批准执行结果（2026-10-25）

本人回复“允许”后，按机器计划摘要执行，没有修改批准的SQL、迁移或备份材料。运行命令：

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B var/deployment/f086-ntfy-2026-10-24/run-isolated-restore.py --accept-plan-sha256 883894771a00cd456096343d2d5baff587db579bf2f562cba54cde233571bdd0
```

这是一次性操作命令；已有回执会拒绝再次执行，不能作为普通只读验证命令重跑。

实际第三次尝试exit0，`state=RESTORE_VERIFIED`。独立临时目标使用已批准镜像ID，数据目录为tmpfs，随机回环端口，无原卷；连接后先执行原目标/空目标检查，再应用11份匹配迁移。邮件扩展只执行已批准静态SQL和宿主空账本，不导入扩展Python或启动Worker。

恢复前严格核对34张恢复表、五张有意不备份的合法核心表均空，只有核心schema_migrations已有匹配11条记录。`search_path`仅在建表事务内使用，之后回到public。使用原认证snapshot直接调用stage_artifacts/restore_database，另生成冻结预期供内存比较，未二次冻结导入数据。

- **34张表全部行**与冻结预期作类型敏感canonical JSON多重集比较，一致；邮件正文不输出、不另存明文。
- **13张扩展表**包含原账本及业务数据；恢复后两个扩展DISABLED。任务/作业完整匹配冻结规则，终态按原规则保留。
- **5项序列**的last_value/is_called只读检查通过，未用nextval消耗序列。
- 0个制品；临时目标容器、临时Windows凭据和新空制品目录均清理。随后单独Docker查询确认恢复标签容器为0，密文备份哈希不变。
- 再次运行原库只读预检exit0：仍11份迁移且哈希一致，0012仍待执行；8000/8001/8010均未监听。原库未写、新版未启动，未调用模型/邮件/ehall。

前两次失败没有隐去：第一次在容器配置断言阶段抛ValueError，没有DDL或恢复；当时没有记录更细错误码。第二次在数据库连接前遇到CannotConnectNowError，Unix socket就绪探针命中初始化临时服务；改为实际TCP服务探针。两次资源均清理，分别保存在isolated-restore-attempt-1.json和-2.json。最终采用显式tmpfs mount并保留全部保护，没有增加生产超时或放宽空目标断言。

本次是已批准真实备份的隔离恢复操作验证，没有新增产品代码，因此未重复全量。上一最小恢复产品修复已运行完整本地1408 passed/137 skipped/7 warnings及完整PostgreSQL/Worker137 passed；本轮额外完成私有脚本compile、Ruff F规则和差异检查，不冒充本轮全量或独立验收。

最终私有执行脚本SHA-256：`aebec05ce34a722a1b9b30efb0d886700840977997c230815fcf41fdb7d85ddc`；结果回执SHA-256：`5b329182bcab6913f252eca566544aa0940a93ddd165d57fbac7261c5f3a7a73`。详细元数据在Git忽略目录isolated-restore-result.json与isolated-restore-evidence.json。下一步准备原库0012及新版冻结启动的具体切换材料，再另行确认。
