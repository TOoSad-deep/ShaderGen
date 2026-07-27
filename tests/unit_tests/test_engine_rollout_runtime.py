"""真实 rollout runtime 的 direct/private-old/reader 接线回归."""

from __future__ import annotations

import json
import stat
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

import backend.app.services.engine_rollout_runtime as runtime_module
from backend.app.core.engine_policy import (
    PromotionAuthorizationV1,
    ShaderEnginePolicyV1,
    direct_default_shader_engine_policy,
    promotion_authorization_sha256,
    resolve_engine_policy,
)
from backend.app.core.promotion_authorization import (
    PromotionAuthorizationVerification,
)
from backend.app.services.engine_rollout import (
    EngineResponseContractFailure,
    ParentRunFailure,
    ParentRunRequest,
    child_attempt_id,
)
from backend.app.services.engine_rollout_runtime import (
    EngineRolloutGenerationResult,
    FrozenPromotionEvidenceVerifier,
    build_engine_rollout_runtime,
)
from shaderforge.store import LocalArtifactStore

_IDENTITY = "a" * 64
_PNG = b"\x89PNG\r\n\x1a\nruntime"


def _authorization() -> PromotionAuthorizationV1:
    return PromotionAuthorizationV1(
        authorization_id="canary-runtime-auth",
        target_stage="canary",
        d090_suite_report_sha256="1" * 64,
        automatic_gate_outcome="supported",
        recursive_verifier_version="promotion-verifier-v1",
        recursive_verification_result="verified",
        human_blind_review_manifest_sha256="2" * 64,
        human_blind_review_result_sha256="3" * 64,
        human_blind_review_b_preference=0.625,
        human_gate_outcome="supported",
        durable_registry_entry_id="direct-runtime-v1",
        durable_evidence_uri="s3://shadergen-evidence/direct-runtime-v1",
        durable_evidence_sha256="4" * 64,
        durability_status="durable",
        direct_implementation_identity=_IDENTITY,
        max_canary_percent=100,
        approved_at=datetime(2026, 7, 27, tzinfo=UTC),
        adr_id="ADR-D095-runtime-test",
    )


def _policy() -> ShaderEnginePolicyV1:
    return ShaderEnginePolicyV1(
        policy_id="canary-runtime-v1",
        stage="canary",
        shadow_percent=0,
        canary_percent=100,
        bucket_basis="project_id_v1",
        direct_engine="direct_glsl_layerplan_v1",
        fallback_engine="shader_graph_v1",
        promotion_authorization=_authorization(),
    )


def _receipt() -> PromotionAuthorizationVerification:
    digest = promotion_authorization_sha256(_authorization())
    assert digest is not None
    return PromotionAuthorizationVerification(
        authorization_sha256=digest,
        registry_sha256="5" * 64,
        registry_entry_id="direct-runtime-v1",
        target_stage="canary",
        durable_evidence_uri="s3://shadergen-evidence/direct-runtime-v1",
        durable_evidence_sha256="4" * 64,
        direct_implementation_identity=_IDENTITY,
    )


class _PublicService:
    def __init__(self, root: Path) -> None:
        self.artifacts = LocalArtifactStore(root)


