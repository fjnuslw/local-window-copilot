# 工程拆解与串联路线

## 1. 总体综述

这个项目不应该一开始就当成一个“大而全桌面 Agent”来做。更合理的方式是把它拆成多个可以独立开发、独立测试、独立展示的小模块，然后通过清晰的数据接口逐步串联。

最终系统可以理解为六个子系统：

```text
窗口采集子系统
  ↓
隐私过滤与上下文构建子系统
  ↓
模型推理子系统
  ↓
异步任务与状态管理子系统
  ↓
数据记录与评估子系统
  ↓
桌面交互子系统
```

每个子系统都可以单独做成一个里程碑。这样工程不会一上来失控，面试时也能讲清楚“从 MVP 到完整系统”的演进过程。

## 2. 拆分原则

拆分时遵循三个原则：

1. 先做可验证闭环，再做完整体验。
2. 先做核心能力，再做工程增强。
3. 不以 mock 或假数据作为主线，优先围绕真实具体场景打通最小可用链路。

也就是说，不要一开始就同时做 Tauri、PostgreSQL、Redis、完整桌面端和复杂自动分析。正确节奏应该是先选定一个真实、高频、容易展示的窗口场景，把这条链路跑通，再横向扩展。

```text
固定真实场景
  → 真实窗口采集
  → 真实模型推理
  → FastAPI API 闭环
  → Redis 异步任务
  → PostgreSQL 行为记录
  → Python + Win32 桌面悬浮窗
  → 评估体系
```

第一批推荐场景：

```text
场景 A：IDE / 终端报错
  适合展示开发者 Copilot 能力，输入内容稳定，面试相关性强。

场景 B：安装失败 / 权限错误窗口
  适合展示错误解释、风险提醒和下一步建议。

场景 C：网页表单 / 设置页面
  适合展示隐私过滤、字段理解和候选问题生成。
```

第一阶段只需要选其中一个主场景。推荐从 `IDE / 终端报错` 或 `安装失败 / 权限错误窗口` 开始，因为它们最容易讲清楚价值，也最容易构造测试样本。

第一阶段直接使用目标运行时：`MiniCPM-V 4.6 GGUF + mmproj + llama.cpp`。不先做 Ollama、PyTorch、云端兼容接口等过渡路线，避免后续切换造成重复工作。

## 3. 子系统一：窗口采集模块

### 3.1 模块目标

从 Windows 当前活动窗口中采集基础信息，输出标准化的原始窗口数据。

### 3.2 输入

```text
当前用户桌面状态
```

### 3.3 输出

```json
{
  "app_name": "Chrome",
  "window_title": "安装失败 - 帮助页面",
  "window_bounds": [100, 80, 1280, 900],
  "screenshot_base64": "...",
  "screenshot_hash": "...",
  "visible_text": "Error 0x80070005 Access denied",
  "ui_elements": []
}
```

### 3.4 技术实现

```text
pywin32：获取当前活动窗口句柄、标题、进程名
mss：截取当前窗口区域
Pillow：图片压缩、base64 编码、hash 计算
uiautomation：读取控件树和控件文本
```

### 3.5 单独验收

- 能打印当前活动窗口标题和应用名。
- 能保存当前窗口截图。
- 能输出基础 UIA 控件树。
- 即使 UIA 失败，也能返回标题和截图。

### 3.6 面试价值

这个模块体现的是“把用户当前桌面状态转成大模型可理解输入”的能力。它是普通聊天机器人和窗口感知 Agent 的核心区别。

## 4. 子系统二：隐私过滤与上下文构建模块

### 4.1 模块目标

将窗口采集模块输出的原始数据，转成安全、结构化、可给模型使用的 `WindowContext`。

### 4.2 输入

```text
RawWindowCapture
```

### 4.3 输出

```json
{
  "app_name": "Chrome",
  "window_title": "安装失败 - 帮助页面",
  "window_type_hint": "installer",
  "screenshot_hash": "...",
  "visible_text": "Error 0x80070005 Access denied",
  "ui_elements": [
    {
      "control_type": "Button",
      "name": "Retry",
      "is_password": false
    }
  ],
  "privacy": {
    "level": "normal",
    "blocked": false,
    "reasons": []
  },
  "captured_at": "2026-06-30T20:00:00"
}
```

### 4.4 技术实现

```text
Pydantic：定义 WindowContext schema
PrivacyFilter：过滤密码、验证码、支付、账号等敏感字段
ContextBuilder：合并截图、标题、文本、控件树
HashService：生成 screenshot_hash 和 ui_tree_hash
```

### 4.5 单独验收

- 密码控件不会进入模型输入。
- 命中敏感关键词时返回 `privacy.blocked = true`。
- 能从真实窗口采集结果构建稳定的 `WindowContext`。
- 能对 UIA 失败场景做降级。

