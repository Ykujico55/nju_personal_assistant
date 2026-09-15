# Example Echo extension

该扩展是“增加功能无需修改核心”的最小证明，注册工具、上下文、计划与 Schema 表单四种槽。它不读取秘密，也不产生外部副作用。

生产扩展应复制此目录结构、替换命名空间，并在自身测试中使用 SDK contract fixtures。不要从扩展导入任何 `personal_assistant.core` 模块。

