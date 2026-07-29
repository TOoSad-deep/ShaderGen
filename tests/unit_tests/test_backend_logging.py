import logging
from io import StringIO

from backend.app.core.log_context import scoped_log_context
from backend.app.core.logging import (
    LOG_FORMAT,
    ContextFormatter,
    configure_logging,
    safe_exception_summary,
)


def _raise_secret_error() -> None:
    try:
        raise ValueError("secret-provider-response")
    except ValueError as cause:
        raise RuntimeError("secret-user-or-shader-content") from cause


def _raise_implicit_context_error() -> None:
    try:
        raise ValueError("implicit-secret")
    except ValueError:
        raise RuntimeError("outer-secret")


def test_safe_exception_summary_keeps_types_and_location_without_messages() -> None:
    try:
        _raise_secret_error()
    except RuntimeError as error:
        summary = safe_exception_summary(error)

    assert "builtins.RuntimeError" in summary
    assert "builtins.ValueError" in summary
    assert "tests/unit_tests/test_backend_logging.py" in summary
    assert "secret-provider-response" not in summary
    assert "secret-user-or-shader-content" not in summary


def test_safe_exception_summary_excludes_implicit_context() -> None:
    try:
        _raise_implicit_context_error()
    except RuntimeError as error:
        summary = safe_exception_summary(error)

    assert "builtins.RuntimeError" in summary
    assert "builtins.ValueError" not in summary


def test_safe_exception_summary_keeps_explicit_cause() -> None:
    try:
        _raise_secret_error()
    except RuntimeError as error:
        summary = safe_exception_summary(error)

    assert "exception_types=builtins.RuntimeError<-builtins.ValueError" in summary


def test_exception_log_redacts_terminal_output_but_preserves_raw_exc_info() -> None:
    configure_logging("INFO")
    logger = logging.getLogger("backend.logging_test")
    logger.handlers.clear()
    logger.setLevel(logging.ERROR)
    logger.propagate = False
    terminal_output = StringIO()
    terminal_handler = logging.StreamHandler(terminal_output)
    terminal_handler.setFormatter(ContextFormatter(LOG_FORMAT))
    raw_records: list[logging.LogRecord] = []

    class CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            raw_records.append(record)

    logger.addHandler(terminal_handler)
    logger.addHandler(CaptureHandler())

    with scoped_log_context(
        request_id="request-1",
        run_id="run-1",
        project_id="project-1",
        attempt_id="attempt-1",
        stage="renderer",
    ):
        try:
            _raise_secret_error()
        except RuntimeError:
            logger.exception("event=test.failure error_code=renderer_failed")

    record = next(
        item for item in raw_records if "event=test.failure" in item.getMessage()
    )
    assert record.request_id == "request-1"
    assert record.run_id == "run-1"
    assert record.project_id == "project-1"
    assert record.attempt_id == "attempt-1"
    assert record.stage == "renderer"
    assert record.exc_info is not None
    assert record.exc_text is None
    assert "secret-provider-response" not in terminal_output.getvalue()
    assert "secret-user-or-shader-content" not in terminal_output.getvalue()
    assert (
        "exception_types=builtins.RuntimeError<-builtins.ValueError"
        in terminal_output.getvalue()
    )
    logger.handlers.clear()
    logger.propagate = True
