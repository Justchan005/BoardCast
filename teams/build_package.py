"""Build the Teams app package to upload in Teams (Apps > Manage your apps > Upload a custom app).

    python -m teams.build_package            # reads TEAMS_BOT_ID and PUBLIC_BASE_URL from env / .env
    -> teams/build/boardcast-teams.zip

Icons are drawn here (192px colour, 32px white outline), so no image files need committing.
"""
from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
OUT = HERE / "build"
BLUE = (31, 79, 216, 255)
WHITE = (255, 255, 255, 255)


def _board(draw: ImageDraw.ImageDraw, size: int, colour, width: int) -> None:
    """The BoardCast mark: a notice board on a stand."""
    s = size / 24
    draw.rounded_rectangle([4 * s, 4 * s, 20 * s, 16 * s], radius=1.5 * s, outline=colour, width=width)
    draw.line([12 * s, 16 * s, 12 * s, 20 * s], fill=colour, width=width)
    draw.line([9 * s, 20 * s, 15 * s, 20 * s], fill=colour, width=width)


def color_icon() -> Image.Image:
    img = Image.new("RGBA", (192, 192), BLUE)
    _board(ImageDraw.Draw(img), 192, WHITE, 14)
    return img


def outline_icon() -> Image.Image:
    img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))   # Teams requires white on transparent
    _board(ImageDraw.Draw(img), 32, WHITE, 2)
    return img


def build(bot_id: str, base_url: str) -> Path:
    if not bot_id:
        raise SystemExit("Set TEAMS_BOT_ID (the bot's app id from the Teams Developer Portal).")
    host = urlparse(base_url).hostname or "localhost"
    manifest = (HERE / "appPackage" / "manifest.json").read_text(encoding="utf-8")
    manifest = (manifest.replace("${BOT_ID}", bot_id).replace("${PUBLIC_BASE_URL}", base_url.rstrip("/"))
                .replace("${PUBLIC_HOST}", host))
    json.loads(manifest)  # fail early on a broken manifest
    OUT.mkdir(exist_ok=True)
    zip_path = OUT / "boardcast-teams.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", manifest)
        for name, img in (("color.png", color_icon()), ("outline.png", outline_icon())):
            path = OUT / name
            img.save(path)
            z.write(path, name)
    return zip_path


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv(HERE.parent / ".env")
    except ImportError:
        pass
    print(build(os.getenv("TEAMS_BOT_ID", ""), os.getenv("PUBLIC_BASE_URL", "https://example.com")))
