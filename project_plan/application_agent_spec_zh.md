# 桌宠式窗口 Copilot：上下文与记忆设计草案

更新时间：2026-07-02

## 1. 目标

当前目标不是做一个会自动执行任务的通用 Agent，而是做一个反应快、上下文干净、能陪伴用户工作的窗口 Copilot。

它应该擅长三件事：

```text
感知用户当前大概在做什么
给出轻量互动提示和候选问题
用户追问时结合当前窗口、最近摘要和短期记忆回答
```

## 2. 硬约束

主链路必须可解释。不要用替代旁路遮盖问题：

- 模型看不懂，就标记不确定。
- 需要隐私暂停，就暂停。
- 运行环境缺失，就报明确错误。
- 结果不可靠，就不要假装已经理解。

当前项目要相信自己的主链路：截图由 VLM 直接理解，结构化上下文只做降噪，不做第二套识别系统。

## 3. 什么是“干净上下文”

干净上下文不是把能抓到的东西全部塞给模型，而是只保留对当前任务有稳定价值、低噪声、低维护成本的信息。

当前必要字段：

```text
observation_id
captured_at
app_name
window_title
window_kind_hint
window_bounds
screenshot_path
screenshot_hash
privacy_state
last_summary
last_user_question
relevant_memory_items
```

当前不放入上下文：

- 整页识别文本。
- 控件树。
- 鼠标键盘日志。
- 进程级敏感细节。
- 大段历史对话。
- 自动操作计划。

原因很简单：当前信息主体已经在截图里，VLM 是主理解者。额外字段只用于帮助模型定位场景、减少重复、连接记忆，而不是替代视觉理解。

## 4. ObservationCard

`ObservationCard` 是当前 Context Builder 的落地形态。它不是大而全的窗口上下文，而是一张最小观察卡。

示例：

```json
{
  "observation_id": "obs_20260702_001",
  "captured_at": "2026-07-02T21:30:00+08:00",
  "app_name": "Code.exe",
  "window_title": "window - Visual Studio Code",
  "window_kind_hint": "ide",
  "window_bounds": {
    "left": 120,
    "top": 80,
    "width": 1680,
    "height": 980
  },
  "screenshot_path": "backend/data/captures/current.png",
  "screenshot_hash": "sha256:...",
  "privacy_state": "normal"
}
```

实现入口：

```text
backend/app/schemas/observation.py
backend/app/services/observation_builder.py
```

## 5. Memory

当前只做两层记忆：

```text
working memory:
  当前或最近一次窗口观察。

session memory:
  最近摘要、用户问题、助手回答。
```

Memory 不做长期人格，不做跨天用户画像，不做 embedding 检索。当前目标是让追问能接上刚才的窗口语境，而不是构建庞大的个人记忆库。

写入规则：

- 每次成功分析窗口后，保存 observation 和 analysis summary。
- 用户提问时，保存问题和关联 observation id。
- 助手回答后，保存回答和关联 observation id。
- 检索时优先同一窗口、同一截图 hash、最近时间。

实现入口：

```text
backend/app/schemas/memory.py
backend/app/services/memory.py
backend/app/services/assistant_chat.py
```

## 6. Agent Orchestrator 的当前判断

当前不需要独立 Orchestrator 类。

原因：

- 主流程很短：观察 -> 分析 -> 展示 -> 用户追问 -> 回答。
- 决策状态很少：normal、privacy、error、conversation_paused。
- 独立 Orchestrator 容易过早制造名词和文件数量。

现阶段用确定性服务组合即可：

```text
WindowWatcher
WindowAnalysisService
AssistantChatService
MemoryService
AssistantStateService
```

如果后续出现多个可选行动、任务队列、工具执行或长期目标，再重新评估 Orchestrator。当前不是时候。

## 7. 问答上下文构造

用户追问时，prompt 只需要：

```text
当前 ObservationCard
最近一次窗口分析 summary/key_points/caution
用户问题
少量相关 session memory
```

不需要：

- 全量历史窗口列表。
- 全量对话记录。
- 自动任务计划。
- 模型不可见的推测字段。

这样能保证回答快、上下文短、问题可定位。

## 8. 评估方法

下一步不要继续堆抽象，先做小样本评估：

```text
20 个真实窗口样本
每个样本保存截图、ObservationCard、模型输出、人工判断
标记误读、漏读、不确定、隐私暂停和重复提示
```

通过评估回答三个问题：

- 当前 VLM 是否能稳定看懂用户正在做什么。
- ObservationCard 是否真的让上下文更干净。
- Memory 是否提升追问质量，而不是污染回答。

## 9. 应用级部署前的深度方向

优先级从高到低：

1. 观察质量评估和 prompt 稳定性。
2. 短期记忆的写入、检索和污染控制。
3. 提示时机：什么时候该说话，什么时候该安静。
4. 隐私暂停和明确错误状态。
5. 启动体验、模型文件检查、RuntimeStore 检查和端口检查。
6. 日志与小样本评估面板。

不是优先方向：

- 重型数据库主链路。
- 多模型适配。
- 自动操作电脑。
- 长期人格记忆。
- 把桌宠包装成完整自主 Agent。
