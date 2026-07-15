from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from hashlib import sha256
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from agent.app.contracts.llm import LLMResponse, TokenUsage
from agent.app.lab.models import ArtifactDescriptor, NodeLabError
from agent.app.nodes.integrations.node_lab.model import (
    SUPPORTED_NODE_IDS,
    ModelRoleExecutor,
)
from shaderforge.contracts import BudgetPolicy
from tests.unit_tests.png_to_shader_v1_samples import (
    GOLDEN_GLSL,
    analysis_payload,
    author_payload,
    json_text,
    review_payload,
)

FAILURE_FIXTURES = {
    "visual_analysis": "visual-analysis-parser-rejected-v1",
    "author_initial": "author-initial-parser-rejected-v1",
    "author_compile_repair": "author-compile-repair-parser-rejected-v1",
    "visual_critic": "visual-critic-parser-rejected-v1",
    "author_visual_refine": "author-visual-refine-parser-rejected-v1",
}


@dataclass(frozen=True)
class Request:
    lab_run_id: str
    node_id: str
    execution_mode: str = "fixture"
    fixture_id: str | None = None
    mock_response_artifact_id: str | None = None
    preview_only: bool = False
    allow_model_call: bool = False
    effect_mode: str = "lab_commit"


class FakeArtifacts:
    def __init__(self) -> None:
        self._sequence = 0
        self._items: dict[tuple[str, str], tuple[ArtifactDescriptor, bytes]] = {}

    def upload_artifact(
        self,
        *,
        lab_run_id: str,
        kind: str,
        content_type: str,
        data: bytes,
    ) -> ArtifactDescriptor:
        self._sequence += 1
        artifact_id = f"artifact-{self._sequence:04d}"
        descriptor = ArtifactDescriptor(
            artifact_id=artifact_id,
            lab_run_id=lab_run_id,
            kind=kind,
            content_type=content_type,
            sha256=sha256(data).hexdigest(),
            size_bytes=len(data),
            created_at="2026-07-14T00:00:00Z",
        )
        self._items[(lab_run_id, artifact_id)] = (descriptor, data)
        return descriptor

    def read_artifact(
        self,
        lab_run_id: str,
        artifact_id: str,
    ) -> tuple[ArtifactDescriptor, bytes]:
        try:
            return self._items[(lab_run_id, artifact_id)]
        except KeyError as exc:
            raise NodeLabError(
                "artifact_not_found",
                "测试 Artifact 不存在。",
                stage="test_artifact_read",
                lab_run_id=lab_run_id,
            ) from exc


class FakeGateway:
    def __init__(self, text: str, *, reasoning: str | None = None) -> None:
        self.text = text
        self.reasoning = reasoning
        self.calls: list[tuple[Any, Any]] = []

    async def ainvoke(self, messages, options) -> LLMResponse:
        self.calls.append((messages, options))
        return LLMResponse(
            message=AIMessage(content=self.text),
            text=self.text,
            reasoning_content=self.reasoning,
            model_ref="fake:model-actual",
            requested_model_ref=options.model_ref,
            model_identity_source="response_metadata",
            latency_ms=7,
            usage=TokenUsage(input_tokens=10, output_tokens=20, total_tokens=30),
        )


def descriptor(node_id: str) -> SimpleNamespace:
    return SimpleNamespace(node_id=node_id)


def budget(*, model_calls: int = 4) -> dict[str, Any]:
    return {
        "budget_policy": asdict(
            BudgetPolicy(
                max_visual_refinements=2,
                max_compile_repairs=1,
                max_model_calls=model_calls,
                max_wall_time_seconds=300,
            )
        ),
        "started_at": 0.0,
        "model_call_count": 0,
        "model_calls": (),
        "events": (),
    }


def analysis_state() -> dict[str, Any]:
    return {
        **budget(),
        "image": b"reference-image",
        "content_type": "image/png",
        "target_measurements": {
            "image_sha256": sha256(b"reference-image").hexdigest(),
            "foreground_bbox_uv": [0.15, 0.15, 0.85, 0.85],
        },
        "instruction": "复刻粉色凝胶球",
    }


def candidate_state() -> dict[str, Any]:
    rendered = b"rendered-image"
    glsl_hash = sha256(GOLDEN_GLSL.encode()).hexdigest()
    render_hash = sha256(rendered).hexdigest()
    candidate = {
        "candidate_id": "candidate-best",
        "parent_candidate_id": None,
        "glsl_sha256": glsl_hash,
        "render_sha256": render_hash,
        "prompt_version": "shader_author_initial_v1_1",
        "model_ref": "fixture:model",
        "iteration": 0,
    }
    return {
        **budget(),
        "image": b"reference-image",
        "content_type": "image/png",
        "rendered_image": rendered,
        "rendered_content_type": "image/png",
        "glsl": GOLDEN_GLSL,
        "target_measurements": {"foreground_bbox_uv": [0.15, 0.15, 0.85, 0.85]},
        "visual_analysis": analysis_payload(),
        "score_breakdown": {"total_loss": 0.2, "roi_losses": {"highlight": 0.3}},
        "residual_summary": {"highlight": "too long"},
        "current_candidate": candidate,
        "current_best_candidate": candidate,
        "render_evidence_binding": {
            "candidate_id": "candidate-best",
            "glsl_sha256": glsl_hash,
            "image_sha256": render_hash,
        },
        "visual_review": review_payload(),
    }


