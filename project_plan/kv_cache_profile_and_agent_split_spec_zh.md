# KV Cache 友好的 Profile/Context 与双线 Agent 拆分 Spec

> 维护状态：历史 spec。本文中默认读取 latest summary / recent summaries 注入对话的设计已废弃；当前唯一权威主线见 ../docs/context_observation_tool_mainline_spec_zh.md。

更新时间：2026-07-03

## 1. 目标

下一阶段要解决三个问题：

```text
用户可以在 WebUI 编辑角色性格和偏好
系统提示词保持稳定，适合 prefix / KV cache
自动观察线和用户对话线从“服务职责分离”推进到“agent runtime 分离”
```

核心原则：

- 可编辑内容进入 **profile/context packet**，不改稳定 base system prompt。
- 固定 prompt、动态上下文、对话历史分层构造。
- 观察线负责看图总结；对话线负责基于文字上下文和记忆回答。
- 不引入 Redis / PostgreSQL / Docker。
- 不做 fallback 路线掩盖主链路问题。

---

## 2. Prompt 分层

当前问题：

```text
system = 固定规则 + 当前窗口摘要 + 最近窗口摘要 + 记忆 + 人设
```

这样每次窗口变化都会改变 system message 的前缀，prefix / KV cache 很难复用。

目标结构：

```text
base_prefix        稳定，不随用户、窗口、记忆变化
profile_packet     用户在 WebUI 编辑的角色/偏好 md，低频变化
context_packet     当前窗口摘要、最近窗口摘要、相关记忆，高频变化
dialogue_tail      最近多轮 user/assistant + 当前问题
```

### 2.1 base_prefix

来源：代码内置或固定 prompt 文件。

要求：

- 内容稳定。
- 不包含当前窗口摘要。
- 不包含最近 N 条窗口摘要。
- 不包含用户可编辑角色性格正文。
- 不包含记忆条目正文。
- 只定义协议、输出规则、上下文块格式和安全边界。

示例职责：

```text
你是本地桌宠式窗口 Copilot。
你会收到 profile/context/dialogue 三类输入。
不要输出 JSON、日志、接口名。
无法确定时说明不确定。
不要声称能自动点击、输入或操作电脑。
```

### 2.2 profile_packet

来源：本地 md profile 库，由 WebUI 编辑。

建议目录：

```text
backend/data/profiles/default/
  ASSISTANT.md
  USER.md
  MEMORY.md
```

文件职责：

| 文件 | 作用 | 是否第一阶段启用 |
|------|------|------------------|
| `ASSISTANT.md` | 助手名字、性格、语气、回答风格 | 是 |
| `USER.md` | 用户偏好、常用工作方式、语言偏好 | 是 |
| `MEMORY.md` | 人工确认后的长期记忆 | 暂缓，只预留 |

注意：

- `ASSISTANT.md` / `USER.md` 是 profile，不是 base system prompt。
- 修改 profile 会改变 profile_packet，因此可能使 profile 之后的 KV 失效，但不会污染 base_prefix。
- 第一阶段只支持 WebUI 手动编辑，不允许模型自动写长期记忆。

### 2.3 context_packet

来源：运行时服务。

内容：

```text
当前窗口摘要
最近窗口摘要
相关短期记忆
当前 ObservationCard 的低噪声字段
```

约束：

- 按 token/字符预算截断。
- 同一窗口重复摘要要去重或合并。
- 高风险/隐私窗口不注入截图内容。
- 只做文字上下文，不在对话默认附带截图。

### 2.4 dialogue_tail

来源：对话历史。

要求：

- 保持真实 messages 结构：
  `user -> assistant -> user -> assistant -> 当前 user`
- 不再把历史拼成单条 user 文本。
- 只注入最近 N 轮，N 由 WebUI 配置。

---

## 3. WebUI Profile 编辑

新增 WebUI 页签：

```text
Profile
```

第一阶段字段：

```text
当前 profile 名称：default
ASSISTANT.md 编辑器
USER.md 编辑器
保存按钮
重载按钮
context preview：显示下一次对话会注入的 profile/context
```

