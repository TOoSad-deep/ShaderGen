from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256
from typing import Any

import pytest

from shaderforge.program_spec import (
    REQUIRED_CHECKS,
    TRUSTED_VALIDATOR_VERSION,
    AttestationError,
    ExecutionReceipt,
    ProgramSpecParseError,
    ValidationAttestation,
    build_author_identity,
    build_layer_author_identity,
    build_layer_plan,
    build_program_spec,
    is_executable,
    issue_attestation,
    match_attestation,
    recompute_spec_sha256,
    sha256_hex_text,
)
from shaderforge.program_spec.receipt import _test_receipt_capabilities
from shaderforge.validation import (
    ProgramSpecSafetyLimits,
    validate_program_spec_safety,
)

VALID_SHADER = """precision mediump float;
varying vec2 v_uv;
uniform sampler2D u_image;
uniform vec2 u_resolution;
uniform float u_time;
uniform float u_strength;
uniform vec3 u_color;

void main() {
    vec2 p = v_uv - vec2(0.5);
    float mask = 1.0 - smoothstep(0.29, 0.31, length(p));
    gl_FragColor = vec4(mix(vec3(1.0), u_color * u_strength, mask), 1.0);
}
"""

REFERENCE_SHA256 = sha256_hex_text("reference-image-bytes")
INSTRUCTION_SHA256 = sha256_hex_text("user-instruction")
PARENT_SHA256 = sha256_hex_text("parent-spec")


def _model_output() -> dict:
    return {
        "schema_version": "shader_program_spec_v1",
        "fragment_source": VALID_SHADER,
        "uniform_schema": {
            "u_strength": {
                "type": "float",
                "minimum": 0.0,
                "maximum": 1.0,
                "default": 0.5,
            },
            "u_color": {
                "type": "vec3",
                "minimum": [0.0, 0.0, 0.0],
                "maximum": [1.0, 1.0, 1.0],
                "default": [1.0, 0.2, 0.4],
            },
        },
        "uniform_values": {"u_strength": 0.5, "u_color": [1.0, 0.2, 0.4]},
        "tunable_manifest": [
            {
                "path": "u_strength",
                "type": "float",
                "minimum": 0.0,
                "maximum": 1.0,
                "step": 0.05,
            }
        ],
        "canvas": {"width": 512, "height": 512},
        "renderer_contract_id": "webgl1_static_no_texture_v1",
    }


def _initial_identity():
    return build_author_identity(
        reference_sha256=REFERENCE_SHA256,
        instruction_sha256=INSTRUCTION_SHA256,
        model_ref="test-model-v1",
        prompt_version="initial_v1",
        role="initial",
        sampling_params={"temperature": 0.2, "seed": 7},
    )


def _spec():
    return build_program_spec(_model_output(), author_identity=_initial_identity())


# 显式 test-only 信任根：绝不使用进程级默认 issuer，模拟生产之外的签发。
_SIGNER, _ISSUER = _test_receipt_capabilities(
    key=b"test-only-receipt-key", issuer_id="test_only"
)
_RGB = bytes([128, 128, 128]) * 16
_PNG = b"\x89PNG-fake-bytes"


def _ok_receipt(spec, *, rgb: bytes = _RGB) -> ExecutionReceipt:
    return _SIGNER.issue_after_draw(
        source_sha256=spec.source_sha256,
        spec_sha256=spec.spec_sha256,
        rgb_bytes=rgb,
        png_bytes=_PNG,
        renderer_version="test_renderer_v1",
        runtime_metadata={
            "browser_version": "test-browser",
            "gl_version": "test-gl",
            "glsl_version": "test-glsl",
        },
    )


def _issue(spec) -> ValidationAttestation:
    return issue_attestation(
        spec, receipt=_ok_receipt(spec), static_ok=True, issuer=_ISSUER
    )


def test_canonical_hash_is_stable_across_key_order() -> None:
    first = _spec()
    shuffled = dict(reversed(list(_model_output().items())))
    second = build_program_spec(shuffled, author_identity=_initial_identity())

    assert first.spec_sha256 == second.spec_sha256
    assert first.source_sha256 == second.source_sha256
    assert first.binding_sha256 == second.binding_sha256
    assert len(first.spec_sha256) == 64


