"""最小优化热路径使用的 RGB MAE。."""

from __future__ import annotations

from io import BytesIO

import numpy as np
from PIL import Image, UnidentifiedImageError


def decode_rgb(image_bytes: bytes) -> np.ndarray:
    """把图片解码为 `[0, 1]` float32 RGB。."""
    try:
        image = Image.open(BytesIO(image_bytes)).convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("无法解码用于 MAE 的图片。") from exc
    return np.asarray(image, dtype=np.float32) / 255.0


def rgb_mae(reference: np.ndarray, rendered: np.ndarray) -> float:
    """计算相同尺寸 RGB 的全局 MAE，尺寸不符时 fail closed。."""
    if reference.shape != rendered.shape or reference.ndim != 3 or reference.shape[2] != 3:
        raise ValueError("MAE 两侧必须是相同尺寸的 RGB 图片。")
    return float(np.mean(np.abs(reference.astype(np.float32) - rendered.astype(np.float32))))


__all__ = ["decode_rgb", "rgb_mae"]
