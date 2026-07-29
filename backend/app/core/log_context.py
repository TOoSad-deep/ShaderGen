"""请求范围内的安全日志关联上下文."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Final

_EMPTY: Final = "-"

request_id: ContextVar[str] = ContextVar("request_id", default=_EMPTY)
run_id: ContextVar[str] = ContextVar("run_id", default=_EMPTY)
project_id: ContextVar[str] = ContextVar("project_id", default=_EMPTY)
attempt_id: ContextVar[str] = ContextVar("attempt_id", default=_EMPTY)
stage: ContextVar[str] = ContextVar("stage", default=_EMPTY)


def bind_log_context(**values: str | None) -> list[tuple[ContextVar[str], Token[str]]]:
    """绑定已知关联字段，并返回供 ``reset_log_context`` 使用的 token."""
    fields = {
        "request_id": request_id,
        "run_id": run_id,
        "project_id": project_id,
        "attempt_id": attempt_id,
        "stage": stage,
    }
    tokens: list[tuple[ContextVar[str], Token[str]]] = []
    for name, value in values.items():
        context_var = fields.get(name)
        if context_var is not None:
            tokens.append((context_var, context_var.set(value or _EMPTY)))
    return tokens


def reset_log_context(tokens: list[tuple[ContextVar[str], Token[str]]]) -> None:
    """按反序重置一次绑定，避免 ASGI task 复用时泄露上下文."""
    for context_var, token in reversed(tokens):
        context_var.reset(token)


@contextmanager
def scoped_log_context(**values: str | None) -> Iterator[None]:
    """在一个同步或异步调用片段内临时绑定日志关联字段."""
    tokens = bind_log_context(**values)
    try:
        yield
    finally:
        reset_log_context(tokens)
