"""LayerPlan/direct GLSL shadow A/B harness 的离线单元测试.

全部使用 fake gateway 与 fake renderer：覆盖 A/B 预期控制差异、状态与预算隔离、
非法 Spec 不 draw、attestation 只在成功 draw 后签发、strict 选择不读
LayerPlan、以及内容寻址私有报告。
"""

from __future__ import annotations

import json
from hashlib import sha256
from io import BytesIO
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from PIL import Image

import agent.app.services.layerplan_glsl_shadow as shadow_service
from agent.app.contracts.llm import (
    EffectiveCallIdentity,
    EffectiveSamplingParams,
    LLMCallOptions,
    LLMResponse,
    TokenUsage,
)
from agent.app.services.layerplan_glsl_shadow import (
    LayerPlanGlslShadowRunner,
    ShadowABConfig,
    ShadowABConfigError,
    ShadowEvidenceError,
    is_strict_improvement,
    verify_shadow_run,
    write_shadow_run,
)
from shaderforge.program_spec import is_executable
from shaderforge.program_spec.hashing import canonical_json
from shaderforge.program_spec.receipt import _test_receipt_capabilities
from shaderforge.rendering.models import PreparedRenderResult

CANVAS = 64


def _reference_png(gray: int = 128) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (CANVAS, CANVAS), (gray, gray, gray)).save(buffer, "PNG")
    return buffer.getvalue()


def _solid_png(gray: int) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (CANVAS, CANVAS), (gray, gray, gray)).save(buffer, "PNG")
    return buffer.getvalue()


