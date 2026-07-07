# 上下文、观察与工具主线 Spec

更新时间：2026-07-05

状态：当前权威工程 spec。后续修改代码前必须先对齐本文；历史 spec 中关于 planner、三工具、关键词路由、窗口摘要默认注入的内容不再指导实现。

## 1. 目标

当前主线不是“把更多摘要塞进 prompt”，而是建立一条可审计、少分叉、能让模型自己决定何时取证的链路：

```text
观察线：目标窗口截图 -> VLM 结构化观察 -> SQLite 记录 + 截图文件

对话线：用户问题 + profile + 当前会话历史
      -> 模型按需调用 memory.search(query)
      -> 工具返回相关观察/记忆证据
      -> 模型基于证据回答

调试线：WebUI 高级页查看 SQLite 中的观察字段、截图、工具调用轨迹
```

核心约束：

- 模型可见工具只保留 `memory.search(query)`。
- 移除独立 planner。不要再让一个模型先做工具规划、另一个模型回答。
- 不默认注入 `window:latest_analysis` 或 `window:summaries` 到对话上下文。
- 不再用固定中文方位词自动裁剪。
- 不再用关键词或 substring 做情绪、意图、记忆检索。
- 不用 fallback 堆叠掩盖主链路问题。缺截图、缺观察、工具失败都要明确暴露。

## 2. 双线职责

### 2.1 观察线

观察线只负责生成证据，不负责对话。

输入：

- 一个明确目标窗口。
- 原始截图。
- 窗口 metadata：标题、进程、bounds、hash、捕获时间。

处理：

- 使用 VLM 按 `experiments/prompts/analyze_window_v2.txt` 生成结构化观察。
- 观察内容必须尽量保留可见文字、区域、UI 元素、实体和不确定区域。
- `summary` 只是索引字段，不是最终答案来源。

输出：

- 写入 `window:latest_analysis`。
- 写入 `window:summaries` 滚动观察记录。
- 保存截图 PNG 文件。
- 可以写入短期 working observation，但不得自动污染长期记忆。

观察线不做：

- 不生成候选问题。
- 不主动给用户建议。
- 不写长期 profile。
- 不负责回答用户追问。

### 2.2 对话线

对话线只负责承接用户问题和组织回答。

默认注入：

- 稳定 system prompt。
- 用户/助手 profile。
- 当前运行期会话历史。
- 当前用户问题。

默认不注入：

- 当前窗口结构化观察。
- 最近窗口观察。
- 窗口摘要历史。

模型如果需要窗口、页面、历史观察或记忆证据，必须自己调用：

```text
memory.search(query)
```

#### 2.2.1 tool probe 职责分离 + 流式工具调用

对话线采用 probe → stream(+可循环) 的多阶段模型调用：

- **probe 阶段**（`complete_chat_response` + `tools`）：让模型决定是否调用 `memory.search`。probe 返回的 `content` 不是最终答案——它可能是思考过程、工具调用意图的自然语言描述、或空。只有 `tool_calls` 字段是 probe 的有效产出。probe 无 `tool_calls` 时 `content` 一律丢弃。

- **stream 阶段**（`stream_chat` + `tools`）：生成面向用户的最终答案。**stream 始终携带 tools 参数**，模型在流式输出中仍可发起 tool_calls。

  - 如果 probe 发出了 `tool_calls`：执行工具后，stream 携带工具结果。
  - 如果 probe 没发出 `tool_calls`：stream 仍然有 tools，模型可在流式阶段自行决定是否调用工具。
  - **如果 stream 输出中检测到 tool_calls**：暂停文本 yield → 执行工具 → 将结果追加到 messages → 用更新后的 messages 重新 stream → 继续产生最终答案。
  - 流式工具调用最多 2 轮（防止无限循环）。

这个设计解决了 MiniCPM-V function calling 不稳定的现实：即使 probe 阶段失败，stream 阶段仍有第二次机会调 memory.search。

