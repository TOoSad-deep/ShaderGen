from __future__ import annotations

import pytest

from agent.app.graphs.png_to_shader_v1_graph import png_to_shader_v1_graph
from agent.app.lab.capabilities import build_deterministic_capability_registry
from agent.app.lab.models import NodeLabError
from agent.app.nodes.integrations.node_lab import build_png_to_shader_v1_registry

MODEL_NODE_FIXTURES = {
    "visual_analysis": "visual-analysis-success-v1",
    "author_initial": "author-initial-success-v1",
    "author_compile_repair": "author-compile-repair-success-v1",
    "visual_critic": "visual-critic-success-v1",
    "author_visual_refine": "author-visual-refine-success-v1",
}
MODEL_NODE_FAILURE_FIXTURES = {
    "visual_analysis": "visual-analysis-parser-rejected-v1",
    "author_initial": "author-initial-parser-rejected-v1",
    "author_compile_repair": "author-compile-repair-parser-rejected-v1",
    "visual_critic": "visual-critic-parser-rejected-v1",
    "author_visual_refine": "author-visual-refine-parser-rejected-v1",
}


def test_registry_exactly_matches_production_graph_nodes() -> None:
    registry = build_png_to_shader_v1_registry()
    registered = {descriptor.node_id for descriptor in registry.describe_nodes()}
    graph_nodes = set(png_to_shader_v1_graph.get_graph().nodes) - {
        "__start__",
        "__end__",
    }

    assert len(registered) == 20
    assert registered == graph_nodes


def test_every_descriptor_has_machine_readable_test_and_benchmark_metadata() -> None:
    descriptors = build_png_to_shader_v1_registry().describe_nodes()

    for descriptor in descriptors:
        assert descriptor.input_schema["type"] == "object"
        assert descriptor.output_schema["type"] == "object"
        assert descriptor.test_profiles
        assert descriptor.benchmark_profiles[:2] == ["micro", "node"]
        assert descriptor.benchmark_metrics[:2] == [
            "schema_pass",
            "invariant_pass",
        ]
        assert descriptor.source_ref.startswith("src/agent/app/")
        assert descriptor.input_examples
        assert descriptor.input_schema["examples"] == [
            example.inputs for example in descriptor.input_examples
        ]

    render = next(item for item in descriptors if item.node_id == "render_and_evaluate")
    assert render.requires_browser is True
    assert render.cold_start_sensitive is True
    assert "renderer_cold" in render.benchmark_profiles
    assert "renderer_warm" not in render.benchmark_profiles

    author = next(item for item in descriptors if item.node_id == "author_initial")
    assert author.requires_model is True
    assert author.implementation_status == "available"
    assert author.execution_modes == ["fixture", "mock", "real"]
    assert author.default_fixture_ids == [
        "author-initial-success-v1",
        "author-initial-parser-rejected-v1",
    ]

    measure = next(item for item in descriptors if item.node_id == "measure_target")
    assert measure.implementation_status == "available"
    assert measure.execution_modes == ["deterministic"]


def test_all_nodes_declare_an_ai_off_mode_and_real_is_model_only() -> None:
    descriptors = build_png_to_shader_v1_registry().describe_nodes()
    model_nodes = {item.node_id for item in descriptors if item.requires_model}
    deterministic_nodes = {
        item.node_id for item in descriptors if not item.requires_model
    }

    assert model_nodes == set(MODEL_NODE_FIXTURES)
    assert len(deterministic_nodes) == 15
    for descriptor in descriptors:
        assert {"deterministic", "fixture", "mock"} & set(descriptor.execution_modes)
        assert ("real" in descriptor.execution_modes) is descriptor.requires_model
        if descriptor.requires_model:
            assert descriptor.execution_modes == ["fixture", "mock", "real"]
            assert descriptor.default_fixture_ids == [
                MODEL_NODE_FIXTURES[descriptor.node_id],
                MODEL_NODE_FAILURE_FIXTURES[descriptor.node_id],
            ]
        else:
            assert "deterministic" in descriptor.execution_modes