def _plan_payload() -> dict[str, Any]:
    return {
        "schema_version": "layer_plan_v1",
        "layers": [
            {
                "layer_id": "bg",
                "role": "background",
                "z_index": 0,
                "region": {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
                "dominant_colors": [[0.5, 0.5, 0.5, 1.0]],
                "confidence": 0.9,
            }
        ],
    }


_CANONICAL_DECLARATIONS = (
    "precision mediump float;\n"
    "varying vec2 v_uv;\n"
    "uniform sampler2D u_image;\n"
    "uniform vec2 u_resolution;\n"
    "uniform float u_time;\n"
)


def _spec_payload(gain: float, *, fragment_source: str | None = None) -> str:
    source = fragment_source or (
        _CANONICAL_DECLARATIONS + "uniform float u_gain;\n"
        "void main(){gl_FragColor=vec4(vec3(u_gain),1.0);}\n"
    )
    return json.dumps(
        {
            "schema_version": "shader_program_spec_v1",
            "fragment_source": source,
            "uniform_schema": {
                "u_gain": {
                    "type": "float",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "default": 0.5,
                }
            },
            "uniform_values": {"u_gain": gain},
            "tunable_manifest": [
                {
                    "path": "u_gain",
                    "type": "float",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "step": 0.01,
                }
            ],
            "canvas": {"width": CANVAS, "height": CANVAS},
            "renderer_contract_id": "webgl1_static_no_texture_v1",
        }
    )


class _FakeGateway:
    """按 System Prompt 角色 + LayerPlan 注入（臂身份）分发确定性响应.

    Initial/Refine 队列按 ``(role, has_layer_plan)`` 区分：Arm A 的消息不含
    ``layer_plan_advisory``，Arm B 含；两臂因此获得确定性的独立响应序列，
    不依赖全局调用顺序，也不改变 cache_hit 语义。
    """

    def __init__(
        self,
        *,
        plan_responses: list[str] | None = None,
        initial_responses: list[str] | None = None,
        refine_responses: list[str] | None = None,
        initial_responses_b: list[str] | None = None,
        refine_responses_b: list[str] | None = None,
        repair_responses: list[str] | None = None,
        usage: TokenUsage | None = TokenUsage(
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
        ),
    ) -> None:
        self._queues: dict[Any, list[str]] = {
            "plan": list(plan_responses or [json.dumps(_plan_payload())]),
            ("initial", False): list(initial_responses or [_spec_payload(0.5)]),
            ("initial", True): list(
                initial_responses_b or initial_responses or [_spec_payload(0.5)]
            ),
            ("refine", False): list(refine_responses or [_spec_payload(0.5)]),
            ("refine", True): list(
                refine_responses_b or refine_responses or [_spec_payload(0.5)]
            ),
            "repair": list(repair_responses or [_spec_payload(0.5)]),
        }
        self._usage = usage
        self.calls: list[dict[str, Any]] = []
        self._last_text: str | None = None

    async def ainvoke(
        self,
        messages: Any,
        options: LLMCallOptions,
    ) -> LLMResponse:
        system_text = str(messages[0].content)
        if "视觉分析" in system_text:
            role = "plan"
            key: Any = "plan"
        elif "Refine Author" in system_text or "Initial Author" in system_text:
            role = "refine" if "Refine Author" in system_text else "initial"
            has_plan = any(
                isinstance(part, dict)
                and part.get("type") == "text"
                and "<layer_plan_advisory>" in str(part.get("text", ""))
                for part in messages[1].content
            )
            key = (role, has_plan)
        else:
            role = "repair"
            key = "repair"
        if role == "repair" and self._last_text is not None:
            # 模拟固执模型：结构修复返回同一份非法输出，修复路径同样失败。
            text = self._last_text
        else:
            queue = self._queues[key]
            text = queue.pop(0) if len(queue) > 1 else queue[0]
        self._last_text = text
        self.calls.append(
            {"role": role, "messages": list(messages), "options": options}
        )
        return LLMResponse(
            message=AIMessage(content=text),
            text=text,
            reasoning_content=None,
            model_ref="fake-shadow-model",
            latency_ms=1,
            usage=self._usage,
            effective_identity=EffectiveCallIdentity(
                provider="fake",
                model_ref="fake-shadow-model",
                model_identity_source="response_metadata",
                sampling=EffectiveSamplingParams(
                    temperature=0.0,
                    thinking="off",
                    reasoning_effort=None,
                    response_format="json_object",
                    max_output_tokens=options.max_output_tokens,
                ),
            ),
        )


class _FakePrepared:
    def __init__(
        self,
        renderer: _FakeRenderer,
        fragment_source: str,
        width: int,
        height: int,
    ) -> None:
        self._renderer = renderer
        self._fragment_source = fragment_source
        self._width = width
        self._height = height

    async def render_uniforms(
        self,
        uniform_values: Any,
        *,
        capture_png: bool = False,
        receipt_spec_sha256: str | None = None,
    ) -> PreparedRenderResult:
        self._renderer.events.append("draw")
        self._renderer.draw_calls.append(dict(uniform_values))
        if self._renderer.fail_draw:
            return PreparedRenderResult(
                success=False,
                rgb_bytes=None,
                image_bytes=None,
                width=self._width,
                height=self._height,
                console_errors=(),
                duration_ms=1.0,
                draw_error="fake_draw_failed",
            )
        gray = round(float(uniform_values["u_gain"]) * 255)
        rgb = bytes([gray, gray, gray]) * (self._width * self._height)
        png = _solid_png(gray) if capture_png else None
        mode = self._renderer.receipt_mode
        if mode == "missing":
            receipt = None
        else:
            signer = _FOREIGN_SIGNER if mode == "foreign_issuer" else _TEST_SIGNER
            receipt_rgb = (
                bytes([gray ^ 0xFF]) * len(rgb) if mode == "tampered_rgb" else rgb
            )
            # fake renderer 在成功 draw 路径上用显式 test-only issuer 就地签发
            # receipt，语义与真实 renderer 一致；runner 只验证、绝不签发。
            assert receipt_spec_sha256 is not None
            receipt = signer.issue_after_draw(
                source_sha256=sha256(self._fragment_source.encode("utf-8")).hexdigest(),
                spec_sha256=receipt_spec_sha256,
                rgb_bytes=receipt_rgb,
                png_bytes=None if mode == "missing_png" else png,
                renderer_version="fake_shadow_renderer_v1",
                runtime_metadata=(
                    {}
                    if mode == "missing_runtime"
                    else {
                        "browser_version": "fake-browser",
                        "gl_version": "fake-gl",
                        "glsl_version": "fake-glsl",
                    }
                ),
            )
        return PreparedRenderResult(
            success=True,
            rgb_bytes=rgb,
            image_bytes=png,
            width=self._width,
            height=self._height,
            console_errors=(),
            duration_ms=1.0,
            execution_receipt=receipt,
        )

    async def close(self) -> None:
        self._renderer.close_count += 1


class _FakeRenderer:
    """按 uniform u_gain 渲染纯灰图的协议注入 Renderer."""

    def __init__(
        self,
        events: list[str] | None = None,
        *,
        fail_draw: bool = False,
        receipt_mode: str = "ok",
    ) -> None:
        """配置 fake receipt 的缺失、像素、runtime 或信任根异常."""
        self.events = events if events is not None else []
        self.fail_draw = fail_draw
        self.receipt_mode = receipt_mode
        self.prepare_calls: list[dict[str, Any]] = []
        self.draw_calls: list[dict[str, Any]] = []
        self.close_count = 0

    async def prepare(
        self,
        fragment_source: str,
        width: int,
        height: int,
        uniform_schema: Any,
    ) -> _FakePrepared:
        self.events.append("prepare")
        self.prepare_calls.append(
            {"fragment_source": fragment_source, "width": width, "height": height}
        )
        return _FakePrepared(self, fragment_source, width, height)


def _text_parts(message: Any) -> list[str]:
    return [
        str(part["text"])
        for part in message.content
        if isinstance(part, dict) and part.get("type") == "text"
    ]


_TEST_SIGNER, _TEST_ISSUER = _test_receipt_capabilities(
    issuer_id="test_only_shadow_runner"
)
_FOREIGN_SIGNER, _FOREIGN_ISSUER = _test_receipt_capabilities(
    issuer_id="foreign_process"
)


async def _run(
    gateway: _FakeGateway,
    renderer: _FakeRenderer,
    config: ShadowABConfig | None = None,
) -> Any:
    runner = LayerPlanGlslShadowRunner(
        gateway=gateway,
        renderer=renderer,
        config=config or ShadowABConfig(refine_budget_per_arm=0),
        receipt_issuer=_TEST_ISSUER,
    )
    return await runner.run(_reference_png(), instruction="match the gray square")


@pytest.mark.anyio
async def test_ab_arms_differ_only_by_layer_plan() -> None:
    gateway = _FakeGateway()
    renderer = _FakeRenderer()
    result = await _run(gateway, renderer)

    assert result.status == "ok"
    plan_calls = [call for call in gateway.calls if call["role"] == "plan"]
    assert len(plan_calls) == 1, "LayerPlan 必须只为 B 生成一次"
    initial_calls = [call for call in gateway.calls if call["role"] == "initial"]
    assert len(initial_calls) == 2

    arm_a_texts = _text_parts(initial_calls[0]["messages"][1])
    arm_b_texts = _text_parts(initial_calls[1]["messages"][1])
    assert not any("<layer_plan_advisory>" in text for text in arm_a_texts)
    assert any("<layer_plan_advisory>" in text for text in arm_b_texts)
    arm_a_rest = [t for t in arm_a_texts if "<layer_plan_advisory>" not in t]
    arm_b_rest = [t for t in arm_b_texts if "<layer_plan_advisory>" not in t]
    assert arm_a_rest == arm_b_rest, "除 LayerPlan 外两臂文本输入必须一致"
    assert initial_calls[0]["options"] == initial_calls[1]["options"]
    assert (
        initial_calls[0]["messages"][0].content
        == initial_calls[1]["messages"][0].content
    ), "两臂必须使用同一 Prompt 主体"

    arm_a, arm_b = result.arms
    assert arm_a.arm_id == "A" and arm_b.arm_id == "B"
    assert result.layer_plan is not None
    assert arm_a.layer_plan_sha256 is None
    assert arm_b.layer_plan_sha256 == result.layer_plan.plan_sha256
    assert arm_a.current_best is not None and arm_b.current_best is not None
    assert arm_a.current_best.spec.author_identity.plan_sha256 is None
    assert (
        arm_b.current_best.spec.author_identity.plan_sha256
        == result.layer_plan.plan_sha256
    )


@pytest.mark.anyio
async def test_arm_budget_and_state_isolation() -> None:
    gateway = _FakeGateway()
    renderer = _FakeRenderer()
    result = await _run(
        gateway,
        renderer,
        ShadowABConfig(direct_author_llm_budget=1, refine_budget_per_arm=0),
    )

    arm_a, arm_b = result.arms
    assert result.status == "ok", "两臂 Author 预算一致，LayerPlan 不消耗 B 臂预算"
    assert arm_a.ledger.llm_call_count == 1
    assert arm_b.ledger.llm_call_count == 1, "B 臂 Author 调用与 A 臂完全对齐"
    assert arm_a.ledger.total_tokens == 15
    assert arm_b.ledger.total_tokens == 15
    assert arm_a.plan_ledger is None, "A 臂永远没有 LayerPlan 记账"
    assert arm_b.plan_ledger is not None
    assert arm_b.plan_ledger.llm_call_count == 1, "LayerPlan 生成只计入 plan ledger"
    assert arm_b.plan_ledger.total_tokens == 15
    assert arm_a.current_best is not None and arm_b.current_best is not None


@pytest.mark.anyio
async def test_missing_token_usage_remains_unknown_in_both_ledgers() -> None:
    result = await _run(
        _FakeGateway(usage=None),
        _FakeRenderer(),
        ShadowABConfig(refine_budget_per_arm=0),
    )

    arm_a, arm_b = result.arms
    assert result.status == "ok"
    assert arm_a.ledger.total_tokens is None
    assert arm_b.ledger.total_tokens is None
    assert arm_b.plan_ledger is not None
    assert arm_b.plan_ledger.total_tokens is None


@pytest.mark.anyio
async def test_layer_plan_budget_exhausted_only_degrades_arm_b() -> None:
    gateway = _FakeGateway()
    renderer = _FakeRenderer()
    result = await _run(
        gateway,
        renderer,
        ShadowABConfig(plan_llm_budget=0, refine_budget_per_arm=0),
    )

    arm_a, arm_b = result.arms
    assert result.status == "inconclusive"
    assert arm_a.status == "ok", "plan 预算耗尽不得影响 A 臂"
    assert arm_a.current_best is not None
    assert arm_b.status == "inconclusive"
    assert arm_b.inconclusive_code == "layer_plan_generation_failed"
    assert arm_b.ledger.llm_call_count == 0, "plan 失败不消耗 B 臂 Author 预算"
    assert arm_b.plan_ledger is not None
    assert arm_b.plan_ledger.llm_call_count == 0
    assert arm_b.plan_ledger.total_tokens == 0, "零调用的 token 总量应保持精确 0"
    assert arm_b.current_best is None


@pytest.mark.anyio
async def test_invalid_spec_never_prepared_or_drawn() -> None:
    unbounded = (
        _CANONICAL_DECLARATIONS + "uniform float u_gain;\n"
        "void main(){ float acc=0.0;"
        " for (int i = 0; i < steps; i++) { acc += u_gain; }"
        " gl_FragColor=vec4(vec3(acc),1.0);}\n"
    )
    gateway = _FakeGateway(
        initial_responses=[_spec_payload(0.5, fragment_source=unbounded)]
    )
    renderer = _FakeRenderer()
    result = await _run(gateway, renderer)

    assert renderer.prepare_calls == [], "非法 Spec 绝不得进入 prepare/draw"
    assert renderer.draw_calls == []
    for arm in result.arms:
        assert arm.status == "inconclusive"
        assert arm.inconclusive_code == "static_validation_failed"
        assert arm.current_best is None
        assert arm.candidates == []
    assert result.status == "inconclusive"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "bad_source",
    [
        # 缺 canonical 兼容声明（无 v_uv/u_image/u_resolution/u_time）
        "precision mediump float;\n"
        "uniform float u_gain;\n"
        "void main(){gl_FragColor=vec4(vec3(u_gain),1.0);}\n",
        # 额外 sampler 声明：只放行 uniform sampler2D u_image;
        _CANONICAL_DECLARATIONS
        + "uniform sampler2D u_tex;\n"
        + "void main(){gl_FragColor=vec4(1.0);}",
        # 兼容 sampler 仅声明不可采样
        _CANONICAL_DECLARATIONS + "void main(){gl_FragColor=texture2D(u_image, v_uv);}",
    ],
)
async def test_non_canonical_glsl_never_prepared_or_drawn(bad_source: str) -> None:
    # 语义调用与结构修复都返回同一违规输出，确保 fail-closed 收敛。
    bad_payload = _spec_payload(0.5, fragment_source=bad_source)
    gateway = _FakeGateway(
        initial_responses=[bad_payload],
        repair_responses=[bad_payload],
    )
    renderer = _FakeRenderer()
    result = await _run(gateway, renderer)

    assert renderer.prepare_calls == [], "违反 GLSL 契约的输出绝不得进入 prepare/draw"
    assert renderer.draw_calls == []
    for arm in result.arms:
        assert arm.status == "inconclusive"
        assert arm.current_best is None
        assert arm.candidates == []
    assert result.status == "inconclusive"