### 4.6 面试价值

这个模块体现的是产品边界和安全意识：大模型应用不只是“把数据塞给模型”，还要考虑什么数据能进模型、什么数据必须被过滤。

## 5. 子系统三：模型推理模块

### 5.1 模块目标

基于 `WindowContext` 调用多模态模型，返回结构化窗口理解结果和问答结果。

### 5.2 输入

```text
WindowContext
```

### 5.3 输出

```json
{
  "window_type": "error_dialog",
  "summary": "当前窗口显示安装失败，错误信息可能与权限不足有关。",
  "key_points": [
    "出现错误码 0x80070005",
    "可能与权限不足有关",
    "窗口提供 Retry 和 Cancel 操作"
  ],
  "candidate_questions": [
    {
      "question": "这个错误是什么意思？",
      "category": "explanation",
      "reason": "当前窗口包含错误码",
      "priority": 0.95
    }
  ],
  "caution": "不要在不了解原因的情况下反复重试安装。"
}
```

### 5.4 技术实现

```text
InferenceService：统一模型调用接口
PromptTemplate：窗口分析 prompt 和窗口问答 prompt
JSONParser：解析模型输出
JSONRepair：处理模型输出不合法 JSON
ModelRuntimeManager：管理 llama.cpp runtime 和 MiniCPM-V 4.6 GGUF
```

### 5.5 开发顺序

1. 准备 MiniCPM-V-4_6-F16.gguf 和 mmproj-model-f16.gguf。
2. 准备 llama.cpp 可执行文件。
3. 用固定真实场景截图跑通 llama-mtmd-cli。
4. 调整 prompt，让模型稳定输出 JSON。
5. 封装 `ModelRuntimeManager`，再接入 FastAPI。

### 5.6 单独验收

- 输入真实场景的 `WindowContext`，能返回合法 `AnalyzeWindowResponse`。
- 模型输出不是合法 JSON 时，系统能给出可控错误或修复。
- 能记录模型延迟、失败原因、JSON 解析成功率。

### 5.7 面试价值

这个模块体现的是 LLM 应用核心能力：prompt 设计、结构化输出、模型适配、错误兜底和可观测性。

## 6. 子系统四：异步任务与状态管理模块

### 6.1 模块目标

把耗时的窗口分析从同步 API 中拆出去，避免前端阻塞，并让用户看到分析进度。

### 6.2 输入

```text
AnalyzeWindowJob
```

### 6.3 输出

```json
{
  "task_id": "task_123",
  "status": "running",
  "stage": "inference",
  "progress": 0.6
}
```

### 6.4 技术实现

```text
Redis：任务状态、短期结果缓存、截图 hash 去重
RQ / arq / Celery：后台任务执行
SSE / WebSocket：推送任务进度
```

### 6.5 核心状态

```text
pending：任务已创建
capturing：正在采集窗口
privacy_checking：正在做隐私过滤
inference：正在调用模型
parsing：正在解析模型输出
succeeded：分析成功
failed：分析失败
privacy_paused：敏感窗口暂停
```

### 6.6 单独验收

- 前端请求分析后立即拿到 `task_id`。
- 后端任务异步执行。
- 可以通过 `/api/tasks/{task_id}` 查询状态。
- 可以通过 SSE 接收进度。
- 相同 screenshot_hash 可以命中缓存，避免重复推理。

### 6.7 面试价值

这个模块体现的是后端工程能力：异步任务、状态机、缓存、进度推送、去重和限流。

## 7. 子系统五：PostgreSQL 数据记录与行为分析模块

### 7.1 模块目标

记录脱敏后的窗口分析结果、模型运行情况和用户行为，用于评估系统效果和展示后端数据建模能力。

### 7.2 需要记录什么

可以记录：

```text
用户点击分析按钮
用户点击候选问题
候选问题类型和排序
窗口类型
模型调用延迟
模型是否成功返回
JSON 是否解析成功
隐私暂停是否触发
用户是否清空历史
```

不默认记录：

```text
原始截图
完整 UI tree
密码字段
验证码
银行卡信息
完整私密聊天内容
```

### 7.3 技术实现

```text
PostgreSQL：持久化数据
SQLAlchemy：ORM
Alembic：数据库迁移
JSONB：存灵活事件 payload
GIN index：加速 JSONB 查询
```

### 7.4 核心表

```text
window_contexts
analysis_runs
conversations
user_events
settings
eval_samples
eval_results
```

### 7.5 单独验收

- 每次分析会生成一条 `analysis_runs`。
- 每次候选问题点击会生成一条 `user_events`。
- 可以统计 P95 延迟、失败率、候选问题点击率。
- 清空历史时能删除用户可见数据。

