from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

import pytest
from pydantic import BaseModel

from agent.app.nodes.png_to_shader_v1.integrations.node_lab import (
    build_png_to_shader_v1_registry,
)
from agent.app.nodes.png_to_shader_v1.integrations.node_lab.fixtures import (
    build_png_to_shader_v1_fixture_registry,
)
from agent.app.services.node_lab import (
    create_lab_run,
    create_node_lab_application,
    describe_nodes,
    execute_step,
)
from nodelab.benchmark import source_environment
from nodelab.capabilities import CapabilityRegistry
from nodelab.integration import (
    ContextNodeExecutor,
    DirectNodeExecutor,
    NodeExecutorBinding,
    RunnableNodeExecutor,
)
from nodelab.models import (
    CapabilityDescriptor,
    CapabilityExecutionRequest,
    LabRunCreateRequest,
    NodeDescriptor,
    NodeExecutionResult,
    NodeInputExample,
    NodeLabError,
    StepExecutionRequest,
)
from nodelab.provider import NodeProviderBuilder
from nodelab.runner import NodeLabApplication
from nodelab.store import NodeLabStore

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


class EchoCapabilityExecutor:
    async def execute_capability(
        self,
        request: CapabilityExecutionRequest,
        descriptor: CapabilityDescriptor,
        runtime: object | None = None,
    ) -> NodeExecutionResult:
        del descriptor, runtime
        return NodeExecutionResult(output_patch={"echo": request.inputs["value"]})


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


@pytest.mark.anyio
async def test_custom_provider_does_not_inherit_v1_capabilities_fixtures_or_suites(
    tmp_path: Path,
) -> None:
    plain = create_node_lab_application(
        root=tmp_path / "plain",
        node_provider=EchoNodeProvider(),
    )
    assert plain.pipeline_id == "custom_pipeline"
    assert plain.describe_capabilities() == ()
    assert plain.describe_suites() == ()

    capability = CapabilityDescriptor(
        pipeline_id="custom_pipeline",
        capability_id="echo-capability",
        summary="返回输入值。",
        benchmark_profiles=["micro"],
        benchmark_metrics=["schema_pass"],
        source_ref="tests.echo_capability",
        input_schema={
            "type": "object",
            "properties": {"value": {}},
            "required": ["value"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"echo": {}},
            "required": ["echo"],
            "additionalProperties": False,
        },
    )
    configured = create_node_lab_application(
        root=tmp_path / "configured",
        node_provider=EchoNodeProvider(),
        capability_registry=CapabilityRegistry([capability]),
        capability_executor_factory=lambda _host: EchoCapabilityExecutor(),
    )
    run = configured.create_run(LabRunCreateRequest())
    response = await configured.execute_capability(
        CapabilityExecutionRequest(
            lab_run_id=run.lab_run_id,
            capability_id="echo-capability",
            inputs={"value": "hello"},
        )
    )
    assert response.pipeline_id == "custom_pipeline"
    assert response.output == {"echo": "hello"}


class _BuilderInput(BaseModel):
    value: str


class _BuilderOutput(BaseModel):
    echoed: str


