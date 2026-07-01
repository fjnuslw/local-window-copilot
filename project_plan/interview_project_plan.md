# 基于本地窗口感知的非执行型 Agent 项目计划书

## 1. 项目定位

本项目计划实现一个运行在 Windows 本地桌面的窗口感知型 AI 助手。系统通过读取当前活动窗口的截图、窗口标题、应用名称和 UI Automation 控件树，构建结构化的 `WindowContext`，再调用本地多模态大模型进行窗口理解，生成当前窗口摘要、关键点、风险提醒、候选问题和文本问答结果。

项目重点不是自动操作电脑，而是提供一个隐私优先、非执行型的本地 AI Copilot：

```text
Observe：采集当前窗口信息
Understand：理解窗口内容和任务语境
Suggest：生成用户可能想问的问题
Respond：基于窗口上下文回答用户
```

系统明确不做自动点击、自动输入、自动提交表单、自动删除文件、自动修改系统设置等执行型操作。

## 2. 面试项目目标

本项目面向大模型应用方向实习面试，目标是展示以下能力：

- 大模型应用架构设计能力：多模态输入、上下文构建、模型调用、结构化输出解析。
- 后端工程能力：FastAPI 服务设计、异步任务、数据库建模、缓存与状态管理。
- 产品边界意识：非执行型 Agent、隐私保护、敏感信息过滤。
- 系统评估能力：窗口分类准确率、候选问题相关性、推理延迟、失败率统计。
- 可扩展设计能力：模型适配层、任务队列、行为日志、评估数据闭环。

最终简历定位建议：

```text
设计并实现一个隐私优先的本地窗口感知型 LLM Agent，通过融合窗口截图、UI Automation 控件树和窗口元信息构建结构化上下文，调用本地多模态模型生成窗口摘要、候选问题和问答结果，并基于 FastAPI、Redis、PostgreSQL 构建异步推理、行为日志和评估体系。
```

## 3. 最终产品形态

用户电脑桌面上常驻一个可拖动、可置顶的悬浮助手。用户打开浏览器报错页面、安装器、IDE 报错、PDF 文档、网页表单或设置页面后，可以点击“分析当前窗口”。

系统完成以下流程：

1. 读取当前活动窗口标题、应用名称、窗口位置。
2. 捕获当前活动窗口截图。
3. 读取基础 UI Automation 控件树和可见文本。
4. 过滤敏感控件和敏感窗口。
5. 构建 `WindowContext`。
6. 调用本地多模态模型分析窗口。
7. 展示窗口摘要、关键点、风险提醒和候选问题。
8. 用户点击候选问题或手动输入问题。
9. 系统基于当前窗口上下文给出文本回答。

产品界面包含：

- 折叠态悬浮球：显示 idle、analyzing、ready、error、privacy_paused 状态。
- 展开态面板：展示当前窗口信息、摘要、候选问题、控制按钮。
- 对话态面板：展示用户问题、助手回答和上下文摘要。
- 设置页：配置模型地址、历史记录、隐私保护、自动分析频率。

## 4. 总体技术架构

```text
Desktop Floating Window
  Python + Win32 layered window + Pillow
  |
  | HTTP / SSE / WebSocket / local state bridge
  v
FastAPI Local Backend
  |
  +--> Window Capture Service
  +--> UI Automation Service
  +--> Privacy Filter
  +--> Context Builder
  +--> Candidate Question Service
  +--> Inference Adapter
  |
  +--> Redis
  |     +-- task queue
  |     +-- task status
  |     +-- screenshot hash cache
  |     +-- rate limit
  |
  +--> PostgreSQL
  |     +-- window contexts
  |     +-- conversations
  |     +-- user events
  |     +-- analysis runs
  |     +-- eval results
  |
  v
llama.cpp server / Local VLM Runtime
  |
  v
MiniCPM-V GGUF or other local vision-language model
```

## 5. 技术栈规划

### 5.1 桌面端

```text
MVP:
  Python 3
  Win32 API via ctypes
  UpdateLayeredWindow per-pixel alpha
  Pillow RGBA rendering
  Local PNG layered mascot assets

Later productization option:
  Tauri v2
  React
  TypeScript
  Vite
```

职责：

- 提供悬浮窗、候选问题、聊天面板、设置页。
- 调用 FastAPI 后端。
- 通过 SSE 或 WebSocket 接收分析进度。
- 展示模型状态、隐私暂停状态和错误提示。

当前已经完成 MVP 桌面壳：`apps/desktop-floating-window/`。它是真实 Windows 置顶透明窗口，不是浏览器网页。Tauri 不再作为当前关键路径，只作为后续更完整设置页、系统托盘、自动更新和安装包的产品化备选。

### 5.2 后端 API

