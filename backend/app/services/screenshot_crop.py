"""局部截图工具：根据用户问题中的方位关键词裁剪截图。

用于 Work Lens / 视觉追问场景（spec §9 Phase 4：视觉细节问题使用局部截图）。
当用户问"左侧那个面板""底部状态栏"时，先裁剪对应区域再送入模型，
提升局部细节的识别准确率。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

# 方位关键词 -> 裁剪比例 (left_ratio, top_ratio, right_ratio, bottom_ratio)
# 比例基于原图宽高，0.0=起边，1.0=终边
REGION_KEYWORDS: dict[str, tuple[float, float, float, float]] = {
    # 水平方位
    "左侧": (0.0, 0.0, 0.5, 1.0),
    "左边": (0.0, 0.0, 0.5, 1.0),
    "左半": (0.0, 0.0, 0.5, 1.0),
    "左部": (0.0, 0.0, 0.5, 1.0),
    "右侧": (0.5, 0.0, 1.0, 1.0),
    "右边": (0.5, 0.0, 1.0, 1.0),
    "右半": (0.5, 0.0, 1.0, 1.0),
    "右部": (0.5, 0.0, 1.0, 1.0),
    # 垂直方位
    "上方": (0.0, 0.0, 1.0, 0.5),
    "上面": (0.0, 0.0, 1.0, 0.5),
    "上半": (0.0, 0.0, 1.0, 0.5),
    "顶部": (0.0, 0.0, 1.0, 0.33),
    "底部": (0.0, 0.67, 1.0, 1.0),
    "下方": (0.0, 0.5, 1.0, 1.0),
    "下面": (0.0, 0.5, 1.0, 1.0),
    "下半": (0.0, 0.5, 1.0, 1.0),
    # 四角
    "左上": (0.0, 0.0, 0.5, 0.5),
    "右上": (0.5, 0.0, 1.0, 0.5),
    "左下": (0.0, 0.5, 0.5, 1.0),
    "右下": (0.5, 0.5, 1.0, 1.0),
    # 代码/文字通常需要比普通中心块更宽的上下文，避免只截到空白或局部缩进。
    "中间的代码": (0.12, 0.12, 0.88, 0.88),
    "中间代码": (0.12, 0.12, 0.88, 0.88),
    "代码区域": (0.12, 0.12, 0.88, 0.88),
    "文字区域": (0.12, 0.12, 0.88, 0.88),
    # 中间
    "中间": (0.25, 0.25, 0.75, 0.75),
    "中部": (0.25, 0.25, 0.75, 0.75),
    "中央": (0.25, 0.25, 0.75, 0.75),
}


def detect_region(question: str) -> tuple[float, float, float, float] | None:
    """从问题中检测方位关键词，返回裁剪比例 (left, top, right, bottom)。

    无命中时返回 None（表示使用整张图）。
    多个命中时取第一个匹配的方位。
    """
    for keyword, ratio in REGION_KEYWORDS.items():
        if keyword in question:
            return ratio
    return None


def crop_screenshot(
    image_path: Path,
    region: tuple[float, float, float, float],
    *,
    output_path: Path | None = None,
) -> Path:
    """按比例裁剪截图，返回裁剪后的图片路径。

    参数：
    - image_path：原图路径
    - region：(left_ratio, top_ratio, right_ratio, bottom_ratio)，值域 [0, 1]
    - output_path：输出路径，None 时自动生成（原图同目录加 _crop 后缀）
    """
    with Image.open(image_path) as image:
        width, height = image.size
        left = int(width * region[0])
        top = int(height * region[1])
        right = int(width * region[2])
        bottom = int(height * region[3])
        cropped = image.crop((left, top, right, bottom))
        if output_path is None:
            stem = image_path.stem
            suffix = image_path.suffix or ".png"
            output_path = image_path.parent / f"{stem}_crop{suffix}"
        cropped.save(output_path)
    return output_path


def maybe_crop_for_question(
    question: str,
    image_path: Path,
    *,
    output_dir: Path | None = None,
) -> tuple[Path, str]:
    """根据问题自动裁剪截图。

    返回 (实际使用的图片路径, reason)：
    - reason="crop:<keyword>" 表示裁剪后使用局部图
    - reason="full" 表示无方位关键词，使用整图
    """
    region = detect_region(question)
    if region is None:
        return image_path, "full"

    # 找到命中的关键词用于 reason
    keyword = next(
        (kw for kw in REGION_KEYWORDS if kw in question),
        "unknown",
    )
    output_path = None
    if output_dir is not None:
        stem = image_path.stem
        suffix = image_path.suffix or ".png"
        output_path = output_dir / f"{stem}_crop_{keyword}{suffix}"
    cropped_path = crop_screenshot(image_path, region, output_path=output_path)
    return cropped_path, f"crop:{keyword}"