@pytest.mark.anyio
async def test_attestation_only_issued_after_successful_draw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    real_issue = shadow_service.issue_attestation

    def _spy(spec: Any, **kwargs: Any) -> Any:
        events.append("attest")
        return real_issue(spec, **kwargs)

    monkeypatch.setattr(shadow_service, "issue_attestation", _spy)
    gateway = _FakeGateway()
    renderer = _FakeRenderer(events)
    result = await _run(gateway, renderer)

    assert events == ["prepare", "draw", "attest", "prepare", "draw", "attest"]
    for arm in result.arms:
        assert arm.current_best is not None
        attestation = arm.current_best.spec.validation_attestation
        assert attestation is not None
        assert attestation.draw_ok and attestation.compile_ok and attestation.link_ok
        assert attestation.spec_sha256 == arm.current_best.spec.spec_sha256
        assert is_executable(arm.current_best.spec, issuer=_TEST_ISSUER)


@pytest.mark.anyio
async def test_failed_draw_never_attested(monkeypatch: pytest.MonkeyPatch) -> None:
    issued: list[str] = []
    real_issue = shadow_service.issue_attestation

    def _spy(spec: Any, **kwargs: Any) -> Any:
        issued.append(spec.spec_sha256)
        return real_issue(spec, **kwargs)

    monkeypatch.setattr(shadow_service, "issue_attestation", _spy)
    gateway = _FakeGateway()
    renderer = _FakeRenderer(fail_draw=True)
    result = await _run(gateway, renderer)

    assert issued == [], "draw 失败绝不得签发 attestation"
    for arm in result.arms:
        assert arm.status == "inconclusive"
        assert arm.inconclusive_code == "draw_failed"
        assert arm.current_best is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("receipt_mode", "detail"),
    [
        ("missing", "receipt_missing"),
        ("tampered_rgb", "receipt_pixel_mismatch"),
        ("missing_png", "receipt_pixel_mismatch"),
        ("missing_runtime", "receipt_pixel_mismatch"),
        ("foreign_issuer", "receipt_mismatch"),
    ],
)
async def test_missing_tampered_or_foreign_receipt_never_attested(
    monkeypatch: pytest.MonkeyPatch,
    receipt_mode: str,
    detail: str,
) -> None:
    """缺失/篡改/外来 issuer 的 receipt 一律 fail-closed，绝不形成候选."""
    issued: list[str] = []
    real_issue = shadow_service.issue_attestation

    def _spy(spec: Any, **kwargs: Any) -> Any:
        issued.append(spec.spec_sha256)
        return real_issue(spec, **kwargs)

    monkeypatch.setattr(shadow_service, "issue_attestation", _spy)
    gateway = _FakeGateway()
    renderer = _FakeRenderer(receipt_mode=receipt_mode)
    result = await _run(gateway, renderer)

    for arm in result.arms:
        assert arm.status == "inconclusive"
        assert arm.inconclusive_code == "static_validation_failed"
        assert arm.current_best is None
        assert arm.candidates == []
        details = [
            str(event.get("detail", ""))
            for event in arm.events
            if event.get("error_code") == "static_validation_failed"
        ]
        assert any(detail in item for item in details), (receipt_mode, arm.events)
    if receipt_mode == "missing":
        assert issued == [], "无 receipt 时不得进入签发路径"