```text
FastAPI
Python 3.11+
Pydantic v2
SQLAlchemy 2.x
Alembic
Uvicorn
```

职责：

- 提供本地 HTTP API。
- 编排窗口采集、隐私过滤、上下文构建和模型调用。
- 管理异步分析任务。
- 持久化分析结果、用户行为和模型运行记录。
- 提供评估脚本和报告接口。

### 5.3 Windows 窗口采集

```text
pywin32
uiautomation
mss
Pillow
```

职责：

- 获取当前活动窗口句柄。
- 获取窗口标题、进程名、窗口位置。
- 捕获当前窗口截图。
- 读取 UI Automation 控件树。
- 标记密码控件和不可见控件。

### 5.4 模型推理

```text
llama.cpp server
MiniCPM-V GGUF
ModelRuntimeManager
```

职责：

- 接收图片和文本上下文。
- 生成结构化 JSON 分析结果。
- 回答基于窗口上下文的问题。

后端通过 `ModelRuntimeManager` 管理 llama.cpp runtime、MiniCPM-V 4.6 GGUF、mmproj 文件、进程启动、健康检查和推理调用。MVP 不先做多模型适配，避免目标路线之外的重构成本。

### 5.5 Redis

```text
Redis
RQ / arq / Celery 可选
```

职责：

- 存储异步任务状态。
- 缓存最近的窗口上下文。
- 基于截图 hash 做去重。
- 做自动分析频率限制。
- 保存短期模型分析进度。

建议 Redis 数据形态：

```text
analysis:task:{task_id} -> status/result/error
window:hash:{screenshot_hash} -> recent_context_id
rate_limit:window_analyze:{session_id} -> counter
current_context:{session_id} -> latest window context summary
```

### 5.6 PostgreSQL

```text
PostgreSQL
JSONB
GIN indexes
```

职责：

- 保存窗口上下文元数据。
- 保存对话记录。
- 保存用户行为事件。
- 保存模型分析运行记录。
- 保存评估样本和评估结果。

PostgreSQL 不应默认保存原始截图、完整 UI tree、密码字段、验证码、支付信息或私密聊天内容。默认只保存脱敏后的结构化元数据和模型输出。

## 6. 核心数据模型

### 6.1 window_contexts

用于保存一次窗口采集和分析后的上下文摘要。

```text
id
session_id
app_name
window_title
window_type
window_bounds_json
screenshot_hash
screenshot_path nullable
visible_text_summary
ui_elements_summary_jsonb
summary
key_points_jsonb
candidate_questions_jsonb
caution
privacy_level
created_at
```

### 6.2 analysis_runs

用于记录模型调用和分析链路。

```text
id
window_context_id
task_id
model_name
runtime
status
latency_ms
input_tokens nullable
output_tokens nullable
json_parse_success
error_type nullable
error_message nullable
created_at
finished_at nullable
```

### 6.3 conversations

用于保存用户围绕某个窗口上下文的问答。

```text
id
window_context_id
user_question
assistant_answer
confidence
caution
latency_ms
created_at
```

### 6.4 user_events

用于记录隐私安全的用户行为事件。

```text
id
session_id
event_type
window_context_id nullable
event_payload jsonb
app_version
privacy_level
created_at
```

示例：

```json
{
  "event_type": "candidate_question_clicked",
  "event_payload": {
    "window_type": "error_dialog",
    "question_category": "next_step",
    "question_rank": 1,
    "latency_ms": 4200
  }
}
```

### 6.5 settings

用于保存用户配置。

```text
key
value
updated_at
```

建议配置：

```text
auto_analyze_enabled
save_history_enabled
sensitive_window_protection_enabled
model_path
llama_server_url
max_candidate_questions
analysis_interval_seconds
```

## 7. API 设计

### 7.1 Health

```http
GET /health
```

返回后端、数据库、Redis、模型服务状态。

### 7.2 分析当前窗口

```http
POST /api/window/analyze
```

行为：

1. 后端采集当前窗口。
2. 执行隐私过滤。
3. 创建异步分析任务。
4. 返回 `task_id`。

### 7.3 获取任务状态

```http
GET /api/tasks/{task_id}
```

返回：

```json
{
  "task_id": "...",
  "status": "running",
  "progress": 0.6,
  "stage": "inference"
}
```

### 7.4 订阅任务进度

```http
GET /api/tasks/{task_id}/events
```

使用 SSE 推送 analyzing、privacy_paused、inference、completed、failed 等状态。

### 7.5 基于窗口提问

```http
POST /api/window/ask
```

请求：

```json
{
  "window_context_id": 12,
  "question": "我下一步应该怎么做？"
}
```

### 7.6 最近上下文

```http
GET /api/context/recent
```

### 7.7 清空历史

```http
DELETE /api/context/history
```

### 7.8 模型状态

