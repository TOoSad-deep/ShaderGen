"""scene_mvp 运行进度的进程内存事件缓冲.

约束：单进程单 worker 语义，重启即失；终态审计仍以 agent_events 过程账本为准，
这里只服务运行中的前端轮询，不写图片参考图、Scene 或 GLSL。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

MAX_EVENTS_PER_RUN = 2000
RUN_PROGRESS_TTL_SECONDS = 30 * 60


@dataclass
class _RunProgress:
    """单个 run 的内存进度状态."""

    project_id: str
    generation_mode: str
    quality_preset: str
    started_at: str
    last_touch: float
    status: str = "running"
    stop_reason: str | None = None
    latest_seq: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)
    snapshot: dict[str, Any] = field(default_factory=dict)
    render_png: bytes | None = None
    render_seq: int = 0


class RunProgressRegistry:
    """按 run_id 存放运行中事件与最新渲染帧的内存注册表."""

    def __init__(self) -> None:
        """初始化空注册表；不需要后台线程，读取侧惰性清扫."""
        self._runs: dict[str, _RunProgress] = {}

    def begin(
        self,
        run_id: str,
        *,
        project_id: str,
        generation_mode: str,
        quality_preset: str,
    ) -> None:
        """登记一次新运行；同 id 仍在运行视为冲突，已结束允许复用."""
        self._sweep()
        existing = self._runs.get(run_id)
        if existing is not None and existing.status == "running":
            raise ValueError(f"run_id={run_id} 已有进行中的运行。")
        self._runs[run_id] = _RunProgress(
            project_id=project_id,
            generation_mode=generation_mode,
            quality_preset=quality_preset,
            started_at=datetime.now(UTC).isoformat(),
            last_touch=time.monotonic(),
        )

    def publish(self, run_id: str, event: dict[str, Any]) -> None:
        """追加一个白名单进度事件并刷新快照；未知或已结束的 run 直接丢弃."""
        run = self._runs.get(run_id)
        if run is None or run.status != "running":
            return
        run.latest_seq += 1
        record = {"seq": run.latest_seq, **event}
        run.events.append(record)
        if len(run.events) > MAX_EVENTS_PER_RUN:
            del run.events[: len(run.events) - MAX_EVENTS_PER_RUN]
        run.last_touch = time.monotonic()
        snapshot = run.snapshot
        snapshot["current_node"] = record.get("node")
        for key in ("budgets", "counters", "best"):
            value = record.get(key)
            if isinstance(value, dict):
                snapshot[key] = value

    def publish_render(self, run_id: str, png: bytes) -> None:
        """覆盖式保存最新渲染帧；只保留一帧，历史帧不占用内存."""
        run = self._runs.get(run_id)
        if run is None or run.status != "running" or not png:
            return
        run.render_png = png
        run.render_seq += 1
        run.last_touch = time.monotonic()

    def finish(self, run_id: str, status: str, stop_reason: str | None = None) -> None:
        """把运行标记为终态；重复 finish 或未知 run 视为无操作."""
        run = self._runs.get(run_id)
        if run is None or run.status != "running":
            return
        run.status = status
        run.stop_reason = stop_reason
        run.last_touch = time.monotonic()

    def read(self, run_id: str, after: int = 0) -> dict[str, Any]:
        """读取 seq 大于 after 的增量事件与最新快照；未知 id 返回 pending."""
        self._sweep()
        run = self._runs.get(run_id)
        if run is None:
            return {
                "status": "pending",
                "generation_mode": None,
                "quality_preset": None,
                "started_at": None,
                "events": [],
                "latest_seq": 0,
                "snapshot": {},
            }
        events = [event for event in run.events if int(event["seq"]) > after]
        return {
            "status": run.status,
            "generation_mode": run.generation_mode,
            "quality_preset": run.quality_preset,
            "started_at": run.started_at,
            "events": events,
            "latest_seq": run.latest_seq,
            "snapshot": {**run.snapshot, "render_seq": run.render_seq},
        }

    def read_render(self, run_id: str) -> tuple[bytes | None, int]:
        """返回最新渲染帧与帧序号；无帧时返回 (None, 0)."""
        run = self._runs.get(run_id)
        if run is None or run.render_png is None:
            return None, 0
        return run.render_png, run.render_seq

    def _sweep(self) -> None:
        """丢弃超过 TTL 的条目（含崩溃后永远停在 running 的条目）."""
        now = time.monotonic()
        stale = [
            run_id
            for run_id, run in self._runs.items()
            if now - run.last_touch > RUN_PROGRESS_TTL_SECONDS
        ]
        for run_id in stale:
            del self._runs[run_id]


__all__ = [
    "MAX_EVENTS_PER_RUN",
    "RUN_PROGRESS_TTL_SECONDS",
    "RunProgressRegistry",
]
