"""V2+ 内容身份共用的 canonical JSON v1。."""

from __future__ import annotations

import json
import math
import unicodedata
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from enum import Enum
from hashlib import sha256
from typing import Any

from pydantic import BaseModel

CANONICAL_JSON_VERSION = "canonical_json_v1"


def _canonical_float(value: float) -> str:
    if not math.isfinite(value):
        raise ValueError("canonical JSON 拒绝 NaN 和 Infinity。")
    if value == 0.0:
        value = 0.0
    return value.hex().lower()


def canonicalize(value: Any) -> Any:
    """转换为可稳定 JSON 编码的 NFC/binary64 canonical projection。."""
    if isinstance(value, BaseModel):
        return canonicalize(value.model_dump(mode="python"))
    if is_dataclass(value) and not isinstance(value, type):
        return canonicalize(asdict(value))
    if isinstance(value, Enum):
        return canonicalize(value.value)
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return _canonical_float(value)
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str):
                raise TypeError("canonical JSON object key 必须是 string。")
            key = unicodedata.normalize("NFC", raw_key)
            if key in normalized:
                raise ValueError("canonical JSON 的 NFC key 发生碰撞。")
            normalized[key] = canonicalize(raw_value)
        return {key: normalized[key] for key in sorted(normalized)}
    if isinstance(value, (set, frozenset)):
        items = [canonicalize(item) for item in value]
        return sorted(items, key=_encoded_sort_key)
    if isinstance(value, (list, tuple)):
        return [canonicalize(item) for item in value]
    raise TypeError(f"{type(value).__name__} 不能进入 canonical JSON。")


def _encoded_sort_key(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_json_bytes(value: Any) -> bytes:
    """输出无空白、UTF-8 编码的 canonical JSON bytes。."""
    return json.dumps(
        canonicalize(value),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    """计算 canonical JSON 的 SHA-256。."""
    return sha256(canonical_json_bytes(value)).hexdigest()
