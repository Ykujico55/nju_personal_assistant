# 开源参考与实现取舍

本轮先只读研究原始 GitHub 仓库的 README、模块说明和 LICENSE，再独立实现。没有克隆、安装或执行这些项目，也没有将其运行代码作为本插件依赖。

| 原始项目 | 许可证 | 参考的设计 |
|---|---|---|
| [AutoCast-Boss](https://github.com/gstranded/autocast-boss-assistant) | [MIT](https://github.com/gstranded/autocast-boss-assistant/blob/main/LICENSE) | 岗位队列、公司/招聘者会话核对、发送后回执分离；沟通成功不等于简历已发送 |
| [BossAutoApply](https://github.com/FanzhiZeng/BossAutoApply) | [MIT](https://github.com/FanzhiZeng/BossAutoApply/blob/main/LICENSE) | 选择器、纯筛选逻辑和页面适配分开；岗位链接去跟踪参数后识别同一职位 |
| [JobSpy](https://github.com/speedyapply/JobSpy) | [MIT](https://github.com/speedyapply/JobSpy/blob/main/LICENSE) | 各来源输出统一职位字段；本插件先兼容常见 CSV/JSON 字段，不引入其抓取依赖 |

具体参考入口：

- [AutoCast 会话匹配](https://github.com/gstranded/autocast-boss-assistant/blob/main/extension/shared/conversation-match.js)、[派发边界](https://github.com/gstranded/autocast-boss-assistant/blob/main/extension/shared/operation-dispatch-gate.js)。
- [BossAutoApply 选择器](https://github.com/FanzhiZeng/BossAutoApply/blob/main/src/shared/selectors.js)、[筛选逻辑](https://github.com/FanzhiZeng/BossAutoApply/blob/main/src/shared/matcher.js)、[内容脚本](https://github.com/FanzhiZeng/BossAutoApply/blob/main/src/content/content.js)。
- [JobSpy 职位模型](https://github.com/speedyapply/JobSpy/blob/main/jobspy/model.py)。

BOSS 的“立即沟通”可能直接产生一条平台默认消息，不能先点击再把最终发送当作唯一写动作。简历发送还可能依赖招聘者回复；因此本插件区分准备、已沟通、等待回复、简历已发送和结果未知。上述项目中的自动重试、网络响应 hook 或模糊目标降级没有作为本插件的执行策略。

页面选择器只是初始候选。本轮合成 DOM 测试不能证明真实 BOSS 的全部版本兼容，平台结构变化时应返回待人工核对。原 AIHawk 地址研究时已重定向成另一类浏览器项目，因此未把旧的 AIHawk 介绍当作当前实现依据。
