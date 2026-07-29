"""固定 fake LLM/Renderer 的 direct attempt 全内存链集成验收."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest

from agent.app.services.layerplan_glsl_direct import (
    LayerPlanGlslDirectConfig,
    LayerPlanGlslDirectRunner,
)
from backend.app.services.engine_rollout import ParentRunFailure
from backend.app.services.engine_rollout_runtime import build_engine_rollout_runtime
from shaderforge.program_spec import is_executable
from shaderforge.store import LocalArtifactStore
from tests.direct_fakes import (
    TEST_ISSUER as _TEST_ISSUER,
)
from tests.direct_fakes import (
    FakeRenderer as _FakeRenderer,
)
from tests.direct_fakes import (
    reference_png as _reference_png,
)
from tests.unit_tests.test_layerplan_glsl_direct_runner import _LayeredFakeGateway


class _OwnedFakeRunner:
    def __init__(self, config: LayerPlanGlslDirectConfig) -> None:
        self._runner = LayerPlanGlslDirectRunner(
            gateway=_LayeredFakeGateway(),
            renderer=_FakeRenderer(),
            config=config,
            receipt_issuer=_TEST_ISSUER,
        )

    async def run(self, reference_image: bytes, **kwargs):
        return await self._runner.run(reference_image, **kwargs)

    async def close(self) -> None:
        return None


class _HangingOwnedRunner:
    def __init__(self, _config: LayerPlanGlslDirectConfig) -> None:
        pass

    async def run(self, _reference_image: bytes, **_kwargs):
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def close(self) -> None:
        return None


class _ExplodingOwnedRunner:
    def __init__(self, _config: LayerPlanGlslDirectConfig) -> None:
        pass

    async def run(self, _reference_image: bytes, **_kwargs):
        raise RuntimeError("private failure detail")

    async def close(self) -> None:
        return None


@pytest.mark.anyio
async def test_fake_llm_renderer_direct_chain_retains_private_canonical_result() -> (
    None
):
    """覆盖 plan → Initial → safety → receipt → metric → immutable result."""
    gateway = _LayeredFakeGateway()
    renderer = _FakeRenderer()
    runner = LayerPlanGlslDirectRunner(
        gateway=gateway,
        renderer=renderer,
        config=LayerPlanGlslDirectConfig(
            implementation_identity_sha256="b" * 64,
            plan_llm_budget=1,
            direct_author_llm_budget=1,
            compile_budget=1,
            draw_budget=1,
            refine_budget=0,
        ),
        receipt_issuer=_TEST_ISSUER,
    )

    result = await runner.run(_reference_png(), instruction="match")

    assert result.status == "ok"
    assert result.layer_plan is not None
    assert result.current_best is not None
    assert result.current_best.spec.validation_attestation is not None
    assert is_executable(result.current_best.spec, issuer=_TEST_ISSUER)
    assert result.current_best.metrics["metric_version"] == "min_scene_composite_v3"
    assert result.current_best.rgb_bytes
    assert result.current_best.png_bytes
    assert result.direct_ledger.compile_count == 1
    assert result.direct_ledger.draw_count == 1
    assert renderer.close_count == 1
    json.dumps(result.to_safe_summary(), allow_nan=False)


@pytest.mark.anyio
async def test_runtime_progress_publishes_selected_render(tmp_path: Path) -> None:
    runtime = build_engine_rollout_runtime(
        public_store=LocalArtifactStore(tmp_path / "public"),
        private_attempt_root=tmp_path / "private",
        direct_runner_factory=_OwnedFakeRunner,
    )
    progress: list[tuple[dict[str, object], bytes | None]] = []
    try:
        await runtime.generate(
            _reference_png(),
            "image/png",
            project_id=str(uuid4()),
            run_id=str(uuid4()),
            on_progress=lambda event, render: progress.append((event, render)),
        )
    finally:
        await runtime.close()

    renders = [
        render for event, render in progress if event.get("phase") == "direct_completed"
    ]
    assert len(renders) == 1
    assert renders[0] is not None
    assert renders[0].startswith(b"\x89PNG\r\n\x1a\n")

    node_events = [
        event
        for event, render in progress
        if event.get("phase") in {"node_running", "node_completed"}
    ]
    assert len(node_events) % 2 == 0
    assert all(
        running["node"] == completed["node"]
        and running["status"] == "running"
        and completed["status"] == "completed"
        and "duration_ms" not in running
        and isinstance(completed.get("duration_ms"), float)
        for running, completed in zip(node_events[::2], node_events[1::2], strict=True)
    )
    assert {
        "prepare_reference",
        "author_layer_plan",
        "author_initial",
        "compile_candidate",
        "validate_candidate",
        "prepare_program",
        "render_program",
        "verify_receipt",
        "attest_candidate",
        "evaluate_candidate",
        "select_candidate",
        "decide_uniform_optimization",
        "release_resources",
        "finalize_attempt",
    }.issubset({event["node"] for event in node_events})


@pytest.mark.anyio
async def test_runtime_closes_direct_lifecycle_events_on_attempt_timeout(
    tmp_path: Path,
) -> None:
    runtime = build_engine_rollout_runtime(
        public_store=LocalArtifactStore(tmp_path / "public"),
        private_attempt_root=tmp_path / "private",
        direct_runner_factory=_HangingOwnedRunner,
        attempt_timeout_seconds=0.01,
    )
    progress: list[tuple[dict[str, object], bytes | None]] = []
    try:
        with pytest.raises(ParentRunFailure):
            await runtime.generate(
                _reference_png(),
                "image/png",
                project_id=str(uuid4()),
                run_id=str(uuid4()),
                on_progress=lambda event, render: progress.append((event, render)),
            )
    finally:
        await runtime.close()

    attempt_events = [
        event for event, _render in progress if event.get("node") == "direct_glsl"
    ]
    assert [event["phase"] for event in attempt_events] == [
        "direct_start",
        "direct_failed",
    ] * 3
    assert all(
        event.get("failure_code") == "engine_attempt_cancelled"
        for event in attempt_events[1::2]
    )


@pytest.mark.anyio
async def test_runtime_closes_direct_lifecycle_events_on_unexpected_failure(
    tmp_path: Path,
) -> None:
    runtime = build_engine_rollout_runtime(
        public_store=LocalArtifactStore(tmp_path / "public"),
        private_attempt_root=tmp_path / "private",
        direct_runner_factory=_ExplodingOwnedRunner,
    )
    progress: list[tuple[dict[str, object], bytes | None]] = []
    try:
        with pytest.raises(ParentRunFailure):
            await runtime.generate(
                _reference_png(),
                "image/png",
                project_id=str(uuid4()),
                run_id=str(uuid4()),
                on_progress=lambda event, render: progress.append((event, render)),
            )
    finally:
        await runtime.close()

    attempt_events = [
        event for event, _render in progress if event.get("node") == "direct_glsl"
    ]
    assert [event["phase"] for event in attempt_events] == [
        "direct_start",
        "direct_failed",
    ] * 3
    assert all(
        event.get("failure_code") == "direct_attempt_failed"
        for event in attempt_events[1::2]
    )
