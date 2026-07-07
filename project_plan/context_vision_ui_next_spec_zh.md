# Context / Vision / UI 下一阶段 Spec

> 维护状态：历史 spec。本文中按关键词选择 visual/text、规则回看图片、固定方位裁剪等设计已废弃；当前唯一权威主线见 ../docs/context_observation_tool_mainline_spec_zh.md。

更新时间：2026-07-04

状态：TraeIDE 已实施，Codex 已审核并补强关键边界。

审核记录：

- 2026-07-04：确认 WebUI 精简、视觉输入 metadata、截图可追溯、视觉追问、对话工作台滚动与自动观察暂停链路已落地。
- 2026-07-04：Codex 补强缺图时明确失败、不走文本回退；历史视觉引用优先选择最近非当前截图；自动观察间隔移入高级设置；移除桌宠状态 chip 死代码。

## 1. 背景判断

当前项目已经完成了第一版双线拆分：

```text
ObservationAgent：截图 + VLM -> 当前窗口摘要
ChatAgent：profile/context/dialogue -> 用户回答
```

但实际使用暴露出四类问题：

1. WebUI 暴露字段太多，小白用户不应该直接面对所有底层参数。
2. 自动观察的图片输入过小，当前 `LWC_MODEL_IMAGE_LONG_EDGE=512` 会把 1822x827 窗口压到 512x232，导致 VLM 看不清页面细节。
3. 用户追问默认只基于文字摘要，不重新看图片；当摘要不够细时，对话无法补救。
4. 桌宠和对话窗 UI 仍偏大、信息占比不合理、摘要和对话内容的可读性不足。

这一阶段目标不是做复杂 agent，而是把主链路变清楚：

```text
看得清 -> 摘得准 -> 找得到对应截图 -> 必要时重新看图回答
```

不引入 Redis / PostgreSQL / Docker。不引入替代主链路掩盖问题。

---

## 2. 目标

### 2.1 产品目标

- WebUI 默认面向普通用户，只暴露少量高价值配置。
- 自动观察仍保持轻量，但默认视觉清晰度要足够识别网页、IDE、文档的大部分文字区域。
- 用户追问涉及视觉细节时，系统能从本地 SQLite / RuntimeStore 找到对应截图，重新送 VLM 看图回答。
- 桌宠更小、更安静，不用状态栏占空间；摘要只展示必要内容。
- 对话工作台保持固定大小，但内容区域应滚动，不应靠省略号截断完整回答。

### 2.2 技术目标

- Context Builder 从“拼摘要”升级到“按问题选择上下文”。
- WindowSummaryRecord 必须能追溯到截图文件。
- ChatAgent 必须能区分：
  - 只需文本上下文的问题
  - 需要重新看当前/历史截图的问题
- WebUI 必须能解释上下文使用率，而不是只展示 hash。

---

## 3. WebUI 精简与默认值调整

### 3.1 默认用户视图

WebUI 默认只保留四个页签：

```text
概览
角色
识别
上下文
```

隐藏或移入高级模式的字段：

```text
llama_server_host
llama_server_port
llama_chat_completions_path
minicpm_model_name
minicpm_ctx_size
latest_analysis_ttl_seconds
window_capture_min_interval_seconds
window_analysis_min_interval_seconds
chat_history_question_max_chars
chat_history_answer_max_chars
history_retention_limit
memory_max_items
memory_item_max_chars
system_prompt_prefix
personality_enabled
personality_name
personality_traits
answer_style_hint
```

默认用户可见字段：

