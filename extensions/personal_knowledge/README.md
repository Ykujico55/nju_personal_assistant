# Personal Knowledge extension

`personal.knowledge` 把用户显式授权的目录变成可验证引用的个人知识索引：

```text
授权根目录 → 文件事件 + 周期性 reconciliation → SHA-256 内容哈希
     → Markdown/TXT/PDF 抽取（稳定 locator）→ 版本化分块
     → PostgreSQL FTS + pgvector → RRF 混合排序 → Evidence + 原文引用
```

## 注册槽位

| 槽 | 能力 ID | 风险 |
|---|---|---|
| `EventSource` | `knowledge.file_changes` | READ |
| `ContextProvider` | `knowledge.retrieve` | READ |
| `ContextProvider` | `knowledge.workspace` | READ（目录元数据） |
| `ContextProvider` | `knowledge.document` | READ（直接读取一份授权文件正文） |
| `ToolProvider` | `knowledge.search` | READ |
| `ToolProvider` | `knowledge.read` | READ（无需索引） |
| `ToolProvider` | `knowledge.reindex` | INTERNAL_WRITE（只改索引，不改源文件） |
| `ScheduleProvider` | `knowledge.reconcile` | — |
| `FormSchemaProvider` | `knowledge.roots` | — |
| `MigrationProvider` | `knowledge.index_schema` | — |

本阶段没有任何 `EXTERNAL_WRITE` 能力；原文件始终只读，系统生成内容只进入受管索引。

## 数据库访问

扩展不接触数据库凭据、连接串或主机路径。所有语句经宿主通用数据能力
（`host.data.migrate` / `host.data.transaction` / `host.data.execute`）在扩展自己的
`ext_personal_2e_knowledge` Schema 内执行；宿主强制单语句、参数上限、核心表黑名单与
`search_path` 隔离。扩展迁移随扩展制品发布，由宿主校验 SHA-256 后应用并登记
`extension_data_migrations`，核心 `migrations/0001`–`0005` 不被修改。

## 配置

配置通过通用扩展配置通道（manifest `config_schema` + Admin API
`PUT /admin/v1/extensions/{id}/config`）注入，核心无任何 `personal.knowledge` 分支：

```json
{
  "roots": [{"path": "C:/Users/me/Documents/notes", "key": "notes"}],
  "include_hidden": false,
  "max_file_bytes": 16777216,
  "embedding": {"provider": "none"}
}
```

`embedding.provider=none` 是默认值：检索显式降级为仅 FTS，向量列为 NULL，
结果标记 `vector_mode: disabled`。`embedding.provider=ollama` 只允许回环 HTTP
（`http://127.0.0.1:11434`），个人正文绝不发往远程服务；模型/维度/版本随版本记录，
模型变化会触发重建而不是混用向量。

## 验证

- 单元测试：`tests/unit/test_knowledge_*.py`（路径安全、抽取、分块、融合、索引）。
- 真实 PostgreSQL + pgvector：`tests/integration/test_postgres_f05.py`。
- 真实 Extension Worker：`tests/integration/test_personal_knowledge_worker_real.py`。
- 契约：`tests/contract/test_f05_contract_consistency.py`。

不要从扩展导入 `personal_assistant.core` 或 `personal_assistant.infrastructure`，
也不要在扩展中读取 `os.environ` 或持有数据库凭据。


## 0.1.1 命中结果修复

检索结果的 page、line_start、line_end 只在适用时返回整数，其他情况省略。0.1.0曾将这些可选字段输出为null，违反结果Schema，使实际有命中的搜索被宿主判为失败；空结果不受这一问题影响。本补丁不放宽Schema、不改变旧迁移，不要求重建已有索引。

更新源码不会更新已安装Worker。需先预览并确认0.1.1材料，再按正常升级流程应用；原授权目录和索引保留。运行绑定旧扩展版本的任务应保留原记录，升级后建立新查询任务。只需搜索具体词语，无需重新reindex。

