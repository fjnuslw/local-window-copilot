# 对话上下文管理改造 Spec

更新时间：2026-07-06

状态：新增工程 spec。本文聚焦对话线的模型调用边界、长对话压缩、工具结果预算和可观测性。与 `context_observation_tool_mainline_spec_zh.md` 配合使用；观察线仍按现有主线生成证据，对话线在调用模型前完成上下文治理。

维护提醒：每次新增或修订 `docs/co-work/00*-trae-*.md` 协作任务后，先回到本文检查总目标、阶段边界、默认值和禁用模式是否需要同步更新。


协作任务状态：

- 001/002：`ContextTokenEstimator` 已完成，覆盖文本、JSON、图片、tool_calls 的 rough 估算。
- 003：`ContextAssembler` 预算账本已完成，支持 segment report、totals、over_limit 展示。
- 004：Token 预算接入 `inspect_context`，补显式 segment hints，旧 `chars // 2` 展示估算停止使用。
- 005：Compact 状态存储已完成，范围限定为 rolling summary、compact lock、compact metrics 的 RuntimeStore 读写封装。
- 006：CompactPlanner 已完成，范围限定为纯选择逻辑：计算 raw tail、uncovered sessions、source budget 与本批 source sessions。
- 007：CompactSummarizer prompt/state builder 已完成，范围限定为 prompt 构建、summary 校验和 success state 构造。
- 008：CompactExecutor 已完成，范围限定为单次执行入口、注入式 model client、lock、metrics 与 RuntimeStore 提交。
- 009：Compact 触发接入与 summary 注入已完成，范围限定为自动/手动触发、trace、summary 注入和清理联动。
- 010：WebUI compact 面板与运行观测已完成，范围限定为状态接口、上下文页面板、手动按钮与 trace 可读性。
- 011：Tool result 预算化已完成，范围限定为工具结果进入 messages 前的单项与合计预算治理。
- 012：请求前预算拦截已完成，范围限定为模型调用前的 input_limit 检查、trace 与 session error。

上下文管理主线完成判定：001-012 已完成。当前具备 rough token 估算、segment 账本、inspect 展示、compact state/planner/summary/executor、自动/手动 compact、WebUI 观测、tool result 预算化，以及模型调用前 input_limit 拦截。后续可单独推进响应 usage 回填与更细粒度 UI 可观测。
> **重要补充（2026-07-06，§A–§H）**：调研确认系统已有持久 FTS5 索引 `chat_history_fts`（`backend/app/services/chat_history_index.py`），`memory.search` 已能经 BM25 逐字召回所有历史 session（见 `agent_tools.py` `_search_chat_history_fts`）。§7 原设计的 rolling_summary 调整为轻量状态指针：逐字细节交给 FTS5 按需召回，summary 只承载"在做什么、卡在哪、下一步查什么"+指针。
>
> 据此本文末尾新增八个补充条款作为 compact 机制的权威定义：
> - **§A** Compact 触发与执行模型（细化 §7 触发/异步/单飞/失败处理）
> - **§B** Minimal 指针式 summary 模板（替换 §7 原 6 段模板）
> - **§C** 三层历史职责分工（raw tail / rolling_summary / chat_history_fts）
> - **§D** 摘要请求的独立预算
> - **§E** Prefix cache 与 summary 的 messages 位置
> - **§F** 默认值现实取值与流式 usage 回填
> - **§G** Compact 持久化与原子提交
> - **§H** Compact MVP 实施顺序
>
> 当 §A–§H 与正文（尤其 §6/§7/§9）冲突时，**以 §A–§H 为准**。

## 1. 背景

当前对话线暴露出两个结构性风险：

- `assistant_chat.inspect_context` 使用 `chars // 2` 估算 token，只服务于展示，未参与请求装配、裁剪和拦截。
- `build_chat_messages` 按最近 N 轮原样追加 user/assistant 历史，依赖轮数上限控制上下文规模，缺少 token 预算、滚动摘要和工具结果限量。

当对话变长、工具结果变大、结构化观察字段变密时，模型会遇到三类问题：

