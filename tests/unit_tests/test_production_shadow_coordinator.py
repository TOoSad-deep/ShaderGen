"""D095 production shadow 的 policy、容量、隔离与私有 Artifact 回归."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import time
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI

import backend.app.main as backend_main
import backend.app.services.production_shadow as production_shadow_module
from backend.app.core.engine_policy import (
    ShaderEnginePolicyV1,
    disabled_shader_engine_policy,
    resolve_engine_policy,
    stable_project_bucket,
)
from backend.app.core.settings import BackendSettings
from backend.app.services.production_shadow import (
    ProductionShadowArtifactError,
    ProductionShadowConfig,
    ProductionShadowCoordinator,
    direct_shadow_attempt_id,
    verify_production_shadow_attempt,
)
from backend.app.services.shader import ProjectLockRegistry
from backend.app.services.shader_generation import (
    ShaderGenerationCommand,
    ShaderGenerationDependencies,
    ShaderGenerationUseCaseError,
    execute_shader_generation,
)


def _policy(percent: int = 100) -> ShaderEnginePolicyV1:
    return ShaderEnginePolicyV1(
        policy_id="production-shadow-test-v1",
        stage="production_shadow",
        shadow_percent=percent,
        canary_percent=0,
        bucket_basis="project_id_v1",
        direct_engine="direct_glsl_layerplan_v1",
        fallback_engine="shader_graph_v1",
        promotion_authorization=None,
    )


def _config(root: Path, **overrides: Any) -> ProductionShadowConfig:
    values: dict[str, Any] = {
        "output_root": root,
        "queue_capacity": 2,
        "worker_count": 1,
        "attempt_timeout_seconds": 0.2,
        "close_timeout_seconds": 0.1,
        "resource_close_timeout_seconds": 0.05,
    }
    values.update(overrides)
    return ProductionShadowConfig(**values)


class _ImmediateRunner:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.closed = False

    async def run(self, *_args: Any, **_kwargs: Any) -> Any:
        return self.result

    async def close(self) -> None:
        self.closed = True


class _BlockingRunner:
    def __init__(self, started: asyncio.Event, release: asyncio.Event) -> None:
        self.started = started
        self.release = release
        self.closed = False

    async def run(self, *_args: Any, **_kwargs: Any) -> Any:
        self.started.set()
        await self.release.wait()
        raise AssertionError("测试不应释放 blocking runner。")

    async def close(self) -> None:
        self.closed = True


class _Serializable:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload)


class _FakeDirectResult:
    """只为协调器 Artifact 边界提供完整私有 plan/spec/render/metric."""

    status = "ok"
    failure_code = None

    def __init__(self, config: Any, *, marker: str) -> None:
        self.config = config
        self.reference_sha256 = "1" * 64
        self.identity = SimpleNamespace(metric_version="min_scene_composite_v3")
        self.layer_plan = SimpleNamespace(
            schema_version="layer_plan_v1",
            layers=[_Serializable({"id": f"layer-{marker}", "label": "private"})],
            reference_sha256="1" * 64,
            author_identity=_Serializable({"model_ref": "private-model"}),
            observations_ref="private-observations",
            plan_sha256="2" * 64,
        )
        spec = SimpleNamespace(
            schema_version="shader_program_spec_v1",
            renderer_contract_id="webgl1_static_no_texture_v1",
            fragment_source=f"private-glsl-{marker}",
            uniform_schema=[_Serializable({"name": "u_gain", "type": "float"})],
            uniform_values={"u_gain": 0.5},
            tunable_manifest=[_Serializable({"name": "u_gain"})],
            canvas=_Serializable({"width": 1, "height": 1}),
            source_sha256="3" * 64,
            binding_sha256="4" * 64,
            spec_sha256="5" * 64,
            author_identity=_Serializable({"model_ref": "private-model"}),
            validation_attestation=_Serializable({"verified": True}),
        )
        self.current_best = SimpleNamespace(
            spec=spec,
            png_bytes=b"\x89PNG\r\n\x1a\nprivate",
            rgb_bytes=b"\x80\x80\x80",
            mae=0.1,
            loss=0.2,
            metrics={"total_loss": 0.2},
            residual_summary={"region": "center"},
            parent_spec_sha256=None,
            provenance="model_generated_direct_glsl",
        )

    def to_safe_summary(self) -> dict[str, Any]:
        return {
            "schema_version": "direct_glsl_attempt_result_v1",
            "status": "ok",
            "current_best": {
                "spec_sha256": "5" * 64,
                "loss": 0.2,
            },
        }


async def _wait_for(path: Path) -> None:
    for _ in range(100):
        if path.exists():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"等待 Artifact 超时：{path}")


def test_private_file_write_is_fsynced_atomic_and_permission_bounded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o700)
    data = b"private-shadow-artifact"
    replace_calls: list[tuple[Path, Path]] = []
    fsync_calls: list[int] = []
    write_events: list[str] = []
    original_replace = os.replace
    original_fsync = os.fsync

    def tracked_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        source_path = Path(source)
        target_path = Path(target)
        assert source_path.parent == target_path.parent
        assert source_path.name.startswith(f".{target_path.name}.")
        assert source_path.suffix == ".tmp"
        assert source_path.read_bytes() == data
        assert stat.S_IMODE(source_path.stat().st_mode) == 0o600
        assert write_events == ["fsync"]
        write_events.append("replace")
        replace_calls.append((source_path, target_path))
        original_replace(source_path, target_path)

    def tracked_fsync(descriptor: int) -> None:
        fsync_calls.append(descriptor)
        write_events.append("fsync")
        original_fsync(descriptor)

    monkeypatch.setattr(production_shadow_module.os, "replace", tracked_replace)
    monkeypatch.setattr(production_shadow_module.os, "fsync", tracked_fsync)

    digest = production_shadow_module._write_private_file(
        staging,
        "private/current-best/render.png",
        data,
    )
    target = staging / "private/current-best/render.png"

    assert digest == sha256(data).hexdigest()
    assert len(replace_calls) == 1
    assert replace_calls[0][1] == target
    assert len(fsync_calls) >= 2
    assert write_events == ["fsync", "replace", "fsync"]
    assert target.read_bytes() == data
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(target.parent.parent.stat().st_mode) == 0o700
    assert not list(staging.rglob("*.tmp"))


@pytest.mark.anyio
async def test_disabled_kill_switch_and_bucket_miss_never_construct_worker_or_root(
    tmp_path: Path,
) -> None:
    calls = 0

    def forbidden_factory(_config: Any) -> Any:
        nonlocal calls
        calls += 1
        raise AssertionError("skipped shadow 不得构造 runner。")

    disabled = disabled_shader_engine_policy()
    disabled_root = tmp_path / "disabled"
    coordinator = ProductionShadowCoordinator(
        policy=disabled,
        resolution=resolve_engine_policy(disabled, kill_switch_active=False),
        config=_config(disabled_root),
        runner_factory=forbidden_factory,
    )
    await coordinator.start()
    skipped = coordinator.submit(
        project_id="project-a",
        parent_run_id=uuid4(),
        image=b"image",
        content_type="image/png",
        instruction="",
    )
    await coordinator.close()
    assert skipped["reason"] == "shadow_skipped_disabled"
    assert not disabled_root.exists()

    policy = _policy()
    killed_root = tmp_path / "killed"
    killed = ProductionShadowCoordinator(
        policy=policy,
        resolution=resolve_engine_policy(policy, kill_switch_active=True),
        config=_config(killed_root),
        runner_factory=forbidden_factory,
    )
    await killed.start()
    killed_summary = killed.submit(
        project_id="project-a",
        parent_run_id=uuid4(),
        image=b"image",
        content_type="image/png",
        instruction="",
    )
    await killed.close()
    assert killed_summary["reason"] == "shadow_skipped_kill_switch"
    assert not killed_root.exists()

    partial = _policy(1)
    project_id = next(
        f"miss-{index}"
        for index in range(10_000)
        if stable_project_bucket(
            policy_id=partial.policy_id,
            project_id=f"miss-{index}",
        )
        >= 100
    )
    miss_root = tmp_path / "miss"
    missed = ProductionShadowCoordinator(
        policy=partial,
        resolution=resolve_engine_policy(partial, kill_switch_active=False),
        config=_config(miss_root),
        runner_factory=forbidden_factory,
    )
    await missed.start()
    miss_summary = missed.submit(
        project_id=project_id,
        parent_run_id=uuid4(),
        image=b"image",
        content_type="image/png",
        instruction="",
    )
    await missed.close()
    assert miss_summary["reason"] == "shadow_skipped_bucket"
    assert calls == 0
    assert not miss_root.exists()


@pytest.mark.anyio
async def test_lifespan_builds_starts_injects_and_closes_from_frozen_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    captured: dict[str, Any] = {}

    class _FakeCoordinator:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        async def start(self) -> None:
            events.append("shadow_start")

        async def close(self) -> None:
            events.append("shadow_close")

    async def open_database(app: FastAPI, _url: str | None) -> None:
        app.state.db_pool = None

    async def close_database(app: FastAPI) -> None:
        app.state.db_pool = None

    monkeypatch.setattr(
        backend_main,
        "ProductionShadowCoordinator",
        _FakeCoordinator,
    )
    monkeypatch.setattr(backend_main, "open_database_pool", open_database)
    monkeypatch.setattr(backend_main, "close_database_pool", close_database)
    monkeypatch.setattr(
        backend_main,
        "get_default_png_to_shader_min_service",
        lambda: None,
    )
    settings = BackendSettings(
        engine_policy=_policy(25),
        production_shadow_artifact_root=tmp_path,
        production_shadow_queue_capacity=7,
        production_shadow_worker_count=2,
        production_shadow_attempt_timeout_seconds=9.0,
        production_shadow_close_timeout_seconds=3.0,
        production_shadow_resource_close_timeout_seconds=1.0,
    )
    app = FastAPI()
    async with backend_main.build_lifespan(settings)(app):
        assert isinstance(
            app.state.production_shadow_coordinator,
            _FakeCoordinator,
        )
        assert captured["policy"] is settings.engine_policy
        assert captured["resolution"] == settings.engine_policy_resolution
        config = captured["config"]
        assert config.output_root == tmp_path
        assert config.queue_capacity == 7
        assert config.worker_count == 2
        assert config.attempt_timeout_seconds == 9.0
        assert events == ["shadow_start"]
    assert events == ["shadow_start", "shadow_close"]
    assert app.state.production_shadow_coordinator is None


@pytest.mark.anyio
async def test_submit_is_nonblocking_capacity_is_bounded_and_close_is_bounded(
    tmp_path: Path,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    runners: list[_BlockingRunner] = []

    def factory(_config: Any) -> _BlockingRunner:
        runner = _BlockingRunner(started, release)
        runners.append(runner)
        return runner

    policy = _policy()
    coordinator = ProductionShadowCoordinator(
        policy=policy,
        resolution=resolve_engine_policy(policy, kill_switch_active=False),
        config=_config(
            tmp_path,
            queue_capacity=1,
            attempt_timeout_seconds=10.0,
            close_timeout_seconds=0.03,
            resource_close_timeout_seconds=0.01,
        ),
        runner_factory=factory,
    )
    await coordinator.start()
    first_started = time.perf_counter()
    first = coordinator.submit(
        project_id="project-1",
        parent_run_id=uuid4(),
        image=b"one",
        content_type="image/png",
        instruction="private-one",
    )
    assert time.perf_counter() - first_started < 0.02
    assert first["status"] == "accepted"
    await asyncio.wait_for(started.wait(), timeout=0.2)
    second = coordinator.submit(
        project_id="project-2",
        parent_run_id=uuid4(),
        image=b"two",
        content_type="image/png",
        instruction="private-two",
    )
    third = coordinator.submit(
        project_id="project-3",
        parent_run_id=uuid4(),
        image=b"three",
        content_type="image/png",
        instruction="private-three",
    )
    assert second["status"] == "accepted"
    assert third["status"] == "skipped"
    assert third["reason"] == "shadow_skipped_capacity"
    close_started = time.perf_counter()
    await coordinator.close()
    assert time.perf_counter() - close_started < 0.2
    assert runners and runners[0].closed


@pytest.mark.anyio
async def test_attempt_timeout_writes_only_safe_failure_summary(tmp_path: Path) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    policy = _policy()
    parent = uuid4()
    coordinator = ProductionShadowCoordinator(
        policy=policy,
        resolution=resolve_engine_policy(policy, kill_switch_active=False),
        config=_config(tmp_path, attempt_timeout_seconds=0.01),
        runner_factory=lambda _config: _BlockingRunner(started, release),
    )
    await coordinator.start()
    submitted = coordinator.submit(
        project_id="project-timeout",
        parent_run_id=parent,
        image=b"private-image",
        content_type="image/png",
        instruction="private-instruction",
    )
    attempt_dir = tmp_path / str(parent) / str(direct_shadow_attempt_id(parent))
    await _wait_for(attempt_dir)
    await coordinator.close()
    summary = verify_production_shadow_attempt(attempt_dir)
    assert submitted["status"] == "accepted"
    assert summary["status"] == "timeout"
    assert summary["failure_code"] == "shadow_attempt_timeout"
    serialized = json.dumps(summary)
    assert "private-image" not in serialized
    assert "private-instruction" not in serialized
    assert (attempt_dir / "private/implementation-identity.json").is_file()
    assert not (attempt_dir / "private/layer-plan.json").exists()
    assert not (attempt_dir / "private/current-best").exists()


@pytest.mark.anyio
async def test_attempts_use_fresh_runner_and_isolated_write_once_tamper_checked(
    tmp_path: Path,
) -> None:
    policy = _policy()
    runners: list[_ImmediateRunner] = []

    def factory(config: Any) -> _ImmediateRunner:
        runner = _ImmediateRunner(
            _FakeDirectResult(config, marker=str(len(runners) + 1))
        )
        runners.append(runner)
        return runner

    coordinator = ProductionShadowCoordinator(
        policy=policy,
        resolution=resolve_engine_policy(policy, kill_switch_active=False),
        config=_config(tmp_path, worker_count=2),
        runner_factory=factory,
    )
    await coordinator.start()
    parents = (uuid4(), uuid4())
    for index, parent in enumerate(parents):
        accepted = coordinator.submit(
            project_id=f"project-{index}",
            parent_run_id=parent,
            image=f"image-{index}".encode(),
            content_type="image/png",
            instruction=f"private-{index}",
        )
        assert accepted["attempt_id"] == str(direct_shadow_attempt_id(parent))
        duplicate = coordinator.submit(
            project_id=f"project-{index}",
            parent_run_id=parent,
            image=b"different-private-image",
            content_type="image/png",
            instruction="different-private-instruction",
        )
        assert duplicate["reason"] == "shadow_skipped_duplicate"
    attempt_dirs = [
        tmp_path / str(parent) / str(direct_shadow_attempt_id(parent))
        for parent in parents
    ]
    for attempt_dir in attempt_dirs:
        await _wait_for(attempt_dir)
        verify_production_shadow_attempt(attempt_dir)
        assert (attempt_dir / "private/layer-plan.json").is_file()
        assert (attempt_dir / "private/current-best/spec.json").is_file()
        assert (attempt_dir / "private/current-best/render.png").is_file()
        assert (attempt_dir / "private/current-best/metric.json").is_file()
        assert "private-glsl" not in (attempt_dir / "safe-summary.json").read_text()
    completed_duplicate = coordinator.submit(
        project_id="project-0",
        parent_run_id=parents[0],
        image=b"different-after-completion",
        content_type="image/png",
        instruction="different-after-completion",
    )
    assert completed_duplicate["reason"] == "shadow_skipped_duplicate"
    await coordinator.close()
    assert len(runners) == 2
    assert len({id(runner) for runner in runners}) == 2
    assert all(runner.closed for runner in runners)

    target = attempt_dirs[0] / "private/current-best/spec.json"
    original = target.read_bytes()
    target.write_bytes(original + b"tampered")
    os.chmod(target, 0o600)
    with pytest.raises(ProductionShadowArtifactError, match="篡改"):
        verify_production_shadow_attempt(attempt_dirs[0])
    target.write_bytes(original)
    os.chmod(target, 0o600)

    extra = attempt_dirs[0] / "extra"
    extra.write_bytes(b"extra")
    os.chmod(extra, 0o600)
    with pytest.raises(ProductionShadowArtifactError, match="文件集合漂移"):
        verify_production_shadow_attempt(attempt_dirs[0])
    extra.unlink()

    renamed = target.with_name("renamed.json")
    target.rename(renamed)
    with pytest.raises(ProductionShadowArtifactError, match="缺失或改名"):
        verify_production_shadow_attempt(attempt_dirs[0])
    renamed.rename(target)

    os.chmod(target, 0o644)
    with pytest.raises(ProductionShadowArtifactError, match="权限非法"):
        verify_production_shadow_attempt(attempt_dirs[0])
    os.chmod(target, 0o600)

    link = attempt_dirs[0] / "link"
    link.symlink_to(target)
    with pytest.raises(ProductionShadowArtifactError, match="symlink"):
        verify_production_shadow_attempt(attempt_dirs[0])


def _authoritative_result(project_id: Any, run_id: Any) -> SimpleNamespace:
    return SimpleNamespace(
        project_id=str(project_id),
        run_id=str(run_id),
        glsl="authoritative-shader-graph-glsl",
        renderer_path="compiled_graph_program_cache_v1",
        scene={"schema_version": "shader_graph_v1"},
        trace=(),
        shader_graph_shadow=None,
        status="completed",
        stop_reason="budget_exhausted",
        render_width=8,
        render_height=8,
        current_best_mae=0.1,
        current_best_loss=0.2,
        metric_breakdown={},
        template_version="shader_graph_v1",
        render_count=1,
        render_budget=1,
        llm_call_count=0,
        llm_budget=0,
        refine_budget=0,
        run_classification="independent_experiment",
        experiment_id=None,
        config_fingerprint="a" * 64,
        report_schema_version="scene_mvp_run_report_v1",
        patch_candidate_draw_budget=0,
        patch_evidence=(),
        target_mae=0.0,
        target_loss=0.0,
        target_reached=False,
        prepare_duration_ms=1.0,
        uniform_render_count=1,
        uniform_render_p95_ms=1.0,
        quality_preset="fast",
    )


def _command(project_id: Any, run_id: Any) -> ShaderGenerationCommand:
    return ShaderGenerationCommand(
        image=b"image",
        filename="target.png",
        content_type="image/png",
        project_id=project_id,
        run_id=run_id,
        quality_preset="fast",
        instruction="private",
        started_at=time.perf_counter(),
    )


@pytest.mark.anyio
async def test_shadow_submit_failure_does_not_change_authoritative_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = uuid4()
    run_id = uuid4()
    result = _authoritative_result(project_id, run_id)

    async def generate(*_args: Any, **_kwargs: Any) -> Any:
        return result

    class _FailingShadow:
        def submit(self, **_kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("private shadow failure")

    monkeypatch.setattr(
        "backend.app.services.shader_generation.generate_scene_shader_from_image",
        generate,
    )
    response = await execute_shader_generation(
        _command(project_id, run_id),
        ShaderGenerationDependencies(
            pool=None,
            min_service=object(),
            locks=ProjectLockRegistry(),
            production_shadow=_FailingShadow(),
        ),
    )
    assert response.glsl == "authoritative-shader-graph-glsl"
    assert response.run_id == run_id


@pytest.mark.anyio
async def test_response_contract_failure_never_submits_shadow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = uuid4()
    run_id = uuid4()
    result = _authoritative_result(project_id, run_id)
    result.renderer_path = "unknown-renderer"

    async def generate(*_args: Any, **_kwargs: Any) -> Any:
        return result

    class _SpyShadow:
        calls = 0

        def submit(self, **_kwargs: Any) -> dict[str, Any]:
            self.calls += 1
            return {"status": "accepted"}

    shadow = _SpyShadow()
    monkeypatch.setattr(
        "backend.app.services.shader_generation.generate_scene_shader_from_image",
        generate,
    )
    with pytest.raises(ShaderGenerationUseCaseError) as raised:
        await execute_shader_generation(
            _command(project_id, run_id),
            ShaderGenerationDependencies(
                pool=None,
                min_service=object(),
                locks=ProjectLockRegistry(),
                production_shadow=shadow,
            ),
        )
    assert getattr(raised.value, "code", None) == "response_contract_failed"
    assert shadow.calls == 0
