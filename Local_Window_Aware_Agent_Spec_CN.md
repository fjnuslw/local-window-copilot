# 基于 MiniCPM-V 4.6 的本地窗口感知非执行型 Agent 项目 Spec

## 1. 项目名称

基于 MiniCPM-V 4.6 的本地窗口感知非执行型 Agent

英文名可暂定为：

```text
Local Window-Aware Agent
```

## 2. 项目目标

本项目旨在开发一个运行在用户本地电脑上的悬浮式窗口感知助手。

系统通过读取当前活动窗口的截图、窗口标题、应用名称和 UI Automation 控件树，构建结构化的 `WindowContext`，并基于 MiniCPM-V 4.6 GGUF 进行端侧多模态推理，生成当前窗口摘要、关键点、候选问题和文本回答。

项目强调本地化、隐私保护和非执行型交互。

系统默认不上传截图、不上传窗口内容、不控制鼠标键盘、不执行任何系统操作。

## 3. 项目定位

本项目不是自动化电脑控制工具，也不是全功能 GUI Agent。

它的准确定位是：

```text
本地窗口感知 Agent
= 当前窗口理解
+ 候选问题生成
+ 文本问答
+ 隐私保护
- 自动点击
- 自动输入
- 系统控制
```

系统只做“观察、理解、建议、回答”，不做“执行”。

## 4. 核心能力范围

### 4.1 系统可以做什么

系统可以：

- 读取当前活动窗口标题。
- 识别当前活动应用名称。
- 捕获当前活动窗口截图。
- 读取 UI Automation 控件树。
- 构建结构化窗口上下文 `WindowContext`。
- 调用本地 MiniCPM-V 4.6 GGUF 模型进行多模态理解。
- 生成当前窗口摘要。
- 提取当前窗口关键点。
- 自动生成候选问题。
- 根据用户点击的候选问题或手动输入问题进行文本回答。
- 在悬浮窗中展示摘要、候选问题和回答。
- 本地保存必要的窗口分析历史。
- 允许用户清除本地历史。
- 对敏感窗口进行过滤或暂停分析。

### 4.2 系统不能做什么

系统不得：

- 自动点击按钮。
- 自动输入文字。
- 自动移动鼠标。
- 自动触发快捷键。
- 自动提交表单。
- 自动删除文件。
- 自动发送消息。
- 自动修改系统设置。
- 自动安装或卸载软件。
- 默认上传截图或窗口内容到云端。
- 在用户不知情的情况下持续记录屏幕。

## 5. MVP 目标平台

MVP 阶段优先支持：

```text
Windows 10 / Windows 11
```

推荐硬件：

```text
NVIDIA GPU
8GB 显存以上
16GB 内存以上
```

MVP 阶段不强制支持：

```text
macOS
Linux
移动端
CPU-only 低配运行
```

CPU fallback 可以作为后续版本扩展。

## 6. 推荐技术栈

### 6.1 桌面端

推荐：

```text
Tauri v2
React
TypeScript
```

桌面端负责：

- 悬浮窗 UI。
- 候选问题展示。
- 文本输入框。
- 聊天结果展示。
- 设置页面。
- 与本地 FastAPI 服务通信。

### 6.2 本地后端

推荐：

```text
FastAPI
Python 3.11+
Pydantic
SQLite
SQLModel / SQLAlchemy
```

本地后端负责：

- 提供本地 HTTP API。
- 捕获窗口信息。
- 构建 `WindowContext`。
- 管理模型调用。
- 执行隐私过滤。
- 生成候选问题。
- 保存本地历史。
- 提供模型状态接口。

### 6.3 模型推理

推荐：

```text
MiniCPM-V 4.6 GGUF
llama.cpp server
```

推理方式：

```text
Tauri 前端
  ↓
FastAPI 本地服务
  ↓
llama.cpp server
  ↓
MiniCPM-V 4.6 GGUF
```

不建议在 MVP 中直接打包完整 PyTorch 环境。

### 6.4 窗口信息采集

Windows 下可使用：

```text
Windows UI Automation
pywin32
uiautomation
mss
Pillow
```

可采集内容：

- 当前活动窗口句柄。
- 当前窗口标题。
- 当前应用进程名。
- 当前窗口截图。
- 当前窗口控件树。
- 控件文本。
- 控件类型。
- 控件位置。
- 是否为密码控件。

### 6.5 本地数据库

MVP 使用：

```text
SQLite
```

不建议本地版本使用 PostgreSQL。

PostgreSQL 适用于后续云端能力，例如：

