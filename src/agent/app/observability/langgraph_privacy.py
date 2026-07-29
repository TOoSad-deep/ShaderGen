"""Fail-closed tracing policy for graphs whose state contains private assets."""

from __future__ import annotations

import langsmith

_configured = False


def configure_private_graph_observability() -> None:
    """Disable tracing globally and redact payloads if it is re-enabled."""
    global _configured
    if _configured:
        return
    langsmith.configure(
        enabled=False,
        client=langsmith.Client(
            auto_batch_tracing=False,
            hide_inputs=True,
            hide_outputs=True,
        ),
    )
    _configured = True


__all__ = ["configure_private_graph_observability"]