### 7.6 面试价值

这个模块体现的是数据闭环能力：不仅能调用模型，还能记录、分析和改进模型应用表现。

## 8. 子系统六：桌面交互模块

### 8.1 模块目标

提供真正可演示的桌面产品体验。

### 8.2 技术实现

```text
当前 MVP：
Python 3
Win32 API via ctypes
UpdateLayeredWindow per-pixel alpha
Pillow
本地 PNG 分层素材

后续产品化备选：
Tauri v2 + React + TypeScript + Vite
```

当前已经完成 `apps/desktop-floating-window/`：真实 Windows 置顶透明悬浮窗、可拖动、可切换状态、通过 `state_bridge.json` 接收外部状态。下一步不是重做 UI，而是把状态桥接到 FastAPI 事件。

### 8.3 页面与组件

```text
FloatingBubble：悬浮球
AssistantPanel：展开面板
CandidateQuestions：候选问题 chips
ChatBox：问答区域
SettingsPanel：设置页
StatusIndicator：任务状态
```

### 8.4 单独验收

- 悬浮球可置顶、可拖动、可展开。
- 点击“分析当前窗口”能调用后端。
- 能展示 analyzing、ready、error、privacy_paused 状态。
- 能展示摘要、候选问题和回答。
- 设置页能配置模型地址、是否保存历史、是否启用隐私保护。

### 8.5 面试价值

这个模块体现的是把后端 AI 能力产品化的能力。面试演示时，桌面端能让项目显得非常完整。

## 9. 串联方式：从小闭环到大闭环

整个项目应该按“纵向切片”推进，而不是按技术栈横向堆功能。

### 9.1 闭环 1：固定真实场景闭环

目标：选定一个真实窗口场景，直接用真实采集和真实模型打通最小链路。

```text
IDE / 终端报错窗口
  → pywin32 / mss / uiautomation
  → WindowContext
  → 真实 VLM
  → AnalyzeWindowResponse
```

价值：

- 所有代码都服务于最终产品。
- 第一周就能验证项目核心价值。
- 方便沉淀真实测试样本和评估基线。

### 9.2 闭环 2：FastAPI API 闭环

目标：把固定真实场景链路封装成稳定 API。

```text
POST /api/window/analyze
  → CaptureService
  → PrivacyFilter
  → ContextBuilder
  → InferenceService
  → 返回结构化结果
```

价值：

- API 从第一版开始就对接真实能力。
- 后续桌面端、Redis、PostgreSQL 都围绕这个接口扩展。

### 9.3 闭环 3：场景扩展闭环

目标：从第一个真实场景扩展到第二、第三个真实场景。

```text
IDE / 终端报错
安装失败 / 权限错误
网页表单 / 设置页面
  → 统一 WindowContext schema
  → 统一 AnalyzeWindowResponse schema
```

价值：

- 验证 schema 和 prompt 的泛化能力。
- 开始形成评估数据集。

### 9.4 闭环 4：异步任务闭环

目标：把同步分析改成异步分析。

```text
POST /api/window/analyze
  → 创建 task_id
  → Redis task queue
  → worker 执行模型推理
  → SSE 推送状态
  → 前端展示结果
```

价值：

- 解决模型推理慢的问题。
- 展示 Redis、任务队列和状态管理能力。

### 9.5 闭环 5：数据闭环

目标：把用户行为和模型表现记录到 PostgreSQL。

```text
用户行为
  → /api/events
  → user_events

模型调用
  → analysis_runs
  → latency / failure / json_parse_success
```

价值：

- 能统计系统表现。
- 能支撑评估报告。
- 面试时后端含金量明显提升。

### 9.6 闭环 6：桌面产品闭环

目标：把已完成的原生桌面悬浮窗接入后端，完成最终可演示产品。

```text
Python + Win32 桌面悬浮窗
  → FastAPI
  → Redis task
  → Inference worker
  → PostgreSQL
  → 前端展示
```

价值：

- 项目从“后端 demo”变成“完整 AI 应用”。

## 10. 推荐开发顺序

### 第 1 周：固定真实场景闭环

任务：

- 选定第一个真实场景，推荐 `IDE / 终端报错` 或 `安装失败 / 权限错误窗口`。
- 实现当前活动窗口标题和应用名读取。
- 实现当前活动窗口截图。
- 实现基础 UIA 控件树读取，失败时允许降级。
- 接入一个真实 VLM 接口。
- 写第一个窗口分析 prompt。
- 输出真实的摘要、关键点、候选问题和 caution。

产出：

- 一个针对真实窗口场景可运行的端到端分析脚本。

### 第 2 周：FastAPI 封装

任务：

