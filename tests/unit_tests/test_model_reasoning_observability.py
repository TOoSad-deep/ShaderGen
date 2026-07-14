from __future__ import annotations

import logging
from hashlib import sha256

from agent.app.observability.model_reasoning import log_reasoning_content


def test_reasoning_log_contains_only_length_and_digest(caplog) -> None:
    reasoning = "PRIVATE_REASONING_CONTENT"
    caplog.set_level(logging.INFO, logger="agent.model")

    log_reasoning_content("generate_glsl", reasoning)

    assert reasoning not in caplog.text
    assert "reasoning_chars=25" in caplog.text
    assert sha256(reasoning.encode("utf-8")).hexdigest() in caplog.text


def test_empty_reasoning_does_not_emit_log(caplog) -> None:
    caplog.set_level(logging.INFO, logger="agent.model")

    log_reasoning_content("generate_glsl", None)

    assert caplog.text == ""
