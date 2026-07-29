"""后端日志配置."""

import logging
import re
import traceback
from copy import copy
from pathlib import Path

from backend.app.core import log_context


class LoggingContextFilter(logging.Filter):
    """将 ContextVar 中的请求关联字段注入每一条日志记录."""

    def filter(self, record: logging.LogRecord) -> bool:
        """注入当前任务的关联字段."""
        record.request_id = log_context.request_id.get()
        record.run_id = log_context.run_id.get()
        record.project_id = log_context.project_id.get()
        record.attempt_id = log_context.attempt_id.get()
        record.stage = log_context.stage.get()
        return True


class ContextFormatter(logging.Formatter):
    """只补充消息中尚未显式出现的关联字段，避免终端重复键值."""

    _context_fields = ("request_id", "run_id", "project_id", "attempt_id", "stage")

    def format(self, record: logging.LogRecord) -> str:
        """在副本中构造上下文和安全异常摘要，避免污染其他 handler."""
        safe_record = copy(record)
        message = safe_record.getMessage()
        fields = [
            f"{name}={getattr(safe_record, name, '-')}"
            for name in self._context_fields
            if not re.search(rf"(?:^|\s){name}=", message)
        ]
        safe_record.context_fields = f"{' '.join(fields)} " if fields else ""
        # 其他 formatter 可能已在原始 record 上缓存了未脱敏的 exc_text。
        safe_record.exc_text = None
        if safe_record.exc_info:
            error = safe_record.exc_info[1]
            if isinstance(error, BaseException):
                safe_record.exc_text = safe_exception_summary(error)
            else:
                safe_record.exc_text = "exception_types=unknown traceback_frames=-"
            safe_record.exc_info = None
        return super().format(safe_record)


def _safe_frame_path(filename: str) -> str:
    """保留仓库内相对路径，同时避免把开发机绝对路径写入日志."""
    parts = Path(filename).parts
    for marker in ("backend", "src", "tests"):
        if marker in parts:
            return "/".join(parts[parts.index(marker) :])
    return Path(filename).name


def safe_exception_diagnostics(error: BaseException) -> tuple[str, str]:
    """返回安全的异常类型链与栈位置，不包含异常消息、locals 或源码."""
    chain: list[str] = []
    frames: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen and len(chain) < 8:
        seen.add(id(current))
        error_type = type(current)
        chain.append(f"{error_type.__module__}.{error_type.__qualname__}")
        frames.extend(
            f"{_safe_frame_path(frame.filename)}:{frame.lineno}:{frame.name}"
            for frame in traceback.extract_tb(current.__traceback__)[-20:]
        )
        current = current.__cause__
    return "<-".join(chain) or "unknown", ",".join(dict.fromkeys(frames)) or "-"


def safe_exception_summary(error: BaseException) -> str:
    """返回可直接追加到终端日志的安全异常摘要."""
    error_types, frames = safe_exception_diagnostics(error)
    return f"exception_types={error_types} traceback_frames={frames}"


LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(context_fields)s%(message)s"

_record_factory_installed = False
_previous_record_factory = logging.getLogRecordFactory()


def _install_safe_record_factory() -> None:
    """为所有 handler 注入上下文，保留原始异常供各 handler 独立处理."""
    global _record_factory_installed
    if _record_factory_installed:
        return

    def record_factory(*args: object, **kwargs: object) -> logging.LogRecord:
        record = _previous_record_factory(*args, **kwargs)
        record.request_id = log_context.request_id.get()
        record.run_id = log_context.run_id.get()
        record.project_id = log_context.project_id.get()
        record.attempt_id = log_context.attempt_id.get()
        record.stage = log_context.stage.get()
        record.context_fields = ""
        return record

    logging.setLogRecordFactory(record_factory)
    _record_factory_installed = True


def _configure_handler(handler: logging.Handler) -> None:
    """为已有 handler 添加统一格式；兼容 uvicorn 的独立 handler."""
    if not any(isinstance(item, LoggingContextFilter) for item in handler.filters):
        handler.addFilter(LoggingContextFilter())
    handler.setFormatter(ContextFormatter(LOG_FORMAT))


def configure_logging(level_name: str = "INFO") -> None:
    """配置后端日志格式和级别."""
    level = getattr(logging, level_name.upper(), logging.INFO)
    _install_safe_record_factory()
    logging.basicConfig(
        level=logging.WARNING,
        format=LOG_FORMAT,
    )
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        _configure_handler(handler)

    # uvicorn 默认可能关闭传播并持有自己的 handler，显式处理以保证格式一致。
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        for handler in logging.getLogger(logger_name).handlers:
            _configure_handler(handler)

    for logger_name in ("backend", "agent", "shaderforge"):
        logging.getLogger(logger_name).setLevel(level)
