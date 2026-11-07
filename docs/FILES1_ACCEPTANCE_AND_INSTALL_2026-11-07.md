# FILES.1 验收与安装记录

日期：2026-11-07。范围：`personal.files@0.1.0`。本地验收和原实例安装均通过，FILES.1 标为 DONE。本人授权安装，并明确同意复用整个知识库目录的读写权限，允许按需新建子目录或编辑文件；没有扩大到删除目录或自动恢复旧任务。

## 验收结论与修复

主实现者进行了自检，并交由独立子审查代理构造反例；不将同一实现者的自检称为完全独立验收。修复前独立反例为 **2 failed、1 passed**，确认一个P1：Windows 8.3父目录别名可绕过不存在叶子的规范路径检查，造成同一物理目标有不同未决门闩，也可绕过禁入目录名。首次写入已发生但回执UNKNOWN，换成长路径却可再次写入。该失败日志保留。

修复逐级检查现有路径组件，拒绝非规范短别名，并在解析授权根后复查禁入组件；保留真正故障和UNKNOWN保护。按本人的目录写权限要求补充单层mkdir：仍用六个原工具中的files.preview/write，父目录必须已存在、不覆盖、不递归补父目录。mkdir不传正文且旧hash必须null；成功历史回执不会重建后来改变的目录。删除仍只开放本机人工确认的单个文件操作。

修改限独立插件、对应测试和文档；没有给宿主核心加入文件业务分支，没有新增迁移或第三方运行依赖。

## 本轮实际验证

在 `C:/Users/kunji/Desktop/homework/生成式软件工程/lab1` 执行；验证材料目录为 `var/verification/files1-accept-2026-11-07/`。

| 范围 | 本轮结果 | 日志 |
|---|---|---|
| 整个文件扩展专项 | 72 passed，18.37秒，无skip | plugin-final.log |
| 原独立反例原样复跑 | 3 passed，0.53秒 | independent/after-path-fix.log |
| 新增目录独立组合 | 4 passed，1.21秒 | independent/mkdir-independent.log |
| 一次关键回归 | 643 passed、6 warnings，150.02秒 | core.log |
| Ruff、宿主Mypy | 通过，290源文件 | core.log |
| 插件严格Mypy | 通过，7源文件 | plugin-mypy.log |
| pip check、git diff --check | 通过；Git保留既有换行提示 | 工具执行记录 |

