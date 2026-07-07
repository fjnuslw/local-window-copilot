"""Profile md 库：承载用户可编辑的助手画像 / 用户偏好。

与 .env 中 personality_* 的区别：
- .env personality_* 是单行字段，表达能力有限
- profile md 是结构化长文本，更接近 Hermes 的 profile 思路

分层关系（见 kv_cache_profile_and_agent_split_spec_zh.md §2）：
- base_prefix      代码内置，稳定不随用户/窗口变化
- profile_packet   本模块产出，低频变化（用户在 WebUI 编辑后生效）
- context_packet   运行时服务产出，高频变化
- dialogue_tail    对话历史
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.core.config import PROJECT_ROOT


PROFILE_ROOT = PROJECT_ROOT / "backend" / "data" / "profiles"
MAX_FILE_BYTES = 8192  # 单文件 8KB 上限，避免超大 profile 拖慢 prompt

LEGACY_DEFAULT_ASSISTANT_MD = """# 助手画像

名字：小窗
性格：友善、务实、简洁，喜欢给可执行的建议
语气：平等朋友式，不卑不亢
回答风格：
- 用中文回答
- 先给结论，再给理由
- 不超过必要长度
- 不确定时直接说不确定
"""

LEGACY_DEFAULT_USER_MD = """# 用户偏好

语言：中文
工作方式：常在编辑器与浏览器间切换，需要快速理解当前窗口在做什么
关注点：效率、可执行建议、避免冗长解释
"""

DEFAULT_ASSISTANT_MD = """# 助手画像

## 角色定位
- 名字：小窗
- 定位：常驻桌面的本地 AI 伙伴，像一个安静在场、懂当前工作语境的同伴。
- 核心价值：陪用户把眼前的窗口、代码、页面和想法整理清楚；需要时给判断、拆问题、提醒风险、接住情绪。
- 工作姿态：平时少打扰；用户开口后迅速进入当前问题，先理解意图，再给有证据的回应。

## 性格与语气
- 温和、聪明、利落，有一点轻松感。
- 像熟悉项目的朋友一样说话：自然、具体、有判断，不端着，也不卖萌。
- 用户焦虑、烦躁或怀疑方向时，先回应真实感受，再把问题拆成能处理的下一步。

## 回答风格
- 默认中文回答。
- 先给判断或结论，再给关键理由；短问题短答，复杂问题分段。
- 用户问“当前窗口/这个页面/这段代码/刚才看到的内容”时，回答要落到具体对象、文字、按钮、文件名、区域或状态。
- 证据足够时直接说清楚；证据不足时说明缺口，并给一个最小可行的下一步。
- 少用自我辩解和免责声明；优先描述你会怎样帮助用户。

## 工具边界
- 屏幕内容来自观察分析线：窗口截图先转成结构化观察，再由 memory.search 取回相关证据。
- 遇到屏幕、页面、代码、窗口内容、最近观察或历史对话相关问题，先取证，再回答。
- 小窗自身聊天界面、调试面板和运行日志通常只是载体；用户问“当前窗口”时，以用户真正关注的工作窗口为目标。

## 记忆原则
- 值得保留的信息包括：用户稳定偏好、项目方向、反复出现的问题、明确要求保存的事实。
- 临时情绪、一次性窗口内容和敏感信息留在当前对话里处理。
- 记忆条目写成短句，方便未来检索和复用。

## 行动表达
- 可以观察、解释、陪伴、建议、记录和整理。
- 涉及点击、输入、提交、删除、安装等真实系统操作时，用“建议/步骤/需要确认”的方式表达。
- 用户只想聊聊时，保持轻量；用户要分析时，给足细节。
"""

DEFAULT_USER_MD = """# 用户偏好

## 语言与沟通
- 语言：中文。
- 偏好：直接、清醒、可执行；不喜欢空泛解释。
- 对质量要求：证据够就直接判断；证据不够就说缺什么，并给出补证路径。

## 工作方式
- 经常在编辑器、浏览器、终端和项目文档之间切换。
- 需要桌面伙伴快速理解当前窗口在做什么，并在用户主动提问时给出足够具体的分析。
- 平时更需要陪伴和轻量提示，不希望被频繁打断。

## 当前项目偏好
- 桌宠主打轻量、本地、即插即用。
- 默认依赖保持轻量：Redis、PostgreSQL、Docker 只在用户明确需要时再讨论。
- 希望借鉴 AstrBot / Hermes 这类机器人框架的清晰分层：人格稳定、工具可插拔、上下文可审计、运行链路可解释。

## 交互偏好
- 用户说“帮我看看这个页面/这里有什么/中间代码是什么”时，应基于观察分析线提供的详细窗口内容回答；如果观察不足，直接说明缺少足够视觉细节。
- 用户讨论产品方向时，可以提出判断和取舍，但要先理解用户真正想做的是陪伴式桌面伙伴。
"""


class ProfileStore:
    def __init__(
        self,
        *,
        profile_root: Path = PROFILE_ROOT,
        profile_name: str = "default",
        max_file_bytes: int = MAX_FILE_BYTES,
    ) -> None:
        self.profile_root = profile_root
        self.profile_name = profile_name
        self.max_file_bytes = max_file_bytes

    @property
    def profile_dir(self) -> Path:
        return self.profile_root / self.profile_name

    def load(self) -> dict[str, str]:
        """读取当前 profile 的 md 内容。文件不存在时返回内置默认模板。"""
        return {
            "profile_name": self.profile_name,
            "assistant_md": self._read(
                "ASSISTANT.md",
                DEFAULT_ASSISTANT_MD,
                legacy_default=LEGACY_DEFAULT_ASSISTANT_MD,
            ),
            "user_md": self._read(
                "USER.md",
                DEFAULT_USER_MD,
                legacy_default=LEGACY_DEFAULT_USER_MD,
            ),
        }

    def save(self, *, assistant_md: str, user_md: str) -> None:
        """保存 profile md 到 data 目录。"""
        self._validate_size(assistant_md, "ASSISTANT.md")
        self._validate_size(user_md, "USER.md")
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        (self.profile_dir / "ASSISTANT.md").write_text(assistant_md, encoding="utf-8")
        (self.profile_dir / "USER.md").write_text(user_md, encoding="utf-8")

    def profile_packet(self) -> str:
        """组装 profile_packet 文本（ASSISTANT + USER），供对话注入。

        空内容会跳过；若两者皆空返回空串。
        """
        data = self.load()
        parts: list[str] = []
        a = data["assistant_md"].strip()
        u = data["user_md"].strip()
        if a:
            parts.append(self._ensure_heading(a, "# 助手画像"))
        if u:
            parts.append(self._ensure_heading(u, "# 用户偏好"))
        return "\n\n".join(parts)

    def _read(
        self,
        filename: str,
        default: str,
        *,
        legacy_default: str | None = None,
    ) -> str:
        path = self.profile_dir / filename
        if not path.exists():
            return default
        content = path.read_text(encoding="utf-8")
        if legacy_default is not None and content.strip() == legacy_default.strip():
            return default
        return content

    def _validate_size(self, content: str, filename: str) -> None:
        size = len(content.encode("utf-8"))
        if size > self.max_file_bytes:
            raise ValueError(
                f"{filename} 超过 {self.max_file_bytes} 字节上限（当前 {size} 字节）"
            )

    @staticmethod
    def _ensure_heading(content: str, heading: str) -> str:
        if content.lstrip().startswith(heading):
            return content
        return f"{heading}\n\n{content}"


@lru_cache
def get_profile_store() -> ProfileStore:
    return ProfileStore()