- 关键旧信息因固定轮数窗口丢失。
- 大工具结果挤占当前问题和回答空间。
- 请求接近 context 上限后才暴露错误，日志难以解释每个上下文段的占用。

补充现状（见 §C 三层职责分工）：对话历史的留存其实已分三层，但注入边界尚未划清——

- `assistant:chat:history`（`CHAT_HISTORY_KEY`）：runtime 存档，保留 `history_retention_limit`（默认 30）条，始终注入最近 `chat_history_turns`（默认 4）条作为 dialogue tail。
- `chat_history_fts`（`backend/app/services/chat_history_index.py`）：**持久** FTS5 索引，保存所有 session 原文，`memory.search` 已能经 BM25 按需逐字召回（`agent_tools.py` `_search_chat_history_fts`）。
- `assistant:chat:rolling_summary`（待新增）：压缩状态指针，始终注入。

本文的 rolling_summary 定位为活跃状态指针；`chat_history_fts` 保存老对话逐字检索入口。详见 §A/§B/§C。

## 2. 设计目标

- 在模型调用边界建立统一 `ContextBudget`，按 segment 记账、限额、输出报告。
- 用滚动摘要承接较老对话，保留最近尾部原文，保持当前任务连续性。
- 工具结果进入 messages 前完成预算化处理，保留 `record_id`、来源、时间和可追溯路径。
- 摘要失败、预算超限、工具结果过大都要写入结构化 trace，并向 WebUI 暴露。
- 存储层保存完整原文；模型输入层只发送预算内内容。
- 提示词和摘要模板使用直接、肯定、行动导向的表达，避免先否定再转折肯定的防守句式。

## 2.1 本地部署核心思想

本项目当前按本地 MiniCPM / OpenAI-compatible 调用链设计。上下文治理追求少路径、强可见、易调试。

- 单一模型请求路径：正常回答、compact 摘要、预算检查都走明确入口，不做隐藏模型切换、不做提示词替换、不做多 provider token 计数适配。
- rough estimate 是 MVP 控制指标：请求前用本地确定性估算做预算判断；响应里若已有 usage，只用于展示、校准和后续调参。
- compact 优先：估算器、预算报告、工具结果限量、WebUI trace 都服务于 compact 触发和质量验证。
- 完整历史留在存储层：RuntimeStore 与 FTS5 保存原文；模型输入只拿当前工作集、compact_state、recent tail 和按需检索结果。
- 图片和文档按固定成本估算：默认 2000 tokens，不把 base64 长度当成文本 token。
- 工具结果先限量再进入上下文：单条和总量都要可配置，超限时写 trace 并停止本轮请求。
- 失败路径透明：预算超限、compact 失败、summary 校验失败都给出明确错误、trace key 和相关 session_id。
- 表达风格保持直接陈述：写给模型和写给 Trae 的任务说明都要短、具体、可执行。

## 3. Hermes 借鉴点

Hermes 的运行时上下文治理有六个值得借鉴的机制：

- 有效输入预算：压缩阈值基于 `context_length - max_output_tokens - safety_margin`，预留回答空间。
- 预检估算与真实 usage 回填：请求前使用粗略估算触发压缩，请求后用 provider 返回的 `prompt_tokens` 更新状态。
- 头尾保护：保留 system/head 与最近 tail，中间区间进入摘要。
- 尾部按 token 预算保护：最近消息数量只是下限，主要依据 token 预算。
- 工具结果预处理：旧工具结果摘要化、重复内容去重、大 tool args 截断。
- 结构化摘要：保留 Active Task、Completed Actions、Blocked、Relevant Files、Remaining Work 等连续工作所需字段。

本项目采用本地轻量实现，保持单一 `memory.search(query)` 工具主线，不引入 Hermes 运行时依赖。Hermes/Claude Code 的多云、多模型、prompt cache、hook、session resume 等生产复杂度只作为参考，不进入本地 MVP。

## 4. 总体架构

新增模块：

```text
backend/app/services/context_budget.py
backend/app/services/context_summary.py
```

主要对象：

