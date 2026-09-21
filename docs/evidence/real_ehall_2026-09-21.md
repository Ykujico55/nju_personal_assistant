# 真实 ehall 只读采集证据（2026-09-21）

本文件是用户运行的真实站点验收记录（脱敏），用于让后续审计者无需信任实现者
的口头描述即可独立核对“门户/SSO 只读采集已运行”的结论。原始日志由
`scripts/ehall_real_acceptance.py capture` 生成，仅含 URL、时间戳与有界结构，
**不含** cookie、密码、验证码、storage state、原始 HTML 或页面正文。

## 运行方式

```powershell
.venv-win\Scripts\python.exe scripts\ehall_real_acceptance.py capture `
  --origins "https://ehall.nju.edu.cn,https://authserver.nju.edu.cn" `
  --entry "https://ehall.nju.edu.cn/ywtb-portal/index.html" `
  --choice-file "$env:TEMP\ehall_choice.txt" --out "$env:TEMP\ehall_capture.json"
```

用户亲自在弹出的 headed Chromium 中完成 SSO（扫码/验证码）；脚本从不输入凭据、
不处理验证码、没有提交路径。本文件保留的日志可独立证明**一次完整会话**（本机时间
2026-09-21 02:34 开始，时间戳为 UTC，见下）；此前还有多次会话（02:24 / 02:26）
用于复现并修复登录跳转缺陷，但那些会话的 `--out`/日志已被后续运行覆盖，本文件不
把它们作为可核验证据。

```json
{"at": "2026-09-20T18:34:49.111791+00:00", "phase": "capture", "event": "entry_opened", "url": "https://ehall.nju.edu.cn/ywtb-portal/official/index.html#/", "login_page": false, "hint": "请在 Chromium 窗口内亲自完成 SSO（扫码/验证码）；脚本不会输入任何凭据"}
{"at": "2026-09-20T18:34:49.297540+00:00", "phase": "capture", "event": "waiting_for_user_login", "url": "https://ehall.nju.edu.cn/ywtb-portal/official/index.html#/hall", "login_page": false, "blocked_origin_requests": 0}
{"at": "2026-09-20T18:34:54.231648+00:00", "phase": "capture", "event": "waiting_for_user_login", "url": "https://authserver.nju.edu.cn/authserver/login?service=https%3A%2F%2Fehall.nju.edu.cn%3A443%2Flogin%3Fservice%3Dhttps%3A%2F%2Fehall.nju.edu.cn%2Fywtb-portal%2Fofficial%2Findex.html", "login_page": true, "blocked_origin_requests": 0}
{"at": "2026-09-20T18:35:09.055386+00:00", "phase": "capture", "event": "waiting_for_user_login", "url": "https://ehall.nju.edu.cn/ywtb-portal/official/index.html", "login_page": false, "blocked_origin_requests": 0}
{"at": "2026-09-20T18:35:11.647380+00:00", "phase": "capture", "event": "portal_read", "url": "https://ehall.nju.edu.cn/ywtb-portal/official/index.html#/home/official_home", "link_count": 1, "headings": [{"level": 1, "text": "南京大学网上办事服务大厅"}], "actions": ["搜索", "取消", "订阅"], "control_names": ["", ""]}
{"at": "2026-09-20T18:35:11.650939+00:00", "phase": "capture", "event": "awaiting_app_choice", "choice_file": "C:\\Users\\kunji\\AppData\\Local\\Temp\\opencode\\ehall_choice.txt", "links": [{"text": "全部服务", "path": "/ywtb-portal/official/index.html"}]}
```

早前一次成功采集（同一流程）捕获到的真实列表页（`#/role_matter`）指纹：

```
d6d974e37414a98b612964a344c0b6fe6288c2f4b13d81b6769528f31ee87f87
```

## 结论与限制

- 真实门户/SSO 只读采集已运行：SSO 挑战由用户亲自完成，登录后助手只读取门户的
  有界结构与链接。
- 真实验证到的两个缺陷（已修复并有回归测试）：CAS 登录跳转的 `service=` 被开放
  重定向启发式误判导致 `ERR_FAILED`；Companion `status` 不返回 `url`/`login_page`。
- 真实大厅是 SPA：事项卡片为 JS 按钮而非 `<a href>`（门户链接数 1，仅“全部服务”），
  因此当前发现流程已补充读取声明式导航动作；真实 hash 路由（`#/...`）的 adapter
  指纹捕获与替换、真实交易表单与真实提交仍未运行。
- 本文件不包含任何可由第三方直接复用的凭据；所有 URL 均为公开入口地址。
