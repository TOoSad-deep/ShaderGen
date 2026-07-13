"""解析 Shader 相关模型输出."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedShaderReview:
    """模型输出解析后的渲染评审."""

    evaluation: str
    suggestions: tuple[str, ...]


def extract_glsl(text: str) -> str:
    """从模型输出中提取 GLSL 代码."""
    match = re.search(r"```(?:glsl)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    glsl = match.group(1) if match else text
    return glsl.strip()


def parse_shader_review_response(text: str) -> ParsedShaderReview:
    """解析模型输出的渲染评审 JSON."""
    stripped = text.strip()
    try:
        start = stripped.index("{")
        end = stripped.rindex("}") + 1
        data = json.loads(stripped[start:end])
    except (ValueError, json.JSONDecodeError):
        return ParsedShaderReview(evaluation=stripped, suggestions=())

    suggestions = data.get("suggestions", ())
    if isinstance(suggestions, str):
        suggestions = (suggestions,)
    return ParsedShaderReview(
        evaluation=str(data.get("evaluation", "")).strip(),
        suggestions=tuple(
            str(item).strip() for item in suggestions if str(item).strip()
        ),
    )