```text
TokenEstimate
  source: "rough" | "response_usage"
  tokens: int
  chars: int

ContextSegment
  kind: "system" | "profile" | "summary" | "history" | "memory" | "tool_result" | "question"
  role: "system" | "user" | "assistant" | "tool"
  label: str
  content: str | list | dict
  required: bool
  priority: int
  estimate: TokenEstimate
  metadata: dict

ContextBudgetReport
  ctx_size: int
  output_reserve: int
  safety_margin: int
  input_limit: int
  estimated_input_tokens: int
  over_limit: bool
  segments: list[segment report]
  actions: list[str]
```

调用链：

```text
ChatAgent._build_answer_context
-> ContextAssembler.collect_segments(...)
-> ContextBudget.plan(...)
-> ContextAssembler.to_messages(...)
-> VisionModelClient.complete_chat_response / stream_chat
-> 已有 usage 写回 ContextBudgetState
```

## 5. Token 估算

MVP 使用确定性的 rough 估算，来源必须标记为 `source="rough"`，严禁把 rough 值展示成真实 token。

建议公式：

```text
cjk_tokens      = CJK/Hangul/Kana 字符数
latin_tokens    = ceil(ASCII 字符数 / 4)
json_tokens     = ceil(JSON 标点和结构字符数 / 2)
message_overhead = 10
image_tokens    = 每张图片固定估算值，默认 2000
```

估算器需要支持：

- 字符串消息。
- OpenAI style multimodal parts。
- tool call envelope，包括 id、type、function.name、arguments。
- dict/list 内容先转为紧凑 JSON 再估算。
- 图片 part 识别 `image_url`、`image`、`input_image`，统一按固定成本计入 `image_tokens`。

请求完成后，如果响应包含 usage：

```text
last_prompt_tokens = usage.prompt_tokens
last_completion_tokens = usage.completion_tokens
last_total_tokens = usage.total_tokens
last_usage_source = "response_usage"
```

WebUI 同时展示 rough 与已有 usage 的最近值，避免把估算误读为事实。

## 6. 预算模型

新增配置建议：

```python
context_budget_enabled: bool = True
context_budget_safety_tokens: int = 8192
context_compaction_threshold: float = 0.50
chat_raw_tail_turns: int = 2
chat_history_budget_tokens: int = 16000
rolling_summary_target_tokens: int = 1600
memory_context_budget_tokens: int = 6000
tool_result_budget_tokens: int = 12000
tool_result_item_budget_tokens: int = 3000
```

基础计算：

```text
output_reserve = settings.answer_max_tokens
input_limit = settings.minicpm_ctx_size - output_reserve - context_budget_safety_tokens
compaction_threshold = input_limit * context_compaction_threshold
```

Segment 优先级：

| 优先级 | Segment | 规则 |
|---:|---|---|
| 100 | system | 必保留 |
| 95 | 当前用户问题 | 必保留 |
| 90 | profile | 保留；过长时只报警 |
| 80 | rolling summary | 控制在 `rolling_summary_target_tokens` 附近 |
| 70 | 最近原文历史 | 从新到旧加入，最多 `chat_raw_tail_turns` |
| 60 | memory items | 总量受 `memory_context_budget_tokens` 限制 |
| 50 | tool result | 总量和单条都受预算限制 |
| 30 | 调试说明 | 默认不进模型上下文 |

预算执行顺序：

1. 收集所有候选 segment。
2. 计算每段 rough tokens。
3. 先放入 required segment。
4. 加入 rolling summary。
5. 从新到旧加入最近原文历史。
6. 加入 memory/tool_result 的预算化文本。
7. 总量超过 `input_limit` 时停止请求，写 `context_budget.over_limit`，返回可解释错误。

请求禁止在 over-limit 状态下继续发送给模型。

## 7. 滚动摘要

RuntimeStore 新增 key：

```text
assistant:chat:rolling_summary:v1
```

结构：

```json
{
  "version": 1,
  "summary": "",
  "covered_session_ids": [],
  "updated_at": "ISO-8601 UTC",
  "source_session_count": 0,
  "estimate": {
    "source": "rough",
    "tokens": 0,
    "chars": 0
  },
  "last_error": null
}
```