禁止：把 probe 阶段返回的 `content` 当作最终答案直接返回给用户。小 VLM 的 function calling 不稳定，模型有时会把调用意图写成自然语言（如"调用 memory.search 检查窗口内容"）而不是结构化 `tool_calls`。如果把这个 content 当答案，用户会看到一句工具调用描述而非真正回答。

probe 阶段的 `content` 在无 `tool_calls` 时一律丢弃，最终答案由 stream 阶段生成。这是清晰的职责分离：probe 决策，stream 产出。

对话历史策略：

- 当前会话内保留连续 user/assistant messages。
- 后端重启或用户清空会话后，历史必须清除。
- 不把旧会话自动恢复成当前上下文，除非用户明确要求查历史。

## 3. 存储边界

所有运行时 JSON 状态使用本地 SQLite RuntimeStore：

```text
backend/data/runtime/runtime.sqlite3
```

截图文件不存入 SQLite，只保存到文件系统：

```text
backend/data/captures/*.png
```

SQLite 只保存截图路径、hash、尺寸和窗口信息。

### 3.1 `window:latest_analysis`

职责：当前最新一次 VLM 结构化观察。

语义：

- 代表最近一次成功观察，不等于“用户当前正在看的窗口”，除非观察刚刚成功完成。
- 用于 WebUI 当前观察展示、工具检索候选、调试。
- TTL 过期后不应伪装为当前状态。

必须包含：

```text
analyzed_at
observation/window metadata
analysis.summary
analysis.key_points
analysis.regions
analysis.visible_text
analysis.ui_elements
analysis.entities
analysis.uncertain_areas
screenshot_path
screenshot_hash
window_bounds
process_id
vision_input
```

### 3.2 `window:summaries`

职责：滚动结构化观察历史。

兼容原因保留 key 名称，但产品和代码注释中应逐步称为“窗口观察记录”，不要再把它理解成短摘要。

每条记录必须包含：

```text
record_id
created_at
app_name
window_title
window_type
summary
key_points
regions
visible_text
ui_elements
entities
uncertain_areas
screenshot_path
screenshot_hash
window_bounds
process_id
vision_input
```

存储原则：

- 不做破坏性截断。完整字段进入 SQLite。
- UI 可以折叠展示，但必须能展开查看原始字段。
- 滚动上限只限制记录条数，不在写入时裁掉结构化字段。

### 3.3 memory

memory 分两类：

```text
memory:working:observation
memory:items
```

`memory:working:observation` 是当前工作观察卡片，适合做低噪声状态提示，不是完整视觉证据。

`memory:items` 是短期/稳定记忆项，只保存用户偏好、项目决策、明确要记住的事实。

规则：

- 自动观察结果不应直接写入 `memory:items`。
- 用户没有明确要求“记住”时，不写长期记忆。
- 观察记录的权威来源是 `window:latest_analysis` 和 `window:summaries`，不是 memory。

## 4. 手动观察与窗口切换

点击“观察”必须稳定切换到用户真正想看的窗口。

正确行为：

```text
用户点击观察
-> 对话窗隐藏或从屏幕捕获中排除
-> 等待窗口栈稳定
-> 捕获最近非 Local Window Copilot 目标窗口
-> VLM 分析
-> 写入 window:latest_analysis 和 window:summaries
-> UI 显示新 record_id / title / screenshot
```

目标窗口选择原则：

- 优先使用打开对话窗前记录的最后一个非 Copilot 前台窗口句柄。
- 其次使用隐藏对话窗后当前前台非 Copilot 窗口。
- 再其次枚举顶层可见窗口，跳过 Local Window Copilot / Floating Chat / WebUI 调试窗。
- 如果没有可靠目标，返回明确失败，不抓自己的对话窗，也不复用旧观察。

接口原则：

- `/api/assistant/observe` 不能只返回“已触发”。它必须让前端能知道观察是否完成、写入了哪条记录、失败原因是什么。
- 可以同步等待一次观察完成，也可以返回 job id；但 UI 必须展示最终状态。
- 成功后必须能在 WebUI 高级观察面板看到新增记录和对应图片。

