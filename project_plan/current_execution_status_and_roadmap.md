# 当前执行状态与路线

更新时间：2026-07-03

## 当前定位

项目当前定位为 Windows 本地桌宠式窗口 Copilot：

```text
快速观察当前前台窗口
用本地 VLM 生成轻量摘要和关键点
用户追问时结合当前观察和短期记忆回答
不自动点击、输入、提交或执行电脑操作
```

它可以称为具备感知、短期记忆和对话能力的桌面 Copilot 原型，但当前不包装成完整自主 Agent。

## 已完成能力

- 原生 Windows 置顶透明悬浮窗，支持拖动、状态表情和摘要面板。
- FastAPI 后端，端口固定为 `127.0.0.1:18080`。
- 前台窗口标题、进程、坐标和截图采集。
- 自动观察 watcher：截图 hash 去重、同窗口内容变化检测、分析冷却控制、启动后自动观察。
- llama.cpp `llama-server` 本地模型服务管理。
- MiniCPM-V 4.6 F16 视觉语言模型分析窗口截图。
- SQLite RuntimeStore 保存助手状态、最新窗口分析、当前对话、历史对话和短期会话记忆。
- 用户点击“点击提问”后打开独立悬浮对话窗，输入问题时暂停自动观察并生成回答。
- 独立悬浮对话窗展示最近历史、当前流式回答、输入框和恢复观察按钮。
- `ObservationCard` 最小观察卡，统一当前窗口低噪声上下文。

## 当前主链路

```text
apps/desktop-floating-window
  -> FastAPI assistant/window API
  -> WindowCaptureService
  -> ObservationBuilder
  -> WindowAnalysisService
  -> MiniCPM-V via llama.cpp
  -> SQLite RuntimeStore latest analysis / memory / conversation
  -> desktop mascot summary panel
  -> desktop floating chat window
```

## 已清理内容

- 独立的大上下文构建 service。
- 独立的重型流程编排 service。
- 复杂上下文和流程编排 schema。
- 未落地的长期记忆字段。
- 与当前主链路无关的重型测试。
- 外部运行时状态服务和容器启动脚本。

当前保留的小结构：

```text
RuntimeStore
ObservationCard
MemoryService
WindowAnalysisService
AssistantChatService
```

## 当前不做

- 不做 OCR 主链路。
- 不做 UI Automation / UIA 主链路。
- 不做自动电脑操作。
- 不做复杂规划型 Agent。
- 不做长期人格记忆。
- 不做多套替代路线来遮蔽主链路质量。

## 下一步路线

### Step 1：观察质量评估

目标：判断当前 VLM 截图摘要是否足够支撑桌宠提示。

验收：

- 收集 20 个真实窗口样本。
- 对比原截图、模型摘要、关键点和用户可用性。
- 标记不确定、误读、过度推断和隐私暂停场景。

### Step 2：Memory 最小闭环

目标：让用户追问能利用最近窗口摘要和本轮会话信息。

验收：

- 最近观察能写入 working memory。
- 用户问题和回答能进入 session memory。
- 新问题检索最近相关记忆，不注入无关旧内容。

### Step 3：提示时机与桌面交互

目标：小模型优势体现在速度和轻量互动，而不是重任务自动化。

验收：

- 页面稳定后才提示。
- 截图无变化时不重复提示。
- 用户进入提问时自动观察暂停。
- 用户恢复后继续观察。

### Step 4：产品化边界

目标：应用级部署前，先把本地链路打磨稳定。

验收：

- 启动检查能明确报告缺失模型或端口占用。
- 模型不可用时进入明确错误状态。
- 截图保存目录、缓存和日志目录可控。
- 测试覆盖状态 API、捕获、观察、分析、记忆和问答流程。