触发时机：

- `_append_history()` 后检查历史预算。
- 未覆盖的 done session 数量超过 `chat_raw_tail_turns`。
- 原文历史 rough tokens 超过 `chat_history_budget_tokens`。

压缩范围：

- 保留最近 `chat_raw_tail_turns` 轮原文。
- 较老 done session 合并进 rolling summary。
- 已覆盖 session_id 不重复进入摘要。
- 存储层 `CHAT_HISTORY_KEY` 和 FTS5 历史索引保持完整。

摘要模板字段：

```text
## 当前任务
保留用户最新未完成请求的原文或等价短句。

## 已确认事实
列出已经从窗口、工具或对话确认的事实。

## 已完成动作
列出已经完成的动作、时间、目标和结果。

## 待解决问题
列出用户仍期待回答或处理的问题。

## 用户偏好
记录表达偏好、工程约束、禁用模式。

## 相关证据
保留 record_id、session_id、窗口标题、文件路径、错误文本。
```

摘要表达规则：

- 使用用户当前语言。
- 使用短句和具体名词。
- 保留文件路径、命令、错误文本、record_id。
- 敏感值写为 `[REDACTED]`。
- 避免道歉、安抚套话、免责声明。
- 避免先否定再转折肯定的防守句式。

摘要失败处理：

- 保持旧 rolling summary 原样。
- 不新增 covered_session_ids。
- 不丢弃原始历史。
- 写 `context_summary.failed` trace，记录错误类型和消息。
- 本轮上下文只使用已有 summary 与最近原文历史。

## 8. 工具结果预算化

工具结果追加到 messages 前必须通过 `ToolResultBudgeter`。

输入：

```text
tool_name
tool_call_id
raw_result
source records
remaining_tool_budget
```

输出消息内容：

```text
[tool: memory.search]
结果 1
- source: window:summaries
- record_id: ...
- time: ...
- title: ...
- snippet: ...
- screenshot_path: ...

结果 2
...

预算说明
- returned_items: N
- clipped_items: M
- estimated_tokens: K
```

规则：

- 每个工具调用最多 `tool_result_item_budget_tokens`。
- 本轮工具结果合计最多 `tool_result_budget_tokens`。
- 长字段生成 snippet；完整原文通过 record_id 在 WebUI 展开。
- JSON 结果必须保留 source、record_id、created_at、title、score。
- 截图只传路径、hash、尺寸，不把 base64 放入文本上下文。
- 工具执行失败写入结构化错误消息，保留 tool_call_id，停止本轮工具循环。

## 9. 对话消息装配

替换当前裸 `build_chat_messages` 的职责边界：

```text
build_chat_messages
  只负责把已经预算化的 segment 转成 OpenAI messages。

ContextAssembler
  负责收集 profile、summary、history、memory、tool result、question。

ContextBudget
  负责估算、裁剪、报告、超限拦截。
```

推荐装配顺序：

```text
system: BASE_PREFIX
user: profile_packet
user: rolling_summary（存在时）
user/assistant: 最近原文历史
user: memory/context packet（预算化）
tool: tool results（工具循环中追加）
user: current question
```

当前窗口观察继续通过 `memory.search(query)` 获取。直接注入当前窗口观察需要单独产品决策和预算说明。

## 10. 可观测性

新增 trace 事件：

```text
context_budget.assembled
context_budget.over_limit
context_summary.started
context_summary.succeeded
context_summary.failed
tool_result.budgeted
response_usage.updated
```

`inspect_context` 返回：

```json
{
  "ctx_size": 256000,
  "input_limit": 215040,
  "estimated_input_tokens": 18342,
  "usage_percent": 8.5,
  "estimate_source": "rough",
  "last_response_prompt_tokens": 17410,
  "segments": [
    {
      "kind": "profile",
      "label": "profile_packet",
      "tokens": 820,
      "required": true
    }
  ],
  "budget_actions": []
}
```

WebUI 高级页展示：

- 总预算、已用预算、输出预留、安全余量。
- segment 列表，按 token 占用排序。
- rolling summary 覆盖了哪些 session。
- 工具结果裁剪记录。
- 最近响应 usage。