@pytest.mark.anyio
async def test_provider_builder_connects_typed_callable_without_node_changes(
    tmp_path: Path,
) -> None:
    def existing_node(state: Mapping[str, object]) -> dict[str, object]:
        return {"echoed": state["value"]}

    provider = (
        NodeProviderBuilder("builder_pipeline")
        .add_node(
            existing_node,
            node_id="echo",
            input_model=_BuilderInput,
            output_model=_BuilderOutput,
            example_inputs={"value": "hello"},
        )
        .build()
    )
    app = NodeLabApplication(
        store=NodeLabStore(tmp_path),
        node_provider=provider,
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

    assert response.output == {"echoed": "hello"}
    with pytest.raises(NodeLabError) as caught:
        await app.execute_step(
            StepExecutionRequest(
                lab_run_id=run.lab_run_id,
                node_id="echo",
                execution_mode="deterministic",
                inputs={"value": 42},
            )
        )
    assert caught.value.code == "input_contract_invalid"
    assert caught.value.stage == "input_validation"


@dataclass(frozen=True)
class _CommandLike:
    update: dict[str, object]
    goto: str


@pytest.mark.anyio
async def test_context_and_command_like_node_use_standard_adapter(
    tmp_path: Path,
) -> None:
    def runtime_node(
        state: Mapping[str, object],
        context: object,
    ) -> _CommandLike:
        return _CommandLike(
            update={"result": f"{state['value']}:{context}"},
            goto="next-node",
        )

    executor = ContextNodeExecutor(
        runtime_node,
        context_factory=lambda _descriptor, _request, _state: "runtime",
    )
    delegated_source = tmp_path / "delegated_runtime.py"
    delegated_source.write_text("def delegated_runtime():\n    return 'v1'\n")
    provider = (
        NodeProviderBuilder("context_pipeline")
        .add_node(
            executor=executor,
            node_id="runtime-node",
            source_paths=[delegated_source],
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": True,
            },
            output_schema={
                "type": "object",
                "properties": {"result": {"type": "string"}},
                "required": ["result"],
                "additionalProperties": False,
            },
            example_inputs={"value": "hello"},
        )
        .build()
    )
    provider_source_paths = {Path(path) for path in provider.source_paths()}
    assert Path(__file__).resolve() in provider_source_paths
    assert delegated_source.resolve() in provider_source_paths
    _, source_fingerprint_before, _ = source_environment(
        workspace_root=tmp_path,
        extra_source_paths=provider_source_paths,
        dependency_names=(),
    )
    delegated_source.write_text("def delegated_runtime():\n    return 'v2'\n")
    _, source_fingerprint_after, _ = source_environment(
        workspace_root=tmp_path,
        extra_source_paths=provider_source_paths,
        dependency_names=(),
    )
    assert source_fingerprint_before != source_fingerprint_after
    app = NodeLabApplication(
        store=NodeLabStore(tmp_path),
        node_provider=provider,
        id_factory=_id_factory(["lab-1", "step-1"]),
        now=lambda: FIXED_NOW,
        timer=_timer([1.0, 1.01]),
    )
    run = app.create_run(LabRunCreateRequest())
    response = await app.execute_step(
        StepExecutionRequest(
            lab_run_id=run.lab_run_id,
            node_id="runtime-node",
            inputs={"value": "hello"},
            execution_mode="deterministic",
        )
    )

    assert response.output == {"result": "hello:runtime"}
    assert response.next_action == "next-node"


@pytest.mark.anyio
async def test_runnable_adapter_and_custom_state_reducer_preserve_node_source(
    tmp_path: Path,
) -> None:
    class Runnable:
        async def ainvoke(
            self,
            state: dict[str, object],
            config: Mapping[str, object] | None,
        ) -> dict[str, object]:
            assert config == {"mode": "lab"}
            return {"events": [str(state["value"])]}

    class EventReducer:
        def apply(
            self,
            before: Mapping[str, object],
            update: Mapping[str, object],
        ) -> dict[str, object]:
            return {
                **before,
                **update,
                "events": [*list(before.get("events", [])), *list(update["events"])],
            }

    executor = RunnableNodeExecutor(
        Runnable(),
        config_factory=lambda _request: {"mode": "lab"},
    )
    provider = (
        NodeProviderBuilder("runnable_pipeline")
        .add_node(
            executor=executor,
            node_id="append-event",
            input_schema={
                "type": "object",
                "properties": {
                    "value": {"type": "string"},
                    "events": {"type": "array"},
                },
                "required": ["value", "events"],
                "additionalProperties": True,
            },
            output_schema={
                "type": "object",
                "properties": {"events": {"type": "array"}},
                "required": ["events"],
                "additionalProperties": False,
            },
            example_inputs={"value": "child", "events": []},
        )
        .build()
    )
    app = NodeLabApplication(
        store=NodeLabStore(tmp_path),
        node_provider=provider,
        state_reducer=EventReducer(),
        id_factory=_id_factory(["lab-1", "step-1"]),
        now=lambda: FIXED_NOW,
        timer=_timer([1.0, 1.01]),
    )
    run = app.create_run(LabRunCreateRequest(initial_state={"events": ["root"]}))
    response = await app.execute_step(
        StepExecutionRequest(
            lab_run_id=run.lab_run_id,
            node_id="append-event",
            inputs={"value": "child"},
            execution_mode="deterministic",
        )
    )

    assert response.output == {"events": ["child"]}
    assert response.state_diff.changed["events"]["after"] == ["root", "child"]
    assert Path(__file__).resolve() in {
        Path(path) for path in app.benchmark_source_paths()
    }