def test_spec_hash_excludes_only_attestation_and_binds_identity() -> None:
    spec = _spec()
    attested = spec.with_attestation(_issue(spec))
    assert attested.spec_sha256 == spec.spec_sha256

    # author_identity 参与 spec_sha256：同一语义输出、不同身份即不同哈希。
    other_identity = build_author_identity(
        reference_sha256=REFERENCE_SHA256,
        instruction_sha256=sha256_hex_text("another-instruction"),
        model_ref="test-model-v2",
        prompt_version="initial_v2",
        role="initial",
    )
    other = build_program_spec(_model_output(), author_identity=other_identity)
    assert other.spec_sha256 != spec.spec_sha256


def _identity_tamperings(spec) -> dict[str, Any]:
    identity = spec.author_identity
    return {
        "reference_sha256": replace(identity, reference_sha256="1" * 64),
        "plan_sha256": replace(identity, plan_sha256="2" * 64),
        "instruction_sha256": replace(identity, instruction_sha256="3" * 64),
        "model_ref": replace(identity, model_ref="forged-model"),
        "prompt_version": replace(identity, prompt_version="forged_v9"),
        "sampling_params": replace(identity, sampling_params={"temperature": 1}),
        "role": replace(identity, role="refine", parent_spec_sha256="4" * 64),
        "parent_spec_sha256": replace(identity, parent_spec_sha256="5" * 64),
    }


@pytest.mark.parametrize(
    "field",
    [
        "reference_sha256",
        "plan_sha256",
        "instruction_sha256",
        "model_ref",
        "prompt_version",
        "sampling_params",
        "role",
        "parent_spec_sha256",
    ],
)
def test_tampered_author_identity_fails_hash_and_attestation(field: str) -> None:
    spec = _spec()
    attestation = _issue(spec)
    attested = spec.with_attestation(attestation)
    assert is_executable(attested, issuer=_ISSUER)

    forged_identity = _identity_tamperings(spec)[field]
    tampered = replace(spec, author_identity=forged_identity)

    assert recompute_spec_sha256(tampered) != tampered.spec_sha256
    result = match_attestation(tampered, attestation, issuer=_ISSUER)
    assert not result.ok
    assert "spec_hash_mismatch" in result.reasons
    assert not is_executable(
        replace(tampered, validation_attestation=attestation),
        issuer=_ISSUER,
    )


def test_tampered_semantics_fail_hash_recompute() -> None:
    spec = _spec()
    tampered = replace(spec, fragment_source=spec.fragment_source + "\n")
    assert recompute_spec_sha256(tampered) != tampered.spec_sha256

    safety = validate_program_spec_safety(tampered)
    assert not safety.valid
    assert {item.code for item in safety.errors} >= {"source_hash_mismatch"}


def test_tampered_spec_fails_attestation_match() -> None:
    spec = _spec()
    attestation = _issue(spec)
    tampered = replace(
        spec, uniform_values={"u_strength": 0.9, "u_color": [1.0, 0.2, 0.4]}
    )

    result = match_attestation(tampered, attestation, issuer=_ISSUER)

    assert not result.ok
    assert "spec_hash_mismatch" in result.reasons


@pytest.mark.parametrize(
    "forbidden_key",
    ["validation_attestation", "spec_sha256", "source_sha256", "binding_sha256"],
)
def test_model_output_with_attestation_or_hash_fields_is_rejected(
    forbidden_key: str,
) -> None:
    payload = _model_output()
    payload[forbidden_key] = "0" * 64

    with pytest.raises(ProgramSpecParseError) as excinfo:
        build_program_spec(payload, author_identity=_initial_identity())

    assert excinfo.value.code in {
        "model_forbidden_attestation",
        "model_forbidden_hash_field",
    }


def test_model_output_with_author_identity_is_rejected() -> None:
    payload = _model_output()
    payload["author_identity"] = {"model_ref": "self-claimed"}

    with pytest.raises(ProgramSpecParseError, match="author_identity"):
        build_program_spec(payload, author_identity=_initial_identity())


def test_unknown_field_is_rejected() -> None:
    payload = _model_output()
    payload["extra_note"] = "free text escape hatch"

    with pytest.raises(ProgramSpecParseError) as excinfo:
        build_program_spec(payload, author_identity=_initial_identity())

    assert excinfo.value.code == "unknown_field"


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), True, "0.5"])
def test_non_finite_or_typed_uniform_values_are_rejected(bad_value) -> None:
    payload = _model_output()
    payload["uniform_values"]["u_strength"] = bad_value

    with pytest.raises(ProgramSpecParseError):
        build_program_spec(payload, author_identity=_initial_identity())