def state_for(node_id: str) -> dict[str, Any]:
    if node_id == "visual_analysis":
        return analysis_state()
    if node_id == "author_initial":
        return {**analysis_state(), "visual_analysis": analysis_payload()}
    if node_id == "author_compile_repair":
        return {
            **budget(),
            "previous_author_result": author_payload(),
            "glsl": GOLDEN_GLSL,
            "static_validation": {"valid": False, "violations": ["missing semicolon"]},
            "compile_result": {"success": False, "fragment_log": "missing semicolon"},
            "repair_budget": {"remaining": 1},
        }
    return candidate_state()


@pytest.mark.anyio
@pytest.mark.parametrize("node_id", sorted(SUPPORTED_NODE_IDS))
async def test_versioned_fixtures_cover_all_five_model_nodes(node_id: str) -> None:
    artifacts = FakeArtifacts()
    executor = ModelRoleExecutor(artifacts, clock=lambda: 0.0)

    result = await executor.execute(
        descriptor(node_id),
        Request(lab_run_id="lab-fixture", node_id=node_id),
        state_for(node_id),
    )

    assert result.outcome == "success"
    assert result.usage["model_call_count"] == 1
    assert len(result.provenance["fixture_sha256"]) == 64
    assert len(result.provenance["fixture_file_sha256"]) == 64
    assert result.artifacts
    assert all(item.lab_run_id == "lab-fixture" for item in result.artifacts)
    serialized = json.dumps(result.to_dict(), ensure_ascii=False)
    assert "raw_output" not in serialized
    assert "reasoning_content" not in serialized
    if node_id.startswith("author_"):
        assert "author_artifact_id" in result.output_patch
        assert "candidate_provenance_artifact_id" in result.output_patch
        assert "glsl_artifact_id" in result.output_patch
        assert "glsl" not in result.output_patch
        assert (
            result.output_patch["glsl_sha256"]
            == sha256(GOLDEN_GLSL.encode()).hexdigest()
        )
    elif node_id == "visual_analysis":
        assert "visual_analysis_artifact_id" in result.output_patch
    else:
        assert "visual_review_artifact_id" in result.output_patch


@pytest.mark.anyio
@pytest.mark.parametrize("node_id", sorted(SUPPORTED_NODE_IDS))
async def test_parser_rejection_fixtures_are_offline_and_do_not_leak_raw_output(
    node_id: str,
) -> None:
    artifacts = FakeArtifacts()
    external_gateway = FakeGateway(json_text(analysis_payload()))
    executor = ModelRoleExecutor(
        artifacts,
        gateway=external_gateway,
        real_model_enabled=True,
        clock=lambda: 0.0,
    )

    result = await executor.execute(
        descriptor(node_id),
        Request(
            lab_run_id="lab-fixture-rejected",
            node_id=node_id,
            fixture_id=FAILURE_FIXTURES[node_id],
        ),
        state_for(node_id),
    )

    assert result.outcome == "stopped"
    assert result.usage["semantic_call_count"] == 1
    assert result.usage["json_repair_call_count"] == 1
    assert result.diagnostics["parse_statuses"] == ["invalid", "invalid"]
    assert result.diagnostics["error_codes"]
    assert external_gateway.calls == []
    serialized = json.dumps(result.to_dict(), ensure_ascii=False)
    assert "PRIVATE_RAW_MARKER" not in serialized
    assert "must-not-leak" not in serialized
    assert "still-invalid" not in serialized


@pytest.mark.anyio
async def test_mock_reads_same_run_artifact_and_uses_real_parser() -> None:
    artifacts = FakeArtifacts()
    invalid = analysis_payload()
    invalid["PRIVATE_RAW_MARKER"] = "must-not-leak"
    mock = artifacts.upload_artifact(
        lab_run_id="lab-mock",
        kind="model-mock-response",
        content_type="application/json",
        data=json_text(invalid).encode(),
    )
    executor = ModelRoleExecutor(artifacts, clock=lambda: 0.0)

    result = await executor.execute(
        descriptor("visual_analysis"),
        Request(
            lab_run_id="lab-mock",
            node_id="visual_analysis",
            execution_mode="mock",
            mock_response_artifact_id=mock.artifact_id,
        ),
        {**analysis_state(), **budget(model_calls=1)},
    )

    assert result.outcome == "stopped"
    assert result.diagnostics["error_codes"] == ["unknown_field"]
    serialized = json.dumps(result.to_dict(), ensure_ascii=False)
    assert "PRIVATE_RAW_MARKER" not in serialized
    assert "must-not-leak" not in serialized
    assert result.provenance["mock_response_artifact_id"] == mock.artifact_id
    assert len(result.provenance["mock_response_sha256"]) == 64