@pytest.mark.anyio
async def test_reducer_failures_commit_safe_step_evidence(tmp_path: Path) -> None:
    def existing_node(state: Mapping[str, object]) -> dict[str, object]:
        return {"echoed": state["value"]}

    class RaisingReducer:
        def apply(
            self,
            before: Mapping[str, object],
            update: Mapping[str, object],
        ) -> dict[str, object]:
            del before, update
            raise RuntimeError("secret reducer detail")

    class InvalidReducer:
        def apply(
            self,
            before: Mapping[str, object],
            update: Mapping[str, object],
        ) -> dict[str, object]:
            del before, update
            return {"invalid": object()}

    provider = (
        NodeProviderBuilder("reducer_failure_pipeline")
        .add_node(
            existing_node,
            node_id="echo",
            input_model=_BuilderInput,
            output_model=_BuilderOutput,
            example_inputs={"value": "hello"},
        )
        .build()
    )
    for suffix, reducer in (
        ("raises", RaisingReducer()),
        ("invalid", InvalidReducer()),
    ):
        store = NodeLabStore(tmp_path / suffix)
        app = NodeLabApplication(
            store=store,
            node_provider=provider,
            state_reducer=reducer,
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

        assert response.execution_status == "failed"
        assert response.diagnostics["error"]["stage"] == "state_reduction"
        assert response.diagnostics["error"]["error_type"] in {
            "RuntimeError",
            "ValueError",
        }
        assert "secret reducer detail" not in str(response.to_dict())
        assert app.list_step_ids(run.lab_run_id) == ("step-1",)
        assert app.get_step(run.lab_run_id, "step-1") == response
        assert store.load_state_after(run.lab_run_id, "step-1") == {"value": "hello"}


@pytest.mark.anyio
async def test_execution_fingerprint_includes_reduced_state(tmp_path: Path) -> None:
    def existing_node(state: Mapping[str, object]) -> dict[str, object]:
        return {"echoed": state["value"]}

    class AlternatingReducer:
        def __init__(self) -> None:
            self.calls = 0

        def apply(
            self,
            before: Mapping[str, object],
            update: Mapping[str, object],
        ) -> dict[str, object]:
            self.calls += 1
            return {**before, **update, "reducer_revision": self.calls}

    provider = (
        NodeProviderBuilder("reducer_fingerprint_pipeline")
        .add_node(
            existing_node,
            node_id="echo",
            input_model=_BuilderInput,
            output_model=_BuilderOutput,
            example_inputs={"value": "hello"},
        )
        .build()
    )
    app = NodeLabApplication(
        store=NodeLabStore(tmp_path),
        node_provider=provider,
        state_reducer=AlternatingReducer(),
        id_factory=_id_factory(["lab-1", "step-1", "step-2"]),
        now=lambda: FIXED_NOW,
        timer=_timer([1.0, 1.01, 2.0, 2.01]),
    )
    run = app.create_run(LabRunCreateRequest())
    request = StepExecutionRequest(
        lab_run_id=run.lab_run_id,
        node_id="echo",
        execution_mode="deterministic",
        inputs={"value": "hello"},
    )

    first = await app.execute_step(request)
    second = await app.execute_step(request)

    assert first.output == second.output == {"echoed": "hello"}
    assert first.state_diff.added["reducer_revision"] == 1
    assert second.state_diff.added["reducer_revision"] == 2
    assert first.execution_fingerprint != second.execution_fingerprint


@pytest.mark.anyio
async def test_runnable_adapter_omits_optional_config_when_not_configured(
    tmp_path: Path,
) -> None:
    class Runnable:
        def invoke(self, state: dict[str, object]) -> dict[str, object]:
            return {"echoed": state["value"]}

    provider = (
        NodeProviderBuilder("one_arg_runnable_pipeline")
        .add_node(
            executor=RunnableNodeExecutor(Runnable()),
            node_id="one-arg-runnable",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": True,
            },
            output_schema={
                "type": "object",
                "properties": {"echoed": {"type": "string"}},
                "required": ["echoed"],
                "additionalProperties": False,
            },
            example_inputs={"value": "hello"},
        )
        .build()
    )
    app = NodeLabApplication(
        store=NodeLabStore(tmp_path),
        node_provider=provider,
        id_factory=_id_factory(["lab-1", "step-1"]),
        now=lambda: FIXED_NOW,
        timer=_timer([1.0, 1.01]),
    )
    run = app.create_run(LabRunCreateRequest())
    response = await app.execute_step(
        StepExecutionRequest(
            lab_run_id=run.lab_run_id,
            node_id="one-arg-runnable",
            execution_mode="deterministic",
            inputs={"value": "hello"},
        )
    )

    assert response.output == {"echoed": "hello"}


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
        fixtures=build_png_to_shader_v1_fixture_registry(),
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

    restarted = NodeLabApplication(
        store=NodeLabStore(tmp_path),
        pipeline_id=run.pipeline_id,
    )
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
        fixtures=build_png_to_shader_v1_fixture_registry(),
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