def test_uniform_value_outside_declared_domain_is_rejected() -> None:
    payload = _model_output()
    payload["uniform_values"]["u_strength"] = 1.5

    with pytest.raises(ProgramSpecParseError) as excinfo:
        build_program_spec(payload, author_identity=_initial_identity())

    assert excinfo.value.code == "out_of_domain"


def test_uniform_values_must_match_schema_one_to_one() -> None:
    payload = _model_output()
    del payload["uniform_values"]["u_color"]

    with pytest.raises(ProgramSpecParseError) as excinfo:
        build_program_spec(payload, author_identity=_initial_identity())

    assert excinfo.value.code == "uniform_values_mismatch"


def test_reserved_uniform_name_is_rejected() -> None:
    payload = _model_output()
    payload["uniform_schema"]["u_time"] = {
        "type": "float",
        "minimum": 0.0,
        "maximum": 1.0,
        "default": 0.0,
    }
    payload["uniform_values"]["u_time"] = 0.0

    with pytest.raises(ProgramSpecParseError) as excinfo:
        build_program_spec(payload, author_identity=_initial_identity())

    assert excinfo.value.code == "reserved_uniform"


def test_tunable_manifest_bounds_are_enforced() -> None:
    payload = _model_output()
    payload["tunable_manifest"][0]["maximum"] = 2.0

    with pytest.raises(ProgramSpecParseError) as excinfo:
        build_program_spec(payload, author_identity=_initial_identity())

    assert excinfo.value.code == "out_of_domain"

    payload = _model_output()
    payload["tunable_manifest"][0]["path"] = "u_missing"
    with pytest.raises(ProgramSpecParseError) as excinfo:
        build_program_spec(payload, author_identity=_initial_identity())
    assert excinfo.value.code == "unknown_tunable_path"


def test_resource_limits_are_fail_closed() -> None:
    spec = _spec()
    tight = ProgramSpecSafetyLimits(max_uniforms=1, max_tunables=0)
    result = validate_program_spec_safety(spec, limits=tight)

    assert not result.valid
    assert {item.code for item in result.errors} >= {
        "too_many_uniforms",
        "too_many_tunables",
    }

    oversized = replace(spec, canvas=replace(spec.canvas, width=4096))
    result = validate_program_spec_safety(oversized)
    assert {item.code for item in result.errors} >= {"canvas_too_large"}


def test_valid_spec_passes_safety_with_real_static_validator() -> None:
    result = validate_program_spec_safety(_spec())

    assert result.valid, [item.message for item in result.errors]
    assert result.contract_id == "webgl1_static_no_texture_v1"


def test_texture_sampling_and_unbounded_loop_are_rejected() -> None:
    payload = _model_output()
    payload["fragment_source"] = VALID_SHADER.replace(
        "vec2 p = v_uv - vec2(0.5);",
        "vec4 sampled = texture2D(u_image, v_uv);\n    vec2 p = v_uv - vec2(0.5);",
    )
    result = validate_program_spec_safety(
        build_program_spec(payload, author_identity=_initial_identity())
    )
    assert {item.code for item in result.errors} >= {"texture_sampling"}

    payload = _model_output()
    payload["fragment_source"] = VALID_SHADER.replace(
        "float mask = 1.0 - smoothstep(0.29, 0.31, length(p));",
        "float mask = 0.0;\n    for (int i = 0; i < steps; i++) { mask += 0.1; }",
    )
    result = validate_program_spec_safety(
        build_program_spec(payload, author_identity=_initial_identity())
    )
    assert {item.code for item in result.errors} >= {"unbounded_loop"}


