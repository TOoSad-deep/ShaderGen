"""Shader API schema."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel

MemoryStatus = Literal["durable", "ephemeral", "degraded"]


class ShaderResponse(BaseModel):
    """Shader 生成响应."""

    project_id: UUID
    glsl: str
    memory_status: MemoryStatus


class ShaderReview(BaseModel):
    """Shader 渲染评审."""

    evaluation: str
    suggestions: list[str]


class ShaderReviewResponse(BaseModel):
    """Shader 渲染评审响应."""

    project_id: UUID
    review: ShaderReview
    memory_status: MemoryStatus
