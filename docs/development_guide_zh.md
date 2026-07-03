# Local Window Copilot 中文开发手册

当前真实开发链路：

```text
Windows 桌面悬浮窗
-> FastAPI backend
-> SQLite RuntimeStore
-> llama.cpp / MiniCPM-V
```

悬浮窗只请求 FastAPI。FastAPI 使用本地 SQLite RuntimeStore 保存状态、最近分析、当前对话、历史对话和短期会话记忆。

## 一键启动

```powershell
cd D:\AI_Workspace\window
.\scripts\start_dev.cmd
```

启动前检查：

```powershell
python .\scripts\check_environment.py --for-start
```

## 后端

```powershell
cd D:\AI_Workspace\window\backend
uv run uvicorn app.main:app --host 127.0.0.1 --port 18080 --reload --no-access-log
```

常用地址：

```text
http://127.0.0.1:18080/health
http://127.0.0.1:18080/docs
```

后端职责：

```text
管理助手状态
自动观察当前前台窗口
捕获窗口截图
构建 ObservationCard
启动和调用 llama-server
调用 MiniCPM-V 生成摘要
处理用户追问
使用本地 RuntimeStore 保存状态、摘要、对话和短期记忆
```

## 桌面悬浮窗

```powershell
cd D:\AI_Workspace\window
.\apps\desktop-floating-window\start_desktop_window.cmd
```

悬浮窗行为：

```text
显示机器人状态
显示当前页面摘要
显示候选问题按钮
支持自定义提问
用户提问后打开独立悬浮对话窗
对话窗展示历史对话、当前回答和输入框
回答结束后点击“观察”恢复自动观察
```

## 模型服务

模型文件：

```text
runtime/models/minicpm-v4.6/MiniCPM-V-4_6-F16.gguf
runtime/models/minicpm-v4.6/mmproj-model-f16.gguf
```

llama.cpp 运行时：

```text
runtime/llama.cpp/llama-server.exe
```

FastAPI 会通过 `ModelRuntimeManager` 检查并启动本地 `llama-server`。端口固定为：

```text
127.0.0.1:18181
```

## 用户提问流程

```text
用户点击候选问题或输入自定义问题
-> 小机器人打开独立悬浮对话窗
-> 悬浮窗 POST /api/assistant/questions
-> 后端停止 WindowWatcher
-> 后端读取最近 ObservationCard、窗口摘要和短期记忆
-> MiniCPM-V 生成回答
-> 回答写入 RuntimeStore conversation buffer
-> 对话窗轮询 /api/assistant/conversation 和 /api/assistant/conversations
-> 用户点击“观察”
-> 悬浮窗 POST /api/assistant/resume
-> 后端重新启动 WindowWatcher
```

用户交流期间不继续自动扫屏，避免上下文漂移。

## 测试

```powershell
cd D:\AI_Workspace\window\backend
uv run pytest --basetemp D:\AI_Workspace\window\.tmp\pytest-basetemp
```