@pytest.mark.parametrize(
    ("header", "expected_code"),
    [
        ("int i = 0; i < 1000000000; i++", "loop_iteration_limit"),
        ("int i = 0; i < 8; i--", "unbounded_loop"),
        ("int i = 0; i < 8; i += 0", "unbounded_loop"),
        ("int i = 0; i < 8; i = i + 1", "unbounded_loop"),
        ("int i = 8; other > 0; i--", "unbounded_loop"),
    ],
)
def test_for_loop_must_be_statically_bounded(header: str, expected_code: str) -> None:
    payload = _model_output()
    payload["fragment_source"] = VALID_SHADER.replace(
        "float mask = 1.0 - smoothstep(0.29, 0.31, length(p));",
        f"float mask = 0.0;\n    for ({header}) {{ mask += 0.001; }}",
    )

    result = validate_program_spec_safety(
        build_program_spec(payload, author_identity=_initial_identity())
    )

    assert expected_code in {item.code for item in result.errors}


def test_canonical_for_loop_within_limit_is_accepted() -> None:
    payload = _model_output()
    payload["fragment_source"] = VALID_SHADER.replace(
        "float mask = 1.0 - smoothstep(0.29, 0.31, length(p));",
        "float mask = 0.0;\n    for (int i = 8; i >= 1; i -= 2) { mask += 0.001; }",
    )

    result = validate_program_spec_safety(
        build_program_spec(payload, author_identity=_initial_identity())
    )

    assert result.valid, [item.code for item in result.errors]


def test_for_loop_macro_alias_cannot_bypass_iteration_limit() -> None:
    payload = _model_output()
    payload["fragment_source"] = "#define LOOP for\n" + VALID_SHADER.replace(
        "float mask = 1.0 - smoothstep(0.29, 0.31, length(p));",
        "float mask = 0.0;\n"
        "    LOOP (int i = 0; i < 1000000000; i++) { mask += 0.001; }",
    )

    result = validate_program_spec_safety(
        build_program_spec(payload, author_identity=_initial_identity())
    )

    assert "forbidden_preprocessor" in {item.code for item in result.errors}


def test_author_identity_role_and_parent_binding() -> None:
    with pytest.raises(ProgramSpecParseError) as excinfo:
        build_author_identity(
            reference_sha256=REFERENCE_SHA256,
            instruction_sha256=INSTRUCTION_SHA256,
            model_ref="m",
            prompt_version="p",
            role="refine",
        )
    assert excinfo.value.code == "missing_parent_spec"

    with pytest.raises(ProgramSpecParseError) as excinfo:
        build_author_identity(
            reference_sha256=REFERENCE_SHA256,
            instruction_sha256=INSTRUCTION_SHA256,
            model_ref="m",
            prompt_version="p",
            role="initial",
            parent_spec_sha256=PARENT_SHA256,
        )
    assert excinfo.value.code == "unexpected_parent_spec"


def test_attestation_issue_requires_valid_receipt_and_static_safety() -> None:
    spec = _spec()
    forged = replace(_ok_receipt(spec), digest="0" * 64)

    with pytest.raises(AttestationError) as excinfo:
        issue_attestation(
            spec,
            receipt=forged,
            static_ok=True,
            issuer=_ISSUER,
        )
    assert excinfo.value.code == "receipt_mismatch"

    with pytest.raises(AttestationError) as excinfo:
        issue_attestation(
            spec,
            receipt=_ok_receipt(spec),
            static_ok=False,
            issuer=_ISSUER,
        )
    assert excinfo.value.code == "static_validation_failed"


def test_receipt_from_other_process_key_fails_closed() -> None:
    spec = _spec()
    receipt = _ok_receipt(spec)
    _, foreign_issuer = _test_receipt_capabilities(
        key=b"another-process-key",
        issuer_id="foreign_process",
    )
    attested = spec.with_attestation(_issue(spec))

    assert not foreign_issuer.verify(receipt)
    assert not is_executable(attested, issuer=foreign_issuer)


def test_public_program_spec_api_exposes_verifier_not_signer() -> None:
    import shaderforge.program_spec as public_api

    verifier = public_api.process_receipt_verifier()
    assert not hasattr(verifier, "issue")
    assert not hasattr(verifier, "issue_after_draw")
    assert not hasattr(public_api, "TrustedReceiptIssuer")
    assert not hasattr(public_api, "process_receipt_issuer")