```http
GET /api/model/status
```

### 7.9 行为事件上报

```http
POST /api/events
```

仅记录脱敏后的产品行为，不记录原始截图和敏感窗口内容。

## 8. 隐私与安全设计

必须实现的隐私策略：

- 默认本地推理。
- 默认不上传截图。
- 默认不保存原始截图。
- 默认不保存完整 UI tree。
- 密码控件不进入模型输入。
- 检测到敏感窗口时暂停自动分析。
- 用户可关闭历史记录。
- 用户可一键清空历史。
- 用户行为日志只记录事件类型和脱敏元数据。

敏感关键词：

```text
password
密码
payment
支付
bank
银行
private key
私钥
authentication
verification code
验证码
login
登录
account
账号
delete
删除
install
安装
permission
权限
```

敏感窗口处理策略：

```text
自动分析：暂停
手动分析：需要用户确认
模型输入：过滤敏感字段
数据库：不保存原始敏感内容
前端提示：当前窗口可能包含敏感信息，已暂停自动分析
```

## 9. 候选问题生成机制

候选问题是项目的交互亮点，不完全依赖模型自由生成，而是采用规则模板加模型补全的方式。

规则模板示例：

```text
error_dialog:
  - 这个错误是什么意思？
  - 我下一步应该怎么做？
  - 这个错误有风险吗？

form:
  - 这个页面让我填写什么？
  - 哪些字段是必填？
  - 这里有没有隐私风险？

document:
  - 帮我总结当前内容
  - 这段内容的重点是什么？
  - 这里有什么需要注意的地方？
```

模型负责结合窗口内容补充更具体的问题，并给出 `category`、`reason` 和 `priority`。

## 10. 评估体系

需要构建一个小型窗口截图测试集，覆盖：

```text
错误弹窗
网页
表单
安装器
设置页面
文档
IDE / 编辑器
聊天应用
文件管理器
未知窗口
```

每个样本包含：

```text
screenshot_path
app_name
window_title
visible_text
expected_window_type
expected_summary_keywords
expected_question_categories
```

评估指标：

```text
window_type_accuracy
summary_keyword_coverage
candidate_question_relevance
avg_latency_ms
p95_latency_ms
json_parse_success_rate
privacy_filter_trigger_rate
```

输出：

```text
eval/results.json
eval/report.md
```

## 11. 开发里程碑

### Phase 0：真实场景技术验证

目标：不做假数据主线，直接选择一个真实具体场景验证最关键链路。推荐优先选择 `IDE / 终端报错` 或 `安装失败 / 权限错误窗口`，因为这类场景价值清晰、输入稳定、面试展示效果好。

任务：

- 固定第一个真实场景，并保存 3 到 5 个可复现样本。
- 验证 Windows 当前窗口标题和应用名读取。
- 验证当前窗口截图。
- 验证基础 UI Automation 控件树读取。
- 验证 llama.cpp 或兼容 VLM 接口能接收图片并返回 JSON。
- 编写第一版窗口分析 prompt。

验收：

- 能从真实窗口生成一份可用 `WindowContext`。
- 能让真实模型输出窗口摘要、关键点、候选问题和 caution。
- 这批样本后续直接进入评估集，不做一次性假数据。

### Phase 1：FastAPI MVP

目标：把 Phase 0 的真实场景链路封装成可复用后端 API。

任务：

- 搭建 FastAPI 项目结构。
- 定义 Pydantic schemas。
- 实现 `/health`。
- 实现窗口采集服务。
- 实现隐私过滤器。
- 实现 `WindowContext` builder。
- 实现 `InferenceService`。
- 实现 `/api/window/analyze` 和 `/api/window/ask`。

验收：

- 用 Postman 或 curl 可以触发真实当前窗口分析。
- 返回结构化 JSON，包括摘要、关键点、候选问题和 caution。

### Phase 2：PostgreSQL + Redis 工程增强

目标：展示后端工程能力。

任务：

- 使用 Docker Compose 启动 PostgreSQL 和 Redis。
- 使用 SQLAlchemy 建模。
- 使用 Alembic 管理迁移。
- 实现 `window_contexts`、`analysis_runs`、`conversations`、`user_events`。
- 接入 Redis 任务状态和截图 hash 去重。
- 实现 SSE 或 WebSocket 任务进度推送。

验收：

- 分析任务异步执行。
- 前端或测试脚本可查询任务状态。
- PostgreSQL 中能看到脱敏后的分析记录、行为事件和模型运行日志。

### Phase 3：桌面端接入与产品化

目标：做出可演示产品。

任务：

- 已完成 Python + Win32 原生悬浮窗基础壳。
- 将悬浮窗状态从 `state_bridge.json` 切换到 FastAPI SSE / WebSocket。
- 实现点击悬浮窗触发“分析当前窗口”。
- 实现摘要、候选问题和回答的轻量展示层。
- 实现暂停读取、隐私状态和错误状态。
- 后续评估是否用 Tauri 重做完整设置页和安装包。