class _FakeDirectResult:
    def __init__(self, config: Any, *, ok: bool) -> None:
        identity = SimpleNamespace(to_dict=lambda: {"model_ref": "fixture"})
        attestation = SimpleNamespace(to_dict=lambda: {"draw_ok": True})
        spec = SimpleNamespace(
            fragment_source="void main() {}",
            schema_version="shader_program_spec_v1",
            uniform_schema=(),
            uniform_values={},
            tunable_manifest=(),
            canvas=SimpleNamespace(to_dict=lambda: {"width": 64, "height": 48}),
            renderer_contract_id="webgl1_static_no_texture_v1",
            source_sha256="8" * 64,
            binding_sha256="9" * 64,
            spec_sha256="a" * 64,
            author_identity=identity,
            validation_attestation=attestation,
        )
        self.status = "ok" if ok else "inconclusive"
        self.current_best = (
            SimpleNamespace(
                spec=spec,
                png_bytes=_PNG,
                mae=0.1,
                loss=0.2,
                metrics={"global_mae": 0.1},
                residual_summary={"worst_tile_mae": 0.2},
            )
            if ok
            else None
        )
        self.layer_plan = (
            SimpleNamespace(
                schema_version="layer_plan_v1",
                layers=(SimpleNamespace(to_dict=lambda: {"kind": "shape"}),),
                reference_sha256="b" * 64,
                author_identity=identity,
                observations_ref=None,
                plan_sha256="c" * 64,
            )
            if ok
            else None
        )
        self.canvas_width = 64
        self.canvas_height = 48
        self.identity = SimpleNamespace(implementation_identity_sha256=_IDENTITY)
        self.config = config
        self.config_fingerprint = "6" * 64
        self.plan_ledger = SimpleNamespace(llm_call_count=1)
        self.direct_ledger = SimpleNamespace(
            llm_call_count=1,
            draw_count=1,
            compile_count=1,
        )

    def to_safe_summary(self) -> dict[str, Any]:
        return {
            "schema_version": "direct_glsl_attempt_result_v1",
            "status": self.status,
            "identity": {
                "implementation_identity_sha256": _IDENTITY,
            },
        }


class _FakeDirectRunner:
    def __init__(
        self,
        config: Any,
        *,
        ok: bool,
        raise_error: bool = False,
    ) -> None:
        self.config = config
        self.ok = ok
        self.raise_error = raise_error
        self.closed = False

    async def run(self, *args: Any, **kwargs: Any) -> _FakeDirectResult:
        if self.raise_error:
            raise RuntimeError("private direct fixture error")
        return _FakeDirectResult(self.config, ok=self.ok)

    async def close(self) -> None:
        self.closed = True


class _DirectFactory:
    def __init__(self, *, ok: bool, raise_error: bool = False) -> None:
        self.ok = ok
        self.raise_error = raise_error
        self.runners: list[_FakeDirectRunner] = []

    def __call__(self, config: Any) -> _FakeDirectRunner:
        runner = _FakeDirectRunner(
            config,
            ok=self.ok,
            raise_error=self.raise_error,
        )
        self.runners.append(runner)
        return runner


def _old_result(project_id: str, run_id: str) -> Any:
    return SimpleNamespace(
        project_id=project_id,
        run_id=run_id,
        glsl="old-private-glsl",
        render_width=32,
        render_height=24,
        status="completed",
        stop_reason="bounded_mvp_complete",
        template_version="shader_graph_v1",
        quality_preset="fast",
        current_best_mae=0.3,
        current_best_loss=0.4,
        metric_breakdown={"global_mae": 0.3},
        render_count=2,
        render_budget=48,
        llm_call_count=1,
        llm_budget=2,
        refine_budget=1,
        run_classification="frozen_benchmark",
        experiment_id=None,
        config_fingerprint="7" * 64,
        report_schema_version="png_to_shader_min_report_v1",
        patch_candidate_draw_budget=1,
        patch_evidence=(),
        renderer_path="compiled_graph_program_cache_v1",
        target_mae=0.08,
        target_loss=0.04,
        target_reached=False,
        prepare_duration_ms=1.0,
        uniform_render_count=1,
        uniform_render_p95_ms=1.0,
        scene={"schema_version": "shader_document_v1"},
        trace=({"phase": "finalize", "status": "completed"},),
        shader_graph_shadow=None,
    )


class _FakeOldService:
    def __init__(self, root: Path, *, fail: bool = False) -> None:
        self.store = LocalArtifactStore(root, restrictive_permissions=True)
        self.fail = fail
        self.closed = False

    async def generate(
        self,
        image: bytes,
        content_type: str,
        *,
        project_id: str,
        run_id: str,
        quality_preset: str,
        instruction: str,
        on_progress: Any = None,
    ) -> Any:
        if self.fail:
            raise RuntimeError("private fixture failure must not escape")
        if on_progress is not None:
            on_progress(
                {
                    "node": "private_shader_graph",
                    "phase": "render",
                    "status": "completed",
                },
                None,
            )
        run = self.store.register_run(project_id, run_id)
        run.write_bytes("final/render.png", _PNG + b"-old")
        run.write_json("final/metrics.json", {"loss": 0.4})
        run.write_json(
            "final/manifest.json",
            {
                "schema_version": "png_to_shader_graph_manifest_v1",
                "project_id": project_id,
                "run_id": run_id,
            },
        )
        return _old_result(project_id, run_id)

    async def close(self) -> None:
        self.closed = True


