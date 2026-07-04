# Hermes-like Tool Layer Spec

更新时间：2026-07-04

状态：Codex 已开始落实第一版。

## 1. 背景

旧实现把用户问题分成 `companion / work_lens / visual_answer / text_answer` 四路，并在 Python 里维护越来越长的关键词表。这个方向会导致三个问题：

- 上下文管理被硬编码路由替代，问题越修越碎。
- 小模型被迫表现成客服，遇到“看看页面详细内容”会反问用户，而不是主动调用视觉能力。
- KV cache 分层被动态摘要和模式分支污染，难以稳定调试。

本阶段目标不是做“大而全 agent”，而是建立一个像 Hermes 一样可解释、可调试、可扩展的工具层。

## 2. 核心原则

- 模型可见工具少：只暴露 3 个工具，避免小模型在相似工具之间迷路。
- 后端 provider 可以多：当前屏幕、历史截图、局部裁剪、VLM、profile、短期记忆、对话历史都藏在工具内部。
- 不做 fallback：工具规划失败就是失败，缺截图就明确缺截图，不伪装成文本回答。
- 不继续扩充关键词表：Python 不再用一长串关键词决定 `visual/work/text` 模式。
- profile 和 system prompt 分层：用户可编辑 profile 不改稳定 base prompt。

## 3. 模型可见工具

### 3.1 `screen.look(question)`

用途：
- 用户要求看屏幕、看页面、看窗口、看截图、读文字、看按钮、分析界面细节。

内部 provider：
- 当前窗口 metadata。
- 当前截图。
- 最近屏幕索引。
- 局部裁剪。
- VLM 视觉细看。

输出：
- 基于截图的视觉回答。
- 窗口元信息。
- 选择的 image_id/source/crop_reason。

### 3.2 `memory.search(query)`

用途：
- 用户问过去聊过什么、用户偏好、项目方向、已有记忆、当前上下文背景。

内部 provider：
- `ASSISTANT.md / USER.md` profile packet。
- RuntimeStore memory items。
- 最近对话。
- 最近屏幕索引。
- 用户最近目标与困惑。

### 3.3 `memory.remember(note)`

用途：
- 仅当用户明确要求“记住/以后记得/保存这个偏好”时写入。

写入位置：
- 当前第一版写入本地 RuntimeStore memory item。
- 不自动改 `USER.md`，避免模型静默污染用户可编辑 profile。

## 4. 调用流程

```text
用户问题
-> AgentOrchestrator
-> Tool Planner：输出单行工具名
-> AgentToolRuntime 执行工具
-> 工具结果进入最终回答 prompt
-> VLM/LLM 流式回答用户
```

Tool Planner 只输出一行：

```text
screen.look
```

可选值只有：

```text
screen.look
memory.search
memory.remember
none
```

参数默认使用用户原问题，避免小模型生成嵌套 JSON 时把数组和对象写坏。

## 5. 小模型约束

不把以下 provider 直接注册给模型：

- `context.current_screen()`
- `context.search_screens(query, limit)`
- `vision.inspect(image_id, question)`
- `conversation.search(query)`
- `memory.read_profile()`

原因：
- 名称相似，边界相近，小模型容易误选。
- 多工具链需要更强规划能力，会拖慢桌宠轻量体验。
- 三工具能表达用户意图，provider 细节由后端稳定实现。

## 6. 必须删除的旧逻辑

- `question_router.py` 关键词表。
- `test_question_router.py`。
- `assistant_chat.py` 中四路模式分叉。
- WebUI 中 `companion/work_lens/visual_answer/text_answer` 模式展示。
- 不再使用独立 `work_lens` prompt 作为代码分支。

## 7. 第一版验收标准

- 主对话链路不 import `question_router`。
- 代码中不存在 `classify_question` / `select_image_for_question`。
- 注册工具数量为 3。
- “看页面详细内容”应由 planner 选择 `screen.look`，而不是助手反问用户想看什么。
- 缺少目标窗口截图时明确提示用户先观察，不走文本替代。
- WebUI context preview 显示 `agent_orchestrated` 和注册工具数量。
- 相关测试通过。

## 8. 后续增强

- 给 `screen.look` 增加多区域自动裁剪。
- 给 `memory.search` 增加 SQLite FTS 检索，而不是只做短列表拼接。
- 将 `memory.remember` 分为 session memory 与 profile proposal，用户确认后才写 `USER.md`。
- 若本地模型稳定支持 OpenAI tool calling，再把单行 planner 升级为原生 tool_calls。
