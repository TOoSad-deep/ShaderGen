"""Agent 公共用例共享的安全异常."""


class MemoryUnavailableError(RuntimeError):
    """表示任务 checkpoint 或项目 Store 无法安全读写."""