@pytest.mark.anyio
async def test_strict_selection_ignores_layer_plan() -> None:
    assert is_strict_improvement(0.1, 0.2)
    assert not is_strict_improvement(0.2, 0.2), "相等不算严格改善"
    assert not is_strict_improvement(0.3, 0.2)
    assert not is_strict_improvement(float("nan"), 0.2)

    gateway = _FakeGateway(
        initial_responses=[_spec_payload(0.4)],
        refine_responses=[_spec_payload(0.9), _spec_payload(0.5)],
        refine_responses_b=[_spec_payload(0.9), _spec_payload(0.5)],
    )
    renderer = _FakeRenderer()
    result = await _run(
        gateway,
        renderer,
        ShadowABConfig(refine_budget_per_arm=2),
    )

    for arm in result.arms:
        assert arm.status == "ok"
        assert len(arm.candidates) == 3
        best = arm.current_best
        assert best is not None
        assert best.spec.uniform_values["u_gain"] == 0.5
        assert arm.ledger.accepted_candidates == 2, "initial 与更优 refine"
        assert arm.ledger.rejected_candidates == 1, "更差 refine 整体丢弃"
        losses = [item.loss for item in arm.candidates]
        assert losses[1] > losses[0] > losses[2]

    arm_a, arm_b = result.arms
    assert arm_a.current_best is not None and arm_b.current_best is not None
    assert arm_a.current_best.loss == arm_b.current_best.loss, (
        "接受谓词只读真实 Render+metric，LayerPlan 不改变选择结果"
    )


