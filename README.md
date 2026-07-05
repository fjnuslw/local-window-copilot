<div align="center">

# Local Window Copilot

**Windows 本地桌面 Agent：窗口观察、上下文检索、记忆与流式回答**

一个运行在本地 Windows 桌面上的 AI 伙伴。后台把目标窗口截图转成结构化观察，前台由 `ChatAgent` 管理会话、profile 与证据检索，通过 `memory.search(query)` 在需要时调取本地上下文，并用可追溯证据完成回答。

<br>

<img src="assets/mascot/composed/mascot_idle.png" alt="Local Window Copilot Mascot" width="180" height="180" />

<br>

![Platform](https://img.shields.io/badge/platform-Windows%2010%2B-0078D4?logo=windows&logoColor=white)
![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688?logo=fastapi&logoColor=white)
![SQLite](https://img.shields.io/badge/storage-SQLite%20%2B%20FTS5-003B57?logo=sqlite&logoColor=white)
![VLM](https://img.shields.io/badge/VLM-MiniCPM--V-7C3AED)
![Runtime](https://img.shields.io/badge/runtime-llama.cpp-111827)
![License](https://img.shields.io/badge/license-MIT-green)

<br>

[产品规格](project_plan/ambient_companion_product_spec_zh.md) ·
[开发指南](docs/development_guide_zh.md) ·
[WebUI/Agent 分离 Spec](docs/refactor_webui_and_agent_separation_spec.md) ·
[桌宠素材](assets/mascot/README.md)

</div>

---

## 项目核心

Local Window Copilot 把桌面 Agent 所需的几件事作为主线来实现：窗口观察、上下文编排、记忆检索、流式工具调用和调试可观测性。

| 模块 | 当前实现 | 面试时可讲的工程点 |
|---|---|---|
| 桌面 Agent | Windows 透明置顶悬浮窗 + 5 种状态 | 低打扰交互、状态机、隐私/错误态、前后台协同 |
| 观察线 | 截图 -> MiniCPM-V 结构化观察 -> SQLite | VLM prompt 契约、截图 metadata、可审计视觉证据 |
| 对话线 | `ChatAgent` + `memory.search(query)` | 工具调用、上下文分层、probe->stream 职责分离 |
| 记忆模块 | RuntimeStore + `memory:items` + `chat_history_fts` | 短期记忆、跨会话检索、SQLite FTS5/BM25 排名 |
| 调试台 | WebUI 展示 raw JSON、截图、trace、runtime logs | 可观测性、可复盘、可清理、可回归测试 |
| 本地运行时 | FastAPI + SQLite + llama.cpp + MiniCPM-V | local-first、零外部数据库依赖、OpenAI-compatible 本地推理 |

---

## 当前主线

```text
观察线
  target window screenshot
  -> MiniCPM-V structured observation
  -> window:latest_analysis
  -> window:summaries
  -> screenshot PNG

对话线
  user question + stable system prompt + profile + session history
  -> probe: model decides whether to call memory.search(query)
  -> stream: final answer, with tools still available
  -> evidence records with source / record_id / screenshot metadata
  -> final answer grounded in local evidence

调试线
  WebUI shows latest/history/raw JSON,
  screenshot thumbnails, tool traces and runtime logs.
```

这条主线已经落到当前实现中；相关背景可以从 [WebUI/Agent 分离 Spec](docs/refactor_webui_and_agent_separation_spec.md) 和 [产品规格](project_plan/ambient_companion_product_spec_zh.md) 继续阅读。历史 spec 中的独立 planner、三工具设计、关键词路由和固定窗口摘要注入已经被当前实现替换。

---

## Agent 与上下文管理

### 1. 模型可见工具

当前模型可见工具只有一个：

```text
memory.search(query)
```

它负责按需检索本地证据，返回带 `source`、`record_id`、截图 metadata 的相关上下文。OpenAI-compatible function 名称在协议层兼容为 `memory_search`，文档和 UI 统一称为 `memory.search(query)`。

### 2. 上下文分层

| 上下文 | 进入方式 | 作用 |
|---|---|---|
| stable system prompt | 直接进入 messages | 约束回答原则与工具协议 |
| profile packet | 会话级冻结 | 保持 persona/profile 稳定，提升 llama.cpp prefix cache 命中 |
| session history | 最近 N 轮直接进入 | 保持当前对话连续性 |
| window observations | 通过 `memory.search` 调取 | 回答窗口、页面、代码、截图相关问题 |
| memory items | 近期尾部 + 检索候选 | 用户偏好、任务事实、明确记录 |
| cross-session chat history | `chat_history_fts` 检索 | 找回历史讨论、结论和偏好 |

### 3. probe -> stream

`ChatAgent` 采用两段式回答：

1. **probe 阶段**：模型带 tools 判断是否调用 `memory.search(query)`，只读取结构化 `tool_calls`。
2. **工具执行**：后端执行检索，生成工具结果并追加到 messages。
3. **stream 阶段**：模型流式生成最终回答，tools 仍然可用。
4. **流式工具循环**：stream 中如继续出现 tool call，暂停输出、执行工具、追加结果，再继续 stream，最多 2 轮。

这样可以把小 VLM function calling 的不稳定性限制在工具决策层，最终回答始终由 stream 阶段产出。

---

## 记忆与检索

`memory.search(query)` 的候选集来自多个本地来源：

| Source | 内容 |
|---|---|
| `window:latest_analysis` | 最近一次成功窗口观察 |
| `window:summaries` | 滚动结构化观察历史 |
| `memory:working:observation` | 当前工作观察卡片 |
| `memory:items` | 用户问题、助手回答、用户 note、短期事实 |
| `assistant:chat:history` | 当前运行期对话历史 |
| `chat_history_fts` | 持久跨会话 FTS5 索引 |

检索排序使用 SQLite FTS5 + BM25，并对中文做 bigram 双字滑窗分词。候选集通常是几十条，本地确定性 ranker 可以在毫秒级返回结果，也让观察线 VLM 与证据排序解耦。

---

## 技术架构

```text
┌──────────────────────────────────────────────────────────────┐
│ Desktop Floating Window                                      │
│  Idle / Observing / Analyzing / Privacy / Error               │
└───────────────────────────┬──────────────────────────────────┘
                            │ FastAPI
                            ▼
┌──────────────────────────────────────────────────────────────┐
│ Backend                                                       │
│  assistant.py  window.py  webui.py                            │
│                                                              │
│  ChatAgent                                                   │
│    ├─ profile packet freeze                                  │
│    ├─ probe -> stream(+tools)                                │
│    ├─ memory.search(query)                                   │
│    └─ runtime trace                                          │
│                                                              │
│  Observation pipeline                                         │
│    ├─ window capture                                          │
│    ├─ MiniCPM-V structured observation                        │
│    └─ window summary store                                    │
│                                                              │
│  RuntimeStore                                                 │
│    ├─ runtime_json                                            │
│    ├─ runtime_events                                          │
│    └─ chat_history_fts                                        │
└───────────────────────────┬──────────────────────────────────┘
                            │ OpenAI-compatible API
                            ▼
┌──────────────────────────────────────────────────────────────┐
│ llama.cpp server + MiniCPM-V 4.6                              │
└──────────────────────────────────────────────────────────────┘
```

---

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.11+ / FastAPI / Pydantic / uvicorn |
| 本地存储 | SQLite RuntimeStore / runtime_json / runtime_events / FTS5 |
| 检索 | SQLite FTS5 + BM25 / 中文 bigram tokenization |
| 模型运行时 | llama.cpp `llama-server` / OpenAI-compatible `/v1/chat/completions` |
| 视觉模型 | MiniCPM-V 4.6 GGUF / 256K context / screenshot input |
| 桌面 UI | Python + Win32 / 透明置顶悬浮窗 / PNG mascot states |
| WebUI | 原生 HTML/CSS/JS / SSE 流式对话 / 调试面板 |
| 测试 | pytest / service-level tests / API route tests |

---

## 快速开始

### 环境要求

- Windows 10 / 11
- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- [llama.cpp](https://github.com/ggml-org/llama.cpp) `llama-server`
- MiniCPM-V 4.6 GGUF 权重与 mmproj 文件

### 一键启动

```powershell
cd D:\AI_Workspace\window
.\scripts\start_dev.cmd
```

### 启动前检查

```powershell
python .\scripts\check_environment.py --for-start
```

### 手动启动后端

```powershell
cd D:\AI_Workspace\window\backend
uv run uvicorn app.main:app --host 127.0.0.1 --port 18081 --reload --no-access-log
```

### 手动启动悬浮窗

```powershell
cd D:\AI_Workspace\window
.\apps\desktop-floating-window\start_desktop_window.cmd
```

### WebUI 控制台

```text
http://127.0.0.1:18081/webui/
http://127.0.0.1:18081/docs
```

---

## WebUI 调试能力

WebUI 用来回答工程调试时最重要的几个问题：

- 最近一次窗口观察到底看到了什么
- 滚动观察历史保存了哪些字段
- 模型是否调用了 `memory.search`
- 工具返回了哪些 `source` 与 `record_id`
- profile、runtime config、runtime logs 是否符合预期
- 对话、观察、记忆、日志、FTS5 索引是否能一键清理

关键接口：

```text
GET  /api/webui/observations/latest
GET  /api/webui/observations
GET  /api/webui/observations/{record_id}
GET  /api/webui/observations/{record_id}/image
GET  /api/webui/tool-traces
GET  /api/webui/runtime-logs
GET  /api/webui/profile
PUT  /api/webui/profile
POST /api/webui/memory/clear
POST /api/webui/runtime-events/clear
POST /api/webui/reset-all
```

---

## API 概览

```text
# assistant
GET  /health
GET  /api/assistant/state
POST /api/assistant/state
GET  /api/assistant/events
GET  /api/assistant/latest
POST /api/assistant/questions
POST /api/assistant/questions/stream
GET  /api/assistant/conversation
GET  /api/assistant/conversations
POST /api/assistant/conversations/clear
POST /api/assistant/context-preview
GET  /api/assistant/context-status
POST /api/assistant/resume
POST /api/assistant/pause
POST /api/assistant/observe

# window
POST /api/window/capture
POST /api/window/watch/start
POST /api/window/watch/stop
GET  /api/window/watch/status
```

---

## 代码入口

```text
backend/app/main.py                         FastAPI 入口
backend/app/core/config.py                  LWC_* 配置与本地模型路径

backend/app/api/routes/assistant.py         对话、状态、观察、SSE
backend/app/api/routes/window.py            窗口捕获与监听
backend/app/api/routes/webui.py             WebUI 配置、观察、trace、profile

backend/app/services/assistant_chat.py      ChatAgent / probe->stream / trace
backend/app/services/agent_tools.py         memory.search / FTS5 BM25 ranker
backend/app/services/chat_history_index.py  跨会话对话 FTS5 索引
backend/app/services/runtime_store.py       SQLite RuntimeStore
backend/app/services/runtime_log.py         runtime_events 日志
backend/app/services/memory.py              working observation + memory items
backend/app/services/profile_store.py       profile.md 管理

backend/app/services/window_capture.py      Windows 目标窗口截图
backend/app/services/window_watcher.py      后台窗口观察循环
backend/app/services/window_analysis.py     VLM 分析服务
backend/app/services/window_summary_store.py 结构化观察历史
backend/app/services/observation_builder.py ObservationCard 构建
backend/app/services/vision_model_client.py llama.cpp/OpenAI-compatible 客户端

apps/desktop-floating-window/desktop_floating_window.py  桌宠悬浮窗
experiments/prompts/analyze_window_v2.txt                观察线 VLM prompt
```

---

## 数据存储

默认数据库：

```text
backend/data/runtime/runtime.sqlite3
```

默认截图目录：

```text
backend/data/captures/*.png
```

主要 key / table：

| 名称 | 用途 |
|---|---|
| `assistant:state` | 桌宠状态 |
| `assistant:chat:current` | 当前对话 |
| `assistant:chat:history` | 运行期对话历史 |
| `window:latest_analysis` | 最近一次结构化窗口观察 |
| `window:summaries` | 滚动观察历史 |
| `memory:working:observation` | 当前工作观察卡片 |
| `memory:items` | 短期记忆项 |
| `runtime_events` | trace、runtime logs、memory events |
| `chat_history_fts` | 跨会话对话检索索引 |

---

## 测试

```powershell
cd D:\AI_Workspace\window\backend
uv run pytest --basetemp D:\AI_Workspace\window\.tmp\pytest-basetemp
```

测试覆盖重点：

- `ChatAgent` probe->stream 行为
- `memory.search` 候选收集与 BM25 排名
- 窗口捕获与观察服务
- assistant state / WebUI API
- profile、runtime config、model runtime 管理

---

## 路线图

- [x] 桌宠悬浮窗与 5 种状态
- [x] FastAPI 后端与 SQLite RuntimeStore
- [x] MiniCPM-V 窗口结构化观察
- [x] WebUI 展示 latest/history/raw JSON/截图
- [x] `memory.search(query)` 单工具主线
- [x] probe->stream 职责分离与流式工具调用循环
- [x] FTS5 BM25 证据排序与跨会话对话索引
- [x] runtime logs / tool traces / reset-all 调试接口
- [ ] 工具结果按 token 预算裁剪
- [ ] 演示 GIF / 视频录制
- [ ] 多显示器与窗口选择体验增强
- [ ] Rive 动画迁移

---

## License

MIT License

---

<div align="center">

**Made by [宋林蔚](https://github.com/fjnuslw)**

如果这个项目对你有启发，欢迎 Star。

</div>