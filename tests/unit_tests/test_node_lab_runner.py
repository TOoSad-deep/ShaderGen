from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

import pytest

from agent.app.lab.integration import DirectNodeExecutor, NodeExecutorBinding
from agent.app.lab.models import (
    LabRunCreateRequest,
    NodeDescriptor,
    NodeExecutionResult,
    NodeInputExample,
    NodeLabError,
    StepExecutionRequest,
)
from agent.app.lab.runner import NodeLabApplication
from agent.app.lab.store import NodeLabStore
from agent.app.nodes.png_to_shader_v1.integrations.node_lab import (
    build_png_to_shader_v1_registry,
)
from agent.app.services.node_lab import (
    create_lab_run,
    describe_nodes,
    execute_step,
)

FIXED_NOW = datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc)


class ExplodingExecutor:
    def resolve_inputs(
        self,
        descriptor: NodeDescriptor,
        request: StepExecutionRequest,
    ) -> dict[str, object]:
        del descriptor, request
        return {}

    async def execute(
        self,
        descriptor: NodeDescriptor,
        request: StepExecutionRequest,
        state: Mapping[str, object],
    ) -> NodeExecutionResult:
        del descriptor, request, state
        raise RuntimeError("secret executor detail")


class StaticExecutor:
    def resolve_inputs(
        self,
        descriptor: NodeDescriptor,
        request: StepExecutionRequest,
    ) -> dict[str, object]:
        del descriptor, request
        return {
            "resolved_by": "node-executor",
            "render_status": "success",
            "budget_policy": {
                "max_model_calls": 6,
                "max_compile_repairs": 1,
            },
        }

    async def execute(
        self,
        descriptor: NodeDescriptor,
        request: StepExecutionRequest,
        state: Mapping[str, object],
    ) -> NodeExecutionResult:
        del descriptor, request, state
        return NodeExecutionResult(output_patch={"next_action": "exact"})


class EchoNodeProvider:
    """证明新 pipeline/Node 只需实现通用 provider 协议。"""

    pipeline_id = "custom_pipeline"

    def describe_nodes(self) -> tuple[NodeDescriptor, ...]:
        return (
            NodeDescriptor(
                pipeline_id=self.pipeline_id,
                node_id="echo",
                category="test",
                summary="原样返回输入值。",
                prerequisites=["value"],
                implementation_status="available",
                execution_modes=["deterministic"],
                test_profiles=["unit"],
                benchmark_profiles=["node"],
                benchmark_metrics=["schema_pass"],
                source_ref="tests.echo_node",
                input_schema={
                    "type": "object",
                    "properties": {"value": {}},
                    "required": ["value"],
                    "additionalProperties": True,
                },
                output_schema={
                    "type": "object",
                    "properties": {"echo": {}},
                    "required": ["echo"],
                    "additionalProperties": True,
                },
                input_examples=[
                    NodeInputExample(
                        example_id="echo-success-v1",
                        summary="传入 JSON-safe value。",
                        execution_mode="deterministic",
                        inputs={"value": "hello"},
                    )
                ],
            ),
        )

    def bind(self, host: object) -> tuple[NodeExecutorBinding, ...]:
        del host
        executor = DirectNodeExecutor(lambda state: {"echo": state["value"]})
        return (
            NodeExecutorBinding(
                node_id="echo",
                execution_mode="deterministic",
                executor=executor,
            ),
        )

    def source_paths(self) -> tuple[str, ...]:
        return ("tests/unit_tests/test_node_lab_runner.py",)


def _id_factory(values: list[str]):
    iterator = iter(values)
    return lambda: next(iterator)


def _timer(values: list[float]):
    iterator = iter(values)
    return lambda: next(iterator)