def test_attestation_match_happy_path_and_untrusted_version() -> None:
    spec = _spec()
    attestation = _issue(spec)
    attested = spec.with_attestation(attestation)

    assert match_attestation(attested, attestation, issuer=_ISSUER).ok
    assert is_executable(attested, issuer=_ISSUER)
    assert not is_executable(spec, issuer=_ISSUER)

    foreign = replace(attestation, validator_version="model_self_sign_v0")
    result = match_attestation(attested, foreign, issuer=_ISSUER)
    assert not result.ok
    assert "untrusted_validator_version" in result.reasons

    incomplete = replace(attestation, checks=tuple(REQUIRED_CHECKS[:-1]))
    result = match_attestation(attested, incomplete, issuer=_ISSUER)
    assert not result.ok
    assert any(reason.startswith("missing_checks") for reason in result.reasons)


def test_attestation_binds_trusted_validator_version_by_default() -> None:
    attestation = _issue(_spec())

    assert attestation.validator_version == TRUSTED_VALIDATOR_VERSION
    assert tuple(REQUIRED_CHECKS) == attestation.checks


def test_forged_attestation_never_matches() -> None:
    """手工构造 attestation + 伪造 receipt，不得通过 match 也不可执行."""
    spec = _spec()
    forged_receipt = replace(_ok_receipt(spec), digest="0" * 64)
    forged = ValidationAttestation(
        spec_sha256=spec.spec_sha256,
        validator_version=TRUSTED_VALIDATOR_VERSION,
        checks=REQUIRED_CHECKS,
        compile_ok=True,
        link_ok=True,
        draw_ok=True,
        execution_digest="0" * 64,
        receipt=forged_receipt,
    )

    result = match_attestation(spec, forged, issuer=_ISSUER)

    assert not result.ok
    assert "receipt_mismatch" in result.reasons
    assert not is_executable(spec.with_attestation(forged), issuer=_ISSUER)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rgb_sha256", "0" * 64),
        ("png_sha256", "1" * 64),
        ("nonce", "forged-nonce"),
        ("runtime_metadata", {"gl_version": "forged"}),
        ("spec_sha256", "2" * 64),
    ],
)
def test_tampered_receipt_fields_fail_verify(field: str, value: Any) -> None:
    receipt = _ok_receipt(_spec())
    tampered = replace(receipt, **{field: value})

    assert not _ISSUER.verify(tampered)
    with pytest.raises(AttestationError) as excinfo:
        issue_attestation(_spec(), receipt=tampered, static_ok=True, issuer=_ISSUER)
    assert excinfo.value.code == "receipt_mismatch"


def test_receipt_deserialization_roundtrip_and_reload_fail_closed() -> None:
    """同进程反序列化可验证；模拟进程重启（新 key）后旧 receipt 一律失败."""
    spec = _spec()
    receipt = _ok_receipt(spec)
    reloaded = _ISSUER.receipt_from_dict(receipt.to_dict())

    assert reloaded == receipt
    assert _ISSUER.verify(reloaded)

    _, restarted = _test_receipt_capabilities(
        key=b"another-process-key-y", issuer_id="process_local"
    )
    assert not restarted.verify(reloaded)


def test_attestation_binds_receipt_pixel_hashes_and_digest() -> None:
    spec = _spec()
    attestation = _issue(spec)

    assert attestation.receipt.rgb_sha256 == sha256(_RGB).hexdigest()
    assert attestation.receipt.png_sha256 == sha256(_PNG).hexdigest()
    assert attestation.receipt.spec_sha256 == spec.spec_sha256
    assert attestation.receipt.source_sha256 == spec.source_sha256
    assert attestation.execution_digest == attestation.receipt.digest
    assert attestation.receipt.nonce
    assert attestation.receipt.issued_at > 0


