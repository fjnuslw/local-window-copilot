# Local Window Copilot 中文开发手册

当前主线 spec：[docs/context_observation_tool_mainline_spec_zh.md](./context_observation_tool_mainline_spec_zh.md)。若本文与主线 spec 冲突，以主线 spec 为准。

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
uv run uvicorn app.main:app --host 127.0.0.1 --port 18081 --reload --no-access-log
```

常用地址：

```text
http://127.0.0.1:18081/health
http://127.0.0.1:18081/docs
http://127.0.0.1:18081/webui/
```

后端职责：

```text
管理助手状态
自动观察当前前台窗口
捕获窗口截图
构建 ObservationCard
启动和调用 llama-server
调用 MiniCPM-V 生成结构化窗口观察
处理用户追问
使用本地 RuntimeStore 保存状态、结构化观察、当前会话和短期记忆
```

## 桌面悬浮窗

```powershell
cd D:\AI_Workspace\window
.\apps\desktop-floating-window\start_desktop_window.cmd
```

悬浮窗行为：

```text
显示机器人状态
显示轻量状态和提问入口，不默认展示完整观察
显示“点击提问”入口
点击后打开独立悬浮对话窗
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
用户点击“点击提问”，并在独立对话窗输入问题
-> 小机器人打开独立悬浮对话窗
-> 悬浮窗 POST /api/assistant/questions
-> 后端暂停 WindowWatcher，避免对话窗污染观察目标
-> 后端构建 messages：稳定 system prompt + profile + 当前会话历史 + 当前问题
-> 当前窗口观察和最近窗口观察不默认注入
-> MiniCPM-V 自行判断是否调用唯一工具 memory.search(query)
-> 工具按 query 返回相关观察/记忆证据和 record_id/screenshot 信息
-> MiniCPM-V 基于工具证据或明确缺证据生成回答
-> 回答写入 RuntimeStore 当前 conversation buffer
-> 对话窗轮询 /api/assistant/conversation 和 /api/assistant/conversations
-> 用户点击“观察”
-> 悬浮窗隐藏对话窗并 POST /api/assistant/observe
-> 后端捕获目标非 Copilot 窗口，写入 window:latest_analysis 和 window:summaries
```

用户交流期间不继续自动扫屏，避免上下文漂移。手动观察必须返回可调试结果：新记录、窗口标题、截图 hash 或明确失败原因。
## 测试

```powershell
cd D:\AI_Workspace\window\backend
uv run pytest --basetemp D:\AI_Workspace\window\.tmp\pytest-basetemp
```
