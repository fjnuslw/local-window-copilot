# WebUI 控制台 + 识图/对话职责分离 重构 Spec

> 版本：v0.1.0
> 日期：2026-07-03
> 范围：本次会话完成的两阶段重构（webui 控制台 + agent 职责分离）

---

## 1. 背景与目标

### 1.1 起点问题

改造前的 Local Window Copilot 存在两个明显短板：

1. **缺乏上下文管理界面**：所有模型参数、性格、记忆配置散落在 `.env` 与硬编码中，部署用户无法可视化调整，也无法查看对话历史与注入的上下文。
2. **对话 agent 不记得自己做过什么**：
   - 桌宠 UI 仍渲染"候选问题"按钮，交互冗余；
   - 对话历史以**文本拼接**形式塞进单条 user prompt，模型无法区分"自己之前的回答"与"用户当前问题"，导致追问时反复说同样的话；
   - 识图摘要 prompt 同时承担"描述窗口 + 生成候选问题 + 给建议"三件事，摘要不够详细，且与对话职责混杂。

### 1.2 设计目标

参考 Hermes-agent 的上下文管理思想，做两件事：

- **阶段一**：提供一个轻量、即插即用（仅依赖 Python）的 webui 控制台，支持配置可视化编辑、`.env` 写回 + 热重载、对话调试与上下文透视。
- **阶段二**：把"识图做摘要"与"和用户对话"拆成两个职责清晰的子系统：
  - **识图摘要服务**：单一职责，尽可能详细地描述窗口内容，结果存档到 SQLite，供对话 agent 检索。
  - **对话 agent**：真正的可迭代 agent，使用 **messages 多轮结构**传递对话历史，让模型"记得自己之前说了什么"，并能读取历史窗口摘要作为背景。

---

## 2. 阶段一：WebUI 控制台

### 2.1 设计原则

- **零外部依赖**：不引入 docker / node / 前端构建链，仅 FastAPI + 原生 HTML/JS，部署用户只需 Python 环境。
- **schema 驱动表单**：后端 `FIELD_META` 元数据描述每个字段的 key / label / type / group / description，前端按分组动态渲染，新增字段只需在后端加一条元数据。
- **写回 `.env` + 热重载**：配置变更通过 `dotenv.set_key` 写回 `backend/.env`，然后链式清除 `lru_cache` 单例，新配置立即生效，无需重启后端。

### 2.2 页面结构

webui 挂载在 `http://127.0.0.1:18080/`，包含两个页面：

| 页面 | 路径 | 功能 |
|------|------|------|
| 配置页 | `/` | 按 6 个分组（模型/上下文/记忆/性格/观察/运行时）渲染表单，支持在线编辑并写回 `.env` |
| 对话调试页 | `/`（tab 切换） | 与对话 agent 直接交互，查看流式回答、对话历史、上下文透视 |

### 2.3 配置 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/webui/config` | 返回所有字段（含当前值、默认值、env_key、分组） |
| PUT | `/api/webui/config` | 写回 `.env` 并热重载；只接受 `FIELD_META` 中声明的字段 |
| POST | `/api/webui/reload` | 手动触发热重载 |
| GET | `/api/webui/window-summaries?limit=N` | 列出最近 N 条窗口摘要快照 |
| POST | `/api/webui/window-summaries/clear` | 清空窗口摘要存档 |

### 2.4 热重载链路

```
update_config → set_key 写 .env
             → reload_settings()        # 清 get_settings 缓存
             → get_vision_model_client.cache_clear()
             → get_assistant_chat_service.cache_clear()
             → get_memory_service.cache_clear()
```

下次请求时单例重建，新配置生效。`minicpm_ctx_size` / `llama_server_host` / `port` 等启动参数仍需重启后端。

---

## 3. 阶段二：识图/对话职责分离

### 3.1 架构总览

```
┌─────────────────────────────┐        ┌──────────────────────────────┐
│  识图摘要服务（单一职责）   │        │  对话 agent（可迭代 agent）  │
│  WindowAnalysisService      │        │  AssistantChatService         │
│                             │        │                              │
│  screenshot → analyze_image │        │  question + history          │
│  → detailed_summary         │───────▶│  → build_chat_messages       │
│  → key_points               │ 存档   │  → stream_chat (messages)    │
│  → WindowSummaryStore       │ 检索   │  → 流式回答                  │
└─────────────────────────────┘        └──────────────────────────────┘
              │                                       │
              ▼                                       ▼
   window:summaries (SQLite KV)           assistant:chat:history (SQLite KV)
```

### 3.2 识图摘要服务（单一职责）

**改动**：默认 prompt 切换到 `analyze_window_v2.txt`，职责收窄为"尽可能详细地描述窗口"。

`analyze_window_v2.txt` 的关键约束：
- 不生成候选问题、不与用户对话、不给建议；
- `summary` 字段要求详细描述：应用、面板、可见文字、按钮、状态、选中项等；
- `key_points` 提取 4–8 个；
- 输出 schema 仍保留 `candidate_questions: []` 字段以兼容现有 `WindowAnalysis` 模型，但恒为空数组。

