"""多模态图片消息片段构造."""

from __future__ import annotations

import base64
from typing import Any


def image_url_part(image: bytes, content_type: str) -> dict[str, Any]:
    """把原始图片字节构造成 multimodal image_url 片段."""
    encoded = base64.b64encode(image).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{content_type};base64,{encoded}"},
    }
