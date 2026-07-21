from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import BaseMessage

from agent.app.contracts.llm import LLMCallOptions, LLMResponse
from agent.app.nodes.png_to_shader_v1.integrations.node_lab import (
    MODEL_EXECUTION_MODES,
    DeterministicNodeExecutor,
    ModelRoleExecutor,
    build_png_to_shader_v1_registry,
)
from agent.app.services.node_lab import NodeLabApplication, create_node_lab_application
from backend.app.api.routes.node_lab import router as node_lab_router
from backend.app.services.node_lab import NodeLabBackendService
from nodelab.models import (
    LabRunCreateRequest,
    NodeLabError,
    StepExecutionRequest,
    StepExecutionResponse,
)
from tests.fixtures.png_to_shader_v1_samples import GOLDEN_GLSL

ROOT = Path(__file__).resolve().parents[2]
REFERENCE_IMAGE = ROOT / "benchmarks/png_to_shader_v1/images/pink_gel.png"
NODE_DESCRIPTORS = build_png_to_shader_v1_registry().describe_nodes()
DETERMINISTIC_NODE_IDS = frozenset(
    descriptor.node_id for descriptor in NODE_DESCRIPTORS if not descriptor.requires_model
)
MODEL_NODE_IDS = frozenset(
    descriptor.node_id for descriptor in NODE_DESCRIPTORS if descriptor.requires_model
)


class NeverGateway:
    """记录意外真实模型调用；正常路径绝不能触达这里。"""

    def __init__(self) -> None:
        self.calls: list[tuple[Sequence[BaseMessage], LLMCallOptions]] = []

    async def ainvoke(
        self,
        messages: Sequence[BaseMessage],
        options: LLMCallOptions,
    ) -> LLMResponse:
        self.calls.append((messages, options))
        raise AssertionError("缺少真实模型开关时不应调用 Gateway。")


class MemoryProbe:
    """阶段 C 只允许注入只读 Memory；门禁路径甚至不应读取。"""

    def __init__(self) -> None:
        self.read_calls: list[tuple[str, int]] = []

    async def list_project_memories(
        self,
        project_id: str,
        *,
        limit: int,
    ) -> list[object]:
        self.read_calls.append((project_id, limit))
        return []


def _artifact_ids(value: object) -> Iterator[str]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key.endswith("_artifact_id") and isinstance(item, str) and item:
                yield item
            yield from _artifact_ids(item)
    elif isinstance(value, list):
        for item in value:
            yield from _artifact_ids(item)