def _layer_output() -> dict:
    return {
        "schema_version": "layer_plan_v1",
        "layers": [
            {
                "layer_id": "bg",
                "role": "background",
                "z_index": 0,
                "region": {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
                "dominant_colors": [[0.1, 0.1, 0.2, 1.0]],
                "confidence": 0.9,
            },
            {
                "layer_id": "subject",
                "role": "subject",
                "z_index": 1,
                "region": {"x": 0.25, "y": 0.25, "width": 0.5, "height": 0.5},
                "dominant_colors": [[1.0, 0.2, 0.4, 1.0], [1.0, 1.0, 1.0, 1.0]],
                "confidence": 0.8,
                "notes": "中心主体",
            },
        ],
    }


def _plan():
    return build_layer_plan(
        _layer_output(),
        reference_sha256=REFERENCE_SHA256,
        author_identity=build_layer_author_identity(
            model_ref="vision-model-v1", prompt_version="layer_plan_v1"
        ),
    )


def test_layer_plan_hash_is_stable_and_binds_reference() -> None:
    first = _plan()
    second = build_layer_plan(
        dict(reversed(list(_layer_output().items()))),
        reference_sha256=REFERENCE_SHA256,
        author_identity=build_layer_author_identity(
            model_ref="vision-model-v1", prompt_version="layer_plan_v1"
        ),
    )
    assert first.plan_sha256 == second.plan_sha256

    other = build_layer_plan(
        _layer_output(),
        reference_sha256=sha256_hex_text("other-image"),
        author_identity=build_layer_author_identity(
            model_ref="vision-model-v1", prompt_version="layer_plan_v1"
        ),
    )
    assert other.plan_sha256 != first.plan_sha256


def test_layer_plan_rejects_hash_fields_and_bad_layers() -> None:
    payload = _layer_output()
    payload["plan_sha256"] = "0" * 64
    with pytest.raises(ProgramSpecParseError) as excinfo:
        build_layer_plan(
            payload,
            reference_sha256=REFERENCE_SHA256,
            author_identity=build_layer_author_identity(
                model_ref="m", prompt_version="p"
            ),
        )
    assert excinfo.value.code == "model_forbidden_hash_field"

    payload = _layer_output()
    payload["layers"][0]["role"] = "everything"
    with pytest.raises(ProgramSpecParseError) as excinfo:
        build_layer_plan(
            payload,
            reference_sha256=REFERENCE_SHA256,
            author_identity=build_layer_author_identity(
                model_ref="m", prompt_version="p"
            ),
        )
    assert excinfo.value.code == "invalid_layer_role"

    payload = _layer_output()
    payload["layers"] = payload["layers"] * 5
    with pytest.raises(ProgramSpecParseError) as excinfo:
        build_layer_plan(
            payload,
            reference_sha256=REFERENCE_SHA256,
            author_identity=build_layer_author_identity(
                model_ref="m", prompt_version="p"
            ),
        )
    assert excinfo.value.code == "out_of_domain"


def test_layer_plan_rejects_out_of_range_confidence() -> None:
    payload = _layer_output()
    payload["layers"][0]["confidence"] = 1.5

    with pytest.raises(ProgramSpecParseError) as excinfo:
        build_layer_plan(
            payload,
            reference_sha256=REFERENCE_SHA256,
            author_identity=build_layer_author_identity(
                model_ref="m", prompt_version="p"
            ),
        )

    assert excinfo.value.code == "out_of_domain"


# --- 跨层类型断言：Agent shadow adapter 只输出 canonical 类型 ---


def test_agent_shadow_adapter_returns_canonical_types() -> None:
    """Agent 侧不得定义第二套 LayerPlanV1/ShaderProgramSpecV1 执行真相."""
    import agent.app.contracts.layerplan_glsl_shadow as shadow_contract
    from shaderforge.program_spec import LayerPlanV1, ShaderProgramSpecV1

    assert shadow_contract.ShaderProgramSpecV1 is ShaderProgramSpecV1
    assert shadow_contract.LayerPlanV1 is LayerPlanV1

    # shadow renderer 契约禁止纹理采样，但仍要求 canonical 兼容声明。
    model_output = _model_output()
    model_output["fragment_source"] = (
        "precision mediump float;\n"
        "varying vec2 v_uv;\n"
        "uniform sampler2D u_image;\n"
        "uniform vec2 u_resolution;\n"
        "uniform float u_time;\n"
        "uniform float u_strength;\n"
        "uniform vec3 u_color;\n"
        "void main(){gl_FragColor=vec4(u_color * u_strength, 1.0);}\n"
    )
    spec = shadow_contract.assemble_program_spec(
        shadow_contract.parse_program_spec_semantics(
            json.dumps(model_output),
            expected_width=512,
            expected_height=512,
        ),
        author_identity=_initial_identity(),
    )
    assert type(spec) is ShaderProgramSpecV1
    assert spec.spec_sha256 == recompute_spec_sha256(spec)

    plan = shadow_contract.assemble_layer_plan(
        shadow_contract.parse_layer_plan_semantics(json.dumps(_layer_output())),
        reference_sha256=REFERENCE_SHA256,
        author_identity=build_layer_author_identity(model_ref="m", prompt_version="p"),
    )
    assert type(plan) is LayerPlanV1
