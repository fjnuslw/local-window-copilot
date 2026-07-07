# Runtime Logging / Observability Spec

状态：当前主线约束。用于定位截图、VLM 分析、工具调用、对话回答中的真实失败点。

## 目标

- 所有关键运行阶段必须有结构化日志，而不是只把状态置为 `error`。
- 错误日志必须包含异常类型、异常消息、traceback，以及相关截图/窗口/模型请求元信息。
- 日志只用于工程审计，不进入模型上下文，不参与 `memory.search` 检索。
- 不用关键词 fallback 掩盖失败；失败应暴露为可追踪证据。

## 存储

复用 `RuntimeStore.runtime_events`，统一事件名：

```text
system:log
```

payload 固定结构：

```json
{
  "ts": "ISO-8601 UTC",
  "level": "debug|info|warning|error",
  "component": "window_watcher|window_analysis|vision_model|assistant_chat|agent_tools|runtime",
  "action": "短动作名",
  "message": "人类可读一句话",
  "fields": { "任意结构化字段": "值" },
  "exception": {
    "type": "异常类名",
    "message": "异常消息",
    "traceback": "完整 traceback"
  }
}
```

## 观察链路必须打点

窗口观察主链路：

```text
window_watcher.capture_start
window_watcher.capture_success
window_watcher.analysis_start
window_analysis.vision_request
vision_model.request
vision_model.response
vision_model.parse_success
vision_model.failure
window_analysis.latest_write
window_analysis.summary_write
window_watcher.analysis_success
window_watcher.failure
```

若失败，至少要能回答：

- 截图是否成功生成？截图路径/hash 是什么？
- VLM 服务是否被调用？模型端点是什么？
- VLM 返回了什么原始文本？
- 是请求失败、JSON 解析失败、schema 校验失败，还是 SQLite 写入失败？
- `assistant:state.error` 中最后一次错误是什么？

## WebUI

高级页提供“运行日志”：

- 展示最近 `system:log`。
- 支持按 level/component 在前端快速识别。
- error 日志默认展示异常类型和消息，traceback 放在 details 中。

## 禁止事项

- 禁止把日志文本注入对话上下文。
- 禁止用 substring/关键词 fallback 伪造成功。
- 禁止吞掉异常后只写 `state=error`。
- 禁止把截图 base64 写入日志；只记录路径、hash、尺寸。