@pytest.mark.anyio
async def test_custom_provider_connects_new_node_without_lab_specific_code(
    tmp_path: Path,
) -> None:
    app = NodeLabApplication(
        store=NodeLabStore(tmp_path),
        node_provider=EchoNodeProvider(),
        id_factory=_id_factory(["lab-1", "step-1"]),
        now=lambda: FIXED_NOW,
        timer=_timer([1.0, 1.01]),
    )
    run = app.create_run(LabRunCreateRequest())
    response = await app.execute_step(
        StepExecutionRequest(
            lab_run_id=run.lab_run_id,
            node_id="echo",
            execution_mode="deterministic",
            inputs={"value": "hello"},
        )
    )

    assert run.pipeline_id == "custom_pipeline"
    assert response.pipeline_id == "custom_pipeline"
    assert response.output == {"echo": "hello"}
    assert response.provenance["execution_source"] == "production_node"

    foreign = NodeLabApplication(
        store=NodeLabStore(tmp_path),
        registry=build_png_to_shader_v1_registry(),
    )
    with pytest.raises(NodeLabError) as caught:
        foreign.get_run(run.lab_run_id)
    assert caught.value.code == "pipeline_scope_mismatch"


def test_create_run_and_artifact_are_isolated_by_opaque_ids(tmp_path: Path) -> None:
    store = NodeLabStore(tmp_path)
    app = NodeLabApplication(
        store=store,
        registry=build_png_to_shader_v1_registry(),
        id_factory=_id_factory(["lab-1", "artifact-1", "lab-2"]),
        now=lambda: FIXED_NOW,
    )
    first = app.create_run(LabRunCreateRequest(initial_state={"seed": 1}))
    artifact = app.upload_artifact(
        lab_run_id=first.lab_run_id,
        kind="reference_png",
        content_type="image/png",
        data=b"png",
    )
    second = app.create_run(LabRunCreateRequest())

    descriptor, data = app.read_artifact(first.lab_run_id, artifact.artifact_id)
    assert descriptor.artifact_id == "artifact-1"
    assert data == b"png"
    assert "path" not in descriptor.to_dict()

    with pytest.raises(NodeLabError) as caught:
        app.read_artifact(second.lab_run_id, artifact.artifact_id)
    assert caught.value.code == "artifact_not_found"


@pytest.mark.anyio
async def test_fixture_step_persists_immutable_branches_and_survives_restart(
    tmp_path: Path,
) -> None:
    store = NodeLabStore(tmp_path)
    app = NodeLabApplication(
        store=store,
        registry=build_png_to_shader_v1_registry(),
        id_factory=_id_factory(["lab-1", "step-1", "step-2", "step-3"]),
        now=lambda: FIXED_NOW,
        timer=_timer([1.0, 1.01, 2.0, 2.02, 3.0, 3.03]),
    )
    run = app.create_run(LabRunCreateRequest(initial_state={"seed": "root"}))
    first = await app.execute_step(
        StepExecutionRequest(
            lab_run_id=run.lab_run_id,
            node_id="decide_after_render",
            execution_mode="fixture",
        )
    )
    root_branch = await app.execute_step(
        StepExecutionRequest(
            lab_run_id=run.lab_run_id,
            node_id="decide_after_render",
            execution_mode="fixture",
            inputs={"branch": "root"},
        )
    )
    child_branch = await app.execute_step(
        StepExecutionRequest(
            lab_run_id=run.lab_run_id,
            base_step_id=first.step_id,
            node_id="decide_after_render",
            execution_mode="fixture",
            inputs={"branch": "child"},
        )
    )

    assert first.output == {"next_action": "select"}
    assert first.usage == {"model_call_count": 0, "browser_launch_count": 0}
    assert first.state_diff.added == {"next_action": "select"}
    assert root_branch.base_step_id is None
    assert child_branch.base_step_id == first.step_id
    assert store.load_state_after(run.lab_run_id, first.step_id)["seed"] == "root"
    assert "branch" not in store.load_state_after(run.lab_run_id, first.step_id)
    assert store.load_state_after(run.lab_run_id, root_branch.step_id)["branch"] == (
        "root"
    )
    assert store.load_state_after(run.lab_run_id, child_branch.step_id)["branch"] == (
        "child"
    )

    restarted = NodeLabApplication(store=NodeLabStore(tmp_path))
    assert restarted.list_step_ids(run.lab_run_id) == (
        "step-1",
        "step-2",
        "step-3",
    )
    assert restarted.get_step(run.lab_run_id, first.step_id) == first