`analyze_max_tokens` 从 700 提升到 1200，给详细摘要留足空间。

### 3.3 窗口摘要存档服务（新增）

`WindowSummaryStore`：每次识图分析成功后，把摘要快照写入 SQLite KV（key = `window:summaries`），供对话 agent 检索。

```python
class WindowSummaryStore:
    def record(*, observation, window_type, summary, key_points) -> dict
    def recent(*, limit: int | None = None) -> list[dict]  # 时间正序（旧→新）
    def clear() -> int
```

每条记录包含：`record_id` / `created_at` / `app_name` / `window_title` / `window_type` / `summary` / `key_points`。容量受 `window_summary_history_limit`（默认 30）限制，超出自动丢弃最旧的。

### 3.4 对话 agent 重构（核心）

#### 3.4.1 旧逻辑的问题

旧 `_stream_model_answer` 调用 `stream_answer`，内部用 `build_question_prompt` 把所有上下文（窗口摘要 + 记忆 + 对话历史）**拼成一段文本**塞进单条 user message。模型看到的只是"用户：xxx\n助手：yyy\n用户：zzz"这样的扁平文本，无法把它当作真正的多轮对话，因此无法稳定地"记得自己说过什么"。

#### 3.4.2 新逻辑：messages 多轮结构

新增 `build_chat_messages` 函数，构建标准 messages 数组：

```python
[
  {"role": "system",    "content": "<人设 + 当前窗口摘要 + 历史窗口摘要 + 记忆 + 回答要求>"},
  {"role": "user",      "content": "<历史问题 1>"[:500]},
  {"role": "assistant", "content": "<历史回答 1>"[:800]},
  ...                                                       # 最近 chat_history_turns 轮
  {"role": "user",      "content": "<当前问题>"},
]
```

新增 `stream_chat` 方法以 messages 结构调用模型（OpenAI 兼容流式接口）：

```python
def stream_chat(self, *, messages, image_path: Path | None = None) -> Iterator[str]:
    # 默认纯文本；若传入 image_path，则把它附加到最后一条 user message 上
```

关键点：
- **对话历史以 user/assistant 交替的真实多轮结构传递**，模型能区分自己之前的回答，实现"记得自己做了什么"。
- **当前窗口摘要、历史窗口摘要、记忆**作为 system message 背景，不污染对话历史结构。
- **默认不带截图**（`chat_include_screenshot=False`）：对话 agent 基于识图摘要服务提供的文字摘要工作，职责彻底分离。需要时可开启带截图模式。

#### 3.4.3 system message 注入的背景

`build_chat_messages` 在 system message 中按顺序注入：

1. `system_prompt_prefix`（用户自定义系统提示前缀）
2. 人设块（`personality_enabled` + name/traits）
3. 回答要求（不输出日志/JSON、不确定就说明、保持连贯、不重复）
4. 当前窗口摘要 + 关键点
5. 最近 N 条历史窗口摘要（带时间戳、应用、标题、类型，按时间正序）
6. 相关记忆条目

---

## 4. 文件清单

### 4.1 新增文件