@pytest.mark.anyio
async def test_program_cache_hit_skips_compile_but_new_spec_still_draws() -> None:
    """program cache 只跳过 compile：每个 Spec 提案仍 draw、签发并形成候选.

    重复 refine 都取不会改善的同一值 0.1，parent 因此一直是 initial：
    第一次 refine 是新 Spec（role/parent 身份不同）但命中同一 program；
    第二次 refine 是与第一次逐字节相同的 Spec（parent identity 未变），
    再次命中同一 program——绝不忽略 parent identity。
    """
    gateway = _FakeGateway(
        initial_responses=[_spec_payload(0.4)],
        refine_responses=[_spec_payload(0.1), _spec_payload(0.1)],
    )
    renderer = _FakeRenderer()
    result = await _run(
        gateway,
        renderer,
        ShadowABConfig(refine_budget_per_arm=2),
    )

    for arm in result.arms:
        assert len(arm.candidates) == 3, "initial 与两次 refine 各形成自己的候选"
        assert arm.ledger.compile_count == 1, "同一 program 只 compile 一次"
        assert arm.ledger.draw_count == 3, "每个 Spec 提案仍真实 draw"
        assert arm.ledger.cache_hits == 2, "两次 refine 都命中同一 program cache"
        initial_candidate, refine_one, refine_two = arm.candidates
        assert initial_candidate.spec.source_sha256 == refine_one.spec.source_sha256, (
            "同一可编译 program"
        )
        assert initial_candidate.spec.spec_sha256 != refine_one.spec.spec_sha256, (
            "role/parent 身份不同即不同 Spec"
        )
        assert refine_one.spec.spec_sha256 == refine_two.spec.spec_sha256, (
            "parent 一直是 initial：两次相同提案是 exact 同一 Spec"
        )
        assert (
            refine_one.parent_spec_sha256
            == refine_two.parent_spec_sha256
            == initial_candidate.spec.spec_sha256
        ), "两次 refine 的 parent identity 都必须保持 initial"
        for candidate in arm.candidates:
            assert is_executable(candidate.spec, issuer=_TEST_ISSUER)
            attestation = candidate.spec.validation_attestation
            assert attestation is not None
            assert attestation.spec_sha256 == candidate.spec.spec_sha256, (
                "每个候选的 attestation 各自匹配自己的 Spec hash"
            )
        assert arm.current_best is initial_candidate, "0.1 更差，两次都整体丢弃"
        assert arm.ledger.accepted_candidates == 1
        assert arm.ledger.rejected_candidates == 2
    assert renderer.close_count == 2, "两臂各自在结束时统一关闭 prepared handle"