@pytest.mark.anyio
async def test_unconfigured_real_mode_fails_before_any_model_call(
    tmp_path: Path,
) -> None:
    app = NodeLabApplication(
        store=NodeLabStore(tmp_path),
        registry=build_png_to_shader_v1_registry(),
        id_factory=_id_factory(["lab-1"]),
        now=lambda: FIXED_NOW,
    )
    run = app.create_run(LabRunCreateRequest())

    with pytest.raises(NodeLabError) as caught:
        await app.execute_step(
            StepExecutionRequest(
                lab_run_id=run.lab_run_id,
                node_id="visual_analysis",
                execution_mode="real",
            )
        )

    assert caught.value.code == "executor_not_configured"
    assert app.list_step_ids(run.lab_run_id) == ()


@pytest.mark.anyio
async def test_exact_node_executor_takes_precedence_over_mode_fallback(
    tmp_path: Path,
) -> None:
    app = NodeLabApplication(
        store=NodeLabStore(tmp_path),
        registry=build_png_to_shader_v1_registry(),
        id_factory=_id_factory(["lab-1", "step-1"]),
        now=lambda: FIXED_NOW,
        timer=_timer([1.0, 1.01]),
    )
    app.register_node_executor(
        node_id="decide_after_render",
        execution_mode="fixture",
        executor=StaticExecutor(),
    )
    run = app.create_run(LabRunCreateRequest())

    response = await app.execute_step(
        StepExecutionRequest(
            lab_run_id=run.lab_run_id,
            node_id="decide_after_render",
            execution_mode="fixture",
        )
    )

    assert response.output == {"next_action": "exact"}
    assert response.state_diff.added == {"next_action": "exact"}
    assert response.input_summary["field_names"] == [
        "budget_policy",
        "render_status",
        "resolved_by",
    ]


def test_exact_node_executor_cannot_be_overwritten(tmp_path: Path) -> None:
    app = NodeLabApplication(
        store=NodeLabStore(tmp_path),
        registry=build_png_to_shader_v1_registry(),
    )
    app.register_node_executor(
        node_id="decide_after_render",
        execution_mode="fixture",
        executor=StaticExecutor(),
    )

    with pytest.raises(ValueError, match="禁止覆盖"):
        app.register_node_executor(
            node_id="decide_after_render",
            execution_mode="fixture",
            executor=StaticExecutor(),
        )


@pytest.mark.anyio
async def test_executor_failure_is_persisted_without_secret_text(
    tmp_path: Path,
) -> None:
    app = NodeLabApplication(
        store=NodeLabStore(tmp_path),
        registry=build_png_to_shader_v1_registry(),
        executors={"deterministic": ExplodingExecutor()},
        id_factory=_id_factory(["lab-1", "step-1"]),
        now=lambda: FIXED_NOW,
        timer=_timer([1.0, 1.01]),
    )
    run = app.create_run(LabRunCreateRequest())

    response = await app.execute_step(
        StepExecutionRequest(
            lab_run_id=run.lab_run_id,
            node_id="decide_after_render",
            execution_mode="deterministic",
            inputs={
                "render_status": "success",
                "budget_policy": {
                    "max_model_calls": 6,
                    "max_compile_repairs": 1,
                },
            },
        )
    )

    assert response.execution_status == "failed"
    assert response.outcome == "failed"
    assert response.diagnostics["error"]["code"] == "internal_invariant_failed"
    assert response.diagnostics["error"]["error_type"] == "RuntimeError"
    assert "secret executor detail" not in str(response.to_dict())
    assert app.get_step(run.lab_run_id, response.step_id) == response


@pytest.mark.anyio
async def test_public_service_facade_uses_injected_application(tmp_path: Path) -> None:
    app = NodeLabApplication(
        store=NodeLabStore(tmp_path),
        registry=build_png_to_shader_v1_registry(),
        id_factory=_id_factory(["lab-1", "step-1"]),
        now=lambda: FIXED_NOW,
        timer=_timer([1.0, 1.01]),
    )

    assert len(describe_nodes(application=app)) == 20
    run = create_lab_run(LabRunCreateRequest(), application=app)
    response = await execute_step(
        StepExecutionRequest(
            lab_run_id=run.lab_run_id,
            node_id="decide_after_render",
        ),
        application=app,
    )

    assert response.execution_status == "completed"
    assert response.diagnostics["fixture_id"] == "decide-after-render-success-v1"