- 用户账号。
- 云同步。
- 订阅系统。
- 多设备同步。
- 用户反馈。
- 远程配置。
- 模型版本管理。

## 7. Agent Loop 设计

本项目采用非执行型 agent loop：

```text
Observe → Understand → Suggest → Respond
```

### 7.1 Observe：观察窗口

系统采集当前活动窗口信息。

输入：

- 应用名称。
- 窗口标题。
- 当前窗口截图。
- UI Automation 控件树。
- 可见文本。
- 时间戳。
- 截图哈希。
- UI tree 哈希。

输出：

```text
WindowContext
```

### 7.2 Understand：理解窗口

MiniCPM-V 4.6 根据窗口截图和结构化信息理解当前窗口。

输出：

- 窗口类型。
- 一句话摘要。
- 关键点。
- 风险提示。
- 信息不足说明。

### 7.3 Suggest：生成候选问题

系统根据当前窗口内容生成 3 到 5 个候选问题。

候选问题用于降低用户提问成本。

例如，当窗口是错误弹窗时：

```text
这个错误是什么意思？
我下一步应该怎么做？
这个错误有风险吗？
可以忽略这个提示吗？
需要管理员权限吗？
```

当窗口是表单页面时：

```text
这个页面让我填写什么？
哪些字段是必填？
有没有隐私风险？
我应该怎么检查填写内容？
```

当窗口是文档或网页时：

```text
帮我总结当前内容
这段内容的重点是什么？
这里有没有需要注意的地方？
帮我解释这个页面
```

### 7.4 Respond：回答用户

用户可以点击候选问题，也可以手动输入问题。

系统基于最新的 `WindowContext` 进行回答。

回答必须是文本回答。

回答中不得声称自己会执行操作。

允许的表达：

```text
你可以手动点击右下角的“继续”按钮。
```

不允许的表达：

```text
我将为你点击“继续”按钮。
```

## 8. 核心功能设计

## 8.1 悬浮窗

悬浮窗应支持三种状态。

### 8.1.1 折叠态

表现：

- 小圆球或小卡片。
- 置顶显示。
- 可拖动。
- 点击后展开。

状态提示：

```text
空闲
分析中
已就绪
隐私暂停
错误
```

### 8.1.2 候选态

展示：

- 当前应用名。
- 当前窗口标题。
- 当前窗口摘要。
- 3 到 5 个候选问题。
- “重新分析当前窗口”按钮。
- “暂停读取”按钮。

### 8.1.3 对话态

展示：

- 当前窗口上下文摘要。
- 用户问题。
- 助手回答。
- 候选问题 chips。
- 文本输入框。

## 8.2 手动分析当前窗口

用户点击：

```text
分析当前窗口
```

流程：

```text
获取当前活动窗口
  ↓
捕获窗口截图
  ↓
读取 UIA 控件树
  ↓
执行隐私过滤
  ↓
构建 WindowContext
  ↓
调用 MiniCPM-V 4.6
  ↓
生成摘要和候选问题
  ↓
悬浮窗展示结果
```

## 8.3 窗口切换自动分析

MVP 可选实现。

行为：

- 监听活动窗口变化。
- 窗口切换后等待 800ms。
- 判断窗口标题或截图哈希是否变化。
- 若变化明显，则自动分析。
- 自动分析最小间隔建议为 5 秒。

避免频繁推理导致资源占用过高。

## 8.4 候选问题机制

候选问题是本项目的核心交互创新点。

每个候选问题包含：

```json
{
  "question": "这个错误是什么意思？",
  "category": "explanation",
  "reason": "当前窗口包含错误码和失败提示",
  "priority": 0.92
}
```

候选问题生成方式：

```text
规则模板
  +
MiniCPM-V 4.6 上下文生成
```

不建议完全依赖模型自由生成。

## 8.5 用户问答

用户可通过两种方式提问：

```text
点击候选问题
手动输入问题
```

系统回答时应使用：

- 当前窗口截图。
- 当前窗口标题。
- 当前应用名称。
- UIA 控件树。
- 上一次窗口摘要。
- 当前用户问题。
- 最近少量对话历史。

## 9. 数据模型设计

使用 Pydantic 定义 API 输入输出结构。

## 9.1 UIElement

```python
from pydantic import BaseModel
from typing import Optional, List

class UIElement(BaseModel):
    element_id: Optional[str] = None
    role: Optional[str] = None
    control_type: Optional[str] = None
    name: Optional[str] = None
    value: Optional[str] = None
    bounds: Optional[List[int]] = None
    is_password: bool = False
    is_enabled: Optional[bool] = None
    is_visible: Optional[bool] = None
```

## 9.2 WindowContext

