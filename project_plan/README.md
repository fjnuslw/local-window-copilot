# Project Plan

这个目录只保留两类内容：

- 当前执行依据：可以直接指导代码和测试。
- 历史草稿：只用于回看思考过程，不再指导实现。

## 当前执行依据

- `application_agent_spec_zh.md`：桌宠式窗口 Copilot 的上下文与记忆设计草案。
- `current_execution_status_and_roadmap.md`：当前已完成能力、已清理内容和下一步路线。
- `target_model_implementation_plan.md`：MiniCPM-V / llama.cpp 本地模型主链路。
- `two_line_chat_window_spec_zh.md`：自动观察线和用户对话线的产品拆分。
- `local_runtime_store_policy_zh.md`：本地 RuntimeStore 策略。

## 当前设计原则

- 项目定位是 Windows 桌宠式窗口 Copilot，不是自动操作电脑的广义 Agent。
- 截图 + VLM 是主链路，`ObservationCard` 只提供少量低噪声元信息。
- 本地 RuntimeStore 保存运行时状态和短期会话记忆。
- 用户提问时，使用最近窗口摘要、当前观察卡和短期记忆回答。
- 主链路不行就明确失败、暂停或提示用户，不引入掩盖问题的替代链。