可复现命令：

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest -c extensions/personal_files/pyproject.toml extensions/personal_files/tests -q -o addopts='' -p no:cacheprovider --tb=short
$SavedFilesPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = 'extensions/personal_files/src;extension_sdk/src'
    .\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest var/verification/files1-accept-2026-11-07/independent/test_independent_files1.py var/verification/files1-accept-2026-11-07/independent/test_independent_mkdir.py -q -o addopts='' -p no:cacheprovider --tb=short
} finally {
    $env:PYTHONPATH = $SavedFilesPythonPath
}
.\scripts\test.ps1 -Suite core
.\.venv-win\Scripts\python.exe -Xutf8 -B -m mypy --cache-dir var/verification/files1-accept-2026-11-07/mypy-cache --follow-imports=silent --strict extensions/personal_files/src/personal_files
```

独立测试通过真实Windows短别名、SQLite部分回执失败、重启、大小写/root_key变化、旧计划阻断、目录移除或换为文件后的原回执、已有目录冲突和缺失父目录。真实宿主Manifest→Registry→Gateway→Worker接线在72项中覆盖，不将各次重叠测试相加。

遵照本人避免完整套件过度验证的要求，本轮运行受影响专项及一次关键回归，没有重复完整本地/完整PostgreSQL-Worker套件。插件使用自己的SQLite，不更改宿主审批、Outbox或持久模型；643项不代表全系统、真实模型或业务投递已验收。6项为既有弃用/合成弱密钥拒绝测试警告。

## 冻结的安装材料

- 扩展：personal.files 0.1.0；源码29个文件，不含生成的测试缓存。
- 材料SHA-256：`sha256:f32fdf9804bac410a3dc4f2bae7c87d6865eedd34755b7d9a1d4311353717829`。
- Schema SHA-256：`sha256:fed6b62b75af97a7241e8d828b42c35afc76523dfd971338b7c967beb5024d3a`。
- 6工具、2上下文槽；必需/可选宿主能力均空，依赖标准库与公开SDK。空能力列表不表示不能访问本机文件；范围由本人配置授权。
- 清单：candidate-final.json、candidate-files.json。安装须正式inspect核对同一摘要后确认，不能将旧实现报告中的材料摘要用于本次安装。

## 原实例边界

安装前实际检查：原助手停止，manual冻结，8000/8001/8010没有监听。既有知识库配置经正式get_applied读取为一个legacy兼容根，目录存在。本人指定该根复用为read_write，不新建另一份默认知识库，不给旧任务静默升级工具快照。

安装采用最小正式扩展生命周期链路，不运行宿主startup、迁移、全局恢复、Agent任务或模型请求。只连接既有数据库，正式inspect/confirm/install/enable，通过配置校验和apply记录持久目录授权；验证进程退出时停止它自己的Worker，原实例保持停止和冻结。结果与前后不变项在下段记录。

## 尚不宣称完成的范围

- UTF-8文本最多256KiB；不编辑Office/PDF/图片，不提供任意shell或目录删除。
- 文件变更不等于知识库索引已更新；索引仍按既有知识库流程维护。
- 文件系统最终hash检查至replace仍有非协作编辑器的极小竞态，不宣称操作系统原子CAS或完整保留ACL/扩展属性。
- UNKNOWN不得换键重做，首版无自动对账或强制解锁入口。
- 资料根及文件计划SQLite的工作区备份仍是下一项；现有宿主数据库备份不自动覆盖它们。
- CORE.RECONCILE.1正式部署、F08.6及F07/F08整体验收状态不因此改变。未提交或推送。

## 原实例安装结果（已完成）

只读preflight通过：原15个任务、1个知识库根、目标版本尚未安装。执行一次受控安装，exit0：

- 正式安装作业 `extop_46b1152a5be44637a9cf19c624b83d3a`：SUCCEEDED。
- 正式启用作业 `extop_923c5e9b910a4c44973d92caed362d52`：SUCCEEDED；原库记录 `personal.files 0.1.0 / ENABLED`。
- 使用宿主标准staging与不含pip的专用venv安装；无第三方依赖下载。正式ContractVerifier核对真实stdio工具；只有本次files Worker启用。
- 配置顺序为保存→启用→生命周期锁内校验版本与材料→应用→同步。真实Worker健康、6工具、单runtime的摘要及代际均通过；验证期间config_state=APPLIED。
- 原知识库根 `personal` 配置为 `read_write`，同目录供全实例共享；控制SQLite目录为 `var/extension-state/personal.files`，与知识库隔离。
- 配置摘要 `sha256:9b448b7149f39b02f3555044fb9ac373791f8ac01c088760939515956771a028`，代际 `494ccbfe65574d81bdca08e52e1e552a`。结束时停止验证Worker，保留ENABLED和已应用配置；宿主当前没有运行组件，因此不将停机时的界面状态称为在线APPLIED。
- 安装payload重新计算的摘要与冻结材料完全相同。15个任务id/status/version不变；知识库40来源、40版本、747分块、4墓碑、48事件、1元数据行的计数不变，知识库配置摘要不变；其他扩展id/version/state/hash以及control全部文件摘要不变。
- 原助手保持停止和manual冻结；无迁移、旧任务领取或恢复、模型调用、外部动作、知识文件写入。仅运行files.roots读取根元数据，没有读取个人文件正文。临时Worker已回收。

可复查材料：`install-before.json`、`install-after.json`、`install-result.json`、`post-install-verification.json`。安装helper是本轮单次材料，已存在before日志时拒绝重跑；不能将它作为无条件重装命令。其源码和独立子审均确认只连接既有数据库、使用正式生命周期端口，不启动全宿主。

原实例下次正常启动会恢复此ENABLED扩展；继续保留手动冻结策略，执行Agent任务前需按本人的正常操作解除冻结。使用新对话/新任务，例如“在personal知识库中创建summaries子目录，把这次对话总结保存为summaries/test.md，已有文件先核对差异”。旧任务工具快照不被修改，可用既有携带目标新建任务入口。

下一编号建议 `FILES.BACKUP.1`：覆盖授权资料根和本扩展计划SQLite的工作区备份及隔离恢复。当前仅登记排期，尚未实施。CORE.RECONCILE.1仍WAITING正式验收/部署；F08.6 BLOCKED、F07/F08 IN_PROGRESS不变。
