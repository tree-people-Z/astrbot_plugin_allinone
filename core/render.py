"""文本转图（可选，Pillow 或字体缺失时返回 None）。"""

from __future__ import annotations

import uuid
from pathlib import Path

from astrbot.api import logger

_FONT_CANDIDATES = (
    Path(__file__).parent.parent / "fonts" / "simhei.ttf",
    Path(__file__).parent.parent / "fonts" / "msyh.ttc",
    Path("C:/Windows/Fonts/msyh.ttc"),
    Path("C:/Windows/Fonts/simhei.ttf"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
)


def find_font() -> str | None:
    for candidate in _FONT_CANDIDATES:
        if candidate.exists():
            return str(candidate)
    return None


def text_to_image(text: str, width: int = 720) -> str | None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None

    font_path = find_font()
    if not font_path:
        return None

    try:
        font_size = 22
        font = ImageFont.truetype(font_path, font_size)
        line_height = int(font_size * 1.5)
        margin = 24

        lines = []
        for raw_line in text.splitlines() or [""]:
            while len(raw_line) > 36:
                lines.append(raw_line[:36])
                raw_line = raw_line[36:]
            lines.append(raw_line)

        height = margin * 2 + line_height * max(len(lines), 1)
        image = Image.new("RGB", (width, height), (24, 26, 33))
        draw = ImageDraw.Draw(image)
        y = margin
        for line in lines:
            draw.text((margin, y), line or " ", font=font, fill=(230, 230, 230))
            y += line_height

        out_dir = _temp_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"render_{uuid.uuid4().hex[:16]}.png"
        image.save(str(out_path))
        logger.debug(f"[allinone] 文本转图: {out_path}")
        return str(out_path)
    except Exception as exc:
        logger.warning(f"[allinone] 文本转图失败: {exc}")
        return None


def _temp_dir() -> Path:
    from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

    return Path(get_astrbot_temp_path()) / "astrbot_plugin_allinone"
