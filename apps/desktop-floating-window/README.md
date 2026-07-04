# Desktop Floating Window

原生 Windows 透明悬浮窗。

职责：

```text
显示桌宠状态
显示最近窗口观察
展示候选问题
接收用户自定义提问
打开独立悬浮对话窗
轮询 FastAPI 当前对话和历史对话
```

悬浮窗只访问：

```text
http://127.0.0.1:18080
```

它不直接读写本地数据库文件。

## 启动

```powershell
cd D:\AI_Workspace\window
.\apps\desktop-floating-window\start_desktop_window.cmd
```

## 主要接口

```text
GET  /api/assistant/state
GET  /api/assistant/latest
GET  /api/assistant/conversation
GET  /api/assistant/conversations
POST /api/assistant/questions
POST /api/assistant/resume
```
