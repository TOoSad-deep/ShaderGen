from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_current_pipeline_keeps_layered_compiler_and_program_execution_ir() -> None:
    assert (ROOT / "src/shaderforge/layered_spec/compiler.py").is_file()
    assert (ROOT / "src/shaderforge/program_spec/models.py").is_file()
    assert (ROOT / "src/agent/app/services/layerplan_glsl_direct.py").is_file()
    assert (ROOT / "src/agent/app/graphs/layerplan_glsl_direct.py").is_file()
    assert (ROOT / "src/agent/app/graphs/layerplan_glsl_direct_studio.py").is_file()
    assert (ROOT / "src/agent/app/states/layerplan_glsl_direct.py").is_file()
    assert (ROOT / "langgraph.json").is_file()


def test_legacy_runtime_trees_are_removed() -> None:
    for relative in (
        "src/agent/app/graphs/png_to_shader_min_graph.py",
        "src/agent/app/nodes/png_to_shader_min/runtime.py",
        "src/agent/app/nodes/layerplan_glsl_shadow/authors.py",
        "src/shaderforge/dsl/document.py",
        "src/shaderforge/perception/min_perceive.py",
        "src/nodelab/runner.py",
    ):
        assert not (ROOT / relative).exists()


def test_backend_has_no_legacy_engine_policy_or_shadow_service() -> None:
    assert not (ROOT / "backend/app/core/engine_policy.py").exists()
    assert not (ROOT / "backend/app/services/production_shadow.py").exists()
    source = (ROOT / "backend/app/services/engine_rollout.py").read_text()
    assert "shader_graph_v1" not in source
    assert "fallback" not in source


def test_current_frontend_and_prompt_have_no_removed_product_entrypoints() -> None:
    app_source = (ROOT / "frontend/src/App.tsx").read_text()
    prompt = (
        ROOT / "src/agent/app/prompts/layerplan_visual_analysis_v1.yaml"
    ).read_text()
    assert 'href="/lab"' not in app_source
    assert "场景 JSON" not in app_source
    assert "shadow 实验" not in prompt
