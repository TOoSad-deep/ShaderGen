"""后端日志配置."""

import logging
import os


def configure_logging() -> None:
    """配置后端日志格式和级别."""
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    for logger_name in ("backend", "agent", "shaderforge"):
        logging.getLogger(logger_name).setLevel(level)
