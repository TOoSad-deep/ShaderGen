from __future__ import annotations

import json
from copy import deepcopy

import pytest

from agent.app.contracts.png_to_shader_v1 import AuthorMode, ShaderAuthorResult
from agent.app.parsers.png_to_shader_v1 import (
    PngToShaderParseError,
    parse_shader_author_result,
    parse_visual_analysis,
    parse_visual_review,
    repair_shader_author_initial_bindings,
)
from shaderforge.contracts import ProblemDomain
from tests.unit_tests.png_to_shader_v1_samples import (
    GOLDEN_GLSL,
    analysis_payload,
    author_payload,
    json_text,
    review_payload,
)


def test_visual_analysis_accepts_plain_and_single_json_fence() -> None:
    text = json_text(analysis_payload())

    plain = parse_visual_analysis(text)
    fenced = parse_visual_analysis(f"```json\n{text}\n```")

    assert plain == fenced
    assert plain.subject.center_uv == (0.5, 0.5)


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("prefix {}", "invalid_json"),
        ("```\n{}\n```", "unexpected_wrapper"),
        ('{"a":1,"a":2}', "duplicate_key"),
        ('{"confidence":NaN}', "non_finite_number"),
        ("[]", "not_json_object"),
        ("```json\n{}\n```\n```json\n{}\n```", "unexpected_wrapper"),
    ],
)
def test_parser_rejects_noncanonical_json_envelopes(text: str, code: str) -> None:
    with pytest.raises(PngToShaderParseError) as caught:
        parse_visual_analysis(text)

    assert code in caught.value.error_codes


def test_parser_rejects_missing_unknown_and_wrong_version_fields() -> None:
    missing = analysis_payload()
    missing.pop("layers")
    with pytest.raises(PngToShaderParseError) as missing_error:
        parse_visual_analysis(json_text(missing))
    assert "missing_field" in missing_error.value.error_codes

    unknown = analysis_payload()
    unknown["secret_extra"] = True
    with pytest.raises(PngToShaderParseError) as unknown_error:
        parse_visual_analysis(json_text(unknown))
    assert "unknown_field" in unknown_error.value.error_codes

    wrong = analysis_payload()
    wrong["analysis_version"] = "visual_analysis_v2"
    with pytest.raises(PngToShaderParseError) as version_error:
        parse_visual_analysis(json_text(wrong))
    assert "invalid_literal" in version_error.value.error_codes


def test_analyst_and_critic_cannot_embed_complete_glsl() -> None:
    analysis = analysis_payload()
    analysis["risks"] = [GOLDEN_GLSL]
    with pytest.raises(PngToShaderParseError) as analyst_error:
        parse_visual_analysis(json_text(analysis))
    assert "role_violation" in analyst_error.value.error_codes

    analysis = analysis_payload()
    analysis["glsl"] = GOLDEN_GLSL
    with pytest.raises(PngToShaderParseError) as analyst_field_error:
        parse_visual_analysis(json_text(analysis))
    assert "role_violation" in analyst_field_error.value.error_codes

    review = review_payload()
    review["overall_assessment"] = GOLDEN_GLSL
    with pytest.raises(PngToShaderParseError) as critic_error:
        parse_visual_review(json_text(review), expected_candidate_id="candidate-best")
    assert "role_violation" in critic_error.value.error_codes

    review = review_payload()
    review["shader_code"] = GOLDEN_GLSL
    with pytest.raises(PngToShaderParseError) as critic_field_error:
        parse_visual_review(json_text(review), expected_candidate_id="candidate-best")
    assert "role_violation" in critic_field_error.value.error_codes


def test_numeric_and_bbox_fields_are_strict() -> None:
    for invalid_confidence in (True, "0.8"):
        payload = analysis_payload()
        payload["subject"]["confidence"] = invalid_confidence
        with pytest.raises(PngToShaderParseError):
            parse_visual_analysis(json_text(payload))

    payload = analysis_payload()
    payload["regions_of_interest"][0]["bbox_uv"] = [0.8, 0.2, 0.1, 0.7]
    with pytest.raises(PngToShaderParseError):
        parse_visual_analysis(json_text(payload))


def test_author_parser_extracts_complete_glsl_without_running_static_validator() -> (
    None
):
    payload = author_payload()
    payload["glsl"] = GOLDEN_GLSL.replace(
        "vec3 baseColor = vec3(1.0, 0.2, 0.5);",
        "vec3 baseColor = texture2D(u_image, v_uv).rgb;",
    )

    result = parse_shader_author_result(
        json_text(payload), expected_mode=AuthorMode.INITIAL
    )

    assert "texture2D" in result.glsl


def test_author_initial_mode_has_exact_invariants() -> None:
    result = parse_shader_author_result(
        json_text(author_payload()), expected_mode=AuthorMode.INITIAL
    )
    assert result.mode == AuthorMode.INITIAL

    invalid = author_payload()
    invalid["changed_parameters"] = ["radius"]
    with pytest.raises(PngToShaderParseError) as caught:
        parse_shader_author_result(json_text(invalid), expected_mode=AuthorMode.INITIAL)
    assert "binding_mismatch" in caught.value.error_codes


