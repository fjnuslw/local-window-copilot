# Project Plan

这个目录只保留两类内容：

- 当前执行依据：可以直接指导代码和测试。
- 历史草稿：只用于回看思考过程，不再指导实现。

## 当前执行依据

- `ambient_companion_product_spec_zh.md`：Ambient Companion / 陪伴式桌面伙伴的新产品中心，摘要降级为后台感知能力。
- `application_agent_spec_zh.md`：桌宠式窗口 Copilot 的上下文与记忆设计草案。
- `current_execution_status_and_roadmap.md`：当前已完成能力、已清理内容和下一步路线。
- `target_model_implementation_plan.md`：MiniCPM-V / llama.cpp 本地模型主链路。
- `two_line_chat_window_spec_zh.md`：自动观察线和用户对话线的产品拆分。
- `local_runtime_store_policy_zh.md`：本地 RuntimeStore 策略。
- `kv_cache_profile_and_agent_split_spec_zh.md`：KV cache 友好的 profile/context 分层与双线 agent 拆分。
- `hermes_like_tool_layer_spec_zh.md`：Hermes-like 三工具 agent 层，替代旧关键词路由。
- `context_vision_ui_next_spec_zh.md`：WebUI 精简、视觉识别质量、按问题回看截图与桌宠 UI 下一阶段规格。

## 当前设计原则

- 项目定位是 Windows 桌宠式窗口 Copilot，不是自动操作电脑的广义 Agent。
- 产品中心是陪伴式桌面伙伴；截图 + VLM 是后台感知能力，不是前台产品本体。
- 默认不汇报屏幕，默认陪伴；只有用户邀请时才进入分析。
- 本地 RuntimeStore 保存运行时状态和短期会话记忆。
- 用户提问时先回应意图和情绪；用户邀请分析时，才使用截图、最近窗口摘要、当前观察卡和短期记忆。
- 固定系统提示词、用户可编辑 profile、动态上下文和对话历史必须分层。
- 主链路不行就明确失败、暂停或提示用户，不引入掩盖问题的替代链。