## 4.1 自动观察与窗口解析

自动观察和手动观察必须共用同一个窗口解析器，不能在 watcher、桌宠、WebUI 中各自堆判断。

窗口解析顺序：

```text
1. 如果调用方提供了 preferred_window，且仍是可见、有效、非 Copilot、非低优先级运行时窗口，优先使用它。
2. 读取 Windows 前台窗口句柄；如果句柄存在，先经过统一的 Copilot/运行时排除逻辑。
3. 如果 GetForegroundWindow 返回 0，不能直接让观察失败；必须枚举顶层可见窗口，选择第一个可捕获的非 Copilot 窗口。
4. 如果只剩后端命令行、服务日志等低优先级运行时窗口，只能作为最后兜底候选。
5. 如果没有可靠目标，返回明确错误，不复用旧观察，不伪装成已观察。
```

窗口排除规则只能用于保护主线，不能演变成硬编码业务理解：

- 可以排除 Local Window Copilot 自己的浮窗、聊天窗、WebUI 调试窗。
- 可以降低后端 uvicorn / llama-server / dev 脚本窗口优先级。
- 不允许按“左侧/右侧/代码/Codex”等内容关键词决定观察目标。

聊天窗口规则：

- 打开聊天窗口不得自动暂停 watcher。
- 用户点“观察”时，可以短暂隐藏聊天窗口并触发一次同步观察；观察结束后按配置恢复自动观察。
- `/api/assistant/pause` 只能作为显式调试/用户控制入口，不得被“打开聊天”隐式调用。
- 对话生成期间如果为了避免并发分析临时 stop watcher，必须在 `finally` 中恢复。

失败分型：

```text
capture_failed: 没拿到可捕获窗口或截图失败
vision_failed: VLM 请求失败
vision_length: VLM 命中输出上限但未能解析为结构化 JSON
vision_parse_failed: 响应不是可解析结构化观察
store_failed: latest/summaries 写入失败
```

日志和 UI 必须显示真实失败点。不能用“当前窗口可能是...”这类泛化回答遮盖观察线失败。

## 4.2 VLM 输出与截断约束

MiniCPM-V 观察输出应靠 schema 约束和清晰 prompt 收敛，而不是靠无限增大 `max_tokens`。

要求：

- 观察调用使用 JSON schema / response_format 约束结构化输出。
- `analyze_max_tokens` 只设为模型能稳定完成结构化观察的合理上限；当前建议 8192。
- 如果响应 `finish_reason=length` 且不能解析为完整 `WindowAnalysis`，必须视为失败并写日志。
- 如果响应虽然非 `stop` 但已经能解析成完整结构化观察，可以接受并记录 warning。
- 不允许把超长自然语言原文当作观察成功写入。

### 4.2.1 Prompt 契约与字段数量约束

观察 prompt 的字段数量要求必须匹配小 VLM 的稳定输出能力。运行时数据显示：

- 成功输出通常 1000-2000 tokens，生成 5-7 条 key_points、3-5 个 regions。
- 失败输出打满 max_tokens（退化重复），根因是 prompt 要求的嵌套字段数量超出模型稳定输出能力。

Prompt 字段数量约束原则：

- prompt 中"通常 X 到 Y 条"的措辞会被小 VLM 当作硬性下限。下限过高会触发模型填充退化。
- 数量下限应基于成功输出的实际分布设定，而非理论最大值。
- `candidate_questions` 字段必须为空数组，prompt 不要求模型生成候选问题。
- 嵌套深度（regions 内的子数组）是退化触发的主要因素，应优先控制。
- 字段数量约束属于 prompt 契约管理，不是硬编码——它定义的是模型与 prompt 之间的接口协议。

### 4.2.2 字段使用分析

观察 prompt 中每个字段的下游消费方决定其必要性：