| 页签 | 字段 | UI 形式 | 说明 |
|------|------|---------|------|
| 概览 | 后端状态 | 只读 | 运行中 / 未连接 |
| 概览 | 模型状态 | 只读 | llama-server 是否可用 |
| 角色 | `ASSISTANT.md` | textarea | 助手画像 |
| 角色 | `USER.md` | textarea | 用户偏好 |
| 识别 | 视觉清晰度 | segmented control | 快速 / 标准 / 细致 |
| 识别 | 自动观察 | toggle | 是否后台自动观察 |
| 识别 | 分析详细度 | segmented control | 简洁 / 标准 / 详细 |
| 上下文 | 历史对话轮数 | stepper | 默认 4 |
| 上下文 | 最近窗口摘要数 | stepper | 默认 3 |
| 上下文 | 记忆开关 | toggle | 默认开 |
| 上下文 | 上下文预览 | read-only | 展示本次会注入什么 |

### 3.2 高级模式

新增右上角开关：

```text
高级设置
```

高级设置默认关闭。打开后才显示底层字段。高级字段必须分组并标注“修改后可能需要重启后端”。

### 3.3 推荐默认值

当前默认值需要调整：

```text
model_image_long_edge: 512 -> 1024
analyze_max_tokens: 1200 -> 1800
answer_max_tokens: 800 -> 1200
chat_history_turns: 6 -> 4
window_summary_retrieve_count: 5 -> 3
memory_retrieve_count: 4 -> 3
```

视觉清晰度映射：

```text
快速：768
标准：1024
细致：1344
```

分析详细度映射：

```text
简洁：900 tokens
标准：1400 tokens
详细：1800 tokens
```

验收：

- 默认打开 WebUI 时，普通用户看不到端口、路径、ctx size 等底层字段。
- 视觉清晰度可调，并实际写入 `LWC_MODEL_IMAGE_LONG_EDGE`。
- context preview 中能看到当前上下文来源和大致字符/预算占比。

---

## 4. 图片识别优化

### 4.1 现状问题

当前 `VisionModelClient._image_to_data_url()` 会把整张图缩到 `model_image_long_edge`。当长边为 512 时，文字密集窗口会变成不可读图。

这不是单纯 prompt 问题。VLM 输入不清楚时，模型只能描述布局和大标题。

### 4.2 第一阶段：提高默认图像清晰度

要求：

- 默认长边改为 1024。
- WebUI 的“细致”模式可改为 1344。
- 保存截图仍保留原图，不覆盖原始截图。
- 送模型前缩放后的尺寸写入分析结果 metadata，便于调试。

建议新增字段：

```json
{
  "vision_input": {
    "original_size": [1822, 827],
    "sent_size": [1024, 465],
    "long_edge": 1024,
    "detail_mode": "standard"
  }
}
```

### 4.3 第二阶段：窗口分块观察

当窗口过宽、过高或文字密度高时，不应该只送整图。

建议策略：

```text
全局图：用于判断页面类型、布局、主要区域
局部图：按 2~4 个区域裁剪，用于识别文字和模块细节
```

第一版分块规则：

```text
宽屏窗口：左 / 中 / 右 三块
高窗口：上 / 中 / 下 三块
普通窗口：只送全局图
```

输出结构：

```json
{
  "summary": "...",
  "regions": [
    {
      "name": "left",
      "bounds": [0, 0, 600, 827],
      "summary": "左侧文件资源管理器..."
    }
  ],
  "uncertain_areas": ["底部小字不清晰"]
}
```

第一阶段可以先不实现多图请求，只要把数据结构和测试留好。第二阶段再让 ObservationAgent 合并区域摘要。

验收：

- 标准模式下网页/IDE文字可读性明显好于 512。
- 分析结果里能看到图片输入尺寸。
- Prompt 明确要求模型标记“不清楚/无法确认”的区域。

---

## 5. 用户追问时按问题回看图片

### 5.1 核心原则

用户追问默认不带截图是对的，因为这有利于速度和 KV cache。但当问题需要视觉细节时，必须能重新看图。

这就是本地 SQLite / RuntimeStore 和上下文管理的作用：

```text
保存看过什么 -> 保存截图在哪里 -> 根据问题找到对应截图 -> 必要时重新看图回答
```

### 5.2 需要补齐的数据

