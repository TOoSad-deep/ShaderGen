"""D095 dormant canary/direct-default 父 run 协调与原子发布回归."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import pytest

from agent.app.services.engine_rollout_artifacts import (
    EngineRolloutArtifactError,
    EngineRolloutArtifactService,
    SelectedEngineArtifacts,
)
from backend.app.core.engine_policy import (
    PromotionAuthorizationV1,
    ShaderEnginePolicyV1,
    direct_default_shader_engine_policy,
    promotion_authorization_sha256,
    resolve_engine_policy,
    stable_project_bucket,
)
from backend.app.services.engine_rollout import (
    EngineAttemptContext,
    EngineAttemptFailure,
    EngineAttemptSuccess,
    EngineParentRunCoordinator,
    ParentRunFailure,
    ParentRunRequest,
    PromotionAuthorityUnavailable,
    VerifiedPromotionEvidence,
    child_attempt_id,
    resolve_parent_run_plan,
)
from shaderforge.store import LocalArtifactStore

_DIRECT_IDENTITY = "a" * 64
_PNG = b"\x89PNG\r\n\x1a\nrollout"


def _authorization(
    *,
    target: Literal["canary", "direct_default"] = "canary",
    direct_identity: str = _DIRECT_IDENTITY,
) -> PromotionAuthorizationV1:
    return PromotionAuthorizationV1(
        authorization_id=f"{target}-authorization",
        target_stage=target,
        d090_suite_report_sha256="1" * 64,
        automatic_gate_outcome="supported",
        recursive_verifier_version="promotion-verifier-v1",
        recursive_verification_result="verified",
        human_blind_review_manifest_sha256="2" * 64,
        human_blind_review_result_sha256="3" * 64,
        human_blind_review_b_preference=0.625,
        human_gate_outcome="supported",
        durable_registry_entry_id="direct-glsl-durable-v1",
        durable_evidence_uri="s3://shadergen-evidence/direct-glsl-v1",
        durable_evidence_sha256="4" * 64,
        durability_status="durable",
        direct_implementation_identity=direct_identity,
        max_canary_percent=100,
        approved_at=datetime(2026, 7, 27, tzinfo=UTC),
        adr_id="ADR-D095-test",
    )


def _policy(
    *,
    stage: Literal["canary", "direct_default"] = "canary",
    percent: int = 100,
    direct_identity: str = _DIRECT_IDENTITY,
) -> ShaderEnginePolicyV1:
    authorization = _authorization(
        target="direct_default" if stage == "direct_default" else "canary",
        direct_identity=direct_identity,
    )
    return ShaderEnginePolicyV1(
        policy_id=f"{stage}-policy-v1",
        stage=stage,
        shadow_percent=0,
        canary_percent=100 if stage == "direct_default" else percent,
        bucket_basis="project_id_v1",
        direct_engine="direct_glsl_layerplan_v1",
        fallback_engine="shader_graph_v1",
        promotion_authorization=authorization,
    )


class _Verifier:
    def verify(
        self,
        authorization: PromotionAuthorizationV1,
    ) -> VerifiedPromotionEvidence:
        digest = promotion_authorization_sha256(authorization)
        assert digest is not None
        return VerifiedPromotionEvidence(
            authorization_sha256=digest,
            target_stage=authorization.target_stage,
            durable_registry_entry_id=authorization.durable_registry_entry_id,
            durable_evidence_sha256=authorization.durable_evidence_sha256,
            direct_implementation_identity=(
                authorization.direct_implementation_identity
            ),
        )


def test_default_direct_plan_needs_no_promotion_verifier() -> None:
    policy = direct_default_shader_engine_policy()
    plan = resolve_parent_run_plan(
        policy=policy,
        resolution=resolve_engine_policy(policy, kill_switch_active=False),
        parent_run_id=uuid4(),
        project_id="solo-developer",
    )
    assert plan.primary_engine == "direct_glsl_layerplan_v1"
    assert plan.effective_stage == "direct_default"
    assert plan.promotion_authorization_sha256 is None


def _artifacts(marker: str) -> SelectedEngineArtifacts:
    return SelectedEngineArtifacts(
        final_render=_PNG + marker.encode(),
        metrics_json=(
            json.dumps({"loss": 0.1, "marker": marker}, sort_keys=True) + "\n"
        ).encode(),
        engine_manifest_json=(
            json.dumps(
                {"schema_version": "engine_manifest_v1", "marker": marker},
                sort_keys=True,
            )
            + "\n"
        ).encode(),
    )


class _Executor:
    def __init__(
        self,
        context: EngineAttemptContext,
        *,
        failure_code: str | None = None,
        identity_drift: bool = False,
    ) -> None:
        self.context = context
        self.failure_code = failure_code
        self.identity_drift = identity_drift
        self.closed = False

    async def execute(
        self,
        request: ParentRunRequest,
        context: EngineAttemptContext,
    ) -> EngineAttemptSuccess:
        assert context is self.context
        if self.failure_code is not None:
            raise EngineAttemptFailure(self.failure_code)
        attempt_id = uuid4() if self.identity_drift else context.attempt_id
        return EngineAttemptSuccess(
            attempt_id=attempt_id,
            engine=context.engine,
            representation=context.representation,
            response_payload={
                "glsl": f"{context.engine}-glsl",
                "run_id": str(context.attempt_id),
                "project_id": request.project_id,
                "final_render_url": f"/private/{context.attempt_id}",
            },
            artifacts=_artifacts(context.engine),
        )

    async def close(self) -> None:
        self.closed = True


class _Factory:
    def __init__(
        self,
        *,
        failure_code: str | None = None,
        failure_codes: tuple[str | None, ...] | None = None,
        identity_drift: bool = False,
    ) -> None:
        self.failure_code = failure_code
        self.failure_codes = failure_codes
        self.identity_drift = identity_drift
        self.contexts: list[EngineAttemptContext] = []
        self.executors: list[_Executor] = []

    def __call__(self, context: EngineAttemptContext) -> _Executor:
        self.contexts.append(context)
        failure_code = self.failure_code
        if self.failure_codes is not None:
            position = min(len(self.contexts) - 1, len(self.failure_codes) - 1)
            failure_code = self.failure_codes[position]
        executor = _Executor(
            context,
            failure_code=failure_code,
            identity_drift=self.identity_drift,
        )
        self.executors.append(executor)
        return executor


def _artifact_service(tmp_path: Path) -> EngineRolloutArtifactService:
    return EngineRolloutArtifactService(
        public_store=LocalArtifactStore(tmp_path / "public"),
        private_attempt_store=LocalArtifactStore(
            tmp_path / "private",
            restrictive_permissions=True,
        ),
    )


def test_canary_direct_selection_requires_matching_durable_capability() -> None:
    policy = _policy()
    resolution = resolve_engine_policy(policy, kill_switch_active=False)
    parent = uuid4()
    with pytest.raises(
        PromotionAuthorityUnavailable,
        match="promotion_authority_unavailable",
    ):
        resolve_parent_run_plan(
            policy=policy,
            resolution=resolution,
            parent_run_id=parent,
            project_id="project-direct",
            direct_implementation_identity=_DIRECT_IDENTITY,
        )

    plan = resolve_parent_run_plan(
        policy=policy,
        resolution=resolution,
        parent_run_id=parent,
        project_id="project-direct",
        promotion_verifier=_Verifier(),
        direct_implementation_identity=_DIRECT_IDENTITY,
    )
    assert plan.primary_engine == "direct_glsl_layerplan_v1"
    assert plan.promotion_authorization_sha256 is not None

    drifted = _policy(direct_identity="b" * 64)
    with pytest.raises(
        PromotionAuthorityUnavailable,
        match="direct_implementation_identity_drift",
    ):
        resolve_parent_run_plan(
            policy=drifted,
            resolution=resolve_engine_policy(
                drifted,
                kill_switch_active=False,
            ),
            parent_run_id=parent,
            project_id="project-direct",
            promotion_verifier=_Verifier(),
            direct_implementation_identity=_DIRECT_IDENTITY,
        )


def test_canary_bucket_miss_and_kill_switch_remain_old_without_verifier() -> None:
    policy = _policy(percent=1)
    project_id = next(
        f"miss-{index}"
        for index in range(10_000)
        if stable_project_bucket(
            policy_id=policy.policy_id,
            project_id=f"miss-{index}",
        )
        >= 100
    )
    missed = resolve_parent_run_plan(
        policy=policy,
        resolution=resolve_engine_policy(policy, kill_switch_active=False),
        parent_run_id=uuid4(),
        project_id=project_id,
        direct_implementation_identity=_DIRECT_IDENTITY,
    )
    assert missed.primary_engine == "shader_graph_v1"

    killed_policy = _policy(percent=100)
    killed = resolve_parent_run_plan(
        policy=killed_policy,
        resolution=resolve_engine_policy(
            killed_policy,
            kill_switch_active=True,
        ),
        parent_run_id=uuid4(),
        project_id="always-direct-without-kill",
        direct_implementation_identity=_DIRECT_IDENTITY,
    )
    assert killed.effective_stage == "disabled"
    assert killed.primary_engine == "shader_graph_v1"


@pytest.mark.anyio
async def test_direct_success_publishes_parent_only_after_child_success(
    tmp_path: Path,
) -> None:
    policy = _policy()
    parent = uuid4()
    project_id = "project-direct"
    plan = resolve_parent_run_plan(
        policy=policy,
        resolution=resolve_engine_policy(policy, kill_switch_active=False),
        parent_run_id=parent,
        project_id=project_id,
        promotion_verifier=_Verifier(),
        direct_implementation_identity=_DIRECT_IDENTITY,
    )
    direct = _Factory()
    old = _Factory(failure_code="old_must_not_run")
    artifacts = _artifact_service(tmp_path)
    coordinator = EngineParentRunCoordinator(
        direct_factory=direct,
        shader_graph_factory=old,
        artifacts=artifacts,
    )
    result = await coordinator.execute(
        request=ParentRunRequest(
            parent_run_id=parent,
            project_id=project_id,
            image=b"private-image",
            content_type="image/png",
            instruction="private-instruction",
            quality_preset="fast",
        ),
        plan=plan,
    )

    expected_attempt = child_attempt_id(
        parent,
        "direct_glsl_layerplan_v1",
        0,
    )
    assert result.engine == "direct_glsl_layerplan_v1"
    assert result.representation == "shader_program_spec_v1"
    assert result.response_payload["run_id"] == str(parent)
    assert result.response_payload["engine"] == result.engine
    assert result.response_payload["final_render_url"].endswith(
        f"/{parent}/artifacts/final-render"
    )
    assert result.engine_run["selected_attempt_id"] == str(expected_attempt)
    assert result.engine_run["fallback_from"] is None
    assert len(direct.contexts) == 1
    assert not old.contexts
    assert direct.executors[0].closed
    manifest = artifacts.verify_parent(str(parent))
    assert manifest["engine"] == "direct_glsl_layerplan_v1"
    assert manifest["representation"] == "shader_program_spec_v1"


@pytest.mark.anyio
async def test_direct_failure_creates_fresh_direct_retry_and_never_runs_dsl(
    tmp_path: Path,
) -> None:
    policy = _policy(stage="direct_default")
    parent = uuid4()
    project_id = "project-fallback"
    plan = resolve_parent_run_plan(
        policy=policy,
        resolution=resolve_engine_policy(policy, kill_switch_active=False),
        parent_run_id=parent,
        project_id=project_id,
        promotion_verifier=_Verifier(),
        direct_implementation_identity=_DIRECT_IDENTITY,
    )
    direct = _Factory(failure_codes=("direct_compile_failed", None))
    old = _Factory(failure_code="dsl_must_not_run")
    artifacts = _artifact_service(tmp_path)
    coordinator = EngineParentRunCoordinator(
        direct_factory=direct,
        shader_graph_factory=old,
        artifacts=artifacts,
    )
    result = await coordinator.execute(
        request=ParentRunRequest(
            parent_run_id=parent,
            project_id=project_id,
            image=b"private",
            content_type="image/png",
            instruction="private",
            quality_preset="balanced",
        ),
        plan=plan,
    )

    direct_id = child_attempt_id(parent, "direct_glsl_layerplan_v1", 0)
    retry_id = child_attempt_id(parent, "direct_glsl_layerplan_v1", 1)
    assert direct_id != retry_id
    assert direct.contexts[0].attempt_id == direct_id
    assert direct.contexts[1].attempt_id == retry_id
    assert direct.contexts[0].artifact_scope == "private_attempt"
    assert direct.contexts[1].artifact_scope == "private_attempt"
    assert not old.contexts
    assert result.engine == "direct_glsl_layerplan_v1"
    assert result.engine_run["selected_attempt_id"] == str(retry_id)
    assert result.engine_run["fallback_from"] is None
    assert result.engine_run["fallback_reason"] is None
    assert [item["status"] for item in result.engine_run["attempt_refs"]] == [
        "failed",
        "succeeded",
    ]
    assert result.response_payload["run_id"] == str(parent)
    with pytest.raises(FileNotFoundError):
        artifacts.public_store.resolve_run(str(direct_id))
    with pytest.raises(FileNotFoundError):
        artifacts.public_store.resolve_run(str(retry_id))
    assert artifacts.verify_parent(str(parent))["engine"] == (
        "direct_glsl_layerplan_v1"
    )


@pytest.mark.anyio
async def test_both_direct_attempts_fail_without_publishing_parent(
    tmp_path: Path,
) -> None:
    policy = _policy()
    parent = uuid4()
    plan = resolve_parent_run_plan(
        policy=policy,
        resolution=resolve_engine_policy(policy, kill_switch_active=False),
        parent_run_id=parent,
        project_id="project-fail",
        promotion_verifier=_Verifier(),
        direct_implementation_identity=_DIRECT_IDENTITY,
    )
    artifacts = _artifact_service(tmp_path)
    direct = _Factory(failure_code="direct_draw_failed")
    old = _Factory(failure_code="old_render_failed")
    coordinator = EngineParentRunCoordinator(
        direct_factory=direct,
        shader_graph_factory=old,
        artifacts=artifacts,
    )
    with pytest.raises(
        ParentRunFailure,
        match="direct_attempts_failed",
    ) as raised:
        await coordinator.execute(
            request=ParentRunRequest(
                parent_run_id=parent,
                project_id="project-fail",
                image=b"private",
                content_type="image/png",
                instruction="private",
                quality_preset="fast",
            ),
            plan=plan,
        )
    assert [item.status for item in raised.value.attempt_refs] == [
        "failed",
        "failed",
    ]
    assert [item.engine for item in raised.value.attempt_refs] == [
        "direct_glsl_layerplan_v1",
        "direct_glsl_layerplan_v1",
    ]
    assert len(direct.contexts) == 2
    assert not old.contexts
    with pytest.raises(FileNotFoundError):
        artifacts.public_store.resolve_run(str(parent))


def test_parent_publish_is_atomic_write_once_and_private_root_is_separate(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_service(tmp_path)
    parent = str(uuid4())
    engine_run = {
        "selected_engine": "shader_graph_v1",
        "selected_representation": "shader_document_v1",
        "selected_attempt_id": "attempt-old",
        "attempt_refs": [
            {
                "attempt_id": "attempt-old",
                "engine": "shader_graph_v1",
                "representation": "shader_document_v1",
                "status": "succeeded",
                "failure_code": None,
            }
        ],
    }
    selected = _artifacts("old")
    first = artifacts.publish_parent(
        project_id="project",
        parent_run_id=parent,
        engine="shader_graph_v1",
        representation="shader_document_v1",
        engine_run=engine_run,
        selected=selected,
    )
    second = artifacts.publish_parent(
        project_id="project",
        parent_run_id=parent,
        engine="shader_graph_v1",
        representation="shader_document_v1",
        engine_run=engine_run,
        selected=selected,
    )
    assert first == second
    run = artifacts.public_store.resolve_run(parent)
    assert {path.name for path in run.path_for("final").iterdir()} == {
        "render.png",
        "metrics.json",
        "manifest.json",
    }
    assert not list(run.root.glob(".final.staging-*"))

    with pytest.raises(
        EngineRolloutArtifactError,
        match="原子发布失败",
    ):
        artifacts.publish_parent(
            project_id="project",
            parent_run_id=parent,
            engine="shader_graph_v1",
            representation="shader_document_v1",
            engine_run=engine_run,
            selected=_artifacts("different"),
        )

    extra = run.path_for("final/extra.txt")
    extra.write_text("extra", encoding="utf-8")
    with pytest.raises(EngineRolloutArtifactError, match="不完整"):
        artifacts.verify_parent(parent)

    with pytest.raises(EngineRolloutArtifactError, match="必须隔离"):
        EngineRolloutArtifactService(
            public_store=artifacts.public_store,
            private_attempt_store=artifacts.public_store,
        )
    with pytest.raises(EngineRolloutArtifactError, match="restrictive"):
        EngineRolloutArtifactService(
            public_store=artifacts.public_store,
            private_attempt_store=LocalArtifactStore(
                tmp_path / "private-without-restrictions"
            ),
        )


@pytest.mark.parametrize(
    ("engine", "representation", "selected_representation", "match"),
    [
        (
            "shader_graph_v1",
            "shader_program_spec_v1",
            "shader_program_spec_v1",
            "配对非法",
        ),
        (
            "direct_glsl_layerplan_v1",
            "shader_document_v1",
            "shader_document_v1",
            "配对非法",
        ),
        (
            "shader_graph_v1",
            "shader_document_v1",
            "shader_program_spec_v1",
            "selected_representation",
        ),
    ],
)
def test_parent_publish_rejects_invalid_engine_representation_binding(
    tmp_path: Path,
    engine: Any,
    representation: Any,
    selected_representation: str,
    match: str,
) -> None:
    artifacts = _artifact_service(tmp_path)
    parent = str(uuid4())
    engine_run = {
        "selected_engine": engine,
        "selected_representation": selected_representation,
        "selected_attempt_id": "attempt-selected",
        "attempt_refs": [
            {
                "attempt_id": "attempt-selected",
                "engine": engine,
                "representation": selected_representation,
                "status": "succeeded",
                "failure_code": None,
            }
        ],
    }

    with pytest.raises(EngineRolloutArtifactError, match=match):
        artifacts.publish_parent(
            project_id="project",
            parent_run_id=parent,
            engine=engine,
            representation=representation,
            engine_run=engine_run,
            selected=_artifacts("selected"),
        )
    with pytest.raises(FileNotFoundError):
        artifacts.public_store.resolve_run(parent)