```python
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

class WindowContext(BaseModel):
    app_name: str
    window_title: str
    window_bounds: Optional[List[int]] = None
    screenshot_base64: Optional[str] = None
    screenshot_hash: Optional[str] = None
    visible_text: Optional[str] = None
    ui_elements: List[UIElement] = []
    captured_at: datetime
```

## 9.3 CandidateQuestion

```python
from pydantic import BaseModel
from typing import Optional

class CandidateQuestion(BaseModel):
    question: str
    category: Optional[str] = None
    reason: Optional[str] = None
    priority: Optional[float] = None
```

## 9.4 AnalyzeWindowResponse

```python
from pydantic import BaseModel
from typing import List, Literal, Optional

class AnalyzeWindowResponse(BaseModel):
    window_type: Literal[
        "error_dialog",
        "form",
        "document",
        "webpage",
        "ide",
        "settings",
        "installer",
        "chat",
        "file_explorer",
        "unknown"
    ]
    summary: str
    key_points: List[str]
    candidate_questions: List[CandidateQuestion]
    caution: Optional[str] = None
```

## 9.5 AskWindowRequest

```python
from pydantic import BaseModel
from typing import Optional, List

class AskWindowRequest(BaseModel):
    question: str
    window_context_id: Optional[int] = None
    window_context: Optional[WindowContext] = None
    conversation_history: List[dict] = []
```

## 9.6 AskWindowResponse

```python
from pydantic import BaseModel
from typing import Optional

class AskWindowResponse(BaseModel):
    answer: str
    confidence: Optional[str] = None
    caution: Optional[str] = None
```

## 10. API 设计

所有 API 均运行在本地。

默认地址：

```text
http://127.0.0.1:18080
```

## 10.1 健康检查

```http
GET /health
```

响应：

```json
{
  "status": "ok",
  "model_loaded": true
}
```

## 10.2 分析当前窗口

```http
POST /api/window/analyze
```

请求示例：

```json
{
  "app_name": "Chrome",
  "window_title": "安装失败 - 帮助页面",
  "screenshot_base64": "...",
  "visible_text": "Error 0x80070005 Access denied",
  "ui_elements": []
}
```

响应示例：

```json
{
  "window_type": "error_dialog",
  "summary": "当前窗口显示安装失败，错误信息可能与权限不足有关。",
  "key_points": [
    "出现错误码 0x80070005",
    "窗口包含 Retry 和 Cancel 选项",
    "可能需要管理员权限"
  ],
  "candidate_questions": [
    {
      "question": "这个错误是什么意思？",
      "category": "explanation"
    },
    {
      "question": "我下一步应该怎么做？",
      "category": "next_step"
    }
  ],
  "caution": "不要在不了解原因的情况下反复重试安装。"
}
```

## 10.3 基于当前窗口提问

```http
POST /api/window/ask
```

请求示例：

```json
{
  "question": "我下一步应该怎么做？",
  "window_context_id": 12
}
```

响应示例：

```json
{
  "answer": "这个错误通常和权限不足有关。你可以先手动尝试以管理员身份重新运行安装程序。如果仍然失败，再查看安装路径和安全软件拦截情况。",
  "confidence": "medium",
  "caution": "不要关闭安全软件或修改系统权限，除非你确认安装包来源可信。"
}
```

## 10.4 获取最近窗口上下文

```http
GET /api/context/recent
```

## 10.5 清除历史

```http
DELETE /api/context/history
```

## 10.6 模型状态

```http
GET /api/model/status
```

响应示例：

```json
{
  "runtime": "llama.cpp",
  "model": "MiniCPM-V 4.6 GGUF",
  "loaded": true,
  "device": "cuda",
  "memory_usage_mb": 4200
}
```

## 11. Prompt 设计

## 11.1 窗口分析 Prompt

```text
你是一个本地窗口感知助手。你只能分析当前窗口内容并生成文字反馈，不能执行任何系统操作。

请根据当前窗口截图、窗口标题、应用名和 UI 控件信息，完成以下任务：

1. 判断窗口类型。
2. 用一句话总结当前窗口。
3. 提取 3 到 5 个关键点。
4. 生成 3 到 5 个用户可能想问的候选问题。
5. 如果窗口可能涉及密码、支付、隐私、账号、删除、安装、权限变更等风险，请给出 caution。

要求：
- 输出必须是 JSON。
- 不要声称你可以点击、输入、提交或操作电脑。
- 如果信息不足，请明确说明不确定。
- 用中文输出。
```

期望输出格式：