验收：

- 用户可以在桌面悬浮窗点击“分析当前窗口”。
- 前端能展示摘要、候选问题和回答。
- 敏感窗口能触发隐私暂停提示。

### Phase 4：评估与面试材料

目标：让项目可讲、可量化、可复盘。

任务：

- 构建小型截图测试集。
- 编写 `eval/run_eval.py`。
- 统计窗口类型准确率、摘要关键词覆盖、候选问题相关性和延迟。
- 生成 Markdown 评估报告。
- 编写 README、架构文档、隐私设计文档和简历 bullet。

验收：

- 一键运行评估脚本。
- 有可展示的评估结果和架构图。
- README 能让面试官快速理解项目价值。

## 12. 推荐目录结构

```text
local-window-agent/
  README.md
  docker-compose.yml
  .env.example

  backend/
    pyproject.toml
    app/
      main.py
      api/
      core/
      schemas/
      services/
      db/
      workers/
      prompts/
    alembic/
    tests/

  desktop/
    package.json
    src/
    src-tauri/

  eval/
    dataset/
    run_eval.py
    metrics.py
    reports/

  docs/
    architecture.md
    privacy.md
    api.md
    interview_notes.md

  project_plan/
    interview_project_plan.md
```

## 13. 主要风险与应对

### 风险 1：本地多模态模型推理效果或速度不稳定

应对：

- 直接使用 MiniCPM-V 4.6 GGUF + llama.cpp 路线。
- 先用 `llama-mtmd-cli` 跑通单图，再封装进 FastAPI。
- 如果速度不理想，优先调整上下文长度、图片尺寸、GPU offload 和常驻服务方式。
- 如果 JSON 输出不稳定，优先优化 prompt、reasoning 参数和 JSON repair。
- 评估报告中记录延迟和 JSON 解析失败率。

### 风险 2：UI Automation 控件树不稳定

应对：

- 不把 UIA 作为唯一输入。
- 采用截图为主、UIA 为辅的上下文构建策略。
- 对 UIA 读取失败做降级处理。

### 风险 3：隐私边界容易被质疑

应对：

- 默认不保存截图。
- 默认不上传内容。
- 用户行为日志只保存脱敏元数据。
- 敏感窗口自动暂停。
- README 中单独写清楚“不做什么”。

### 风险 4：项目范围过大

应对：

- 第一版只做手动分析，不做自动窗口监听。
- 第一版不做账号系统、云同步、支付、浏览器插件。
- 自动分析、模型切换、更多评估指标作为后续扩展。

## 14. 简历表述

项目名称：

```text
Local Window-Aware Agent：隐私优先的本地窗口感知型 LLM Agent
```

简历 bullet：

- 设计并实现本地窗口感知型 LLM Agent，融合窗口截图、UI Automation 控件树、窗口标题和应用元信息构建结构化 `WindowContext`。
- 基于 FastAPI 设计本地后端服务，封装窗口采集、隐私过滤、上下文构建、模型推理和问答 API。
- 使用 Redis 构建异步分析任务、任务状态缓存、截图 hash 去重和分析频率限制机制。
- 使用 PostgreSQL 记录脱敏后的用户行为、模型调用链路、窗口分析结果和评估数据，支持候选问题点击率、P95 延迟和失败率统计。
- 通过本地多模态模型生成窗口摘要、关键点、风险提醒和候选问题，实现 Observe → Understand → Suggest → Respond 的非执行型 Agent Loop。
- 设计隐私保护策略，默认不上传截图、不保存敏感字段，针对密码、支付、账号、权限等敏感窗口触发自动暂停。
- 构建窗口截图评估集，从窗口分类准确率、摘要关键词覆盖率、候选问题相关性和推理延迟等维度评估系统效果。

## 15. 面试讲解主线

推荐按以下顺序讲：

1. 为什么做：普通聊天机器人无法理解用户当前桌面语境。
2. 做了什么：本地窗口感知助手，理解当前窗口并生成候选问题和回答。
3. 怎么做：截图 + UIA + 标题构建 `WindowContext`，调用 VLM，输出结构化 JSON。
4. 工程难点：异步推理、JSON 解析、UIA 降级、隐私过滤、任务状态推送。
5. 后端设计：FastAPI API、Redis 任务状态、PostgreSQL 行为日志和评估数据。
6. 隐私边界：不执行操作、不上传截图、不保存敏感字段。
7. 怎么评估：窗口分类准确率、候选问题相关性、P95 延迟、失败率。
8. 后续扩展：自动窗口切换分析、更多模型适配、个性化候选问题、插件化采集。