| 字段 | 下游消费方 | 必要性 |
|------|-----------|--------|
| `window_type` | 工具检索分类、WebUI 展示 | 必要 |
| `summary` | 工具检索 rank_text、WebUI 展示、对话 agent 上下文 | 必要 |
| `key_points` | 工具检索 rank_text、WebUI 展示 | 必要 |
| `regions` | WebUI 展示（可展开）、工具检索 rank_text | 必要但可精简 |
| `regions[].visible_text` | 工具检索 rank_text | 必要 |
| `regions[].ui_elements` | 工具检索 rank_text | 必要 |
| `regions[].uncertainty` | WebUI 展示 | 必要（诚实标注不确定区域） |
| `visible_text` | 工具检索 rank_text、WebUI 展示 | 必要 |
| `ui_elements` | 工具检索 rank_text、WebUI 展示 | 必要 |
| `entities` | 工具检索 rank_text | 必要 |
| `candidate_questions` | 无下游消费方，spec §2.1 要求为空 | 移除生成要求 |
| `caution` | WebUI 展示、对话 agent | 必要 |
| `uncertain_areas` | WebUI 展示、工具检索 | 必要 |

`candidate_questions` 已在 spec §2.1 明确"不生成候选问题"。prompt 中该字段要求保持为空数组即可，不消耗模型生成预算。

### 4.2.3 废弃的 max_tokens 策略

曾经将 `analyze_max_tokens` 从 8192 提升到 16384 以"解决"截断问题。这是症状端修复：

- 成功输出只有 1000-2000 tokens，8192 已是 4-8 倍余量。
- 失败时模型进入退化重复，增大 max_tokens 只延长了失败时间。
- 正确做法是精简 prompt 契约让模型不退化，而非给退化更多空间。

`analyze_max_tokens` 回归 8192。不允许通过增大 max_tokens 掩盖 prompt 契约问题。

## 4.3 实施与验收分工

为了避免调试动作消耗过多上下文，后续按这个分工推进：

```text
Codex：落实 spec、修主线代码、补清晰日志和可审计状态。
用户：负责本机重启服务、切换窗口、点击观察、查看 WebUI/桌宠表现等 smoke 操作。
```

Codex 在无必要时不反复启动服务、不长时间轮询、不跑完整测试矩阵；只做轻量语法/局部一致性检查。

## 4.5 真实模型上下文校验

WebUI 和 `.env` 中的 `minicpm_ctx_size` 只是期望配置，不代表正在运行的 `llama-server` 已经按该值启动。

运行时必须在复用现有模型服务前读取 `/props` 或 `/v1/models` 中的真实 `n_ctx`：

- 如果真实 `n_ctx` 小于配置值，不能静默复用该服务。
- 必须明确报错，提示存在旧的低上下文 `llama-server`。
- 由后端自己启动的模型服务，配置变化后可以先停止再重启。
- 对话主线和观察主线都必须经过同一运行时校验，避免观察正常、对话却打到旧服务。

## 4.6 助手状态与 transient 状态管理

`analyzing` 和 `observing` 是 transient 状态——语义为"正在做某事"，只在进程运行期间有效。

规则：

- 进程启动时，从 SQLite 恢复的 transient 状态（`analyzing`、`observing`）必须重置为 `idle`。
- 重置时记录 reason 标明来源（如 `startup-reset-from-analyzing`），便于调试。
- `error` 状态可以跨进程保留，因为错误信息有持久调试价值。
- `idle`、`privacy` 等非 transient 状态可以跨进程恢复。
- 不允许在进程重启后保留 `analyzing` 状态——这是对能力的伪装，违反"不伪装能力"原则。

## 5. 唯一工具：`memory.search(query)`

### 5.1 模型可见 schema

模型只看到一个工具：

```text
memory.search(query: string)
```

OpenAI function 名称可以继续使用兼容形式 `memory_search`，但 UI 和文档统一称为 `memory.search`。

### 5.2 工具职责

`memory.search` 是证据检索工具，不是固定上下文拼包。

可检索来源：

- `window:latest_analysis`
- `window:summaries`
- `memory:working:observation`
- `memory:items`
- profile
- 当前运行期对话历史

返回内容必须与 query 相关，并标明来源。

