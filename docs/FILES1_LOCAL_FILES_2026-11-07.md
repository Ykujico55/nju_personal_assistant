# FILES.1 通用本地文件扩展：本地实现报告

后续状态：已按本人授权完成路径别名修复、mkdir补充、验收及安装。最新材料与证据见 [验收安装记录](FILES1_ACCEPTANCE_AND_INSTALL_2026-11-07.md)；下文保留最初本地交付时的结果与旧材料摘要，不用于再次安装。

日期：2026-11-07。历史状态：本地实现完成，待正式验收、本人授权安装与实际目录配置。没有修改或重启原实例，没有构建/安装扩展、操作原数据库、调用模型或外部服务、提交或推送。

## 前项收尾

CORE.RECONCILE.1 的本地实现与已有证据已核对：Runtime/API/TUI及宿主证据专项37 passed，临时PostgreSQL5 passed，后续关键回归643 passed。只读核对确认原动作绑定、宿主持久证明、同键回执及旧作业阻断；任务对账后仍等待用户明确继续，不自动重发。

本轮不把这些结果冒称新一轮独立验收、完整门禁或原实例部署。CORE保留WAITING正式验收/部署；按用户明确顺序授权，FILES.1成为唯一当前开发项。F08.6 BLOCKED、F07/F08 IN_PROGRESS不变。

## 交付行为

独立扩展 `extensions/personal_files`，ID `personal.files@0.1.0`，只依赖公开SDK与标准库，无新宿主能力、核心业务分支或迁移。

- **实例共享配置**：`roots`列出授权根的key/label/path/read_only或read_write模式；由既有配置保存/应用流程同步到Worker，所有任务和对话复用。不修改旧任务工具快照。
- **读取**：`files.roots/list/read`提供根、非递归分页目录和UTF-8正文；`files.workspace`和`files.document`接入既有ContextProvider槽，以明确URI读取正文，不用索引或猜路径。
- **预览和写入**：`files.preview`持久保存精确目标、旧SHA、新正文、完整差异、配置摘要、计划哈希和期限；`files.write`仅接受原计划ID及哈希。新建要求目标不存在，修改前重新读取并核对旧SHA。缺少末尾换行在差异中明确标记。
- **回执**：私有SQLite使用事务串行协调同一状态目录下的插件进程。执行前先落盘APPLYING；完成后保存原操作回执。同键不重写文件，换内容同键拒绝。回执不代表文件当前内容。
- **不确定结果**：变更后回执未落盘或进程中断，保留UNKNOWN。同一规范目标有未决写入时，新计划和其他旧计划均受阻断；不能换键自动重做。首版无强制解除或自动判成功入口。
- **删除**：模型仅可准备delete计划；没有执行删除工具或confirmed参数。本人在独立本机TTY运行CLI，查看精确计划，输入要求的文字；再校验配置、期限及旧哈希后只删除一个文件。

写工具和持久预览为R1，其余为READ；不伪装本地删除为R2，也不改变邮件/网页审批。

## 文件与接口

- `src/personal_files/paths.py`：授权根、规范路径、链接/重解析点/硬链接拒绝、身份核对、UTF-8读取和替换。
- `src/personal_files/service.py`：SQLite计划/命令、差异、期限、冲突、目标未决门闩和历史回执。
- `src/personal_files/worker.py`、manifest及schemas：六工具、两上下文、错误码、deadline及标准stdio。
- `src/personal_files/cli.py`：独立本机删除确认；无自动确认开关。
- 五个测试文件：路径、服务、CLI、Worker、真实宿主契约。
- `scripts/test.ps1`：plugins/full增加第三独立插件pytest进程；其余宿主产品代码未在本任务修改。

使用配置和命令见 [插件README](../extensions/personal_files/README.md)。原目录与私有状态目录都必须由本人预先创建；READ首次使用不会创建目录。状态目录不可与资料根互相包含，计划可能保存完整正文，需单独保护和备份。

## 关键实现决策与修正