仅FTS模式仍依赖PostgreSQL的词边界：连续中文句子里的短词可能漏召回；这与模型API和vector_mode=disabled不是同一问题，本补丁不声称提供中文语义检索。


## 0.1.4 目录绑定与索引状态（候选）

索引通过现有 `knowledge_meta` 绑定规范化后的根目录、目录键及扫描选项，不新增核心表或修改迁移。
同一个目录键改指向另一路径时，目录概览与查询只隔离旧索引，不删除旧数据或修改源文件。
旧版未记录绑定的索引也先标为待索引；执行 `knowledge.reindex` 后沿用既有重建、清理语义，
不承诺永久保留多个目录的历史索引。后台既有 reconciliation 同样使用这套完整扫描判定。

`knowledge.workspace` 的全局 `index_state` / `index_reason` 来自只读元数据查询：

- `READY` / `INDEX_READY`：当前绑定的最近完整扫描成功；查询仍逐条重新校验文件内容。
- `PENDING`：`INDEX_BINDING_MISSING`、`ROOT_BINDING_CHANGED` 或 `INDEX_SCAN_INCOMPLETE`。
  未记录绑定、目录或扫描选项变化、部分扫描失败/取消均不会被视为就绪，也不会提供旧索引正文。
- `UNAVAILABLE`：`INDEX_METADATA_UNAVAILABLE` 或 `ROOT_CONFIGURATION_UNAVAILABLE`，无法核实状态。

概览仍不读取正文，单个文件的 `index_state` 保持 `UNKNOWN`；全局就绪不等于逐文件实时有效。
纯 `workspace_snapshot` 未访问索引存储，因此全局仍为 `UNKNOWN`。并发扫描用扫描标识和 CAS
完成绑定，迟到的旧扫描不能把较新的未完成扫描标成就绪。仅源码变更与本地验证，不表示原实例
已安装 0.1.4；安装升级仍需用户确认。

## 0.1.3 实例工作区概览（候选）

`knowledge.workspace` 用于“已配置哪些资料目录／目录里有哪些文件”；`knowledge.retrieve`
继续用于正文关键词检索。宿主通过标准 `ContextQuery.filters.provider_ids` 列表选择槽位；
未提供该字段时保留原检索行为。选择两者时一次 RPC 先返回一份目录概览，剩余 `limit`
用于正文证据。安装契约探针（空文本、limit=0）不访问目录或数据库。

概览只读取实例配置中已有授权根目录的文件元数据，不接受任意路径，不读取正文，也不自动
查询或重建索引。输出根目录 key/label、相对文件名、大小、类型和明确的配置/可访问状态；
不向模型提供绝对根目录路径。目录丢失仍标记“已配置但不可访问”。0.1.3 默认保留完整的
已扫描文件清单，移除此前50个文件和10000个JSON字符的输出裁剪；调用者显式指定
`max_files` 时仍遵守该限制。

遍历继续受运行安全预算保护：最多检查1000个目录条目、深入8层目录，遍历检查点预算2秒
（不是单次操作系统调用的硬超时）。隐藏项、忽略目录和链接/junction不跟随，路径授权规则不变。
达到扫描、深度、时间、路径长度或显式文件数量限制时，返回 `complete=false`、
`truncated=true` 和 `truncation_reasons`，明确说明部分结果的原因；不可访问也会标记不完整。
因此默认不按输出长度删减，不代表超大、过深或不可访问的目录保证完整扫描。
索引状态明确为 UNKNOWN，目录清单不会冒充索引结果。

目录概览是 SENSITIVE/USER_SOURCE 证据；哈希仅标识这份元数据快照，不是文件正文哈希。
中文短词FTS召回边界保持不变；本版本没有新增中文语义搜索。源码候选需用户确认后才能
升级原实例，不能把源码测试通过视为原实例已运行0.1.3。


## 0.1.5 授权文件正文读取（候选）