新增 API：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/webui/profile` | 返回当前 profile 的 md 文件内容 |
| PUT | `/api/webui/profile` | 保存 `ASSISTANT.md` / `USER.md` |
| POST | `/api/webui/profile/reload` | 重新读取 profile 并清除相关缓存 |

保存规则：

- 只允许写入 `backend/data/profiles/<profile_name>/` 下的 md 文件。
- 文件大小限制：单文件建议 8KB 以内。
- WebUI 保存后不修改 base_prefix。
- 保存后下一次对话使用新 profile_packet。

---

## 4. 双线 Agent 拆分

当前状态：

```text
WindowAnalysisService 负责识图摘要
AssistantChatService 负责对话
两者已经职责分离，但还不是两个独立 runtime
```

目标拆分：

```text
ObservationAgent
  输入：截图 + ObservationCard
  输出：WindowSummaryRecord
  模型输入：视觉 prompt + image
  写入：latest_analysis / window:summaries / session memory

ChatAgent
  输入：question + profile_packet + context_packet + dialogue_tail
  输出：assistant answer
  模型输入：默认纯文本 messages
  写入：chat current / chat history / session memory
```

### 4.1 ObservationAgent

职责：

- 截图去重。
- 调用 VLM 生成详细窗口摘要。
- 写 `WindowSummaryStore`。
- 写 working observation 和 analysis summary memory。
- 不生成候选问题。
- 不参与用户多轮对话。

### 4.2 ChatAgent

职责：

- 暂停自动观察，避免上下文漂移。
- 读取 latest summary / recent summaries / session memory / profile。
- 构造 KV cache 友好的 messages。
- 流式回答用户问题。
- 写 chat history 和 question/answer memory。
- 用户关闭对话或点击观察时恢复自动观察。

---

## 5. KV Cache 设计边界

第一阶段只做 prompt 结构准备，不承诺 llama.cpp slot 级持久缓存。

目标消息结构：

```text
messages[0] system: base_prefix
messages[1] user: <profile_packet>
messages[2] user: <context_packet>
messages[3..] user/assistant dialogue_tail
```

如果后续 llama.cpp server slot / cache API 可稳定使用，再升级为：

```text
chat_slot:
  预热 base_prefix
  profile 变化时重建 profile cache
  每次问题只追加 context_packet + dialogue_tail

observation_slot:
  独立视觉链路
  不与 chat_slot 共用对话 cache
```

当前必须避免：

- 把当前窗口摘要放进 base_prefix。
- 把可编辑人格直接拼进 base system prompt。
- 观察识图和用户对话共用同一段会话历史。
- 把 KV cache 当作 memory。

---

## 6. 与 Hermes 的借鉴关系

借鉴：

- 用 md 文件承载人可读、可编辑的长期 profile。
- 把记忆/用户偏好从固定系统提示词里拆出来。
- 让上下文构造有清晰层次，而不是一锅端拼 prompt。

不照搬：

- 不让模型第一阶段自动写 `MEMORY.md`。
- 不引入复杂 memory provider。
- 不做工具型长期记忆检索。
- 不做重型 session search / FTS5。

原因：

桌宠主目标是快、轻、可调试。先让 profile 和 context 分层稳定，再考虑更复杂的 agentic memory。

---

## 7. 实施顺序

### Phase 1：Profile md 库

验收：

- 创建 `backend/data/profiles/default/ASSISTANT.md` 和 `USER.md`。
- WebUI 可编辑并保存。
- 对话 context preview 能看到 profile_packet。
- base_prefix 不因保存 profile 改变。

### Phase 2：Prompt Builder 分层

验收：

- 新增 `ChatPromptBuilder` 或等价模块。
- 输出结构固定为 `base_prefix + profile_packet + context_packet + dialogue_tail`。
- 单元测试验证：窗口摘要变化时 base_prefix 完全不变。

### Phase 3：Agent Runtime 命名拆分

验收：

- `ObservationAgent` 包装当前 `WindowAnalysisService` 主链路。
- `ChatAgent` 包装当前 `AssistantChatService` 主链路。
- 两条线的输入、输出、状态和 prompt 构造边界清楚。

### Phase 4：Cache-aware 调度

验收：

- 明确 `base_prefix_hash` / `profile_hash` / `context_hash`。
- context preview 展示这些 hash。
- 为后续 llama.cpp slot cache 预留接口，但不做虚假缓存承诺。

---

## 8. 当前不做

- 不做 Redis / PostgreSQL / Docker。
- 不做 embedding / vector db。
- 不做 OCR/UIA 主链路。
- 不让模型自动修改 profile md。
- 不把观察线改成会自主规划的 agent。
- 不把对话线默认重新送截图。

