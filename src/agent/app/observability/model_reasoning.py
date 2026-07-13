"""模型 reasoning 的受控日志输出."""

import logging

logger = logging.getLogger("agent.model")


def log_reasoning_content(stage: str, reasoning_content: str | None) -> None:
    """按 Node 配置输出 reasoning 日志."""
    if reasoning_content:
        logger.info(
            "模型思维链 stage=%s reasoning_content=%s",
            stage,
            reasoning_content,
        )