```json
{
  "window_type": "...",
  "summary": "...",
  "key_points": ["..."],
  "candidate_questions": [
    {
      "question": "...",
      "category": "...",
      "reason": "...",
      "priority": 0.9
    }
  ],
  "caution": "..."
}
```

## 11.2 窗口问答 Prompt

```text
你是一个本地窗口感知助手。你只能根据当前窗口上下文回答用户问题，不能执行任何系统操作。

当前窗口上下文包括：
- 应用名
- 窗口标题
- 截图内容
- UI 控件树
- 上一次窗口摘要
- 用户问题

请回答用户问题。

要求：
- 用中文回答。
- 简洁但具体。
- 如果涉及风险，明确提醒。
- 不要说“我将点击”“我会输入”“我已经操作”。
- 只能说“你可以手动……”
- 如果看不清或信息不足，请说明不确定。
```

## 12. 数据库设计

MVP 使用 SQLite。

## 12.1 window_contexts 表

字段：

```text
id
app_name
window_title
window_bounds_json
screenshot_hash
screenshot_path
visible_text
ui_elements_json
summary
window_type
key_points_json
candidate_questions_json
caution
created_at
```

## 12.2 conversations 表

字段：

```text
id
window_context_id
user_question
assistant_answer
confidence
caution
created_at
```

## 12.3 settings 表

字段：

```text
key
value
updated_at
```

建议设置项：

```text
auto_analyze_enabled
save_history_enabled
sensitive_window_protection_enabled
model_path
llama_server_url
max_candidate_questions
analysis_interval_seconds
```

## 13. 隐私保护设计

隐私保护是本项目的核心约束。

必须实现：

- 默认本地推理。
- 默认不上传截图。
- 默认不上传 UI tree。
- 本地历史可关闭。
- 本地历史可清除。
- 自动过滤密码控件。
- 密码字段不得进入模型输入。
- 检测到敏感窗口时暂停自动分析。
- 用户可以手动暂停窗口读取。

敏感窗口关键词：

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

敏感窗口提示：

```text
当前窗口可能包含敏感信息，已暂停自动分析。你可以手动确认是否继续。
```

## 14. 错误处理

系统需要处理以下异常：

- 模型服务未启动。
- 模型文件不存在。
- 模型加载失败。
- 显存不足。
- 当前窗口捕获失败。
- UI Automation 读取失败。
- 截图失败。
- 模型输出 JSON 解析失败。
- 敏感窗口被拦截。
- 用户关闭历史记录。
- llama.cpp server 无响应。

前端提示示例：

```text
模型服务未启动，请在设置中检查模型路径。
```

```text
当前窗口可能包含敏感信息，已暂停自动分析。
```

```text
当前窗口截图失败，请尝试重新分析。
```

## 15. 性能要求

MVP 目标：

```text
窗口信息采集：< 500ms
WindowContext 构建：< 300ms
窗口分析推理：8GB 显存设备上尽量 < 8s
普通问答推理：尽量 < 6s
前端不能阻塞
分析过程中展示 loading 状态
```

需要记录：

- 平均延迟。
- P95 延迟。
- 模型显存占用。
- 截图大小。
- UI tree 节点数量。
- JSON 解析失败率。

## 16. 评估模块

需要构建一个简单测试集。

窗口类别包括：

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

每条测试样本包含：

- 截图。
- 应用名。
- 窗口标题。
- 可见文本。
- 期望窗口类型。
- 期望摘要关键词。
- 期望候选问题类型。

评估指标：

```text
窗口类型准确率
摘要关键词覆盖率
候选问题相关性
平均推理延迟
P95 推理延迟
显存占用
```

输出报告格式：

```json
{
  "window_type_accuracy": 0.82,
  "avg_summary_score": 0.78,
  "avg_candidate_relevance": 0.81,
  "avg_latency_seconds": 5.6,
  "p95_latency_seconds": 8.9
}
```

同时生成 Markdown 报告。

## 17. 前端界面要求

## 17.1 悬浮球

要求：

- 置顶显示。
- 可拖动。
- 可点击展开。
- 显示当前状态。
- 支持暂停读取。

状态：

```text
idle
analyzing
ready
error
privacy_paused
```

## 17.2 展开面板

包含区域：

```text
当前窗口
候选问题
对话区
控制按钮
```

当前窗口区域显示：

- 应用名。
- 窗口标题。
- 一句话摘要。

候选问题区域显示：

- 3 到 5 个候选问题 chips。
- 点击后自动发起问答。

对话区显示：

- 用户问题。
- 助手回答。

控制按钮：

- 分析当前窗口。
- 重新分析。
- 暂停读取。
- 清除历史。
- 设置。