## 11. 实施步骤

### 阶段 A：预算报告

- 新增 `context_budget.py`。
- 接入 `_build_answer_context`，生成 `ContextBudgetReport`。
- `inspect_context` 使用 report 计算 usage。
- 不改变 messages 内容，只增加 trace 和测试。

### 阶段 B：预算化消息装配

- 新增 `ContextAssembler`。
- `build_chat_messages` 接收预算后的 segment。
- 最近历史按 token 加入，超出时停止加入较老原文。
- 测试长中文、英文、JSON、tool_call envelope。

### 阶段 C：滚动摘要

- 新增 `context_summary.py`。
- `_append_history()` 后触发摘要检查。
- 摘要成功后更新 RuntimeStore key。
- 摘要失败保持旧状态并写 trace。

### 阶段 D：工具结果预算

- 在 `_execute_and_append_tools()` 后追加预算化步骤。
- `memory.search` 结果按 record_id/snippet/source 输出。
- WebUI 通过 record_id 查看完整原文。

### 阶段 E：超限拦截

- 请求前强制检查 `estimated_input_tokens <= input_limit`。
- 超限时停止模型调用，返回明确错误。
- trace 中记录最大 segment、建议操作和预算数字。

## 12. 验收标准

- 100 轮短问答后，模型输入仍包含 rolling summary 与最近原文历史。
- 单条 100KB 工具结果不会原样进入 messages。
- `inspect_context` 可显示每个 segment 的 token 估算。
- rolling summary 失败时原始历史仍完整保留。
- 响应包含 usage 后，下一轮可看到响应 usage 数值。
- WebUI 可从预算化工具结果跳转到完整 record。
- 新增测试覆盖中英文混合、JSON、工具结果、摘要失败、预算超限。

## 13. 禁止事项

- 禁止多分支旁路掩盖主链路失败。
- 禁止在 over-limit 状态下继续请求模型。
- 禁止把粗略估算展示为真实 token。
- 禁止把所有历史对话全量注入 prompt。
- 禁止在存储层删除或截断原始历史来节省模型输入。
- 禁止把截图 base64 放入对话文本上下文。
- 禁止摘要失败后改写 covered_session_ids。
- 禁止工具结果无上限进入 messages。
- 禁止系统提示词和摘要模板使用防守式转折表达。
## §A Compact 触发与执行模型

Compact 是本轮改造的核心生命周期。预算报告、响应 usage、工具结果限量、WebUI 审计都服务于 compact 的触发、质量和可解释性。

触发点：

- 自动触发：每轮对话完成后，先写 `CHAT_HISTORY_KEY`，再写 `chat_history_fts`；两处写入完成后检查是否需要 compact。
- 手动触发：未来 WebUI 或调试接口提供“立即 compact”入口，用于排查问题或强制刷新 summary。
- 写入完成后计算 `raw_tail + compact_state + profile + 当前问题` 的 rough tokens。
- 未覆盖的 done session 数量超过 `compact_uncovered_session_threshold`。
- 待注入历史 rough tokens 超过 `compact_history_trigger_tokens`。
- 用户或调试接口显式请求 compact 时按手动触发处理。

006 边界：

- `CompactPlanner` 是后端纯选择器，禁止注册为 Agent tool，禁止暴露给模型调用。
- 006 只产出 plan/report，不调用模型，不写 RuntimeStore，不修改聊天 messages。
- 自动/手动触发在 006 中只体现为 `force` 与 `trigger` 字段；真正执行摘要由后续 `CompactSummarizer` 负责。

执行模式：

- 单飞执行：同一时刻只允许一个 compact 任务处理当前会话历史。
- compact 运行期间，新问题继续使用旧 compact_state 与最近 raw tail。
- compact 成功后，下轮对话读取新 compact_state。
- compact 失败后，旧 compact_state 保持原样，covered_session_ids 保持原样，trace 记录错误。
- compact 任务必须有最大耗时，建议 MVP 为 90 秒。

新增 RuntimeStore key：