`knowledge.read` 工具接收 `{"resource": "knowledge://notes/章节/笔记.md"}`，也接受
包含扩展名的精确唯一文件名（例如 `笔记.md`）。`knowledge.document` 上下文槽使用完全相同的
资源字符串作为 `ContextQuery.text`；不接受自然语言指令、任意绝对路径或模糊文件名。
同名资源返回 `AMBIGUOUS` 和授权候选 URI，需选定文件；名称扫描不完整时返回
`LOOKUP_INCOMPLETE`，不会假称已找到唯一文件。工具与上下文都不读取索引、不自动重建索引，
不创建任务，不更改原文件。Manifest 的标准槽描述供通用路由选择，描述本身不构成动作授权。

仅支持现有 UTF-8 TXT、Markdown 和有限 PDF 提取器。正常结果 `status=READ, complete=true`，
保留完整提取正文、原始文件 SHA-256、`knowledge://` 来源及整份行号/页码范围；上下文正文为
纯文本，标记 `SENSITIVE/USER_SOURCE`，正文内容不得成为新指令。PDF 部分提取或诊断失败返回
`EXTRACTION_INCOMPLETE` 及固定诊断码，正文为空，不把部分文本冒充完整文档。

授权根及其每一层父路径、相对路径各分量均检查符号链接/junction；URI 也不能绕过隐藏项或
忽略目录（Windows 按大小写不敏感规则比较）。读前、打开句柄、读后及提取后的文件身份、
大小和写入时间必须一致；变化返回 `FILE_CHANGED`。文件名查询复用目录概览的1000条目/8层/
2秒检查点预算，并在扫描前后复核原始根路径链，不发布链接下候选名称。

正文不按显示长度截断。原文件大小仍受 `max_file_bytes` 和现有提取器限制；单文档结果以
实际 UTF-8 JSON 字节计算，最多3MiB，为宿主4MiB RPC保留封装余量。同轮多槽最终结果再用
真实 SDK 编码检查4MiB减16KiB的总预算。超限返回 `DOCUMENT_TOO_LARGE` 且正文为空；需
使用较小的文档，当前不提供分页，不放宽协议上限。此状态与 `FILE_TOO_LARGE`（文件/提取器
大小限制）、不支持格式、未找到、目录不可用明确区分，不外带原异常或绝对目录路径。

`knowledge.retrieve` 在索引不是 READY 时返回 `knowledge_index_status` 状态证据，包含固定
`index_state/index_reason`；待索引不再退化为普通无命中。已有绑定安全检查仍保留；指定文件
可以直接读取，关键词检索则仍需一次明确的索引操作。候选尚需用户授权安装，不能据源码
版本宣称原实例已拥有新槽位。


## 0.1.6 索引状态与文件发现修复（候选，尚未安装原实例）

索引绑定元数据通过宿主 JSON codec 保存为对象，不在扩展内重复编码。读取兼容历史单层
JSON 字符串对象，保留 schema、目录绑定摘要及 READY/PENDING 校验；不会把缺失或不完整的
状态自动改成 READY。完整扫描的末尾使用条件更新，过时扫描不能覆盖较新扫描。

`knowledge.reindex` 的 `versions_built` 仅统计新构建的版本。完整扫描核验后复用相同内容、
抽取器和 embedding 版本时计入 `unchanged`，因此 `versions_built=0` 本身不表示失败。
报告新增实际读回的 `index_state/index_reason`，以及 `skipped_empty` 和 `skipped` 诊断。
只有完整读到、有效 UTF-8 解码后为空白的 TXT/Markdown 才可跳过；PDF 无文字、解码失败、
读取失败等仍记入 `errors` 并保持待索引。原来有内容的文件变空时，先条件删除旧索引，
删除竞争失败不能宣称就绪。源文件始终不修改。

目录概览中的每个文件现在给出 `files[].source_uri`，可交给 `knowledge.read`。
`knowledge-workspace://instance/overview` 只是清单来源，不能拿来读取文件。
文件读取参数说明通过标准输入 Schema 进入工具目录，无需宿主业务分支。
