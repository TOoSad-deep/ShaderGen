"""ShaderGen 跨组件运行配置."""

from shaderforge.config.runtime_timeouts import (
    RUNTIME_TIMEOUTS,
    RuntimeTimeouts,
    load_runtime_timeouts,
)

__all__ = [
    "RUNTIME_TIMEOUTS",
    "RuntimeTimeouts",
    "load_runtime_timeouts",
]
