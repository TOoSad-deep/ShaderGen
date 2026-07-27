"""Node Lab HTTP Schema 的公共类型与严格基类."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

NodeLabSuiteId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
    ),
]


class NodeLabHttpModel(BaseModel):
    """Node Lab transport 严格模型基类."""

    model_config = ConfigDict(extra="forbid")