不允许：

- 每次固定返回 latest + recent + memory 大礼包。
- 根据硬编码中文关键词决定“当前窗口/历史窗口/记忆”。
- 用 substring 命中来判断情绪、意图或长期记忆相关性。
- 工具失败后静默退回普通闲聊。

### 5.3 检索实现要求

检索应拆成 provider + ranker：

```text
providers: 读取候选证据
ranker: 根据 query 选择最相关证据
formatter: 输出给模型的结构化工具结果
```

当前实现的 ranker 选择：

- SQLite FTS5 BM25：在内存 SQLite 中对候选证据建临时 FTS5 索引，用 BM25 打分排序。
- 中文分词采用 bigram（双字滑窗）方案：CJK 字符按重叠 2 字 token 分词，ASCII 整词保留。这是中文 IR 的标准做法，不需要外部分词器依赖。
- 候选集通常 30-40 条（window:summaries 30 条 + latest 1 条 + memory items 数条），BM25 在此规模下区分度充分。
- 零 LLM 调用：ranker 是确定性算法，不依赖模型输出 JSON，不引入 VLM 不稳定性。

不可接受：

- `if "当前" in query` 这类意图判断。
- `if "左边" in query` 这类固定方位裁剪。
- `query in text` 这类 substring 记忆检索。
- 用同一 VLM 做 reranker：观察线已证明该模型有高概率无法稳定输出 JSON，排名器复用它会导致连锁失败。
- 工具失败后静默退回普通闲聊。

### 5.3.1 废弃的 LLM reranker 方案

曾经使用同一 MiniCPM-V 作为 memory.search 的 reranker，要求模型输出 `{"ids":[...], "notes":{...}}` 的 JSON。

废弃原因：

- 排名器复用观察线的 VLM，形成连锁失败：VLM JSON 不稳定 → 排名器失败 → memory.search 返回空 → 模型无窗口上下文。
- 候选集只有 30-40 条，LLM reranker 的语义理解优势在此规模下无法体现，反而引入不可靠性。
- LLM reranker 增加 12-30 秒延迟（串行模型调用），BM25 为毫秒级。
- `AgentToolRuntime` 的 `ranker_client` 参数已移除，不再注入 VLM 客户端作为排名器。

### 5.4 工具返回格式

工具结果返回 JSON 文本，建议结构：

```json
{
  "query": "...",
  "results": [
    {
      "source": "window_observation",
      "record_id": "...",
      "created_at": "...",
      "app_name": "...",
      "window_title": "...",
      "screenshot_path": "...",
      "screenshot_hash": "...",
      "content": {
        "summary": "...",
        "key_points": [],
        "regions": [],
        "visible_text": [],
        "ui_elements": [],
        "entities": [],
        "uncertain_areas": []
      },
      "selection_note": "为什么这条证据和 query 相关"
    }
  ],
  "missing": [],
  "warnings": []
}
```

返回限制：

- 默认返回最相关的少量记录。
- 单条记录尽量保留结构化字段。
- 如需省略，省略发生在工具输出格式化阶段，不发生在 SQLite 存储阶段。
- 如果没有证据，返回空 results 和明确 missing，不编造。

## 6. Prompt 与上下文预算

上下文预算只在模型调用边界生效，不在存储层生效。

要求：

- Base system prompt 稳定，不包含当前窗口观察。
- profile 是低频动态上下文，可以注入。
- 当前窗口和最近窗口观察只通过工具进入回答。
- 工具结果可以折叠、限量，但必须保留 record_id 和来源。
- WebUI 中所有折叠内容必须可展开查看完整原文。

### 6.1 prefix cache 冻结快照

llama.cpp 本地模型依赖 prompt 前缀的天然稳定性命中 KV cache。为保证 prefix cache 命中率：

