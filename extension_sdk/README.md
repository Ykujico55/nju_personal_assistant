# Personal Assistant Extension SDK

这是业务扩展唯一允许依赖的宿主契约。它不依赖 FastAPI、数据库或宿主核心。

扩展至少实现 `Extension`，按需实现八种槽协议。Worker 使用换行分隔的 JSON-RPC 2.0；大文件、上下文和能力只传句柄，禁止传宿主绝对路径、数据库连接或原始凭据。

`run_stdio_worker(create_extension)` 可作为最小 Worker 入口。参考实现见 `../extensions/example_echo`。