```text
assistant:chat:compact_lock:v1
assistant:chat:rolling_summary:v1
assistant:chat:compact_metrics:v1
```

`compact_lock`：

```json
{
  "owner": "assistant-chat",
  "started_at": "ISO-8601 UTC",
  "expires_at": "ISO-8601 UTC",
  "source": "auto|manual"
}
```

锁过期后允许新 compact 任务接管。

## §B Minimal 指针式 Summary 模板

rolling_summary 使用轻量状态指针，目标 800-1200 rough tokens。它记录当前任务、决策和检索指针；逐字内容由 `chat_history_fts` 按需召回。

模板：

```text
## 当前任务
用户最新未完成请求；保留原文中的关键名词、文件名、窗口名。

## 当前判断
已经确认的原因、决策和约束。

## 卡点
仍需验证、等待工具证据、等待用户确认的事项。

## 下一步检索指针
- session_id / record_id / source
- 关键词
- 需要重新读取的窗口、文件或工具记录

## 用户偏好
表达风格、工程约束、禁用模式。

## 最近完成
最近已经完成的动作与结果。
```

写作规则：

- 使用短句。
- 使用当前用户语言。
- 保留 `session_id`、`record_id`、文件路径、窗口标题、错误文本。
- 只记录能帮助下一轮继续工作的事实。
- 敏感值写为 `[REDACTED]`。
- 摘要文本保持直接、肯定、行动导向。

## §C 三层历史职责分工

历史上下文分三层：

| 层级 | 存储 | 进入模型方式 | 职责 |
|---|---|---|---|
| 最近原文尾部 | `CHAT_HISTORY_KEY` | 默认注入最近 `chat_raw_tail_turns` 轮 | 保留最新语气、最新问题、刚完成回答 |
| Compact 状态指针 | `assistant:chat:rolling_summary:v1` | 默认注入 | 保留当前任务线、卡点、检索指针 |
| 历史全文索引 | `chat_history_fts` | `memory.search(query)` 按需返回 | 召回旧对话逐字证据 |

规则：

- raw tail 只保留最近少量 done session 原文。
- compact_state 始终小于 `rolling_summary_target_tokens`。
- `chat_history_fts` 保存所有已完成 session 的可检索文本。
- compact_state 中的指针必须能带用户或模型回到 FTS/RuntimeStore 原文。
- 每次 compact 成功后，covered_session_ids 增量更新。

## §D 摘要请求的独立预算

Compact 摘要请求使用独立预算，独立于正常回答请求。

新增配置：

```python
compact_enabled: bool = True
compact_model_max_input_tokens: int = 24000
compact_model_max_output_tokens: int = 1600
compact_source_budget_tokens: int = 18000
compact_template_budget_tokens: int = 2000
compact_previous_summary_budget_tokens: int = 2000
compact_batch_session_limit: int = 12
compact_timeout_seconds: int = 90
compact_uncovered_session_threshold: int = 6
compact_history_trigger_tokens: int = 24000
```

输入装配：

```text
compact prompt =
  compact instruction template
  + previous compact_state
  + uncovered sessions（按时间正序，受 compact_source_budget_tokens 限制）
```

超出 `compact_source_budget_tokens` 时：

- 优先保留每个 session 的 question、answer 头尾、session_id、created_at。
- 超长 answer 使用 head/tail 摘要片段。
- 仍超出时分批 compact，下一次任务继续处理剩余 session。

摘要请求失败时：

- 保持旧 compact_state。
- 保持 uncovered sessions 继续等待下一次 compact。
- 写 `context_summary.failed` trace。
- WebUI 展示失败时间、错误类型、source session 数量。

## §E Prefix Cache 与 Summary 位置

稳定前缀只包含：

```text
messages[0] system: BASE_PREFIX
messages[1] user: profile_packet
```

约束：

- `BASE_PREFIX` 字节稳定。
- `profile_packet` 会话内冻结。
- compact_state 放在 profile 后面。
- raw tail 放在 compact_state 后面。
- 当前问题永远最后追加。

推荐顺序：