- **会话级冻结**：`ChatAgent` 在首次 `ask` 时冻结 `profile_packet` 字符串，整个会话内复用同一字符串对象，不每次重新读文件拼接。
- **字节级一致**：system prompt + profile packet 在同一会话内必须字节级完全一致，不允许中间多空格、换行差异。
- **刷新时机**：profile 文件变更后，下次会话（`ChatAgent` 重建）自然刷新。会话中途不热更新。
- 禁止：在会话中途动态检测 profile 变更并热更新 prompt（破坏 cache）。

## 6.2 跨会话对话检索

`memory.search` 的候选集必须包含**所有历史对话**，不仅是最近 N 条：

- 对话结束时（`session_finished`）把 session 写入持久 FTS5 表 `chat_history_fts`。
- FTS5 列：`session_id`（UNINDEXED）、`search_text`（bigram 分词后的 question + answer）。
- `memory.search` 候选集加入 `chat_history_fts` 的 BM25 检索结果。
- 检索结果作为 `source: "assistant:chat:history"` 类型候选，与 window:summaries、memory:items 平等参与 BM25 排名。
- 禁止：把所有历史对话全量注入上下文（会爆 context）。只通过工具按需检索。

禁止：

- 在各处设置互相叠加的字符截断，导致存储和调试都看不到完整内容。
- 把 `summary` 当作完整视觉证据注入回答。
- 为了省 token 在写入 SQLite 前裁剪字段。

## 7. WebUI 高级观察面板

WebUI 高级设置必须提供“观察存储”调试面板。

### 7.1 面板内容

必须能查看：

- 当前 `window:latest_analysis`
- 最近 `window:summaries`
- 每条记录对应截图
- 原始 JSON 字段
- `vision_input` 尺寸和模型输入参数
- screenshot path/hash/bounds
- 最近工具调用 trace

每条观察记录展示：

```text
created_at
record_id
app_name
window_title
window_type
screenshot thumbnail
summary
key_points
regions
visible_text
ui_elements
entities
uncertain_areas
raw JSON
```

### 7.2 API

建议增加：

```text
GET /api/webui/observations/latest
GET /api/webui/observations?limit=30
GET /api/webui/observations/{record_id}
GET /api/webui/observations/{record_id}/image
GET /api/webui/tool-traces?limit=30
```

图片接口要求：

- 只能读取 `backend/data/captures` 下的文件。
- 用 record_id 映射截图路径，不接受任意用户传入路径。
- 找不到图片时返回明确错误。

### 7.3 对话过程展示

对话框和 WebUI 可以模仿 Codex 的折叠过程视图，但只展示可审计事件：

- 模型是否调用了 `memory.search`。
- 工具 query。
- 工具返回了哪些 record_id/source。
- 最终回答使用了哪些证据。

### 7.4 清理接口

WebUI 必须提供以下清理接口，允许用户手动重置运行时状态：

```text
POST /api/webui/memory/clear          — 清空 memory:items
POST /api/webui/runtime-events/clear   — 清空 runtime_events 全表（含 trace）
POST /api/webui/reset-all              — 一键全清（对话+观察+记忆+日志+trace+FTS5）
```

要求：

- 每个接口返回清理条数，便于用户确认。
- `reset-all` 是原子操作：要么全清成功，要么都不清。
- 清理后 `assistant:state` 重置为 idle。
- 截图文件不在清理范围（保留用于人工排查）。

不展示或伪造不可审计的 chain-of-thought。可以展示“过程摘要”和工具轨迹。

## 8. 必须删除或禁止回归的设计

以下内容不得重新引入：

- `AgentOrchestrator` / 独立 planner。
- 多个模型可见工具：`screen.look`、`memory.remember` 等。
- `companion / work_lens / visual_answer / text_answer` 四路模式。
- 固定中文方位关键词自动裁剪。
- 情绪、意图、记忆检索的 substring/keyword 判断。
- 自动把窗口观察写入长期 memory。
- 当前/最近窗口观察默认注入回答 prompt。
- 缺图、缺观察时用泛泛文本 fallback 继续回答。
- 用同一 VLM 做 memory.search 的 reranker（连锁失败源）。
- 通过增大 `analyze_max_tokens` 超过 8192 来掩盖 prompt 契约问题。
- `AgentToolRuntime` 的 `ranker_client` 参数注入。
- 把 tool probe 阶段返回的 `content` 当作最终答案直接返回（小 VLM 会把工具调用意图写成自然语言而非结构化 `tool_calls`）。

