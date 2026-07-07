# 010：WebUI Compact 面板与运行观测

## 目标

在浏览器 WebUI 的「上下文」页增加 compact 运行面板，让用户能看到当前 rolling summary、覆盖进度、最近一次 compact 指标、lock 状态，并能手动触发一次 compact。

010 是观测与操作切片。它不改变 compact planner、executor、summary prompt，不改变模型对话装配顺序，不新增异步任务队列。

## 背景

001-004 已完成 rough token 估算与 `inspect_context` 预算展示。

005-008 已完成 compact state、planner、summary prompt、executor。

009 已完成自动触发、手动 API、trace 与下一轮 summary 注入。

010 需要把这些后端能力展示给用户，便于确认 compact 是否真正运行、是否失败、当前 summary 覆盖了哪些历史 session。

## 改动范围

允许修改：

- `backend/app/services/assistant_chat.py`
- `backend/app/api/routes/assistant.py`
- `backend/app/webui/static/index.html`
- `backend/tests/test_assistant_state_api.py`
- 必要时新增 `backend/tests/test_assistant_chat_service.py` 用例
- `docs/co-work/context_management_refactor_spec_zh.md`

保持稳定：

- `backend/app/services/context_summary.py` 的 compact 执行语义
- `backend/app/services/vision_model_client.py`
- token 估算规则
- planner 阈值规则
- summary prompt 模板
- 真实对话 messages 装配顺序

## 后端接口

新增：

```text
GET /api/assistant/compact-status
```

返回 JSON：

```json
{
  "enabled": true,
  "auto_enabled": true,
  "summary": {
    "version": 1,
    "present": true,
    "chars": 320,
    "tokens": 180,
    "updated_at": "2026-07-06T12:00:00+00:00",
    "covered_session_count": 7,
    "source_session_count": 7,
    "covered_session_ids_tail": ["s5", "s6", "s7"],
    "text": "## 当前任务\n..."
  },
  "metrics": {
    "last_started_at": "2026-07-06T12:00:00+00:00",
    "last_finished_at": "2026-07-06T12:00:03+00:00",
    "last_status": "ok",
    "source_session_count": 3,
    "covered_session_count": 7,
    "summary_tokens": 180,
    "source_tokens": 4300,
    "error_type": null,
    "error_message": null
  },
  "lock": {
    "active": false,
    "owner": null,
    "source": null,
    "started_at": null,
    "expires_at": null
  },
  "planner": {
    "raw_tail_turns": 2,
    "batch_session_limit": 12,
    "source_budget_tokens": 18000,
    "uncovered_session_threshold": 6,
    "history_trigger_tokens": 24000
  }
}
```

字段规则：

- `summary.text` 直接返回当前 summary 文本；WebUI 负责 HTML escape。
- `covered_session_ids_tail` 只返回最多最近 12 个 id，避免状态接口过大。
- `lock.active=false` 时其余 lock 字段为 `null`。
- `load_lock()` 已会清理过期 lock；接口只呈现当前有效 lock。
- RuntimeStore payload 损坏时允许接口返回 500，由现有错误链路暴露问题。

## ChatAgent 方法

新增：

```python
def compact_status(self) -> dict[str, Any]:
    ...
```

实现要求：

- 只读取 `CompactStateStore`。
- 不调用模型。
- 不触发 compact。
- 不写 RuntimeStore。
- 读取 `get_settings()` 的 compact 配置并回显。
- summary/metrics/lock payload 使用 `context_summary.py` 已有 converter 输出。

## API 路由

新增 route：

```python
@router.get("/compact-status")
def compact_status() -> dict[str, object]:
    return get_assistant_chat_service().compact_status()
```

已有：

```text
POST /api/assistant/compact
```

保留 009 行为。010 只让 WebUI 调用它。

## WebUI

位置：`backend/app/webui/static/index.html` 的「上下文」页。

新增一个 `context-section`，放在上下文预览之前：

- 标题：`Compact 状态`
- 操作按钮：
  - `刷新`
  - `立即 compact`
- 展示区：
  - compact 开关：enabled/auto_enabled
  - summary 状态：present、tokens、chars、updated_at
  - 覆盖进度：covered_session_count/source_session_count
  - 最近一次 metrics：last_status、source_tokens、summary_tokens、时间、错误信息
  - lock：active/source/expires_at
  - summary 文本，使用 `<pre>` 展示

交互规则：

- 进入「上下文」页时调用 `loadCompactStatus()`。
- 点击刷新调用 `GET /api/assistant/compact-status`。
- 点击立即 compact：
  - 按钮置 disabled，显示运行中状态。
  - 调用 `POST /api/assistant/compact`。
  - 完成后刷新 compact status。
  - 同时刷新 context preview 的前提是用户已经输入过问题；若没有问题，仅刷新 status。
  - 失败时显示 toast，并保留原面板内容。

展示规则：

- 所有动态文本必须走 `escapeHTML`。
- 不把 summary 渲染成 Markdown。
- 不展示完整 prompt。
- 不展示 compact source sessions 原文。
- 不展示模型请求 messages。

## Trace 展示

010 不新增 trace 存储。WebUI 继续使用已有「交互轨迹」列表。

`loadTraces()` 对 compact stage 可读性做轻量摘要：

- `context_summary.started`
- `context_summary.succeeded`
- `context_summary.failed`
- `context_summary.locked`
- `context_summary.skipped`

保留原 JSON payload `<pre>`，只在 stage 行或前置短句中增加关键字段。

## 测试要求

后端测试：

1. `GET /api/assistant/compact-status` 委托到 `ChatAgent.compact_status()`。
2. `ChatAgent.compact_status()` 在空 store 下返回 `present=false`、metrics idle、lock inactive。
3. 写入 summary/metrics/lock 后，`compact_status()` 返回对应 tokens、covered count、tail ids、lock active。
4. `POST /api/assistant/compact` 既有委托测试继续通过。

前端静态检查：

1. `index.html` 存在 `compactStatusPanel`、`refreshCompactBtn`、`runCompactBtn`。
2. JS 中存在 `loadCompactStatus()` 与 `runCompactNow()`。
3. 动态 summary 文本使用 `escapeHTML`。

## 验证命令

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests\test_assistant_state_api.py tests\test_assistant_chat_service.py -q
```

若单个图片相关 fixture 受本机临时目录权限影响，可按项目当前已知方式排除对应用例，并在汇报里说明。

## 完成判定

- WebUI 可以看到 compact 状态。
- WebUI 可以手动触发 compact。
- 手动触发结果与 metrics/summary 同步刷新。
- 010 测试通过。
- 总计划书更新 010 状态。