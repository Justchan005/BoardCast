"""Decode QR codes on a poster in code (exact), instead of asking the model."""
from __future__ import annotations

import io

from PIL import Image
from pyzbar.pyzbar import decode  # needs the zbar system library (see Dockerfile)


def decode_links(image: bytes) -> list[str]:
    img = Image.open(io.BytesIO(image))
    links = []
    for result in decode(img):
        value = result.data.decode("utf-8", errors="ignore").strip()
        if value.startswith(("http://", "https://")):
            links.append(value)
    return links
