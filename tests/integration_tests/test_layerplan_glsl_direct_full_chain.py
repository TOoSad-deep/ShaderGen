"""固定 fake LLM/Renderer 的 direct attempt 全内存链集成验收."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest

from agent.app.services.layerplan_glsl_direct import (
    LayerPlanGlslDirectConfig,
    LayerPlanGlslDirectRunner,
)
from backend.app.services.engine_rollout import EngineAttemptFailure, ParentRunFailure
from backend.app.services.engine_rollout_runtime import (
    _write_private_process_renders,
    build_engine_rollout_runtime,
)
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
            gateway=_LayeredFakeGateway(
                initial_gains=(0.9,),
                refine_gains=(0.6, 0.75),
            ),
            renderer=_FakeRenderer(),
            config=replace(config, uniform_tuning_draw_budget=0),
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
async def test_private_process_renders_reject_parameter_trial_source(
    tmp_path: Path,
) -> None:
    runner = LayerPlanGlslDirectRunner(
        gateway=_LayeredFakeGateway(),
        renderer=_FakeRenderer(),
        config=LayerPlanGlslDirectConfig(
            implementation_identity_sha256="b" * 64,
            refine_budget=0,
        ),
        receipt_issuer=_TEST_ISSUER,
    )
    result = await runner.run(_reference_png(), instruction="match")
    assert result.current_best is not None
    parameter_trial = replace(
        result.current_best,
        provenance="parameter_tuning_trial",
    )
    invalid_result = replace(
        result,
        current_best=parameter_trial,
        candidates=(parameter_trial,),
    )
    private_run = LocalArtifactStore(
        tmp_path / "private",
        restrictive_permissions=True,
    ).register_run("project", "attempt")

    with pytest.raises(
        EngineAttemptFailure,
        match="direct_candidate_retention_invalid",
    ):
        _write_private_process_renders(private_run, invalid_result)

    assert not (private_run.root / "private" / "renders").exists()


@pytest.mark.anyio
async def test_runtime_progress_publishes_selected_render(tmp_path: Path) -> None:
    project_id = str(uuid4())
    run_id = str(uuid4())
    runtime = build_engine_rollout_runtime(
        public_store=LocalArtifactStore(tmp_path / "public"),
        private_attempt_root=tmp_path / "private",
        direct_runner_factory=_OwnedFakeRunner,
    )
    progress: list[tuple[dict[str, object], bytes | None]] = []
    try:
        generated = await runtime.generate(
            _reference_png(),
            "image/png",
            project_id=project_id,
            run_id=run_id,
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

    attempt_id = generated.engine_run["selected_attempt_id"]
    private_run = runtime.artifacts.private_attempt_store.resolve_run(attempt_id)
    manifest = json.loads(private_run.read_bytes("private/manifest.json"))
    assert manifest["schema_version"] == "direct_private_attempt_v2"
    retention = manifest["render_retention"]
    assert retention["schema_version"] == "direct_process_renders_v1"
    assert retention["scope"] == "high_level_author_candidates"
    assert retention["parameter_trials_retained"] is False
    assert retention["render_count"] == 3
    assert retention["final_best_sequence"] == 3

    process_renders = retention["renders"]
    assert [item["role"] for item in process_renders] == [
        "initial",
        "refine",
        "refine",
    ]
    assert [item["sequence"] for item in process_renders] == [2, 3, 4]
    assert [item["became_current_best"] for item in process_renders] == [
        True,
        True,
        False,
    ]
    assert [item["is_final_best"] for item in process_renders] == [
        False,
        True,
        False,
    ]
    for item in process_renders:
        data = private_run.read_bytes(item["relative_path"])
        assert data.startswith(b"\x89PNG\r\n\x1a\n")
        assert item["sha256"] == sha256(data).hexdigest()
        assert item["size_bytes"] == len(data)
        assert item["content_type"] == "image/png"
    assert len({item["sha256"] for item in process_renders}) == 3

    selected = next(item for item in process_renders if item["is_final_best"])
    rejected = process_renders[-1]
    assert rejected["objective_loss"] > selected["objective_loss"]
    selected_render = private_run.read_bytes(selected["relative_path"])
    assert private_run.read_bytes("private/render.png") == selected_render
    assert renders[0] == selected_render
    public_files = runtime.artifacts.public_store.verify_public_final_bundle(run_id)
    assert set(public_files) == {"render.png", "metrics.json", "manifest.json"}
    assert public_files["render.png"] == selected_render
    public_manifest = json.loads(public_files["manifest.json"])
    assert "render_retention" not in public_manifest
    assert b"private/renders/" not in public_files["manifest.json"]

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
