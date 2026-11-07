# 0.2.0 本地目录简历适配报告

日期：2026-10-30；任务仍为EXT.CAREER.1。原实例未安装、升级、重启或解冻，未读取本人真实简历或操作真实BOSS/邮箱。

## 已完成

系统此前已有个人知识扩展的授权源目录读取，以及宿主受管Artifact写入。知识库路径实现位于独立业务包内，SDK没有通用任意目录读写接口。本轮沿用这些安全约束，在career.apply内实现目录适配，不依赖知识库私有包，不改宿主核心/SDK/审批/迁移。

新增resume_roots配置和两个工具：

| 工具 | 风险 | 作用 |
|---|---|---|
| career.resume.list | READ | 列出授权目录可读取简历，返回相对路径/大小/格式/哈希，扫描有界 |
| career.resume.import_file | INTERNAL_WRITE | 按root_key+relative_path+expected_sha256读取并写宿主制品 |

使用链路：list → 本人/Agent明确选择 → import_file → 标准ResumeRef → BOSS或邮件prepare → Gateway独立审批 → 原执行链路。无需PWA上传控件、无需将简历base64贴给模型。正常Agent可以直接调用新工具，仍需核对实际工具结果，未新增招聘专用工作流。

源文件只读；写入目标仍是既有受管制品目录，不提供任意改写原件的工具。BOSS与邮件使用同一固定制品引用。列表后文件变化拒绝旧哈希；成功导入后，源文件后来的编辑不会暗改已准备附件。用户需要新版时重新列举/导入/准备和审批。

## 改动文件

- src/career_apply/local_files.py：授权根、相对路径、文件一致性与有界枚举。
- src/career_apply/worker.py：工具注册、本地线程读取、固定制品导入与类型化失败。
- schemas/config.json、新增resume-list/import-file四份输入输出Schema、extension.toml：13工具及配置契约。
- pyproject.toml、src/career_apply/__init__.py：版本升为0.2.0，避免同版本覆盖。
- tests/test_local_files.py、test_local_resume_worker.py：失败先行的目录/竞态/Worker/材料组合回归。
- tests/test_worker.py：工具数与版本同步；增强既有真实stdio测试，在子进程中读取合成授权目录。
- README、INSTALL、NEXT_STEPS及主项目手测/接力文档：移除旧“需上传/缺导入入口”的说明，保留真实站点未验收边界。

## 文件边界

只接受显式配置、已存在的本地绝对目录，最多16个，不自动继承知识库授权。工具参数只接受root_key及相对路径。拒绝绝对路径、盘符/UNC/ADS、父目录穿越、隐藏项、符号链接/Windows junction/reparse、根替换和不支持类型。

最多512 KiB，PDF/DOCX/UTF-8 TXT；与本地CLI规划的8 MiB限制分开。列表最多100项、扫描2000项/深度32，返回truncated/skipped。不可读或过滤项不回传绝对路径/内容，不把空结果解释为整个目录没有文件。

读取先复核每级路径与根身份，打开后在读字节前比对文件identity/size/mtime，最多读上限+1字节，读后再复核路径/根/描述符及SHA-256。Windows Python3.12路径stat和fstat的ctime语义不同，因此共同版本比较采用dev/ino/size/mtime，最终内容另绑定哈希。

工具输出不含正文、base64或绝对目录；文件名/相对路径/哈希等元数据及后续工具观察仍按既有模型披露许可处理。所有扩展仍是本人信任代码，不将目录保护描述成恶意代码沙箱。

## 实际验证

先红：新增Worker集合7 failed/2 passed，失败因缺工具/版本；helper初始因模块缺失失败。证据在verification/local-resume-worker-red-summary.txt与local-files-red.log。

本轮实际通过，命令按项目根目录执行：

~~~powershell
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest -c extensions/career_apply/pyproject.toml extensions/career_apply/tests/test_local_files.py extensions/career_apply/tests/test_local_resume_worker.py extensions/career_apply/tests/test_boss_adapter.py -o addopts=--strict-markers -q
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest -c extensions/career_apply/pyproject.toml extensions/career_apply/tests/test_local_resume_worker.py extensions/career_apply/tests/test_worker.py extensions/career_apply/tests/test_mail.py -o addopts=--strict-markers -q
.\.venv-win\Scripts\python.exe -Xutf8 -B -m pytest -c extensions/career_apply/pyproject.toml extensions/career_apply/tests/test_worker.py -o addopts=--strict-markers -q
.\.venv-win\Scripts\python.exe -Xutf8 -B -m ruff check extensions/career_apply/src extensions/career_apply/tests
~~~

| 范围 | 实际结果 | 日志（插件verification/） |
|---|---|---|
| 目录保护+新Worker+BOSS本地浏览器适配 | 41 passed / 57.51秒 | local-resume-targeted.log |
| 新Worker与原Worker/邮件材料兼容 | 27 passed / 5.64秒 | local-resume-worker-green.log |
| 增强目录列举的真实stdio/Worker文件复验 | 12 passed / 4.27秒 | local-resume-stdio.log |
| 插件全部源与测试Ruff | passed | local-resume-ruff.log |
| 插件Mypy | 11 source files passed | local-resume-mypy.log |

各命令有重叠，不把数字相加。目录集合中实际执行Windows junction/隐藏属性及读前文件替换反例，无skip；原文件内容/mtime保持及错误不泄露绝对路径已断言。Worker证明无需邮箱配置即可列举/导入，精确字节进入BOSS prepare与邮件MIME；stdio证明真实子进程可收到目录配置并列举，EOF退出0。

Mypy可复现命令：在extensions/career_apply目录执行：

~~~powershell
& ..\..\.venv-win\Scripts\python.exe -Xutf8 -B -m mypy --config-file pyproject.toml src
~~~

按用户要求仅运行改动插件和既有相关回归，不运行项目完整套件、完整PostgreSQL/Worker、真实模型/SMTP/BOSS，也不重复前轮审批全套。审批/Outbox/核心未修改。本次没有重建0.2.0 wheel，旧DELIVERY中的0.1.0 wheel是历史材料；当前安装按[INSTALL](INSTALL.md)的干净0.2.0目录进行。

## 下一步

按[安装说明](INSTALL.md)确认材料并安装/升级到0.2.0，配置本人实际resume_roots，使用[手测手册](../../docs/MANUAL_REAL_ENV_TEST_GUIDE_2026-10-30.md)验证真实目录列举和选定简历。随后核对真实BOSS控件及回执，再决定是否批准一个实际投递。

本轮本地目录适配已完成；BOSS实站兼容未验、原实例未切换，EXT.CAREER.1仍IN_PROGRESS。F08.6仍BLOCKED，F07/F08总体状态不变。