@pytest.mark.anyio
async def test_execution_order_frozen_and_recorded() -> None:
    gateway = _FakeGateway()
    renderer = _FakeRenderer()
    result = await _run(
        gateway,
        renderer,
        ShadowABConfig(arm_order=("B", "A"), refine_budget_per_arm=0),
    )

    assert result.execution_order == ("B", "A")
    arm_a, arm_b = result.arms
    sequences = [event["sequence"] for event in arm_b.events + arm_a.events]
    assert sequences == sorted(sequences), "事件序号必须按冻结顺序单调递增"
    assert len(set(sequences)) == len(sequences)
    plan_events = [
        event for event in arm_b.events if event["kind"] == "visual_analysis"
    ]
    assert len(plan_events) == 1
    assert all("sequence" in event for event in arm_a.events)


@pytest.mark.anyio
async def test_content_addressed_private_report(tmp_path: Any) -> None:
    gateway = _FakeGateway()
    renderer = _FakeRenderer()
    result = await _run(gateway, renderer)

    run_dir = write_shadow_run(result, tmp_path)
    report_path = run_dir / "report.json"
    assert report_path.is_file()
    payload = json.loads(report_path.read_text(encoding="utf-8"))

    assert payload["report_schema_version"] == "layerplan_glsl_shadow_ab_report_v1"
    assert payload["run_classification"] == "independent_experiment"
    assert payload["durability_status"] == "local_private_not_registered"
    assert payload["execution_order"] == ["A", "B"]
    assert payload["reference_sha256"] == sha256(_reference_png()).hexdigest()
    assert payload["reference_content_type"] == "image/png"
    assert payload["layer_plan_sha256"] is not None
    assert payload["arms"][0]["plan_ledger"] is None
    assert payload["arms"][1]["plan_ledger"]["llm_call_count"] == 1

    # 评估身份：metric 版本、预处理事实与背景色必须进报告。
    evaluation = payload["evaluation"]
    assert evaluation["metric_version"] == "min_scene_composite_v3"
    assert evaluation["preprocess"]["preprocess_version"]
    assert len(evaluation["background"]) == 3
    # 探索性声明：无 seed 的 temperature=1 A/B 不得声称唯一因果变量。
    assert any("探索" in note for note in payload["validity_notes"])
    assert payload["config"]["requested_sampling_params"]["temperature"] == 0
    assert "sampling_params" not in payload["config"], (
        "requested 请求值不得冒充 effective 事实"
    )

    # 候选身份：报告摘要与 metrics.json 都绑定 metric/residual 哈希。
    for arm_summary in payload["arms"]:
        for candidate_summary in arm_summary["candidates"]:
            assert len(candidate_summary["metric_sha256"]) == 64
            assert len(candidate_summary["residual_sha256"]) == 64
    for metrics_path in run_dir.glob("arms/*/candidates/*/metrics.json"):
        metrics_payload = json.loads(metrics_path.read_text(encoding="utf-8"))
        assert metrics_payload["reference_sha256"] == payload["reference_sha256"]
        assert metrics_payload["metric_version"] == "min_scene_composite_v3"
        assert (
            metrics_payload["metric_sha256"]
            == sha256(
                canonical_json(metrics_payload["metrics"]).encode("utf-8")
            ).hexdigest()
        )
        assert (
            metrics_payload["residual_sha256"]
            == sha256(
                canonical_json(metrics_payload["residual_summary"]).encode("utf-8")
            ).hexdigest()
        )

    # 每次调用记录的是 effective 身份而非请求假值。
    for spec_path in run_dir.glob("arms/*/candidates/*/spec.json"):
        spec_payload = json.loads(spec_path.read_text(encoding="utf-8"))
        identity = spec_payload["author_identity"]
        assert identity["sampling_params"]["provider"] == "fake"
        assert identity["reference_content_type"] == "image/png"
        assert len(identity["input_context_sha256"]) == 64

    files = payload["files"]
    assert "layer_plan.json" in files
    for relative, digest in files.items():
        data = (run_dir / relative).read_bytes()
        assert sha256(data).hexdigest() == digest, f"{relative} 内容哈希必须匹配"

    report_sha256 = payload.pop("report_sha256")
    assert sha256(canonical_json(payload).encode("utf-8")).hexdigest() == report_sha256

    candidate_dirs = list(run_dir.glob("arms/*/candidates/*/spec.json"))
    assert candidate_dirs, "每个候选必须写私有 spec.json"
    for spec_path in candidate_dirs:
        spec_payload = json.loads(spec_path.read_text(encoding="utf-8"))
        assert spec_payload["validation_attestation"] is not None
        render_path = spec_path.with_name("render.png")
        assert render_path.is_file()

    with pytest.raises(FileExistsError):
        write_shadow_run(result, tmp_path)


@pytest.mark.anyio
async def test_run_id_distinguishes_stochastic_results_with_same_config() -> None:
    first = await _run(_FakeGateway(), _FakeRenderer())
    second = await _run(
        _FakeGateway(
            initial_responses=[_spec_payload(0.4)],
            initial_responses_b=[_spec_payload(0.4)],
        ),
        _FakeRenderer(),
    )

    assert first.config_fingerprint == second.config_fingerprint
    assert first.reference_sha256 == second.reference_sha256
    assert shadow_service.shadow_run_id(first) != shadow_service.shadow_run_id(second)