- FastAPI 项目初始化。
- Pydantic schemas。
- `/health`、`/api/window/analyze`、`/api/window/ask`。
- CaptureService。
- ContextBuilder。
- PrivacyFilter。
- InferenceService。

产出：

- 一个通过 API 分析真实当前窗口的后端。

### 第 3 周：场景扩展与提示词稳定

任务：

- 增加第二个真实场景：安装失败 / 权限错误窗口。
- 增加第三个真实场景：网页表单 / 设置页面。
- 优化窗口分析 prompt。
- 优化窗口问答 prompt。
- 实现 JSON 输出解析和错误兜底。

产出：

- 三类真实场景都能输出稳定结构化结果。

### 第 4 周：Redis 异步任务

任务：

- 接入 Redis。
- 实现任务队列。
- 实现任务状态查询。
- 实现 SSE 进度推送。
- 实现 screenshot_hash 去重。

产出：

- 分析任务不再阻塞 API，前端可以看到进度。

### 第 5 周：PostgreSQL 数据层

任务：

- Docker Compose 配置 PostgreSQL 和 Redis。
- SQLAlchemy models。
- Alembic migration。
- user_events、analysis_runs、window_contexts、conversations。
- 行为事件上报 API。

产出：

- 可以统计用户行为、模型延迟、失败率和候选问题点击情况。

### 第 6 周：桌面端产品化

任务：

- 将已完成的 Python + Win32 悬浮窗接入 FastAPI 和 SSE。
- 点击悬浮窗触发窗口分析。
- 展示摘要、候选问题和错误/隐私状态。
- 补充轻量设置入口：暂停读取、清空历史、本地模型状态。
- 如果需要安装包、系统托盘、自动更新，再评估 Tauri 或 PyInstaller 路线。

产出：

- 可以演示的桌面 AI 助手。

### 第 7 周：评估体系与文档

任务：

- 构建小型截图测试集。
- 编写评估脚本。
- 生成评估报告。
- 完善 README、架构文档、隐私设计文档、面试讲解稿。

产出：

- 一个能跑、能讲、能量化的完整面试项目。

## 11. 模块之间的接口

为了让工程可拆可合，每个模块都用清晰的数据接口连接。

### 11.1 RawWindowCapture

```json
{
  "app_name": "string",
  "window_title": "string",
  "window_bounds": [0, 0, 0, 0],
  "screenshot_base64": "string",
  "screenshot_hash": "string",
  "visible_text": "string",
  "ui_elements": []
}
```

### 11.2 WindowContext

```json
{
  "app_name": "string",
  "window_title": "string",
  "screenshot_hash": "string",
  "visible_text": "string",
  "ui_elements": [],
  "privacy": {
    "level": "normal",
    "blocked": false,
    "reasons": []
  },
  "captured_at": "datetime"
}
```

### 11.3 AnalyzeWindowResponse

```json
{
  "window_type": "error_dialog",
  "summary": "string",
  "key_points": [],
  "candidate_questions": [],
  "caution": "string"
}
```

### 11.4 AnalysisTaskStatus

```json
{
  "task_id": "string",
  "status": "running",
  "stage": "inference",
  "progress": 0.6,
  "window_context_id": 12
}
```

### 11.5 UserEvent

```json
{
  "event_type": "candidate_question_clicked",
  "window_context_id": 12,
  "event_payload": {
    "question_category": "next_step",
    "question_rank": 1
  }
}
```

## 12. 最小可展示版本

如果时间有限，最小可展示版本只做这些：

```text
FastAPI
真实窗口标题读取
真实窗口截图
simple UIA
隐私关键词过滤
MiniCPM-V 4.6 GGUF + llama.cpp
摘要 + 候选问题 + 问答
PostgreSQL 记录 analysis_runs 和 user_events
简单 Web 页面或 Tauri 面板
```

可以暂缓：

```text
自动窗口切换分析
复杂 UIA 控件树
完整 Tauri 设置页
模型本地打包
大型评估集
账号系统
云同步
```

## 13. 最终串联后的面试表达

可以这样讲：

```text
我没有一开始直接做一个大而全的 Agent，而是把它拆成窗口采集、上下文构建、模型推理、异步任务、数据记录和桌面交互六个模块。

第一阶段我先做出真实 Windows 桌面悬浮窗壳，再选择 IDE / 终端报错这类真实具体场景，打通真实窗口采集、真实多模态模型推理和结构化输出；第二阶段把能力封装成 FastAPI API；第三阶段扩展到安装失败、网页表单等更多场景；第四阶段用 Redis 把模型推理改成异步任务；第五阶段用 PostgreSQL 记录脱敏行为和模型调用链路；最后围绕这个桌面悬浮窗完成可演示产品。

这样项目既有大模型应用的核心能力，也有后端工程的异步任务、缓存、数据库和评估闭环。
```