def test_author_initial_fixed_bindings_can_be_normalized_locally() -> None:
    payload = author_payload()
    payload["changed_parameters"] = ["radius"]
    payload["protected_regions"] = ["subject"]
    text = json_text(payload)
    with pytest.raises(PngToShaderParseError) as caught:
        parse_shader_author_result(text, expected_mode=AuthorMode.INITIAL)

    repaired = repair_shader_author_initial_bindings(text, caught.value)

    assert repaired is not None
    value, audit = repaired
    assert value.author_version == "shader_author_initial_v1_1"
    assert value.mode == AuthorMode.INITIAL
    assert value.base_candidate_id is None
    assert value.changed_problem_domain == "initial_build"
    assert value.changed_parameters == []
    assert value.protected_regions == []
    assert set(audit["repaired_paths"]) == {
        "$.changed_parameters",
        "$.protected_regions",
    }


def test_author_initial_local_normalization_rejects_schema_or_unknown_binding_errors() -> (
    None
):
    schema_invalid = author_payload()
    schema_invalid["changed_parameters"] = "radius"
    schema_text = json_text(schema_invalid)
    with pytest.raises(PngToShaderParseError) as schema_error:
        parse_shader_author_result(schema_text, expected_mode=AuthorMode.INITIAL)
    assert (
        repair_shader_author_initial_bindings(schema_text, schema_error.value) is None
    )

    text = json_text(author_payload())
    unknown_binding = PngToShaderParseError(
        [
            # 人工构造未知 binding 路径，确认 helper 不会扩大修复边界。
            type(schema_error.value.issues[0])(
                code="binding_mismatch",
                path="$.strategy_summary",
                message="unexpected binding",
            )
        ],
        raw_text=text,
    )
    assert repair_shader_author_initial_bindings(text, unknown_binding) is None

    wrong_identity = author_payload("compile_repair")
    wrong_identity["changed_parameters"] = ["radius"]
    wrong_identity_text = json_text(wrong_identity)
    with pytest.raises(PngToShaderParseError) as identity_error:
        parse_shader_author_result(
            wrong_identity_text,
            expected_mode=AuthorMode.INITIAL,
        )
    assert (
        repair_shader_author_initial_bindings(
            wrong_identity_text,
            identity_error.value,
        )
        is None
    )


def test_compile_repair_preserves_unrelated_visual_manifest() -> None:
    previous = ShaderAuthorResult.model_validate(author_payload())
    repaired = author_payload("compile_repair")
    repaired["glsl"] = GOLDEN_GLSL.replace("float mask", "float mask")

    result = parse_shader_author_result(
        json_text(repaired),
        expected_mode=AuthorMode.COMPILE_REPAIR,
        previous_result=previous,
        compile_diagnostics="ERROR: missing semicolon after mask",
    )

    assert result.changed_problem_domain == "runtime_compile"


def test_compile_repair_rejects_unrelated_parameter_change() -> None:
    previous = ShaderAuthorResult.model_validate(author_payload())
    repaired = author_payload("compile_repair")
    repaired["parameter_manifest"][0]["current_value"] = "0.4"

    with pytest.raises(PngToShaderParseError) as caught:
        parse_shader_author_result(
            json_text(repaired),
            expected_mode=AuthorMode.COMPILE_REPAIR,
            previous_result=previous,
            compile_diagnostics="ERROR: missing semicolon",
        )

    assert "compile_scope_violation" in caught.value.error_codes


def test_compile_repair_allows_diagnostic_backed_nonprotected_symbol_change() -> None:
    previous_payload = author_payload()
    previous = ShaderAuthorResult.model_validate(previous_payload)
    repaired = author_payload("compile_repair")
    repaired["parameter_manifest"][0]["current_value"] = "0.36"
    repaired["changed_parameters"] = ["radius"]

    result = parse_shader_author_result(
        json_text(repaired),
        expected_mode=AuthorMode.COMPILE_REPAIR,
        previous_result=previous,
        compile_diagnostics="ERROR: radius literal must be finite",
    )

    assert result.parameter_manifest[0].current_value == "0.36"


def test_visual_refine_binds_current_best_domain_and_protection() -> None:
    result = parse_shader_author_result(
        json_text(author_payload("visual_refine")),
        expected_mode=AuthorMode.VISUAL_REFINE,
        expected_base_candidate_id="candidate-best",
        expected_problem_domain=ProblemDomain.HIGHLIGHT,
        expected_protected_regions=("subject",),
    )
    assert result.base_candidate_id == "candidate-best"

    wrong = author_payload("visual_refine")
    wrong["base_candidate_id"] = "latest-candidate"
    with pytest.raises(PngToShaderParseError):
        parse_shader_author_result(
            json_text(wrong),
            expected_mode=AuthorMode.VISUAL_REFINE,
            expected_base_candidate_id="candidate-best",
            expected_problem_domain=ProblemDomain.HIGHLIGHT,
            expected_protected_regions=("subject",),
        )


def test_visual_review_binds_candidate_and_rejects_texture_advice() -> None:
    result = parse_visual_review(
        json_text(review_payload()), expected_candidate_id="candidate-best"
    )
    assert result.primary_problem_domain == ProblemDomain.HIGHLIGHT

    wrong = review_payload("other")
    with pytest.raises(PngToShaderParseError) as binding_error:
        parse_visual_review(json_text(wrong), expected_candidate_id="candidate-best")
    assert "binding_mismatch" in binding_error.value.error_codes

    texture = review_payload()
    texture["recommended_changes"][0]["direction"] = "改用 texture2D(u_image,v_uv)"
    with pytest.raises(PngToShaderParseError) as role_error:
        parse_visual_review(json_text(texture), expected_candidate_id="candidate-best")
    assert "role_violation" in role_error.value.error_codes


def test_parser_does_not_mutate_payload() -> None:
    payload = analysis_payload()
    original = deepcopy(payload)
    parse_visual_analysis(json.dumps(payload, ensure_ascii=False))
    assert payload == original