def test_cli_requires_explicit_live_model_opt_in(capsys: Any) -> None:
    from scripts.run_layerplan_glsl_shadow_ab import main

    exit_code = main(["--reference", "ref.png", "--output-root", "out"])
    assert exit_code == 2, "缺省绝不运行真实模型"
    assert "--allow-live-model" in capsys.readouterr().err


def test_cli_parse_canvas() -> None:
    from scripts.run_layerplan_glsl_shadow_ab import _parse_canvas

    assert _parse_canvas(None) == (None, None)
    assert _parse_canvas("256x128") == (256, 128)
    with pytest.raises(SystemExit):
        _parse_canvas("not-a-canvas")


def test_shadow_config_rejects_canvas_above_renderer_contract() -> None:
    oversized = shadow_service.MAX_CANVAS_SIDE + 1

    with pytest.raises(ShadowABConfigError, match="Renderer 契约上限"):
        ShadowABConfig(canvas_width=oversized, canvas_height=16)


@pytest.mark.anyio
async def test_tampered_author_identity_breaks_attestation() -> None:
    """spec_sha256 绑定 author_identity：篡改身份即 attestation 失配."""
    from dataclasses import replace

    gateway = _FakeGateway()
    renderer = _FakeRenderer()
    result = await _run(gateway, renderer)

    for arm in result.arms:
        best = arm.current_best
        assert best is not None
        assert is_executable(best.spec, issuer=_TEST_ISSUER)
        tampered_identity = replace(
            best.spec.author_identity, model_ref="attacker-controlled-model"
        )
        tampered = replace(best.spec, author_identity=tampered_identity)
        assert not is_executable(tampered, issuer=_TEST_ISSUER), (
            "身份篡改必须使 attestation 失配"
        )


@pytest.mark.anyio
async def test_direct_author_llm_budget_ceiling_identical_across_arms() -> None:
    """两臂 direct Author 调用上限一致：plan 成本不侵蚀 B 臂 Author 预算."""
    gateway = _FakeGateway()
    renderer = _FakeRenderer()
    result = await _run(
        gateway,
        renderer,
        ShadowABConfig(direct_author_llm_budget=1, refine_budget_per_arm=1),
    )

    arm_a, arm_b = result.arms
    assert result.status == "ok", "两臂都在 Initial 后耗尽 Author 预算，状态一致"
    for arm in result.arms:
        assert arm.ledger.llm_call_count == 1, "两臂 direct Author 调用上限一致"
        assert arm.ledger.total_tokens == 15, "零调用的 Refine 不得污染已知 token 总量"
        refine_events = [e for e in arm.events if e["kind"] == "refine"]
        assert refine_events, "Refine 仍以相同剩余预算（0）尝试"
        assert refine_events[0]["error_code"] == "llm_budget_exhausted"
    assert arm_b.plan_ledger is not None
    assert arm_b.plan_ledger.llm_call_count == 1, "plan 成本只记入 plan ledger"


# --- 私有证据：原子写入、权限与 verify_shadow_run ---


@pytest.mark.anyio
async def test_verify_shadow_run_roundtrip_and_permissions(tmp_path: Any) -> None:
    import os
    import stat

    gateway = _FakeGateway()
    renderer = _FakeRenderer()
    result = await _run(gateway, renderer)

    run_dir = write_shadow_run(result, tmp_path)
    payload = verify_shadow_run(run_dir)
    assert payload["report_sha256"]
    assert stat.S_IMODE(os.stat(run_dir).st_mode) == 0o700
    for path in run_dir.rglob("*"):
        mode = stat.S_IMODE(os.stat(path).st_mode)
        if path.is_dir():
            assert mode == 0o700, f"{path} 目录必须 0700"
        else:
            assert mode == 0o600, f"{path} 文件必须 0600"


@pytest.mark.anyio
async def test_verify_shadow_run_rejects_directory_name_not_bound_to_report(
    tmp_path: Any,
) -> None:
    result = await _run(_FakeGateway(), _FakeRenderer())
    run_dir = write_shadow_run(result, tmp_path)
    renamed = tmp_path / "shadow-renamed000"
    run_dir.rename(renamed)

    with pytest.raises(ShadowEvidenceError, match="内容寻址身份不匹配"):
        verify_shadow_run(renamed)


@pytest.mark.anyio
async def test_verify_shadow_run_detects_tampered_evidence_file(tmp_path: Any) -> None:
    gateway = _FakeGateway()
    renderer = _FakeRenderer()
    result = await _run(gateway, renderer)
    run_dir = write_shadow_run(result, tmp_path)

    target = next(run_dir.glob("arms/*/ledger.json"))
    target.write_text('{"tampered": true}\n', encoding="utf-8")
    with pytest.raises(ShadowEvidenceError, match="哈希不匹配"):
        verify_shadow_run(run_dir)


