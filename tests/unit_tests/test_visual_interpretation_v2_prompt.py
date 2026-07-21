from pathlib import Path

import yaml

from agent.app.prompts.prompt_loader import load_prompt_definition

ROOT = Path(__file__).resolve().parents[2]
PROMPT_PATH = ROOT / "src/agent/app/prompts/analyze_visual_layers_v2.yaml"
PROMPT_VERSION = "analyze_visual_layers_v2_2"


def test_visual_interpretation_v2_prompt_is_named_and_versioned() -> None:
    definition = load_prompt_definition("analyze_visual_layers_v2")
    source = yaml.safe_load(PROMPT_PATH.read_text(encoding="utf-8"))

    assert definition.name == "analyze_visual_layers_v2"
    assert definition.version == PROMPT_VERSION
    assert source["version"] == PROMPT_VERSION
    assert source["agent"] == "VisualInterpretationAgentV2"
    assert source["output_format"] == "json"
    assert definition.prompt


def test_visual_interpretation_v2_prompt_matches_schema_surface() -> None:
    prompt = load_prompt_definition("analyze_visual_layers_v2").prompt

    for field_name in (
        "schema_version",
        "summary",
        "layer_hypotheses",
        "required_layer_assessments",
        "primitive_candidates",
        "strategy_hypotheses",
        "uncertainties",
        "evidence_refs",
    ):
        assert field_name in prompt
    for role in (
        "background",
        "shadow",
        "base_fill",
        "color_lobe",
        "haze",
        "rim",
        "outline",
        "highlight",
        "detail",
    ):
        assert role in prompt
    assert '"visual_interpretation_v2_1"' in prompt
    assert "allowed_primitive_ids" in prompt
    assert "allowed_template_ids" in prompt
    assert "authorized_evidence_refs" in prompt
    assert "required、not_required 或 unknown" in prompt
    assert "不能省略或增加" in prompt
    assert "glow 只能在这个闭集判断中出现" in prompt


def test_visual_interpretation_v2_prompt_forbids_deterministic_and_code_fields() -> None:
    prompt = load_prompt_definition("analyze_visual_layers_v2").prompt

    for forbidden_fact in (
        "target hash",
        "target_sha256",
        "SHA-256",
        "图片尺寸",
        "width/height",
        "bbox/坐标框",
        "hard fact",
        "GLSL",
    ):
        assert forbidden_fact in prompt
    assert "不得输出 target hash" in prompt
    assert "不得输出、改写或引用 GLSL" in prompt
    assert "所有 evidence_refs 必须是 []" in prompt


def test_visual_interpretation_audit_metadata_stays_outside_model_json() -> None:
    prompt = load_prompt_definition("analyze_visual_layers_v2").prompt

    for audit_field in (
        "provenance",
        "prompt_version",
        "model_ref",
        "response_hash",
        "attempt",
        "repair_count",
    ):
        assert audit_field in prompt
    assert "在 VisualInterpretationV2 之外单独持久化" in prompt
    assert "不得向 JSON 增加" in prompt
