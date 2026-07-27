"""Node Lab HTTP 稳定错误契约."""

from __future__ import annotations

from pydantic import ConfigDict

from nodelab.http.schemas.common import NodeLabHttpModel


class NodeLabErrorDetail(NodeLabHttpModel):
    """稳定的 Node Lab HTTP 错误 detail."""

    model_config = ConfigDict(extra="allow")

    message: str
    code: str
    stage: str
    retryable: bool
    lab_run_id: str | None = None
    step_id: str | None = None
    node_id: str | None = None


class NodeLabErrorResponse(NodeLabHttpModel):
    """FastAPI detail envelope."""

    detail: NodeLabErrorDetail
