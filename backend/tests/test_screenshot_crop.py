"""局部截图工具单元测试：覆盖方位检测与裁剪逻辑。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from app.services.screenshot_crop import (
    crop_screenshot,
    detect_region,
    maybe_crop_for_question,
)


def _make_test_image(path: Path, width: int = 400, height: int = 300) -> Path:
    """创建测试用图片（纯色填充）。"""
    image = Image.new("RGB", (width, height), color=(128, 128, 128))
    image.save(path)
    return path


# ---------- detect_region ----------


def test_detect_region_left() -> None:
    assert detect_region("左侧那个面板") == (0.0, 0.0, 0.5, 1.0)


def test_detect_region_right() -> None:
    assert detect_region("右边有什么") == (0.5, 0.0, 1.0, 1.0)


def test_detect_region_top() -> None:
    assert detect_region("上方菜单栏") == (0.0, 0.0, 1.0, 0.5)


def test_detect_region_bottom() -> None:
    assert detect_region("底部状态栏") == (0.0, 0.67, 1.0, 1.0)


def test_detect_region_corner() -> None:
    assert detect_region("左上角图标") == (0.0, 0.0, 0.5, 0.5)


def test_detect_region_center() -> None:
    assert detect_region("中间那块区域") == (0.25, 0.25, 0.75, 0.75)


def test_detect_region_center_code_uses_wider_context() -> None:
    assert detect_region("中间的代码具体是什么") == (0.12, 0.12, 0.88, 0.88)


def test_detect_region_none_when_no_keyword() -> None:
    assert detect_region("页面里有什么") is None


# ---------- crop_screenshot ----------


def test_crop_screenshot_left_half(tmp_path: Path) -> None:
    """裁剪左半部分，输出尺寸应为原图一半宽。"""
    src = _make_test_image(tmp_path / "src.png", width=400, height=300)
    cropped = crop_screenshot(src, (0.0, 0.0, 0.5, 1.0))
    assert cropped.exists()
    with Image.open(cropped) as img:
        assert img.width == 200
        assert img.height == 300


def test_crop_screenshot_top_half(tmp_path: Path) -> None:
    """裁剪上半部分，输出尺寸应为原图一半高。"""
    src = _make_test_image(tmp_path / "src.png", width=400, height=300)
    cropped = crop_screenshot(src, (0.0, 0.0, 1.0, 0.5))
    assert cropped.exists()
    with Image.open(cropped) as img:
        assert img.width == 400
        assert img.height == 150


def test_crop_screenshot_custom_output_path(tmp_path: Path) -> None:
    """指定输出路径时裁剪到该路径。"""
    src = _make_test_image(tmp_path / "src.png")
    out = tmp_path / "custom_crop.png"
    result = crop_screenshot(src, (0.0, 0.0, 0.5, 0.5), output_path=out)
    assert result == out
    assert out.exists()


def test_crop_screenshot_auto_output_path(tmp_path: Path) -> None:
    """不指定输出路径时自动生成 _crop 后缀文件。"""
    src = _make_test_image(tmp_path / "capture.png")
    result = crop_screenshot(src, (0.25, 0.25, 0.75, 0.75))
    assert result.exists()
    assert "_crop" in result.name


# ---------- maybe_crop_for_question ----------


def test_maybe_crop_returns_full_when_no_region(tmp_path: Path) -> None:
    """无方位关键词时返回原图，reason=full。"""
    src = _make_test_image(tmp_path / "src.png")
    path, reason = maybe_crop_for_question("页面里有什么", src)
    assert path == src
    assert reason == "full"


def test_maybe_crop_returns_cropped_for_left(tmp_path: Path) -> None:
    """问题包含"左侧"时裁剪左半部分。"""
    src = _make_test_image(tmp_path / "src.png", width=400, height=300)
    path, reason = maybe_crop_for_question("左侧面板里有什么", src)
    assert path != src
    assert reason.startswith("crop:")
    assert "左侧" in reason
    assert path.exists()
    with Image.open(path) as img:
        assert img.width == 200


def test_maybe_crop_with_output_dir(tmp_path: Path) -> None:
    """指定 output_dir 时裁剪到该目录。"""
    src = _make_test_image(tmp_path / "src.png")
    out_dir = tmp_path / "crops"
    out_dir.mkdir()
    path, reason = maybe_crop_for_question("底部状态栏", src, output_dir=out_dir)
    assert path.parent == out_dir
    assert path.exists()
    assert reason == "crop:底部"