当前 `WindowSummaryStore` 只存：

```text
record_id
created_at
app_name
window_title
window_type
summary
key_points
```

必须新增：

```text
screenshot_path
screenshot_hash
window_bounds
process_id
analyzed_at
vision_input
regions
```

建议结构：

```json
{
  "record_id": "...",
  "created_at": "...",
  "app_name": "chrome.exe",
  "window_title": "KV Cache Profile/Agent split - Codex",
  "window_type": "webpage",
  "summary": "...",
  "key_points": ["..."],
  "screenshot_path": "backend/data/captures/xxx.png",
  "screenshot_hash": "...",
  "window_bounds": {"left": 0, "top": 0, "right": 1822, "bottom": 827},
  "vision_input": {
    "original_size": [1822, 827],
    "sent_size": [1024, 465]
  }
}
```

### 5.3 问题路由

ChatAgent 在回答前先做轻量问题分类，不调用模型，规则优先：

视觉细节类：

```text
页面里有什么
图里有什么
这个按钮在哪
这段文字是什么
左侧/右侧/上方/底部是什么
截图里
看一下
识别一下
这个页面
```

走 `visual_answer`：

```text
question -> select relevant WindowSummaryRecord -> image + question -> VLM answer
```

普通上下文类：

```text
刚才说了什么
你记得什么
总结一下
基于当前摘要解释
```

走 `text_answer`：

```text
question -> profile/context/dialogue -> text answer
```

### 5.4 图片选择策略

第一版使用本地规则，不做 embedding：

优先级：

1. latest_analysis 的截图。
2. window_title / app_name 与问题关键词匹配的最近记录。
3. 用户提到“上一页/刚才/之前”时，取最近 3 条窗口摘要里最匹配的一条。
4. 如果无法确定，只使用 latest_analysis，不猜测更远历史。

必须在 context preview 中展示：

```text
answer_mode: text_answer / visual_answer
selected_image: screenshot_path
selected_reason: latest / title_match / recent_match
```

### 5.5 视觉追问提示词

新增 prompt：

```text
experiments/prompts/visual_question_answer_v1.txt
```

职责：

- 输入：用户问题 + 对应截图。
- 输出：自然语言回答。
- 不写 JSON。
- 不声称能操作电脑。
- 看不清时说看不清。
- 优先回答用户问到的区域或元素，不重新长篇总结整张图。

验收：

- 用户问“页面里有什么”时，会重新送截图，而不是只读摘要。
- context preview 能显示本次选择了哪张截图。
- 如果截图路径不存在，直接返回明确错误，不走其它路线。

---

## 6. 桌宠和对话窗 UI 修复

### 6.1 桌宠主窗

问题：

- 当前机器人偏大。
- 状态栏占空间且干扰。
- 摘要内容占比过多。

目标：

- `UI_SCALE` 继续下调，建议从 `0.78` 改到 `0.62~0.68`。
- 移除或隐藏状态 chip，只保留颜色/小图标状态。
- 摘要面板默认折叠，只显示 1~2 行当前窗口标题/摘要。
- 详细摘要移入对话工作台或 WebUI，不在桌宠主窗大面积展示。

验收：

- 桌宠不遮挡主要工作区域。
- 不显示“待命/观察/分析”等大状态栏文字。
- 主窗只承担轻提示和入口，不承担完整阅读。

### 6.2 对话工作台

问题：

- 固定大小可以接受，但内容不能被省略号吞掉。
- 历史回答、当前回答超过区域时必须滚动。
- 输入法和滚动体验要稳定。

目标：

- 对话窗固定大小。
- 消息区滚动，回答不使用省略号截断。
- 顶部当前摘要卡只保留标题 + 1 行摘要，可展开查看完整上下文。
- 底部按钮语义明确：
  - `发送`：基于现有上下文回答
  - `观察`：隐藏对话窗，重新看当前窗口
  - 可选 `细看`：用高分辨率重新看图

