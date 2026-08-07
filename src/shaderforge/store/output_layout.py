"""安全地构造可供人工浏览的运行产物目录层级."""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from pathlib import Path

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MAX_SLUG_LENGTH = 64


def _slugify(stem: str) -> str:
    """Reduce one human-readable stem to a portable single path segment."""
    output: list[str] = []
    pending_separator = False
    for character in stem:
        if character.isalnum() or character in {"_", "-"}:
            if pending_separator and output and output[-1] != "-":
                output.append("-")
            output.append(character)
            pending_separator = False
        else:
            pending_separator = True
    return "".join(output)[:_MAX_SLUG_LENGTH].rstrip("-_")


def safe_png_name_slug(upload_filename: str | None, *, fallback: str = "unnamed-png") -> str:
    """把上传 PNG 文件名转换为单个可读且安全的目录名.

    客户端若附带路径则只取最后一个文件名，路径穿越不会影响生成路径；控制
    字符仍会拒绝。文件扩展名不进入目录名；中文和其他字母数字字符会保留，
    其余字符统一折叠为 ``-``.
    """
    if not isinstance(fallback, str) or not fallback:
        raise ValueError("fallback 必须是非空字符串。")
    normalized_fallback = unicodedata.normalize("NFKC", fallback)
    if (
        "/" in normalized_fallback
        or "\\" in normalized_fallback
        or any(not character.isprintable() for character in normalized_fallback)
    ):
        raise ValueError("fallback 包含非法路径或控制字符。")
    fallback_slug = _slugify(normalized_fallback)
    if not fallback_slug:
        raise ValueError("fallback 必须包含可用目录字符。")
    if upload_filename is None:
        return fallback_slug
    if not isinstance(upload_filename, str):
        raise TypeError("upload_filename 必须是字符串或 None。")

    normalized = unicodedata.normalize("NFKC", upload_filename)
    if not normalized:
        return fallback_slug
    if (
        any(not character.isprintable() for character in normalized)
        or normalized in {".", ".."}
    ):
        raise ValueError("upload_filename 包含非法路径或控制字符。")
    leaf = normalized.replace("\\", "/").rsplit("/", 1)[-1]
    stem = leaf.rsplit(".", 1)[0] if "." in leaf else leaf
    return _slugify(stem) or fallback_slug


def validate_output_date(value: str) -> str:
    """校验并返回严格 ``YYYY-MM-DD`` 格式的真实日历日期."""
    if not isinstance(value, str) or not _DATE_PATTERN.fullmatch(value):
        raise ValueError("output_date 必须是 YYYY-MM-DD 格式。")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("output_date 必须是有效日历日期。") from exc
    return value


def _safe_identifier(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} 包含非法字符。")
    return value


def public_run_relative_path(
    upload_filename: str | None,
    output_date: str,
    run_id: str,
) -> Path:
    """构造公开父运行的 ``png_name/date/run_id`` 相对路径."""
    return Path(
        safe_png_name_slug(upload_filename),
        validate_output_date(output_date),
        _safe_identifier(run_id, "run_id"),
    )


def private_attempt_relative_path(
    upload_filename: str | None,
    output_date: str,
    parent_run_id: str,
    attempt_id: str,
) -> Path:
    """构造私有 attempt 的 ``png_name/date/parent_run_id/attempt_id`` 路径."""
    return Path(
        safe_png_name_slug(upload_filename),
        validate_output_date(output_date),
        _safe_identifier(parent_run_id, "parent_run_id"),
        _safe_identifier(attempt_id, "attempt_id"),
    )