class _OldFactory:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.services: list[_FakeOldService] = []

    def __call__(self, root: Path) -> _FakeOldService:
        service = _FakeOldService(root, fail=self.fail)
        self.services.append(service)
        return service


def _build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    direct_ok: bool,
    direct_error: bool = False,
    old_fail: bool = False,
) -> tuple[Any, _PublicService, _DirectFactory, _OldFactory]:
    monkeypatch.setattr(
        runtime_module,
        "current_direct_glsl_implementation_identity",
        lambda: {"identity_sha256": _IDENTITY},
    )
    public = _PublicService(tmp_path / "public")
    direct = _DirectFactory(ok=direct_ok, raise_error=direct_error)
    old = _OldFactory(fail=old_fail)
    policy = _policy()
    runtime = build_engine_rollout_runtime(
        policy=policy,
        resolution=resolve_engine_policy(policy, kill_switch_active=False),
        promotion_verification=_receipt(),
        public_min_service=public,
        private_attempt_root=tmp_path / "private",
        direct_runner_factory=direct,
        private_shader_graph_service_factory=old,
    )
    assert runtime is not None
    return runtime, public, direct, old


def _assert_private_permissions(root: Path) -> None:
    assert root.is_dir()
    for path in [root, *root.rglob("*")]:
        expected = 0o700 if path.is_dir() else 0o600
        assert stat.S_IMODE(path.stat().st_mode) == expected


def test_disabled_stage_does_not_construct_artifact_or_executor_runtime(
    tmp_path: Path,
) -> None:
    policy = ShaderEnginePolicyV1(
        policy_id="disabled-v1",
        stage="disabled",
        shadow_percent=0,
        canary_percent=0,
        bucket_basis="project_id_v1",
        direct_engine="direct_glsl_layerplan_v1",
        fallback_engine="shader_graph_v1",
        promotion_authorization=None,
    )
    runtime = build_engine_rollout_runtime(
        policy=policy,
        resolution=resolve_engine_policy(policy, kill_switch_active=False),
        promotion_verification=None,
        public_min_service=object(),
        private_attempt_root=tmp_path / "must-not-exist",
    )
    assert runtime is None
    assert not (tmp_path / "must-not-exist").exists()


def test_default_direct_runtime_builds_without_promotion_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime_module,
        "current_direct_glsl_implementation_identity",
        lambda: {"identity_sha256": _IDENTITY},
    )
    policy = direct_default_shader_engine_policy()
    runtime = build_engine_rollout_runtime(
        policy=policy,
        resolution=resolve_engine_policy(policy, kill_switch_active=False),
        promotion_verification=None,
        public_min_service=_PublicService(tmp_path / "public"),
        private_attempt_root=tmp_path / "private",
        direct_runner_factory=_DirectFactory(ok=True),
        private_shader_graph_service_factory=_OldFactory(),
    )
    assert runtime is not None
    plan = runtime.plan(parent_run_id=uuid4(), project_id="solo-developer")
    assert plan.primary_engine == "direct_glsl_layerplan_v1"
    assert plan.promotion_authorization_sha256 is None


def test_frozen_verifier_rejects_receipt_drift() -> None:
    receipt = _receipt()
    drifted = PromotionAuthorizationVerification(
        authorization_sha256=receipt.authorization_sha256,
        registry_sha256=receipt.registry_sha256,
        registry_entry_id="other-entry",
        target_stage=receipt.target_stage,
        durable_evidence_uri=receipt.durable_evidence_uri,
        durable_evidence_sha256=receipt.durable_evidence_sha256,
        direct_implementation_identity=receipt.direct_implementation_identity,
    )
    with pytest.raises(
        Exception,
        match="promotion_receipt_identity_drift",
    ):
        FrozenPromotionEvidenceVerifier(drifted).verify(_authorization())


