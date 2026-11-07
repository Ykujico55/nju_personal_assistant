# EXT.DEV.2 原实例安装与真实 DeepSeek 计算器试写（2026-10-30）

本次开发插件安装、系统环境 DeepSeek 密钥接入及真实计算器试写已完成。生成的计算器候选通过14项本地测试，尚未安装为助手工具；完整“生成→安装→调用”的验收仍待本人确认。F08.6保持BLOCKED，F07/F08整体状态不变。

## 原实例安装与模型

实际仓库：C:/Users/kunji/Desktop/homework/生成式软件工程/lab1。沿用原PostgreSQL assistant、var/extensions、var/artifacts和var/control，不迁移、替换或清空原数据。

通过现有Admin inspect→精确材料确认→install→config→enable流程安装assistant.plugin_developer@0.1.0，两项操作均SUCCEEDED；最终只读核对ENABLED，实际发现9个标准developer工具。专用目录var/plugin-development，固定测试预算30秒。

安装材料摘要：sha256:eb72afd8174df95a08a41b730d58894094bfd8e55c459b7df1e3dd4c0a886d22。安装操作extop_6db3856cd93e40538abceff8f228d920，启用操作extop_033a4349303241f5a2a2548582717676。首次缺Idempotency-Key的请求在变更前被拒绝，修正后使用稳定请求键；没有绕过确认机制。

实际使用系统DEEPSEEK_API_KEY，在进程内读取后保存至Windows Credential Manager；var/config/model.json仅更新引用，模型deepseek-flash，端点https://api.deepseek.com。新本机profile为var/deployment/ext-dev2-2026-10-30/host.json，与共享模型配置引用一致；旧原实例profile无模型覆盖项，后续也加载共享引用。旧引用配置备份model.before.json。原始密钥未打印、未写入源码/配置文件/命令行或发给模型。

## 真实试写方法与限制

使用原库真实TaskService、TaskRuntime、Tool Gateway、已安装Worker和DeepSeek。没有替身模型，实施者未代写计算器源码或测试。模型只收到公开需求、生成源码及本次工具观察，未读取原任务正文、邮箱、简历或个人知识；精确模型披露许可仍保留。

限定运行器持guardian锁，仅允许9个developer工具，直接调用runtime.advance，不启动通用队列Worker或调度器；每轮结束恢复冻结。本次单次模型输出预算4096 token，未改变正常宿主默认1024预算，不宣称普通网页编码已同样配置。

实际任务：
- task_d301281619be49dd859634b81fc8352c：初始试写。曾使用错误的type决策字段、新文件null哈希，并在缺文件时提前声称完成。
- task_aa9e5475e09a4982a3d8b7b92cae9304：补齐与修正。实施者反馈导入名、AST运算符白名单、Outcome成员、缺Manifest/Schema/测试及非有限中间值问题；模型通过实际工具修改。

此次证明受监督的真实模型开发流程，不是一次提示后无人干预的可靠开发。第一任务的SUCCEEDED只是模型决策落库，不能代替验收；结论以最终实际材料与测试为准。

## 计算器产物

候选cand_8def589dae584c6ca7086304fbd93612，扩展sample.calculator@0.1.0，共13文件。
源码：var/plugin-development/candidates/cand_8def589dae584c6ca7086304fbd93612。
最终摘要：c360bd6d574ae280cb82f10d10542038e2c47ec0784274c7b4539424fbee5932。

支持加减乘除、优先级、括号、小数和正负号；AST白名单解释，不使用eval/exec/compile。例如2+3*4=14、(2+3)*4=20、9/2=4.5。拒绝变量、调用、属性、布尔、字符串、复数、零除、过长输入及非有限中间结果。保留READ ping，新增READ calculate，无宿主能力和额外第三方运行依赖。

静态核查后，依据本次试写及保留本地验证的授权，由操作者授予精确材料一次固定测试。实际开发插件固定验证器执行候选unittest：14 tests、0 failures、0 errors、0 skipped，0.875秒。13项计算器测试加1项SDK ping生命周期测试；不是全系统测试。第二个只读检查任务确认最终文件、回执及摘要一致，没有重复执行测试。

developer.release.request已导出通过材料快照，返回READY_FOR_LOCAL_INSTALL / install_confirmation_required=true。本次没有安装或启用sample.calculator，没有计算器UI，不能声称原实例新工具调用已通过。

## 命令与证据

~~~powershell
Set-Location -LiteralPath 'C:\Users\kunji\Desktop\homework\生成式软件工程\lab1'
.\.venv-win\Scripts\python.exe -Xutf8 -B var/deployment/ext-dev2-2026-10-30/validate-calculator.py
.\.venv-win\Scripts\python.exe -Xutf8 -B var/deployment/ext-dev2-2026-10-30/verify-final.py
git diff --check
~~~

validate-calculator.py会授予当前摘要一次测试，后续变更必须先重新审阅，不能无条件用于任意候选。同摘要同键重跑返回已保存回执，不算新测试。

本机Git忽略目录var/deployment/ext-dev2-2026-10-30保留install-result.json、enable-result.json、model-decisions.jsonl、calculator-trial*.log、calculator-completion*.log、calculator-reviewed-material.json、calculator-validation.json、calculator-release.json和final-state.json。测试原日志为var/plugin-development/validation-logs/validation-dfccf4d4f37844f88db9b9bd18c9f4f5.log。

## 收尾与下一步

最终核验：开发插件ENABLED；4个原任务仍QUEUED，各自原作业READY / attempts=0 / version=0 / 无租约；外部动作账本仍0，原审批1条EXPIRED；发件和浏览器提交关闭。两个计算器任务为SUCCEEDED。

限定运行器通过正式resume请求确认每轮披露，留下109个试写任务READY作业（20+89）。没有伪造租约或直接SQL改成成功；这些作业指向已终结任务，按现有runtime路径后续正常消费将跳过已终结任务，但本次未执行队列消费。恢复日常Worker前需同时明确原四个待办的处置，不能为清理试写作业而无意执行旧任务。

临时Admin及其本轮子进程已关闭。最终8000/8001/8010均无监听，后台冻结；安装与模型配置已持久化，本轮没有留下常驻Web/Agent/Tunnel服务。旧status.json不能作为现有运行证据。独立ntfy通道未操作。

本轮没有宿主/SDK/开发插件产品源码修改，没有提交推送；按用户要求不重复完整本地、PG/Worker套件，只运行候选固定测试、真实安装启用及定向只读核验。

下一编号仍为EXT.DEV.2：本人确认精确计算器材料后，经现有确认安装并启用，验证真实新工具调用；另行确认旧队列后恢复日常后台。无需重新配置DeepSeek密钥。