@pytest.mark.anyio
async def test_effect_preview_uses_production_messages_without_calling_gateway() -> (
    None
):
    artifacts = FakeArtifacts()
    context = {
        "schema_version": 1,
        "current_phase": "analysis",
        "current_iteration": 0,
        "confirmed_constraints": ["PRIVATE_MEMORY_TEXT"],
        "confirmed_decisions": [],
        "approved_strategies": [],
        "current_review": None,
        "recent_reviews": [],
        "selected_memory_ids": ["memory-safe-id"],
        "estimated_tokens": 23,
        "dropped_memory_count": 0,
    }
    context_artifact = artifacts.upload_artifact(
        lab_run_id="lab-preview",
        kind="context-pack",
        content_type="application/json",
        data=json.dumps(context, ensure_ascii=False).encode(),
    )
    gateway = FakeGateway(json_text(analysis_payload()), reasoning="PRIVATE_REASONING")
    executor = ModelRoleExecutor(
        artifacts,
        gateway=gateway,
        real_model_enabled=True,
        clock=lambda: 0.0,
    )
    state = {
        **analysis_state(),
        "context_pack_artifact_id": context_artifact.artifact_id,
    }

    result = await executor.execute(
        descriptor("visual_analysis"),
        Request(
            lab_run_id="lab-preview",
            node_id="visual_analysis",
            execution_mode="real",
            allow_model_call=True,
            effect_mode="preview",
        ),
        state,
    )

    assert gateway.calls == []
    preview = result.output_patch["preview"]
    assert preview["gateway_call_count"] == 0
    assert preview["prompt"]["version"] == "visual_analysis_v1_2"
    assert "VisualAnalysisAgent" in preview["prompt"]["system_prompt"]
    assert preview["context"] == {
        "present": True,
        "selected_memory_ids": ["memory-safe-id"],
        "estimated_tokens": 23,
    }
    serialized = json.dumps(result.to_dict(), ensure_ascii=False)
    assert "data:image" not in serialized
    assert "PRIVATE_MEMORY_TEXT" not in serialized
    assert "PRIVATE_REASONING" not in serialized


@pytest.mark.anyio
async def test_real_mode_requires_server_and_request_switch_before_gateway() -> None:
    artifacts = FakeArtifacts()
    gateway = FakeGateway(
        json_text(analysis_payload()),
        reasoning="SUPPLIER_REASONING_MUST_NOT_LEAK",
    )
    state = analysis_state()

    disabled = ModelRoleExecutor(
        artifacts,
        gateway=gateway,
        real_model_enabled=False,
        clock=lambda: 0.0,
    )
    with pytest.raises(NodeLabError) as server_error:
        await disabled.execute(
            descriptor("visual_analysis"),
            Request(
                lab_run_id="lab-real",
                node_id="visual_analysis",
                execution_mode="real",
                allow_model_call=True,
            ),
            state,
        )
    assert server_error.value.code == "real_model_not_allowed"
    assert gateway.calls == []

    enabled = ModelRoleExecutor(
        artifacts,
        gateway=gateway,
        real_model_enabled=True,
        clock=lambda: 0.0,
    )
    with pytest.raises(NodeLabError) as request_error:
        await enabled.execute(
            descriptor("visual_analysis"),
            Request(
                lab_run_id="lab-real",
                node_id="visual_analysis",
                execution_mode="real",
                allow_model_call=False,
            ),
            state,
        )
    assert request_error.value.code == "real_model_not_allowed"
    assert gateway.calls == []

    result = await enabled.execute(
        descriptor("visual_analysis"),
        Request(
            lab_run_id="lab-real",
            node_id="visual_analysis",
            execution_mode="real",
            allow_model_call=True,
        ),
        state,
    )
    assert len(gateway.calls) == 1
    assert result.usage == {
        "model_call_count": 1,
        "semantic_call_count": 1,
        "json_repair_call_count": 0,
        "input_tokens": 10,
        "output_tokens": 20,
        "total_tokens": 30,
        "model_latency_ms": 7,
    }
    serialized = json.dumps(result.to_dict(), ensure_ascii=False)
    assert "SUPPLIER_REASONING_MUST_NOT_LEAK" not in serialized


def test_supported_node_ids_are_exactly_the_three_role_graph_nodes() -> None:
    assert SUPPORTED_NODE_IDS == {
        "visual_analysis",
        "author_initial",
        "author_compile_repair",
        "visual_critic",
        "author_visual_refine",
    }
