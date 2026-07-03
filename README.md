# Local Window Copilot

Windows 本地桌宠式窗口 Copilot 原型。

它通过悬浮机器人观察当前前台窗口，使用本地 FastAPI 服务和本地 MiniCPM-V / llama.cpp 生成轻量摘要、候选问题，并在用户追问时结合最近观察和短期会话记忆回答。

当前定位：

```text
快速感知用户正在做什么
给出轻量互动提示和候选问题
用户具体提问时，结合当前截图摘要、最近观察和短期记忆回答
不自动点击、不自动输入、不自动执行电脑操作
不做 OCR/UIA 主链路
不做重型规划 Agent
```

## 主链路

```text
Windows 桌面悬浮窗
-> FastAPI backend
-> SQLite RuntimeStore
-> llama.cpp server
-> MiniCPM-V 视觉语言模型
-> 小机器人摘要面板
-> 独立悬浮对话窗
```

`RuntimeStore` 是本地 SQLite 文件，默认路径：

```text
backend/data/runtime/runtime.sqlite3
```

它保存助手状态、最近窗口分析、当前对话、历史对话和短期会话记忆。普通用户不需要安装额外服务。

## 启动

```powershell
cd D:\AI_Workspace\window
.\scripts\start_dev.cmd
```

启动前检查：

```powershell
python .\scripts\check_environment.py --for-start
```

手动启动后端：

```powershell
cd D:\AI_Workspace\window\backend
uv run uvicorn app.main:app --host 127.0.0.1 --port 18080 --reload --no-access-log
```

手动启动悬浮窗：

```powershell
cd D:\AI_Workspace\window
.\apps\desktop-floating-window\start_desktop_window.cmd
```

## 接口

```text
GET  /health
GET  /api/assistant/state
POST /api/assistant/state
GET  /api/assistant/latest
POST /api/assistant/questions
GET  /api/assistant/conversation
GET  /api/assistant/conversations
POST /api/assistant/resume
POST /api/window/capture
POST /api/window/watch/start
POST /api/window/watch/stop
GET  /api/window/watch/status
```

## 代码入口

```text
backend/app/main.py
backend/app/services/runtime_store.py
backend/app/services/window_capture.py
backend/app/services/window_watcher.py
backend/app/services/window_analysis.py
backend/app/services/observation_builder.py
backend/app/services/memory.py
backend/app/services/assistant_chat.py
apps/desktop-floating-window/desktop_floating_window.py
```

## 测试

```powershell
cd D:\AI_Workspace\window\backend
uv run pytest --basetemp D:\AI_Workspace\window\.tmp\pytest-basetemp
```
