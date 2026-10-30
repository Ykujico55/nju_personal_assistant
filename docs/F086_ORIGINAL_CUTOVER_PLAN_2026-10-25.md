# F08.6 原库0012与新版冻结启动方案及执行结果

## 当前状态

**本人明确批准后，已应用唯一0012迁移，并冻结启动Public/Health/Agent及原主站连接器。** 23张保护表数据未变，19项只读HTTP检查通过，独立通知进程未受影响。手机新版本登录页及通知点击等实际反馈尚待，F08.6仍BLOCKED。

以下方案和批准前检查保留原执行范围；实际结果见最后一节。

## 本次批准的执行范围

1. 复核已绑定的代码/配置/现有扩展摘要、备份与隔离恢复证据；原库实际身份及11份迁移必须完全匹配。任一漂移或端口占用就停止。
2. 在原扩展目录的同级 `var/control/freeze.json` 写入冻结状态；模型/任务循环不领取现有或新增作业，不自动恢复执行。
3. 仅对原库新增 `0012_runtime_records`：创建runtime_records/runtime_commands两张通用表并登记迁移，不改0001–0011或扩展Schema。0012 SHA-256：`f823c7297763872a0fb562c8edd0e0556680b3568ee234053995813abb861917`。
4. 使用既有Windows虚拟环境与本次绑定的仓库代码，隐藏启动已交付host guardian；只启动回环Public8000、独立Health8010、冻结Agent，Admin8001不启动。常驻拥有者持有Windows Job句柄；退出时只收尾本次拥有的进程，不按裸PID接管其他服务。
5. Agent启动会恢复原库现有两份ENABLED扩展：example.echo@0.1.0与nju.smail@0.1.0，精确材料见下表；不安装或升级扩展。Worker可以处理用户后续打开表单/工作流目录的读取请求，冻结不是所有扩展代码都不执行。
6. 重新启动原主站Tunnel `675e44a7-276e-4d59-ab87-ceb7c5ad02ee`，保持固定域名至回环8000的既有配置和兜底404；不改DNS、Access策略或Tailscale路由，不重启独立通知服务。

| 既有扩展 | 已核验payload摘要 |
|---|---|
| example.echo@0.1.0 | `sha256:1c3725193434fa8d027ce95b87b5992fec4bef2fa72f82da18525a241ec70725` |
| nju.smail@0.1.0 | `sha256:b05775055c0daa7932ec924286d0d1f7f95055f3c58a02e9c990c7a80c58353a` |

## 材料与验证

- 263项运行文件绑定摘要；当前唯一产品差异是已完成完整门禁的旧快照修复，未新增业务代码。
- 同日已运行完整本地 **1408 passed、137 skipped、7 warnings**及完整PostgreSQL/Worker **137 passed**；修复三文件摘要仍匹配，此次准备没有重复全量。
- 已离线构建wheel：265条目，236份Python源码、7份PWA、12份迁移逐字节一致，不含扩展源码。wheel SHA-256 `7171f07a7353e878812c1d03677f74ccc41e99f5cc8f7a577e0d85fc0a8185e0`。此次只作材料核验，未安装wheel或修改发行指针；实际启动复用现有环境，运行前复核源码全摘要。
- 本轮依赖检查、三份JavaScript语法和差异检查通过；候选脚本与固定机器计划的check证据见下节。

启动后检查回环端口、冻结标记、心跳、迁移哈希和公开认证边界。进程存活不等于公网或手机验收通过。发信和浏览器提交配置均保持false；不自动resume，不执行模型任务、不主动同步邮箱或制造外部动作。原任务、草稿和邮件数据保留。

## 失败处理

出现迁移/启动/健康失败，保留冻结状态并停止本次拥有的进程，记录不含凭据/正文的元数据。0012采用事务迁移；已成功提交后不自动删除新表，也不自动覆盖原库恢复。已核验的加密恢复点保留，若确需数据恢复，另行审核目标与切换步骤。

## 需要确认的原因

上次本人只批准隔离恢复，并明确未包含原库0012或新版启动。本次将写原库并重新发布入口，而且会执行现有扩展Worker，需要依AGENTS在精确材料和范围上再次确认。此处为执行前授权边界；本人随后已明确回复“允许”，执行结果见最后一节。


## 固定计划及批准前检查

机器计划位于本机Git忽略目录 `var/deployment/f086-ntfy-2026-10-24/original-cutover-plan.json`。

- 计划SHA-256：`2783a8bc32a2ecec097abb0a6dce00cb81429fe385a088c4be9bf2d09ae467c0`。
- 私有操作脚本SHA-256：`6c139b9b739d6f0ec277a3f79f2595c2601a38e129b8b3c49ad54e562ee29a14`。
- 解释器、候选profile、263项运行文件、现有两份扩展payload、密文备份、真实隔离恢复结果、原主站连接器EXE和非秘密YAML配置均已绑定。

