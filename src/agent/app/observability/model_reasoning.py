"""模型 reasoning 的受控日志输出."""

import logging
from hashlib import sha256

logger = logging.getLogger("agent.model")


def log_reasoning_content(stage: str, reasoning_content: str | None) -> None:
    """只输出 reasoning 的长度和摘要，避免内容进入普通终端日志."""
    if reasoning_content:
        digest = sha256(reasoning_content.encode("utf-8")).hexdigest()
        logger.info(
            "模型思维链已捕获 stage=%s reasoning_chars=%s reasoning_sha256=%s",
            stage,
            len(reasoning_content),
            digest,
        )
