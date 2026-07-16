"""后端日志配置."""

import logging


def configure_logging(level_name: str = "INFO") -> None:
    """配置后端日志格式和级别."""
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    for logger_name in ("backend", "agent", "shaderforge"):
        logging.getLogger(logger_name).setLevel(level)
