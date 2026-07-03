# Local Window Copilot Backend

FastAPI 本地服务，负责窗口捕获、自动观察、视觉模型调用、短期记忆和用户问答。

## RuntimeStore

后端使用标准库 SQLite 作为本地运行时存储：

```text
backend/data/runtime/runtime.sqlite3
```

保存内容：

```text
assistant:state
window:latest_analysis
assistant:chat:current
assistant:chat:history
memory:working:observation
memory:items
```

这是本地产品主链路。悬浮窗只访问 FastAPI，不直接读写数据库文件。

## 启动

```powershell
cd D:\AI_Workspace\window\backend
uv run uvicorn app.main:app --host 127.0.0.1 --port 18080 --reload --no-access-log
```

或从项目根目录运行：

```powershell
.\scripts\start_dev.cmd
```

## Health

```powershell
Invoke-RestMethod http://127.0.0.1:18080/health
```

返回内容包含：

```text
status
service
version
assistant_state
runtime_store
```

## 主要接口

```text
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

## 测试

```powershell
cd D:\AI_Workspace\window\backend
uv run pytest --basetemp D:\AI_Workspace\window\.tmp\pytest-basetemp
```