@pytest.mark.anyio
async def test_verify_shadow_run_detects_tampered_report(tmp_path: Any) -> None:
    gateway = _FakeGateway()
    renderer = _FakeRenderer()
    result = await _run(gateway, renderer)
    run_dir = write_shadow_run(result, tmp_path)

    report_path = run_dir / "report.json"
    report_path.write_text(
        report_path.read_text(encoding="utf-8").replace(
            '"independent_experiment"', '"tampered_classification"'
        ),
        encoding="utf-8",
    )
    with pytest.raises(ShadowEvidenceError, match="report_sha256 不匹配"):
        verify_shadow_run(run_dir)


@pytest.mark.anyio
async def test_verify_shadow_run_rejects_extra_file(tmp_path: Any) -> None:
    gateway = _FakeGateway()
    renderer = _FakeRenderer()
    result = await _run(gateway, renderer)
    run_dir = write_shadow_run(result, tmp_path)

    extra = run_dir / "arms" / "A" / "injected.json"
    extra.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ShadowEvidenceError, match="额外文件"):
        verify_shadow_run(run_dir)


@pytest.mark.anyio
async def test_verify_shadow_run_rejects_path_traversal(tmp_path: Any) -> None:
    gateway = _FakeGateway()
    renderer = _FakeRenderer()
    result = await _run(gateway, renderer)
    run_dir = write_shadow_run(result, tmp_path)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    report_path = run_dir / "report.json"
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload["files"] = {"../outside.bin": sha256(outside.read_bytes()).hexdigest()}
    unsigned = dict(payload)
    unsigned.pop("report_sha256")
    payload["report_sha256"] = sha256(
        canonical_json(unsigned).encode("utf-8")
    ).hexdigest()
    report_path.write_text(canonical_json(payload) + "\n", encoding="utf-8")

    with pytest.raises(ShadowEvidenceError, match="规范 POSIX 相对路径"):
        verify_shadow_run(run_dir)


@pytest.mark.anyio
async def test_verify_shadow_run_rejects_dangling_symlink(tmp_path: Any) -> None:
    gateway = _FakeGateway()
    renderer = _FakeRenderer()
    result = await _run(gateway, renderer)
    run_dir = write_shadow_run(result, tmp_path)
    (run_dir / "dangling-link").symlink_to(run_dir / "missing-target")

    with pytest.raises(ShadowEvidenceError, match="symlink"):
        verify_shadow_run(run_dir)


@pytest.mark.anyio
async def test_verify_shadow_run_rejects_wide_permissions(tmp_path: Any) -> None:
    import os

    gateway = _FakeGateway()
    renderer = _FakeRenderer()
    result = await _run(gateway, renderer)
    run_dir = write_shadow_run(result, tmp_path)

    target = next(run_dir.glob("arms/*/ledger.json"))
    os.chmod(target, 0o644)
    with pytest.raises(ShadowEvidenceError, match="权限过宽"):
        verify_shadow_run(run_dir)


@pytest.mark.anyio
async def test_write_shadow_run_rejects_symlink_output_root(tmp_path: Any) -> None:
    gateway = _FakeGateway()
    renderer = _FakeRenderer()
    result = await _run(gateway, renderer)

    real_root = tmp_path / "real"
    real_root.mkdir()
    symlink_root = tmp_path / "linked"
    symlink_root.symlink_to(real_root)
    with pytest.raises(ShadowEvidenceError, match="symlink"):
        write_shadow_run(result, symlink_root)


@pytest.mark.anyio
async def test_verify_shadow_run_rejects_staging_leftover(tmp_path: Any) -> None:
    staging = tmp_path / ".shadow-deadbeef.staging-1-abc"
    staging.mkdir()
    (staging / "report.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ShadowEvidenceError, match="staging"):
        verify_shadow_run(staging)


@pytest.mark.anyio
async def test_write_shadow_run_crash_leaves_no_final_or_staging(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = _FakeGateway()
    renderer = _FakeRenderer()
    result = await _run(gateway, renderer)
    expected_run_dir = tmp_path / shadow_service.shadow_run_id(result)

    def _boom(_: Any) -> dict[str, Any]:
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(shadow_service, "build_report_payload", _boom)
    with pytest.raises(RuntimeError, match="simulated crash"):
        write_shadow_run(result, tmp_path)
    assert not expected_run_dir.exists(), (
        "崩溃绝不留下占用最终 run_id 的半成品"
    )
    assert list(tmp_path.glob(".shadow-*.staging-*")) == [], "staging 必须清理"


@pytest.mark.anyio
async def test_cli_verify_mode(tmp_path: Any, capsys: Any) -> None:
    from scripts.run_layerplan_glsl_shadow_ab import main

    gateway = _FakeGateway()
    renderer = _FakeRenderer()
    result = await _run(gateway, renderer)
    run_dir = write_shadow_run(result, tmp_path)

    assert main(["--verify", str(run_dir)]) == 0
    assert "verify ok" in capsys.readouterr().out