| 文件 | 说明 |
|------|------|
| [window_summary_store.py](file:///d:/AI_Workspace/window/backend/app/services/window_summary_store.py) | 窗口摘要历史存储服务，存 SQLite KV |
| [analyze_window_v2.txt](file:///d:/AI_Workspace/window/experiments/prompts/analyze_window_v2.txt) | 单一职责识图 prompt（详细摘要，不生成候选问题） |
| [index.html](file:///d:/AI_Workspace/window/backend/app/webui/static/index.html) | webui 前端（配置页 + 对话调试页 + 上下文透视） |
| [webui.py](file:///d:/AI_Workspace/window/backend/app/api/routes/webui.py) | webui 路由（config CRUD / reload / window-summaries） |

### 4.2 修改文件

| 文件 | 改动要点 |
|------|----------|
| [config.py](file:///d:/AI_Workspace/window/backend/app/core/config.py) | 默认 prompt 改 v2；analyze_max_tokens 1200；新增 chat_include_screenshot / window_summary_history_limit / window_summary_retrieve_count；chat_history_turns 6；history_retention_limit 30 |
| [vision_model_client.py](file:///d:/AI_Workspace/window/backend/app/services/vision_model_client.py) | 新增 `stream_chat` + `build_chat_messages`；保留 `stream_answer` 兼容 |
| [assistant_chat.py](file:///d:/AI_Workspace/window/backend/app/services/assistant_chat.py) | `_stream_model_answer` 改用 `build_chat_messages` + `stream_chat`；注入 `WindowSummaryStore`；`inspect_context` 返回 window_summaries / chat_include_screenshot 等新字段 |
| [window_analysis.py](file:///d:/AI_Workspace/window/backend/app/services/window_analysis.py) | 注入 `WindowSummaryStore`，analyze_capture 成功后调用 `store.record()` |
| [desktop_floating_window.py](file:///d:/AI_Workspace/window/apps/desktop-floating-window/desktop_floating_window.py) | 移除候选问题渲染；新增"💬 点击提问"按钮，点击直接弹对话框 |
| [main.py](file:///d:/AI_Workspace/window/backend/app/main.py) | 挂载 webui 路由 + mount 静态目录 |
| [memory.py](file:///d:/AI_Workspace/window/backend/app/services/memory.py) | 参数化 max_items |
| [assistant.py](file:///d:/AI_Workspace/window/backend/app/api/routes/assistant.py) | 新增 `/conversations/clear`、`/context-preview` 路由 |
| [start_dev.py](file:///d:/AI_Workspace/window/scripts/start_dev.py) | 打印 webui URL |
| [test_assistant_chat_service.py](file:///d:/AI_Workspace/window/backend/tests/test_assistant_chat_service.py) | `FakeVisionClient` 改 mock `stream_chat` 而非 `stream_answer` |

---

## 5. 配置字段清单（本次新增/调整）

| 字段 | 默认值 | 分组 | 说明 |
|------|--------|------|------|
| `analyze_max_tokens` | 1200 (↑) | model | 详细摘要需要更大 token 上限 |
| `chat_history_turns` | 6 (↑) | context | 注入更多轮历史 |
| `chat_history_question_max_chars` | 500 (↑) | context | 历史问题截断字数 |
| `chat_history_answer_max_chars` | 800 (↑) | context | 历史回答截断字数 |
| `history_retention_limit` | 30 (↑) | context | 历史保留条数 |
| `chat_include_screenshot` | False (新增) | context | 对话是否带截图，默认纯文本 |
| `window_summary_history_limit` | 30 (新增) | context | 窗口摘要存档容量 |
| `window_summary_retrieve_count` | 5 (新增) | context | 对话注入的窗口摘要条数 |
| `personality_enabled` | False (新增) | personality | 启用人设 |
| `personality_name` | "" (新增) | personality | 助手名字 |
| `personality_traits` | "" (新增) | personality | 性格描述 |
| `system_prompt_prefix` | "" (新增) | personality | 自定义系统提示前缀 |
| `answer_style_hint` | "" (新增) | personality | 回答风格提示 |

`window_analysis_prompt_path` 默认指向 `analyze_window_v2.txt`。

---

## 6. 数据流

### 6.1 识图摘要流

```
窗口捕获 → WindowAnalysisService.analyze_capture
         → VisionModelClient.analyze_image（v2 prompt，详细摘要）
         → WindowSummaryStore.record（写 window:summaries）
         → MemoryService.remember_analysis（写记忆）
         → runtime_store.set_json("window:latest_analysis")
```

### 6.2 对话 agent 流

```
用户提问 → AssistantChatService.ask
         → 暂停 watcher，状态置 analyzing
         → _answer(session) 异步任务
            → _stream_model_answer
               → MemoryService.retrieve_for_observation
               → self.history(chat_history_turns)        # 排除当前 session
               → WindowSummaryStore.recent(retrieve_count)
               → build_chat_messages(...)                # system + 多轮历史 + 当前问题
               → VisionModelClient.stream_chat(messages, image_path=None)
               → 流式 _append 到 session
            → session.status = done
            → _append_history
         → 状态置 idle
```

### 6.3 上下文透视

`inspect_context(question)` 返回下一次回答将被注入的全部上下文，供 webui 调试：

```json
{
  "question": "...",
  "latest_analysis_present": true,
  "latest_summary": "...",
  "chat_history_turns_setting": 6,
  "memory_retrieve_count_setting": 4,
  "window_summary_retrieve_count_setting": 5,
  "chat_include_screenshot": false,
  "memory_enabled": true,
  "chat_history": [...],
  "memory_items": [...],
  "window_summaries": [...],
  "personality": {"enabled": false, "name": "", "traits": "", "answer_style_hint": ""}
}
```

---

## 7. 验证

### 7.1 后端 import

`from app.main import app` 成功，路由全部挂载。

### 7.2 单元测试

- `test_assistant_chat_service.py::test_chat_question_pauses_streams_and_resume_restarts[asyncio]` **通过**
  - `FakeVisionClient` 已适配新的 `stream_chat` 接口
- `build_chat_messages` 生成结构正确：system + user（当前问题），历史为空时无多余消息
- `inspect_context` 返回新字段（window_summaries / chat_include_screenshot / window_summary_retrieve_count_setting）
- `WindowSummaryStore` history_limit=30 生效

### 7.3 已知环境性问题（非代码问题）

- `trio` 后端测试失败：环境未安装 trio 模块
- `test_memory_service` / `test_window_analysis_service` / `test_window_watcher_analysis` 部分用例 ERROR：Windows 临时目录权限 `PermissionError: [WinError 5]`，与本次改动无关

---

## 8. 后续可演进方向

- 历史窗口摘要的语义检索（当前是按时间倒序取最近 N 条，可改为按 app_name / window_type 过滤或向量检索）
- 对话 agent 的工具调用能力（当前仅基于摘要回答，无法主动触发截图/分析）
- webui 增加"窗口摘要时间线"可视化页
- `stream_answer` / `build_question_prompt` 可在确认无外部调用后移除（当前保留以兼容）