## 9. 一次性实施清单

后续代码修改按下面完整层次一次性落实，不再拆成容易遗忘的多阶段路线。

1. 观察触发
   - 修 `/api/assistant/observe` 的完成状态返回。
   - 记录并优先捕获最后一个非 Copilot 目标窗口。
   - 成功后返回 record_id/title/screenshot_hash。

2. 存储整理
   - 确认 `window:latest_analysis` 写入完整结构化字段。
   - 确认 `window:summaries` 每条记录含截图路径和详细字段。
   - 停止把自动观察写入 `memory:items`。

3. 工具重做
   - 保留唯一模型可见工具 `memory.search(query)`。
   - 改成 query 相关检索，不再固定拼包。
   - 工具结果带 record_id/source/screenshot 信息。
   - Ranker 使用 FTS5 BM25，不用 LLM reranker。

4. 对话上下文
   - 默认不注入 current/recent observation。
   - 重启或清空后不恢复旧历史。
   - 工具 trace 进入可折叠过程视图。

5. WebUI 高级观察面板
   - 展示 latest/history/raw JSON。
   - 展示截图缩略图和原图。
   - 展示工具调用 trace。

6. Prompt 契约与 max_tokens
   - 精简观察 prompt 中字段数量下限，匹配小 VLM 稳定输出能力。
   - `analyze_max_tokens` 回归 8192，不允许通过增大 max_tokens 掩盖 prompt 问题。
   - `candidate_questions` 保持空数组要求，不消耗模型生成预算。

7. 状态管理
   - 进程启动时重置 transient 状态（analyzing/observing）为 idle。
   - transient 状态不跨进程生命周期。

8. 对话线 probe 职责分离
   - probe 阶段只读取 `tool_calls`，不把 `content` 当最终答案。
   - 无 `tool_calls` 时丢弃 `content`，进入 stream 阶段生成答案。
   - 有 `tool_calls` 时执行工具，携带工具结果进入 stream 阶段。

9. 测试与验收
   - 单元测试覆盖写入字段、工具返回、默认不注入。
   - 手动 smoke 覆盖"切换到另一个窗口 -> 点击观察 -> WebUI 出现新截图和新 title"。
   - 搜索确认旧 planner、关键词路由、固定裁剪、LLM ranker 没有回归。

## 10. 验收标准

功能验收：

- 点击观察后，`window:latest_analysis` 更新时间和窗口标题必须变化到目标窗口。
- WebUI 高级观察面板能看到新记录、完整字段和对应截图。
- 用户问当前页面时，模型可以调用 `memory.search(query)` 获取观察证据。
- 工具返回相关证据，而不是固定返回一坨 latest/recent/memory。
- 没有观察证据时，助手明确说明缺少可用观察。
- VLM 分析成功率 > 80%（基于连续 10 次观察）。
- memory.search 工具零失败（确定性算法，不依赖 VLM JSON 输出）。
- 进程重启后助手状态为 idle，不卡在 analyzing。
- tool probe 无 `tool_calls` 时，probe 的 `content` 不作为最终答案返回；最终答案由 stream 阶段生成。

代码验收：

- 主对话链路不 import planner/orchestrator。
- 模型可见工具注册数量为 1。
- 对话 prompt 默认不含 current/recent observations。
- 存储层不做破坏性字符截断。
- 自动观察不写长期 memory。
- `AgentToolRuntime` 无 `ranker_client` 参数。
- `analyze_max_tokens` 不超过 8192。
- `assistant_state.py` 启动时重置 transient 状态。

调试验收：

- WebUI 能直接回答"SQLite 里到底存了什么观察字段"和"这条观察对应哪张图片"。
- 工具 trace 能回答"模型查了什么、拿到了哪条记录、为什么回答成这样"。