@pytest.mark.anyio
async def test_runtime_direct_success_publishes_and_reads_parent_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, public, direct, old = _build(
        tmp_path,
        monkeypatch,
        direct_ok=True,
    )
    parent = uuid4()
    events: list[dict[str, Any]] = []
    result = await runtime.execute(
        request=ParentRunRequest(
            parent_run_id=parent,
            project_id="project-direct",
            image=b"private-image",
            content_type="image/png",
            instruction="private-instruction",
            quality_preset="fast",
            progress_callback=lambda event, _render: events.append(dict(event)),
        )
    )

    attempt = child_attempt_id(parent, "direct_glsl_layerplan_v1", 0)
    assert result.engine == "direct_glsl_layerplan_v1"
    assert result.response_payload["run_id"] == str(parent)
    assert result.response_payload["min_pipeline"]["renderer_path"] == (
        "direct_program_spec_v1"
    )
    assert result.response_payload["min_pipeline"]["patch_candidate_draw_budget"] == 0
    assert result.response_payload["min_pipeline"]["uniform_render_count"] == 0
    assert [event["phase"] for event in events] == [
        "direct_start",
        "direct_completed",
    ]
    assert "private-instruction" not in json.dumps(events)
    assert direct.runners[0].closed
    assert not old.services
    assert (
        await runtime.read_public_artifact(str(parent), "final-render")
    ).data == _PNG
    private = runtime.artifacts.private_attempt_store.resolve_run(str(attempt))
    assert private.read_bytes("private/shader.frag") == b"void main() {}"
    assert (
        json.loads(private.read_bytes("private/program-spec.json"))["fragment_source"]
        == "void main() {}"
    )
    with pytest.raises(FileNotFoundError):
        public.artifacts.resolve_run(str(attempt))
    _assert_private_permissions(tmp_path / "private")

    with pytest.raises(ParentRunFailure):
        await runtime.execute(
            request=ParentRunRequest(
                parent_run_id=parent,
                project_id="project-direct",
                image=b"retry-must-not-overwrite",
                content_type="image/png",
                instruction="retry",
                quality_preset="fast",
            )
        )
    assert private.read_bytes("private/shader.frag") == b"void main() {}"


@pytest.mark.anyio
async def test_parent_response_contract_reports_invalid_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _public, _direct, _old = _build(
        tmp_path,
        monkeypatch,
        direct_ok=True,
    )
    result = await runtime.execute(
        request=ParentRunRequest(
            parent_run_id=uuid4(),
            project_id="project-invalid-contract",
            image=b"private-image",
            content_type="image/png",
            instruction="",
            quality_preset="fast",
        )
    )
    result.response_payload["min_pipeline"]["render_count"] = "1"

    with pytest.raises(EngineResponseContractFailure) as raised:
        EngineRolloutGenerationResult.from_parent_result(result)
    assert raised.value.code == "engine_response_contract_failed"
    assert raised.value.field == "min_pipeline.render_count"


@pytest.mark.anyio
async def test_runtime_direct_failure_uses_fresh_private_old_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, public, direct, old = _build(
        tmp_path,
        monkeypatch,
        direct_ok=False,
    )
    parent = uuid4()
    result = await runtime.execute(
        request=ParentRunRequest(
            parent_run_id=parent,
            project_id="project-fallback",
            image=b"private-image",
            content_type="image/png",
            instruction="private-instruction",
            quality_preset="fast",
        )
    )

    fallback = child_attempt_id(parent, "shader_graph_v1", 1)
    assert result.engine == "shader_graph_v1"
    assert result.engine_run["fallback_from"] == "direct_glsl_layerplan_v1"
    assert result.engine_run["fallback_reason"] == "direct_attempt_inconclusive"
    assert direct.runners[0].closed
    assert old.services[0].closed
    direct_attempt = child_attempt_id(
        parent,
        "direct_glsl_layerplan_v1",
        0,
    )
    failure = json.loads(
        runtime.artifacts.private_attempt_store.resolve_run(
            str(direct_attempt)
        ).read_bytes("private/failure-summary.json")
    )
    assert failure["failure_code"] == "direct_attempt_inconclusive"
    assert "private-instruction" not in json.dumps(failure)
    assert runtime.artifacts.private_attempt_store.resolve_run(str(fallback))
    with pytest.raises(FileNotFoundError):
        public.artifacts.resolve_run(str(fallback))
    manifest = json.loads(
        (await runtime.read_public_artifact(str(parent), "manifest")).data
    )
    assert manifest["run_id"] == str(parent)
    assert manifest["engine"] == "shader_graph_v1"
    assert manifest["engine_run"]["selected_attempt_id"] == str(fallback)
    _assert_private_permissions(tmp_path / "private")