已执行的纯静态命令：

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B var/deployment/f086-ntfy-2026-10-24/run-original-cutover.py check
.\.venv-win\Scripts\python.exe -m ruff check --no-cache --select F var/deployment/f086-ntfy-2026-10-24/run-original-cutover.py
```

两项exit0；另compile通过。独立只读连接以default_transaction_read_only与只读事务核对该候选的原库身份、精确11迁移、两份ENABLED版本/路径/材料/Manifest，均通过。缺失批准摘要及全0错误摘要两条负向探针均在变更前拒绝，没有生成切换journal、冻结标记或迁移暂存目录。

以上为实现准备和只读核验，不是正式apply，也不是独立阶段验收。没有迁移原库、启动进程、安装/升级扩展、调用模型或发送通知。此处为批准前检查历史；本人随后已明确批准并执行。 apply为隐藏常驻owner，不能运行后立即结束；后续需要停止时先使用原host stop控制命令，让拥有者有序回收本次进程，不凭日志PID接管其他服务。


## 本人批准后的执行结果（2026-10-25）

本人明确回复“允许”后，先再次核对计划和操作脚本摘要未变、journal不存在；随后只启动一次隐藏常驻拥有者，执行已批准摘要的apply。计划摘要 `2783a8bc32a2ecec097abb0a6dce00cb81429fe385a088c4be9bf2d09ae467c0`，批准材料未改。

独占journal在迁移/标记/服务变更前创建；原库身份、前11迁移、两份既有ENABLED扩展均匹配后，先持锁写freeze，再使用逐字节核验的暂存SQL运行现成startup。返回仅 `0012_runtime_records`；后续只读查询确认原库12份版本/哈希完全匹配。未改历史迁移或执行扩展数据升级，没有安装/升级扩展。

guardian当前 **FROZEN_RUNNING**，已启动Public8000、独立Health8010及Agent；Admin8001无监听。常驻拥有者PID 40524，guardian实际PID 14296；PID只作本轮元数据，不作为其他进程的可接管授权。现有两个0.1.0扩展由正常启动恢复。主站连接器PID 29692，使用原配置；本机ready200。独立通知Tunnel47312、ntfy48668继续为原身份运行，通知连接器ready200，没有重启它们。

### 运行后核验

- 只读原库对比加密恢复点：**23张保护表全部行不变**，含任务/消息/草稿/作业/审批与全部13张邮件表；原3任务、1草稿、15邮件保留。原3个READY作业未被领取或改写。
- Agent心跳存在，freeze为true；未生成execution或模型request记录。发信/浏览器提交保持false，没有调用模型、同步邮箱、发测试通知或执行外部提交。
- **19项只读HTTP检查通过**：回环与同一Windows节点Tailscale HTTPS的精确health200、七条禁止路径404；回环公共tasks/system/ui无认证均401且no-store。同节点结果不冒充本轮手机检查。
- 主站公开UI/tasks未认证均302到本人团队Access域；不使用Cookie/JWT、不读取登录后正文。主站与通知连接器ready均200。Tailscale `serve status`明确为tailnet only，仅代理8010；没有Funnel。
- 实际手机刷新/连接反馈已通过异步问题请求，目前尚待；不据进程存活宣称手机或整个F08.6验收通过。

私有回执：original-cutover-journal.json持续更新，database/http/network-postcheck及original-cutover-evidence.json只存聚合/布尔/摘要。当前拥有者、guardian、组件和主站连接器按授权保留。没有运行新的pytest或重复完整门禁，因为本轮无产品源码变更；同日相同产品材料的完整本地1408 passed/137 skipped/7 warnings与PostgreSQL/Worker137 passed见前节及旧快照修复报告。这不是本轮全量重跑或独立验收结论。

### 运行方式与限制

本轮使用已绑定仓库代码及既有.venv-win，不安装wheel或切换release指针；profile仍为本机已审阅候选文件。此为冻结验收运行，没有配置开机自启，不等于长期运行/升级阶段验收。

若需要停止本轮服务，使用现有host控制命令通知拥有者有序收尾（当前不要执行）：

```powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B -m personal_assistant.host_entry stop --profile var/deployment/f086-ntfy-2026-10-24/assistant-host.candidate.json
```

不要重复apply；原库现在已是12迁移，journal也会拒绝第二次切换。若PC重启或服务停止，需按当前12迁移的启动路径恢复，不能重跑本次一次性迁移入口。本次冻结拥有者不接受自动resume；解除冻结或改变运行范围须另行确认。F08.6保持BLOCKED，下一步补手机页面/版本、实际任务通知点击及既有剩余验收证据。