def test_every_node_has_machine_readable_success_and_parser_rejection_examples() -> (
    None
):
    descriptors = build_png_to_shader_v1_registry().describe_nodes()
    node_ids = {item.node_id for item in descriptors}

    for descriptor in descriptors:
        success = descriptor.input_examples[0]
        assert success.example_id.endswith("-success-v1")
        assert success.expected_outcome == "success"
        assert success.execution_mode in descriptor.execution_modes
        assert (
            success.base_step_node_id is None or success.base_step_node_id in node_ids
        )
        assert set(success.artifact_inputs).issubset(success.inputs)
        if descriptor.requires_model:
            rejected = descriptor.input_examples[1]
            assert rejected.expected_outcome == "stopped"
            assert (
                rejected.fixture_id == MODEL_NODE_FAILURE_FIXTURES[descriptor.node_id]
            )
            assert rejected.execution_mode == "fixture"


def test_all_production_graph_nodes_have_available_executors() -> None:
    descriptors = build_png_to_shader_v1_registry().describe_nodes()
    statuses = {item.node_id: item.implementation_status for item in descriptors}

    assert len(statuses) == 20
    assert set(statuses.values()) == {"available"}


def test_node_schemas_use_artifact_ids_and_only_require_true_prerequisites() -> None:
    registry = build_png_to_shader_v1_registry()

    initialize = registry.get("initialize_run")
    assert initialize.input_schema["required"] == ["source_artifact_id"]
    assert initialize.prerequisites == ["source_artifact_id"]
    assert initialize.input_schema["properties"]["source_artifact_id"][
        "pattern"
    ].startswith("^")
    assert "project_id" not in initialize.input_schema["required"]

    analyst = registry.get("visual_analysis")
    assert analyst.input_schema["required"] == [
        "reference_artifact_id",
        "target_measurements",
    ]
    assert "instruction" not in analyst.input_schema["required"]

    render = registry.get("render_and_evaluate")
    assert render.input_schema["required"] == [
        "candidate_record",
        "shader_artifact_id",
        "reference_artifact_id",
        "target_measurements",
    ]
    assert render.output_schema["required"] == ["render_status", "phase"]
    assert {"image", "glsl", "reference_ref"}.isdisjoint(
        render.input_schema["properties"]
    )

    seed = registry.get("prepare_measurement_seed")
    assert seed.input_schema["required"] == [
        "reference_artifact_id",
        "target_measurements",
    ]
    assert seed.output_schema["required"] == [
        "phase",
        "measurement_seed_attempted",
        "author_artifact_id",
        "author_summary",
        "glsl_artifact_id",
        "glsl_sha256",
        "glsl_chars",
        "candidate_provenance_artifact_id",
        "author_model",
        "candidate_origin",
        "candidate_generator_version",
        "events",
    ]
    assert seed.input_examples[0].base_step_node_id == "select_current_best"

    finalize = registry.get("finalize")
    assert finalize.input_schema["required"] == ["target_measurements"]
    assert finalize.output_schema["required"] == [
        "final_result",
        "final_manifest_artifact_id",
        "phase",
        "stop_reason",
        "events",
    ]
    assert "current_best_record" not in finalize.input_schema["required"]

    for descriptor in registry.describe_nodes():
        required = descriptor.input_schema["required"]
        properties = descriptor.input_schema["properties"]
        assert descriptor.prerequisites == required
        assert set(required).issubset(properties)


def test_unknown_node_is_rejected_without_reflection() -> None:
    registry = build_png_to_shader_v1_registry()

    with pytest.raises(NodeLabError) as caught:
        registry.get("agent.app.nodes.__dict__")

    assert caught.value.code == "node_not_found"
    assert caught.value.node_id == "agent.app.nodes.__dict__"


def test_capability_descriptors_expose_actual_input_limits() -> None:
    registry = build_deterministic_capability_registry()
    descriptors = registry.describe_capabilities()

    assert len(descriptors) == 8
    render = registry.get("render-shader")
    assert render.input_schema["additionalProperties"] is False
    properties = render.input_schema["properties"]
    assert properties["width"]["maximum"] == 1024
    assert properties["height"]["maximum"] == 1024
    assert {"renderer_cold", "renderer_warm"}.issubset(render.benchmark_profiles)
    validate = registry.get("validate-shader")
    assert validate.input_schema["properties"]["max_shader_chars"]["maximum"] == 30_000