```text
system: BASE_PREFIX
user: profile_packet
user: compact_state
user/assistant: raw tail
user: current question
```

compact_state 更新只影响 profile 后的动态上下文。system/profile 的字节级前缀保持稳定，继续服务 llama.cpp prefix cache。

## §F 默认值现实取值与 Usage 回填

MVP 默认值采用保守输入上限，先跑通 compact 链路和观测：

```python
effective_input_limit_tokens: int = 64000
context_budget_safety_tokens: int = 8192
answer_max_tokens: int = 8192
chat_raw_tail_turns: int = 2
rolling_summary_target_tokens: int = 1200
memory_context_budget_tokens: int = 4000
tool_result_budget_tokens: int = 8000
```

`minicpm_ctx_size=256000` 作为服务端声明窗口记录进 report。日常请求使用 `effective_input_limit_tokens` 控制。经过 llama-server 实测确认耗时、显存和稳定性后，再提高有效输入上限。

Usage 回填：

- `stream_chat` 和 `complete_chat_response` 需要捕获响应 usage。
- 记录 `prompt_tokens`、`completion_tokens`、`total_tokens`。
- WebUI 同时展示 rough estimate 与已有 usage。
- compact 触发统一使用本轮 rough estimate；响应 usage 只用于观测和校准，不发起额外 token 计数请求。

## §G Compact 持久化与原子提交

Compact 成功提交必须满足：

- 新 summary 非空。
- covered_session_ids 只包含本次已写入 summary 的 session。
- compact_state 写入 RuntimeStore 成功。
- compact_metrics 写入 RuntimeStore 成功。
- trace 写入 `context_summary.succeeded`。

提交顺序：

```text
1. 读取旧 compact_state
2. 读取 uncovered sessions
3. 生成新 compact_state
4. 校验新 compact_state
5. 写 RuntimeStore 新 compact_state
6. 写 metrics
7. 释放 compact_lock
```

失败处理：

- 释放 compact_lock。
- 旧 compact_state 继续有效。
- uncovered sessions 继续保留。
- 错误写入 `context_summary.failed`。

`compact_metrics`：

```json
{
  "last_started_at": "ISO-8601 UTC",
  "last_finished_at": "ISO-8601 UTC",
  "last_status": "ok|error",
  "source_session_count": 0,
  "covered_session_count": 0,
  "summary_tokens": 0,
  "source_tokens": 0,
  "error_type": null,
  "error_message": null
}
```

## §H Compact MVP 实施顺序

MVP 优先实现 compact 闭环：

1. `ContextTokenEstimator`
   - 支持 string、dict/list、tool_call envelope。
   - 输出 segment token report。

2. `CompactStateStore`
   - 读写 `assistant:chat:rolling_summary:v1`。
   - 读写 compact lock 与 metrics。

3. `CompactPlanner`
   - 计算 raw tail、uncovered sessions、source budget。
   - 选择本批进入 compact 的 session。
   - 自动触发在每轮对话写入 `CHAT_HISTORY_KEY` 与 FTS5 后检查；手动触发由未来 WebUI 或调试接口发起。
   - 本阶段只返回 plan，不执行摘要、不写状态、不改变 messages。

4. `CompactSummarizer`
   - 007：已完成 §B prompt 构建、summary 输出校验、success state 构造。
   - 008：已完成注入式 model client、compact lock、metrics 与 RuntimeStore 提交。
   - 009：已完成自动/手动触发、trace 与下一轮 summary 注入。
   - 010：已完成 WebUI compact 面板、运行观测与手动按钮。
   - 011：已完成工具结果进入 messages 前的单项与合计预算治理。
   - 012：已完成模型调用前 input_limit 检查、trace 与 session error。
   - 使用 §D 独立预算。

5. `ContextAssembler`
   - 注入 system/profile/compact_state/raw_tail/current question。
   - 保持当前窗口观察经 `memory.search` 获取。

6. WebUI compact 面板
   - 展示 compact_state、covered ids、metrics、最近错误。
   - 提供手动触发 compact 的按钮。

上下文管理主线已收口；响应 usage 回填可作为后续观测增强单独推进。
