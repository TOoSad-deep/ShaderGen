"""V2+ Pydantic 契约共享的严格、不可变基础类型。."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

__all__ = [
    "FiniteFloat",
    "FrozenModel",
    "JsonValue",
    "NonEmptyString",
    "Sha256Hex",
]

FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class FrozenModel(BaseModel):
    """拒绝未知字段并冻结属性赋值的领域模型基类。."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
    )
