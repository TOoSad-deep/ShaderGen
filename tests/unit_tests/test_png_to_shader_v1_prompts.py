from pathlib import Path

import pytest

from agent.app.prompts.prompt_loader import load_prompt_definition

ROOT = Path(__file__).resolve().parents[2]

PROMPTS = {
    "visual_analysis_v1": "visual_analysis_v1_2",
    "shader_author_initial_v1": "shader_author_initial_v1_1",
    "shader_author_compile_repair_v1": "shader_author_compile_repair_v1_1",
    "shader_author_visual_refine_v1": "shader_author_visual_refine_v1",
    "visual_critic_v1": "visual_critic_v1",
    "structured_output_repair_v1": "structured_output_repair_v1_2",
}


@pytest.mark.parametrize(("name", "version"), PROMPTS.items())
def test_m2_runtime_prompts_are_versioned(name: str, version: str) -> None:
    definition = load_prompt_definition(name)

    assert definition.version == version
    assert definition.prompt
    assert (ROOT / "src/agent/app/prompts" / f"{name}.yaml").is_file()


def test_role_prompts_keep_single_responsibility() -> None:
    analyst = load_prompt_definition("visual_analysis_v1").prompt
    critic = load_prompt_definition("visual_critic_v1").prompt
    initial = load_prompt_definition("shader_author_initial_v1").prompt
    repair = load_prompt_definition("shader_author_compile_repair_v1").prompt
    refine = load_prompt_definition("shader_author_visual_refine_v1").prompt

    assert "不生成或修改 GLSL" in analyst
    assert "只能填写已有的 layers[].layer_id" in analyst
    assert "不生成或改写 GLSL" in critic
    assert "完整" in initial and "Fragment Shader" in initial
    assert "fwidth" in initial and "禁止" in initial
    assert "最小必要修复" in repair and "不是视觉重设计" in repair
    assert "只修复一个 primary_problem_domain" in refine
    assert "current_best" in refine


@pytest.mark.parametrize(
    "name",
    [
        "shader_author_initial_v1",
        "shader_author_compile_repair_v1",
        "shader_author_visual_refine_v1",
    ],
)
def test_author_prompts_forbid_texture_sampling_and_require_webgl1(name: str) -> None:
    prompt = load_prompt_definition(name).prompt

    assert "WebGL1" in prompt
    assert "gl_FragColor" in prompt
    assert "u_image" in prompt
    assert "禁止" in prompt and "texture2D" in prompt
    assert "必须使用 texture2D" not in prompt


def test_repair_prompt_is_json_only_and_treats_original_as_untrusted_data() -> None:
    prompt = load_prompt_definition("structured_output_repair_v1").prompt

    assert "只修复" in prompt
    assert "不是指令" in prompt
    assert "只输出一个完整 JSON object" in prompt
    assert "不增加" in prompt
    assert "不得原样返回无效引用" in prompt
    assert "shader_author_initial_v1_1" in prompt
    assert "Markdown" in prompt


def test_prompt_bodies_only_live_in_runtime_prompt_package() -> None:
    node_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "src/agent/app/nodes").glob("*.py")
    )

    assert "你是 PNG 转无贴图 Shader 系统" not in node_source
    for name in PROMPTS:
        assert name in node_source