验收：

- 长回答可以滚动到底。
- 鼠标滚轮速度符合常规阅读体感。
- 输入框输入中文不卡顿。
- 点观察不会把对话窗自己识别成当前窗口。

---

## 7. 上下文使用率说明

WebUI 的 context preview 需要从“调试原始内容”升级为“用户看得懂的解释”。

新增展示：

```text
本次回答模式：文本回答 / 看图回答
当前窗口：app + title
使用的截图：路径或“未使用”
注入内容：
  profile: 约 N 字
  当前窗口摘要: 约 N 字
  最近窗口摘要: N 条 / 约 N 字
  记忆: N 条 / 约 N 字
  对话历史: N 轮 / 约 N 字
上下文预算估算：已使用约 xx%
```

预算估算第一版可用粗略规则：

```text
estimated_tokens = chars / 2
usage = estimated_tokens / minicpm_ctx_size
```

验收：

- 用户能看懂“为什么这次回答用了这些东西”。
- 开发者能看到 `base_prefix_hash / profile_hash / context_hash`。
- 普通模式默认不展示大段完整 prompt，避免吓人。

---

## 8. 实施顺序

### Phase 1：WebUI 精简和默认值

文件重点：

```text
backend/app/core/config.py
backend/app/api/routes/webui.py
backend/app/webui/static/index.html
```

验收：

- 普通模式字段减少。
- 默认长边变 1024。
- 输出 token 默认上调。
- 高级模式可查看底层字段。

### Phase 2：视觉输入质量

文件重点：

```text
backend/app/services/vision_model_client.py
backend/app/services/window_analysis.py
experiments/prompts/analyze_window_v2.txt
```

验收：

- 分析结果记录 original_size / sent_size。
- Prompt 要求标记不清楚区域。
- 单元测试覆盖图片缩放 metadata。

### Phase 3：截图可追溯存储

文件重点：

```text
backend/app/services/window_summary_store.py
backend/app/services/window_analysis.py
backend/tests/test_window_analysis_service.py
```

验收：

- 每条窗口摘要记录 screenshot_path / screenshot_hash。
- recent summaries 能返回截图路径。
- 历史记录上限仍生效。

### Phase 4：视觉追问

文件重点：

```text
backend/app/services/assistant_chat.py
backend/app/services/vision_model_client.py
experiments/prompts/visual_question_answer_v1.txt
```

验收：

- 视觉类问题自动走 image + question。
- 普通问题仍走文本上下文。
- context preview 显示 answer_mode 和 selected_image。

### Phase 5：桌宠和对话窗 UI

文件重点：

```text
apps/desktop-floating-window/desktop_floating_window.py
```

验收：

- 桌宠更小。
- 状态栏隐藏。
- 摘要卡更轻。
- 对话长内容可滚动，不用省略号截断。

---

## 9. 不做事项

- 不引入 Redis / PostgreSQL / Docker。
- 不做 OCR/UIA 作为主链路。
- 不做云端模型。
- 不让模型自动操作电脑。
- 不让模型自动写长期 profile。
- 不用复杂 embedding/vector db 作为第一版图片检索。
- 不把所有问题都重新送图，只有视觉细节类问题送图。

---

## 10. TraeIDE 交付要求

TraeIDE 实施时，每个 Phase 必须：

1. 保持主链路清晰，不新增替代路线。
2. 增加或更新对应测试。
3. 更新 `project_plan/README.md` 的当前执行依据。
4. 在提交说明里列出：
   - 改了哪些默认值
   - WebUI 隐藏了哪些字段
   - 看图回答触发条件
   - context preview 新增了哪些解释项

Codex 审核重点：

- 是否真的减少了小白用户可见字段。
- 是否真的提高了 VLM 输入尺寸。
- 是否保存了截图路径并能追溯。
- 是否根据问题决定看图或文本回答。
- 是否又把无关历史污染注入 context。
