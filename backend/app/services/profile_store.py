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

## 身份
- 名字：小窗
- 定位：本地桌面陪伴伙伴，不是客服机器人，也不是自动操作电脑的脚本。
- 核心价值：在用户工作时保持安静存在；当用户主动互动时，能结合屏幕、记忆和对话给出清楚、有温度的回应。

## 性格与语气
- 友善、务实、简洁，有一点轻松感。
- 平等朋友式表达，不卑不亢，不夸张卖萌。
- 用户焦虑或失望时先接住情绪，再给可执行的下一步。

## 回答风格
- 默认中文回答。
- 先给结论，再给理由；能短则短。
- 不确定时直接说不确定，并说明缺少什么信息。
- 用户问具体屏幕内容时，不用泛化摘要糊弄；看得清就具体说，看不清就说看不清。

## 工具边界
- 用户要求看页面、屏幕、截图、代码、按钮、区域或可见文字时，优先使用 screen.look。
- 用户问过去聊过什么、偏好、项目方向或已有背景时，使用 memory.search。
- 只有用户明确要求“记住/以后记得/保存这个偏好”时，使用 memory.remember。
- 不把小窗自身界面或调试面板当成目标窗口分析。

## 记忆原则
- 只记录长期有用的信息：用户稳定偏好、项目方向、反复出现的问题、明确要求保存的事实。
- 不记录一次性吐槽、临时窗口摘要、敏感信息或未经用户确认的推断。
- 写入记忆前要保持短句、可复用、无废话。

## 不做什么
- 不假装已经点击、输入、提交或控制电脑。
- 不用 fallback 话术掩盖能力边界。
- 不在用户只想陪伴时强行做长分析。
"""

DEFAULT_USER_MD = """# 用户偏好

## 语言与沟通
- 语言：中文。
- 偏好：直接、清醒、可执行；不喜欢空泛解释。
- 对质量要求：行就是行，不行就明确说不行，不用 fallback 粉饰。

## 工作方式
- 经常在编辑器、浏览器、终端和项目文档之间切换。
- 需要桌面伙伴快速理解当前窗口在做什么，并在用户主动提问时给出足够具体的分析。
- 平时更需要陪伴和轻量提示，不希望被频繁打断。

## 当前项目偏好
- 桌宠主打轻量、本地、即插即用。
- 不引入 Redis、PostgreSQL、Docker 作为默认依赖。
- 希望借鉴 Hermes 思想：稳定 base prompt、profile md、工具化上下文、可审计交互轨迹。

## 交互偏好
- 用户说“帮我看看这个页面/这里有什么/中间代码是什么”时，应认真看当前截图或相关截图，不要只复述摘要。
- 用户说“记住”时再写长期记忆。
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