@pytest.mark.anyio
async def test_runtime_generate_emits_parent_progress_and_preserves_failed_children(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _public, _direct, _old = _build(
        tmp_path,
        monkeypatch,
        direct_ok=False,
    )
    events: list[dict[str, Any]] = []
    parent = uuid4()
    generated = await runtime.generate(
        b"private-image",
        "image/png",
        project_id="project-generate",
        run_id=str(parent),
        quality_preset="fast",
        instruction="private-instruction",
        on_progress=lambda event, _render: events.append(dict(event)),
    )

    assert generated.run_id == str(parent)
    assert generated.engine == "shader_graph_v1"
    assert generated.engine_run["fallback_from"] == "direct_glsl_layerplan_v1"
    assert {event.get("phase") for event in events} >= {
        "engine_start",
        "direct_start",
        "direct_failed",
        "engine_fallback",
        "engine_completed",
        "render",
    }
    direct_failed = next(
        event for event in events if event.get("phase") == "direct_failed"
    )
    assert direct_failed["failure_code"] == "direct_attempt_inconclusive"
    assert "private-instruction" not in json.dumps(events)


@pytest.mark.anyio
async def test_direct_exception_emits_generic_safe_failed_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _public, _direct, _old = _build(
        tmp_path,
        monkeypatch,
        direct_ok=False,
        direct_error=True,
    )
    events: list[dict[str, Any]] = []
    generated = await runtime.generate(
        b"private-image",
        "image/png",
        project_id="project-direct-error",
        run_id=str(uuid4()),
        quality_preset="fast",
        instruction="private-instruction",
        on_progress=lambda event, _render: events.append(dict(event)),
    )

    assert generated.engine == "shader_graph_v1"
    failed = next(event for event in events if event.get("phase") == "direct_failed")
    assert failed["failure_code"] == "direct_attempt_failed"
    assert "fixture error" not in json.dumps(events)
    assert "private-instruction" not in json.dumps(events)


@pytest.mark.anyio
async def test_runtime_close_and_aclose_are_idempotent_and_observable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _public, _direct, _old = _build(
        tmp_path,
        monkeypatch,
        direct_ok=True,
    )
    assert not runtime.closed
    await runtime.close()
    await runtime.aclose()
    assert runtime.closed
    with pytest.raises(
        Exception,
        match="engine_rollout_runtime_closed",
    ):
        runtime.plan(parent_run_id=uuid4(), project_id="closed-runtime")


@pytest.mark.anyio
async def test_both_failed_attempts_keep_private_safe_summaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, public, _direct, _old = _build(
        tmp_path,
        monkeypatch,
        direct_ok=False,
        old_fail=True,
    )
    parent = uuid4()
    with pytest.raises(ParentRunFailure, match="direct_and_fallback_failed"):
        await runtime.execute(
            request=ParentRunRequest(
                parent_run_id=parent,
                project_id="project-both-fail",
                image=b"private-image",
                content_type="image/png",
                instruction="private-instruction",
                quality_preset="fast",
            )
        )

    for engine, index, expected in (
        ("direct_glsl_layerplan_v1", 0, "direct_attempt_inconclusive"),
        ("shader_graph_v1", 1, "shader_graph_attempt_failed"),
    ):
        attempt = child_attempt_id(parent, engine, index)  # type: ignore[arg-type]
        summary = json.loads(
            runtime.artifacts.private_attempt_store.resolve_run(
                str(attempt)
            ).read_bytes("private/failure-summary.json")
        )
        assert summary["failure_code"] == expected
        assert "private-instruction" not in json.dumps(summary)
    with pytest.raises(FileNotFoundError):
        public.artifacts.resolve_run(str(parent))