def _assert_no_bytes(value: object) -> None:
    assert not isinstance(value, bytes)
    if isinstance(value, Mapping):
        for item in value.values():
            _assert_no_bytes(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_bytes(item)


async def _initialize_and_measure(
    application: NodeLabApplication,
    *,
    project_id: str,
) -> tuple[str, StepExecutionResponse, StepExecutionResponse]:
    run = application.create_run(LabRunCreateRequest(project_id=project_id))
    source = application.upload_artifact(
        lab_run_id=run.lab_run_id,
        kind="source_png",
        content_type="image/png",
        data=REFERENCE_IMAGE.read_bytes(),
    )
    initialized = await application.execute_step(
        StepExecutionRequest(
            lab_run_id=run.lab_run_id,
            node_id="initialize_run",
            execution_mode="deterministic",
            inputs={
                "source_artifact_id": source.artifact_id,
                "quality_preset": "balanced",
                "instruction": "复刻粉色凝胶球",
            },
        )
    )
    measured = await application.execute_step(
        StepExecutionRequest(
            lab_run_id=run.lab_run_id,
            node_id="measure_target",
            execution_mode="deterministic",
            base_step_id=initialized.step_id,
        )
    )
    return run.lab_run_id, initialized, measured


def test_application_registers_exact_production_node_executors(tmp_path: Path) -> None:
    application = create_node_lab_application(root=tmp_path / "node-lab")
    registered = getattr(application, "_node_executors")
    expected = {
        *((node_id, "deterministic") for node_id in DETERMINISTIC_NODE_IDS),
        *(
            (node_id, mode)
            for node_id in MODEL_NODE_IDS
            for mode in MODEL_EXECUTION_MODES
        ),
    }

    assert set(registered) == expected
    assert len(DETERMINISTIC_NODE_IDS) == 15
    assert len(MODEL_NODE_IDS) == 5
    for node_id in DETERMINISTIC_NODE_IDS:
        assert isinstance(
            registered[(node_id, "deterministic")],
            DeterministicNodeExecutor,
        )
    for node_id in MODEL_NODE_IDS:
        for mode in MODEL_EXECUTION_MODES:
            assert isinstance(registered[(node_id, mode)], ModelRoleExecutor)


@pytest.mark.anyio
async def test_stage_c_fixture_flow_uses_real_application_api_and_private_artifacts(
    tmp_path: Path,
) -> None:
    gateway = NeverGateway()
    application = create_node_lab_application(
        root=tmp_path / "node-lab",
        model_gateway=gateway,
        real_model_enabled=False,
    )
    lab_run_id, initialized, measured = await _initialize_and_measure(
        application,
        project_id="project-stage-c-flow",
    )
    analyzed = await application.execute_step(
        StepExecutionRequest(
            lab_run_id=lab_run_id,
            node_id="visual_analysis",
            execution_mode="fixture",
            base_step_id=measured.step_id,
        )
    )
    persisted = await application.execute_step(
        StepExecutionRequest(
            lab_run_id=lab_run_id,
            node_id="persist_visual_analysis",
            execution_mode="deterministic",
            base_step_id=analyzed.step_id,
        )
    )
    authored = await application.execute_step(
        StepExecutionRequest(
            lab_run_id=lab_run_id,
            node_id="author_initial",
            execution_mode="fixture",
            base_step_id=persisted.step_id,
        )
    )
    materialized = await application.execute_step(
        StepExecutionRequest(
            lab_run_id=lab_run_id,
            node_id="materialize_candidate",
            execution_mode="deterministic",
            base_step_id=authored.step_id,
        )
    )
    responses = (
        initialized,
        measured,
        analyzed,
        persisted,
        authored,
        materialized,
    )

    assert all(response.execution_status == "completed" for response in responses)
    assert [response.base_step_id for response in responses] == [
        None,
        initialized.step_id,
        measured.step_id,
        analyzed.step_id,
        persisted.step_id,
        authored.step_id,
    ]
    assert measured.output["target_measurements"]["image_width"] > 0
    assert analyzed.output["visual_analysis_artifact_id"]
    assert persisted.output["visual_analysis_artifact_id"]
    assert authored.output["author_artifact_id"]
    assert authored.output["candidate_provenance_artifact_id"]
    assert authored.output["glsl_artifact_id"]
    assert materialized.output["current_candidate_id"] == "candidate-0001"
    assert gateway.calls == []

    for response in responses:
        response_value = response.to_dict()
        _assert_no_bytes(response_value)
        for artifact in response.artifacts:
            assert artifact.lab_run_id == lab_run_id
        for artifact_id in _artifact_ids(response.output):
            descriptor, _data = application.read_artifact(lab_run_id, artifact_id)
            assert descriptor.lab_run_id == lab_run_id

    _descriptor, glsl_bytes = application.read_artifact(
        lab_run_id,
        authored.output["glsl_artifact_id"],
    )
    assert glsl_bytes.decode("utf-8") == GOLDEN_GLSL
    serialized = json.dumps(
        [response.to_dict() for response in responses],
        ensure_ascii=False,
        sort_keys=True,
    )
    assert GOLDEN_GLSL not in serialized
    assert "raw_output" not in serialized
    assert "reasoning" not in serialized.lower()


@pytest.mark.anyio
async def test_measurement_seed_application_branch_is_independent_and_ai_off(
    tmp_path: Path,
) -> None:
    gateway = NeverGateway()
    application = create_node_lab_application(
        root=tmp_path / "node-lab",
        model_gateway=gateway,
        real_model_enabled=False,
    )
    lab_run_id, _initialized, measured = await _initialize_and_measure(
        application,
        project_id="project-measurement-seed",
    )
    prepared = await application.execute_step(
        StepExecutionRequest(
            lab_run_id=lab_run_id,
            node_id="prepare_measurement_seed",
            execution_mode="deterministic",
            base_step_id=measured.step_id,
        )
    )
    materialized = await application.execute_step(
        StepExecutionRequest(
            lab_run_id=lab_run_id,
            node_id="materialize_candidate",
            execution_mode="deterministic",
            base_step_id=prepared.step_id,
        )
    )

    assert prepared.output["candidate_origin"] == "deterministic"
    assert prepared.output["candidate_generator_version"] == (
        "measurement_affine_seed_v1"
    )
    assert prepared.usage["model_call_count"] == 0
    record = materialized.output["candidate_record"]
    assert record["parent_candidate_id"] is None
    assert record["origin"] == "deterministic"
    assert record["generator_version"] == "measurement_affine_seed_v1"
    assert gateway.calls == []
    _assert_no_bytes(prepared.to_dict())
    _assert_no_bytes(materialized.to_dict())


@pytest.mark.anyio
async def test_real_mode_without_server_switch_is_rejected_before_gateway_call(
    tmp_path: Path,
) -> None:
    gateway = NeverGateway()
    application = create_node_lab_application(
        root=tmp_path / "node-lab",
        model_gateway=gateway,
        real_model_enabled=False,
    )
    run = application.create_run(LabRunCreateRequest(project_id="project-real-gate"))

    with pytest.raises(NodeLabError, match="真实模型") as raised:
        await application.execute_step(
            StepExecutionRequest(
                lab_run_id=run.lab_run_id,
                node_id="visual_analysis",
                execution_mode="real",
                allow_model_call=True,
            )
        )

    assert raised.value.code == "real_model_not_allowed"
    assert gateway.calls == []
    assert application.list_step_ids(run.lab_run_id) == ()


@pytest.mark.anyio
async def test_project_commit_promotion_is_rejected_without_step_or_memory_access(
    tmp_path: Path,
) -> None:
    lab_root = tmp_path / "node-lab"
    memory = MemoryProbe()
    application = create_node_lab_application(root=lab_root, memory_reader=memory)
    run = application.create_run(
        LabRunCreateRequest(project_id="project-effect-policy")
    )
    files_before = {
        path.relative_to(lab_root): path.read_bytes()
        for path in lab_root.rglob("*")
        if path.is_file()
    }

    with pytest.raises(NodeLabError, match="禁止写入") as raised:
        await application.execute_step(
            StepExecutionRequest(
                lab_run_id=run.lab_run_id,
                node_id="promote_validated_strategy",
                execution_mode="deterministic",
                effect_mode="project_commit",
            )
        )

    assert raised.value.code == "effect_not_allowed"
    assert application.list_step_ids(run.lab_run_id) == ()

    with pytest.raises(NodeLabError) as unrelated_node_error:
        await application.execute_step(
            StepExecutionRequest(
                lab_run_id=run.lab_run_id,
                node_id="initialize_run",
                execution_mode="deterministic",
                effect_mode="project_commit",
                inputs={"source_artifact_id": "never-read"},
            )
        )
    assert unrelated_node_error.value.code == "effect_not_allowed"
    assert application.list_step_ids(run.lab_run_id) == ()
    assert memory.read_calls == []
    assert {
        path.relative_to(lab_root): path.read_bytes()
        for path in lab_root.rglob("*")
        if path.is_file()
    } == files_before


def test_http_stage_c_fixture_flow_lists_dag_and_private_artifacts(
    tmp_path: Path,
) -> None:
    application = create_node_lab_application(root=tmp_path / "node-lab")
    app = FastAPI()
    app.state.node_lab_service = NodeLabBackendService(application)
    app.include_router(node_lab_router)

    with TestClient(app) as client:
        lab_run_id = client.post(
            "/api/lab/v1/runs",
            json={"project_id": "project-stage-c-http"},
        ).json()["lab_run_id"]
        source = client.post(
            f"/api/lab/v1/runs/{lab_run_id}/artifacts",
            data={"kind": "source_png"},
            files={
                "file": (
                    "pink-gel.png",
                    REFERENCE_IMAGE.read_bytes(),
                    "image/png",
                )
            },
        ).json()

        requests = (
            {
                "node_id": "initialize_run",
                "execution_mode": "deterministic",
                "inputs": {
                    "source_artifact_id": source["artifact_id"],
                    "quality_preset": "balanced",
                },
            },
            {"node_id": "measure_target", "execution_mode": "deterministic"},
            {"node_id": "visual_analysis", "execution_mode": "fixture"},
            {
                "node_id": "persist_visual_analysis",
                "execution_mode": "deterministic",
            },
            {"node_id": "author_initial", "execution_mode": "fixture"},
            {
                "node_id": "materialize_candidate",
                "execution_mode": "deterministic",
            },
        )
        responses: list[dict[str, Any]] = []
        base_step_id: str | None = None
        for payload in requests:
            body = dict(payload)
            if base_step_id is not None:
                body["base_step_id"] = base_step_id
            response = client.post(
                f"/api/lab/v1/runs/{lab_run_id}/steps",
                json=body,
            )
            assert response.status_code == 200, response.text
            value = response.json()
            assert value["execution_status"] == "completed"
            assert value["base_step_id"] == base_step_id
            responses.append(value)
            base_step_id = value["step_id"]

        dag = client.get(f"/api/lab/v1/runs/{lab_run_id}/steps").json()
        artifacts = client.get(f"/api/lab/v1/runs/{lab_run_id}/artifacts").json()

    assert dag["step_ids"] == [item["step_id"] for item in responses]
    assert [item["base_step_id"] for item in dag["steps"]] == [
        None,
        *dag["step_ids"][:-1],
    ]
    assert [item["node_id"] for item in dag["steps"]] == [
        request["node_id"] for request in requests
    ]
    assert responses[-1]["output"]["current_candidate_id"] == "candidate-0001"
    assert artifacts["artifacts"]
    assert all(item["lab_run_id"] == lab_run_id for item in artifacts["artifacts"])