1. Worker接线初稿使用宿主不支持的JSON Schema关键词。真实ManifestParser → ToolRegistry冻结 → ToolGateway反例先2 failed；已去掉default/if/then/not，并避开现有组合Schema在冻结后不兼容的问题。preview条件由Worker在接触文件前严格验证；不改核心校验器。真实Gateway → CapabilityRoutingExecutor → Worker → 文件服务完整路径已通过。
2. 路径层首次读取不能隐式mkdir，反例先1 failed；改为配置目录必须已存在。
3. Windows目录项的缓存stat不能可靠给出硬链接数，改用实际lstat，不把正常文件错误隐藏。
4. 同目标UNKNOWN可由新计划绕过的反例先1 failed；增加规范目标位置绑定和持久门闩，原键仍保留，未删除或篡改回执。
5. 先新增测试再实现。最初服务/CLI/路径模块尚不存在时为收集失败；不将其描述为旧生产版本上的行为反例。版本冲突与故障注入使用合成临时文件；不读用户资料。

## 实际验证

在实际仓库根 `C:/Users/kunji/Desktop/homework/生成式软件工程/lab1` 执行。

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest -c extensions/personal_files/pyproject.toml extensions/personal_files/tests -q -o addopts='' -p no:cacheprovider --tb=short --durations=5
```

**56 passed，20.41秒**，无skip。覆盖路径逃逸与真实硬链接、UTF-8 BOM/CRLF、无覆盖新建、版本变化、实际多进程竞争、原键回放、回执前崩溃、UNKNOWN阻断新计划、删除拒绝/过期/确认期间人改、真实stdio以及宿主Gateway合同。合成故障注入不是操作系统断电试验；SQLite结果不冒充PostgreSQL或原实例证据。

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B -m mypy --follow-imports=silent --strict extensions/personal_files/src/personal_files
```

**7源文件通过**。

```powershell
.\scripts\test.ps1 -Suite core
```

**643 passed、6 warnings，137.30秒**；随后脚本全仓Ruff通过，宿主Mypy **290源文件通过**。6项为既有Starlette/httpx/AnyIO与Cookie弃用警告、合成弱HMAC拒绝反例警告，未通过忽略警告或删除断言伪造绿色。日志 `var/verification/files1-2026-11-07/core.log`。本轮不再重复完整套件。日志：`var/verification/files1-2026-11-07/plugin-final.log`、`plugin-mypy.log`；早期服务/CLI合并16项与56项重叠，不相加。

仅运行关键回归和受影响专项。没有运行完整本地或完整PostgreSQL/Worker套件，因为本任务为独立插件与测试入口，不修改共享宿主运行状态、审批或Outbox；没有做阶段发布或真实目录验收。

## 材料与未实现范围

静态Manifest解析通过；正式构建/安装仍需审查确认。候选源码摘要：`sha256:9e64b308b8902cda86afccc9e22e54340c36bfeab459c9ebff539a8fdaf1944c`。Schema摘要：`sha256:17d46e581f3df1edf2bebf09fbb3c912427ba9e87cc226346304cda9a17819df`。必需/可选宿主能力均为空，运行依赖只有公开SDK；不代表不访问本机文件，访问范围由明确配置约束。静态记录在 `var/verification/files1-2026-11-07/candidate.json`；后续若改材料需重算，正式安装前按既有inspect/confirm流程核对同一摘要。清理了新插件下的本轮Ruff/Mypy缓存后重新计算源码摘要，未将缓存作为安装材料。

- 文件上限256KiB，UTF-8文本；不提供Office/PDF/图片编辑、缺失父目录创建、递归删除、任意shell。
- SQLite只协调同一状态目录下的协作进程。最后SHA检查到文件替换之间仍有非协作编辑器的极短竞态；不宣称操作系统级原子CAS。原文件ACL/扩展属性不承诺保留，使用专用普通资料目录。
- 不提供UNKNOWN自动对账或强制覆盖；保留资料/状态后另行准备人工恢复方案，不能删状态来绕过。
- 现有宿主数据库备份不自动包含资料根及这个SQLite控制目录，工作区备份是后续任务。
- 扩展是用户信任代码，Worker/venv不构成恶意代码沙箱；本地所有者CLI不是受监督R2权威审批。
- 尚未安装、真实对话模型调用或原目录验收。下一步先正式验收并安装候选，然后推进工作区备份，再回到邮件、ehall、投递简历的真实使用验证。
