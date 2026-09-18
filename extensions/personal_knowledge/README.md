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
| `ToolProvider` | `knowledge.search` | READ |
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