## 18. MVP 范围

## 18.1 MVP 必须实现

- Tauri 悬浮窗。
- FastAPI 本地后端。
- 活动窗口标题读取。
- 当前窗口截图。
- 基础 UI Automation 控件树读取。
- WindowContext 构建。
- 隐私过滤。
- MiniCPM-V 4.6 GGUF 本地推理。
- 窗口分析 API。
- 候选问题生成。
- 用户问答 API。
- SQLite 本地存储。
- 敏感窗口暂停分析。
- 清除本地历史。
- 基础评估脚本。

## 18.2 MVP 不实现

- 语音输入。
- 语音输出。
- 鼠标控制。
- 键盘控制。
- 自动点击。
- 自动填表。
- 云同步。
- 用户账号。
- PostgreSQL。
- 支付系统。
- 浏览器插件。
- 多设备同步。
- RAG。
- 模型微调。
- 自动执行任务。

## 19. 推荐项目目录结构

```text
local-window-agent/
  README.md

  desktop/
    package.json
    src/
      App.tsx
      components/
        FloatingBubble.tsx
        AssistantPanel.tsx
        CandidateQuestions.tsx
        ChatBox.tsx
        SettingsPanel.tsx
      api/
        client.ts
      styles/
    src-tauri/
      tauri.conf.json
      src/

  backend/
    pyproject.toml
    app/
      main.py
      api/
        health.py
        window.py
        model.py
        context.py
      core/
        config.py
        logging.py
      schemas/
        window.py
        model.py
        chat.py
      services/
        capture_service.py
        uia_service.py
        context_builder.py
        privacy_filter.py
        inference_service.py
        candidate_service.py
      db/
        session.py
        models.py
        crud.py
      prompts/
        analyze_window.txt
        ask_window.txt

  runtime/
    llama-server/
    models/
      README.md

  eval/
    dataset/
    run_eval.py
    metrics.py
    report_template.md

  docs/
    architecture.md
    privacy.md
    development.md
```

## 20. 开发顺序

建议按以下顺序实现：

```text
1. FastAPI 后端骨架
2. Pydantic schemas
3. /health 接口
4. 当前活动窗口元信息读取
5. 当前窗口截图
6. UI Automation 控件树读取
7. WindowContext builder
8. 隐私过滤器
9. llama.cpp 推理服务封装
10. /api/window/analyze
11. /api/window/ask
12. SQLite 持久化
13. Tauri 悬浮窗 UI
14. 候选问题展示
15. 手动分析按钮
16. 用户文本输入
17. 设置页面
18. 评估脚本
19. 打包脚本
```

## 21. 验收标准

MVP 完成标准：

```text
1. 应用可以作为桌面悬浮窗启动。
2. 用户可以点击“分析当前窗口”。
3. 系统可以获取当前窗口标题和应用名。
4. 系统可以捕获当前窗口截图。
5. 系统可以读取基础 UIA 控件树。
6. 后端可以构建 WindowContext。
7. MiniCPM-V 4.6 在本地完成窗口分析。
8. 前端展示窗口摘要。
9. 前端展示 3 到 5 个候选问题。
10. 用户点击候选问题后可以得到回答。
11. 系统不执行任何鼠标、键盘或系统操作。
12. 敏感窗口可以触发暂停分析。
13. 用户可以清除本地历史。
14. 评估脚本可以在小型截图测试集上运行。
15. 项目不依赖云端 API。
```

## 22. 简历表述参考

项目名称：

```text
基于 MiniCPM-V 4.6 的本地窗口感知非执行型 Agent
```

项目描述：

```text
设计并实现一个运行在本地端的悬浮式窗口感知 Agent。系统通过读取当前活动窗口截图、窗口标题和 UI Automation 控件树构建 WindowContext，并调用 MiniCPM-V 4.6 GGUF 进行端侧多模态理解，生成当前窗口摘要、关键点和候选问题。系统默认不执行鼠标键盘操作，强调本地推理、隐私保护和低资源部署。
```

技术亮点：

```text
- 设计 Observe → Understand → Suggest → Respond 的非执行型 agent loop。
- 融合窗口截图、窗口标题和 UI Automation 控件树构建 WindowContext。
- 基于 MiniCPM-V 4.6 GGUF 与 llama.cpp 实现端侧多模态推理。
- 实现候选问题生成机制，降低用户面对复杂窗口时的提问成本。
- 设计隐私保护策略，包括本地推理、敏感控件过滤和截图不出端。
- 构建窗口类型测试集，从摘要准确率、候选问题相关性和推理延迟等维度评估系统效果。
```
