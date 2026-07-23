"""scene_mvp 私有 Patch replay bundle v1 的聚焦测试。.

验证完整 typed patch/候选/raw/matured 只写入 run 目录 private/replay/，
公开 trace/patch_evidence/manifest 与 HTTP 白名单不泄露，且
accepted/tie/duplicate/invalid/renderer_failed 各类 step 均可审计。
"""

import json
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace

import numpy as np
import pytest
from langchain_core.messages import AIMessage
from PIL import Image, ImageDraw

from agent.app.config.model_config import SHADER_GEN_MODEL_NAME
from agent.app.contracts.llm import LLMResponse
from agent.app.contracts.png_to_shader_min_replay import decode_verified_replay_json
from agent.app.nodes.png_to_shader_min import MinRendererRegistry, make_min_nodes
from agent.app.nodes.png_to_shader_min.model_author import (
    MIN_AUTHOR_REFINE_PROMPT,
    MIN_AUTHOR_REPAIR_PROMPT,
)
from agent.app.nodes.png_to_shader_min.runtime import (
    _write_replay_json_once,
    _write_replay_render,
)
from agent.app.services.png_to_shader_min import (
    PngToShaderMinService,
    _build_progress_event,
)
from shaderforge.evaluation import evaluate_min_scene
from shaderforge.perception import perceive_min_target
from shaderforge.scene import MinScene
from shaderforge.store import LocalArtifactStore

_PROJECT = "replay-project"
_RUN = "replay-run"


def _pink_orb_png() -> bytes:
    image = Image.new("RGB", (96, 96), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((14, 14, 82, 82), fill=(245, 80, 130))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class _WhitePrepared:
    def __init__(self) -> None:
        self.width = 0
        self.height = 0
        self.prepare_duration_ms = 1.0
        self.render_durations_ms: tuple[float, ...] = ()

    @property
    def render_count(self) -> int:
        return len(self.render_durations_ms)

    def _png(self, rgb: bytes) -> bytes:
        image = Image.frombytes("RGB", (self.width, self.height), rgb)
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    async def render_uniforms(self, _values, *, capture_png=False):
        self.render_durations_ms = (*self.render_durations_ms, 1.0)
        rgb = Image.new("RGB", (self.width, self.height), "white").tobytes()
        return SimpleNamespace(
            success=True,
            rgb_bytes=rgb,
            image_bytes=self._png(rgb) if capture_png else None,
            draw_error=None,
        )

    async def close(self) -> None:
        return None


class _MaturityPrepared(_WhitePrepared):
    async def render_uniforms(self, values, *, capture_png=False):
        self.render_durations_ms = (*self.render_durations_ms, 1.0)
        feature_kind = float(values["u_feature_kinds"][0])
        value = (
            float(values["u_feature_0_color_power"][3]) if feature_kind > 0.0 else 0.25
        )
        channel = round(value * 255)
        rgb = bytes((channel, channel, channel)) * self.width * self.height
        return SimpleNamespace(
            success=True,
            rgb_bytes=rgb,
            image_bytes=None,
            draw_error=None,
        )


class _FailingPrepared(_WhitePrepared):
    async def render_uniforms(self, _values, *, capture_png=False):
        self.render_durations_ms = (*self.render_durations_ms, 1.0)
        return SimpleNamespace(
            success=False,
            rgb_bytes=None,
            image_bytes=None,
            draw_error="synthetic_draw_failure",
        )


class _FakeRenderer:
    prepared_class = _WhitePrepared

    def __init__(self) -> None:
        self.prepared = self.prepared_class()

    async def prepare(self, _source, width, height, _uniform_schema):
        self.prepared.width = width
        self.prepared.height = height
        return self.prepared

    async def close(self) -> None:
        return None


class _MaturityRenderer(_FakeRenderer):
    prepared_class = _MaturityPrepared


class _FailingRenderer(_FakeRenderer):
    prepared_class = _FailingPrepared


class _FakeGateway:
    def __init__(self, *responses: str | Exception) -> None:
        self.responses = list(responses)

    async def ainvoke(self, _messages, _options):
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return LLMResponse(
            message=AIMessage(content=response),
            text=response,
            reasoning_content=None,
            model_ref="fake:replay-author",
            requested_model_ref="fake:replay-author",
            latency_ms=1,
        )


def _anchor_state() -> dict[str, object]:
    """构造带 current_best 锚点的 refine 输入状态（无 feature 主体）。."""
    perception = perceive_min_target(_pink_orb_png())
    scene_data = perception.fallback_scene.model_dump(mode="python")
    scene_data["object"]["features"] = ()
    scene = MinScene.model_validate(scene_data)
    target_rgb = np.full(
        (scene.canvas.height, scene.canvas.width, 3), 56 / 255.0, dtype=np.float32
    )
    anchor_rgb = np.full_like(target_rgb, 64 / 255.0)
    metric = evaluate_min_scene(target_rgb, anchor_rgb, scene.canvas.background)
    anchor_png = Image.new("RGB", (scene.canvas.width, scene.canvas.height), (64,) * 3)
    buffer = BytesIO()
    anchor_png.save(buffer, format="PNG")
    best = {
        "scene": scene.model_dump(mode="json"),
        "mae": metric.global_mae,
        "loss": metric.total_loss,
        "metrics": metric.to_dict(),
        "residual_summary": {},
        "glsl": "anchor-glsl",
        "render": buffer.getvalue(),
    }
    return {
        "project_id": _PROJECT,
        "run_id": _RUN,
        "image": _pink_orb_png(),
        "content_type": "image/png",
        "instruction": "保留主体",
        "scene": best["scene"],
        "current_best": best,
        "target_rgb": target_rgb,
        "metric_background": scene.canvas.background,
        "render_count": 0,
        "render_budget": 64,
        "llm_call_count": 1,
        "llm_budget": 4,
        "refine_count": 0,
        "refine_budget": 2,
        "target_mae": 0.04,
        "target_loss": 0.02,
        "quality_preset": "balanced",
        "run_classification": "independent_experiment",
        "experiment_id": "replay-test",
        "config_fingerprint": "f" * 64,
        "report_schema_version": "test-report-v1",
        "feature_queue": (),
        "trace": (),
        "recent_rejected_patch_summaries": (),
        "patch_evidence": (),
    }


_PATCH_JSON = (
    '{"operation":"add","path":"/object/features","value":'
    '{"id":"local_highlight","type":"gaussian_lobe","center":[0,0],'
    '"axes":[0.4,0.3],"color":[1,1,1],"intensity":0.3}}'
)


def _step_dir(tmp_path, name: str = "refine-001"):
    return tmp_path / _PROJECT / _RUN / "private" / "replay" / "steps" / name


def _read_json(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _run_dir(tmp_path):
    return tmp_path / _PROJECT / _RUN


async def _author_and_branch(tmp_path, renderer, gateway, state):
    registry = MinRendererRegistry(renderer)  # type: ignore[arg-type]
    nodes = make_min_nodes(LocalArtifactStore(tmp_path), registry, gateway)
    refined = await nodes["author_refine"](state)
    update = await nodes["render_and_evaluate"]({**state, **refined})
    return update


@pytest.mark.anyio
async def test_accepted_step_writes_private_patch_record_and_bundle(tmp_path) -> None:
    state = _anchor_state()
    update = await _author_and_branch(
        tmp_path, _MaturityRenderer, _FakeGateway(_PATCH_JSON), state
    )
    assert update["current_best"] != state["current_best"]

    patch_draft = _read_json(_step_dir(tmp_path) / "patch.json")
    assert patch_draft["schema_version"] == "scene_mvp_replay_patch_v1"
    assert patch_draft["typed_patch"]["operation"] == "add"
    assert patch_draft["typed_patch"]["value"]["id"] == "local_highlight"
    assert patch_draft["author"]["model_ref"] == "fake:replay-author"
    assert patch_draft["author"]["requested_model_ref"] == SHADER_GEN_MODEL_NAME
    assert (
        patch_draft["author"]["source_prompt"]["name"] == MIN_AUTHOR_REFINE_PROMPT.name
    )
    assert (
        patch_draft["author"]["output_prompt"]["name"] == MIN_AUTHOR_REFINE_PROMPT.name
    )

    record = _read_json(_step_dir(tmp_path) / "record.json")
    assert record["schema_version"] == "scene_mvp_replay_step_v1"
    assert record["status"] == "pending"
    assert record["typed_patch"]["value"]["type"] == "gaussian_lobe"
    assert record["anchor"]["scene"] == state["current_best"]["scene"]
    assert record["anchor"]["scene_sha256"]
    assert record["anchor"]["loss"] is not None
    assert record["anchor"]["render_sha256"]
    assert record["candidate_scene"] == record["raw"]["scene"]
    assert record["candidate_scene_sha256"]
    assert record["raw"]["loss"] is not None
    assert record["matured"]["loss"] < record["anchor"]["loss"]
    assert record["raw"]["render"]["sha256"]
    assert record["raw"]["render"]["size_bytes"] > 0
    assert record["raw"]["render"]["content_type"] == "image/png"
    assert (_run_dir(tmp_path) / record["raw"]["render"]["path"]).is_file()
    assert record["author"]["source_prompt"]["name"] == MIN_AUTHOR_REFINE_PROMPT.name
    assert record["author"]["source_prompt"]["version"]
    assert len(record["maturity_proposals"]) == 11
    proposal = record["maturity_proposals"][0]
    assert {
        "parameter_path",
        "direction",
        "loss",
        "scene_sha256",
        "render_rgb_sha256",
        "render_rgb_encoding",
        "render_width",
        "render_height",
    } <= set(proposal)
    assert "render_sha256" not in proposal
    assert record["draws"]["total_candidate_draw_count"] == 12
    assert record["acceptance"]["accepted"] is True
    assert record["acceptance"]["rejected_reason"] is None

    # 公开面不得出现完整 patch、候选 scene 或私有路径。
    public_text = json.dumps(
        {
            "trace": update["trace"],
            "patch_evidence": update["patch_evidence"],
            "pending_patch_summary": update["pending_patch_summary"],
        },
        ensure_ascii=False,
    )
    assert "typed_patch" not in public_text
    assert "private/replay" not in public_text
    assert update["pending_replay_step"] is None
    assert len(update["replay_step_refs"]) == 1

    finalize_state = {
        **state,
        **update,
        "author_model": "fake:replay-author",
        "stop_reason": "bounded_mvp_complete",
    }
    registry = MinRendererRegistry(_MaturityRenderer)  # type: ignore[arg-type]
    nodes = make_min_nodes(LocalArtifactStore(tmp_path), registry, _FakeGateway())
    final = await nodes["finalize"](finalize_state)
    assert final["status"] == "completed"

    bundle_path = _run_dir(tmp_path) / "private" / "replay" / "bundle.json"
    bundle_bytes = bundle_path.read_bytes()
    bundle = json.loads(bundle_bytes)
    assert bundle["schema_version"] == "scene_mvp_replay_bundle_v1"
    assert bundle["step_count"] == 1
    assert bundle["steps"][0]["acceptance"]["accepted"] is True
    assert bundle["identity"]["model_refs"]["actual"] == ["fake:replay-author"]
    assert bundle["identity"]["model_refs"]["requested"] == [SHADER_GEN_MODEL_NAME]
    assert bundle["identity"]["prompts"]["refine"]["version"]
    assert bundle["identity"]["source_revision"]["status"] == "unavailable"
    assert bundle["config"]["patch_candidate_draw_budget"] == 12

    manifest = _read_json(_run_dir(tmp_path) / "final" / "manifest.json")
    summary = manifest["private_replay_bundle"]
    assert summary["schema_version"] == "scene_mvp_replay_bundle_v1"
    assert summary["sha256"] == sha256(bundle_bytes).hexdigest()
    assert summary["size_bytes"] == len(bundle_bytes)
    assert summary["step_count"] == 1
    assert summary["durability_status"] == "local_ignored"
    manifest_text = json.dumps(manifest, ensure_ascii=False)
    assert "typed_patch" not in manifest_text
    assert "private/replay/steps" not in manifest_text
    assert "candidate_scene" not in manifest_text


@pytest.mark.anyio
async def test_tie_step_records_rejection_and_keeps_incumbent(tmp_path) -> None:
    state = _anchor_state()
    update = await _author_and_branch(
        tmp_path, _FakeRenderer, _FakeGateway(_PATCH_JSON), state
    )
    assert update["current_best"] == state["current_best"]
    record = _read_json(_step_dir(tmp_path) / "record.json")
    assert record["acceptance"]["accepted"] is False
    assert record["acceptance"]["rejected_reason"] == "no_strict_loss_improvement"
    assert record["raw"] is not None
    assert record["matured"] is not None
    assert record["draws"]["total_candidate_draw_count"] == 12


@pytest.mark.anyio
async def test_duplicate_step_records_zero_draw_with_patch_draft(tmp_path) -> None:
    state = _anchor_state()
    registry = MinRendererRegistry(_FakeRenderer)  # type: ignore[arg-type]
    nodes = make_min_nodes(
        LocalArtifactStore(tmp_path), registry, _FakeGateway(_PATCH_JSON, _PATCH_JSON)
    )
    first = await nodes["author_refine"](state)
    fingerprint = first["pending_patch_summary"]["patch_fingerprint"]
    state = {
        **state,
        "refine_count": 1,
        "recent_rejected_patch_summaries": (
            {"patch_fingerprint": fingerprint, "rejected_reason": "x"},
        ),
    }
    duplicate = await nodes["author_refine"]({**state, "llm_call_count": 1})
    assert duplicate["pending_patch_summary"]["status"] == "duplicate"
    update = await nodes["render_and_evaluate"]({**state, **duplicate})

    assert update["render_count"] == 0
    record = _read_json(_step_dir(tmp_path, "refine-002") / "record.json")
    assert record["status"] == "duplicate"
    assert record["acceptance"]["duplicate_of_recent"] is True
    assert record["acceptance"]["rejected_reason"] == "duplicate_recent_patch"
    assert record["draws"]["total_candidate_draw_count"] == 0
    assert record["raw"] is None
    assert record["maturity_proposals"] == []
    assert record["typed_patch"]["value"]["id"] == "local_highlight"
    assert update["current_best"] == state["current_best"]


@pytest.mark.anyio
async def test_invalid_step_records_without_patch_draft(tmp_path) -> None:
    state = _anchor_state()
    update = await _author_and_branch(
        tmp_path, _FakeRenderer, _FakeGateway("not-a-json-patch"), state
    )
    record = _read_json(_step_dir(tmp_path) / "record.json")
    assert record["status"] == "invalid"
    assert record["typed_patch"] is None
    assert record["patch_ref"] is None
    assert record["acceptance"]["rejected_reason"] == "invalid_patch"
    assert record["draws"]["total_candidate_draw_count"] == 0
    assert not (_step_dir(tmp_path) / "patch.json").exists()
    assert update["current_best"] == state["current_best"]


@pytest.mark.anyio
async def test_renderer_failure_step_records_draw_error(tmp_path) -> None:
    state = _anchor_state()
    update = await _author_and_branch(
        tmp_path, _FailingRenderer, _FakeGateway(_PATCH_JSON), state
    )
    record = _read_json(_step_dir(tmp_path) / "record.json")
    assert record["acceptance"]["accepted"] is False
    assert record["acceptance"]["rejected_reason"] == "renderer_failed"
    assert record["raw"] is None
    assert record["draws"]["render_count_after"] == 1
    assert update["current_best"] == state["current_best"]


@pytest.mark.anyio
async def test_public_artifact_whitelist_rejects_replay_names(tmp_path) -> None:
    service = PngToShaderMinService(
        None,
        LocalArtifactStore(tmp_path),
        MinRendererRegistry(_FakeRenderer),  # type: ignore[arg-type]
    )
    for name in ("replay", "bundle", "private", "replay-bundle"):
        with pytest.raises(ValueError, match="不支持"):
            service.read_public_artifact(_RUN, name)


def test_replay_json_write_refuses_duplicate_step(tmp_path) -> None:
    run = LocalArtifactStore(tmp_path).start_run(_PROJECT, _RUN)
    payload = {"schema_version": "scene_mvp_replay_step_v1"}
    _write_replay_json_once(run, "private/replay/steps/refine-001/record.json", payload)
    with pytest.raises(RuntimeError, match="拒绝覆盖"):
        _write_replay_json_once(
            run, "private/replay/steps/refine-001/record.json", payload
        )


_REMOVE_GHOST_JSON = '{"operation":"remove","path":"/object/features","value":"ghost"}'


def _finalize_state(state: dict, update: dict) -> dict:
    return {**state, **update, "stop_reason": "bounded_mvp_complete"}


async def _finalize(tmp_path, finalize_state: dict) -> dict:
    registry = MinRendererRegistry(_MaturityRenderer)  # type: ignore[arg-type]
    nodes = make_min_nodes(LocalArtifactStore(tmp_path), registry, _FakeGateway())
    return await nodes["finalize"](finalize_state)


def test_verified_decode_rejects_tamper_and_bad_refs(tmp_path) -> None:
    run = LocalArtifactStore(tmp_path).start_run(_PROJECT, _RUN)
    path = "private/replay/steps/refine-001/record.json"
    ref = _write_replay_json_once(
        run, path, {"schema_version": "scene_mvp_replay_step_v1", "refine_count": 1}
    )
    data = run.read_bytes(path)
    decoded = decode_verified_replay_json(
        data,
        ref,
        expected_path=path,
        expected_schema_version="scene_mvp_replay_step_v1",
        expected_refine_count=1,
    )
    assert decoded["refine_count"] == 1

    tampered = data.replace(b'"refine_count":1', b'"refine_count":2')
    with pytest.raises(ValueError, match="sha256"):
        decode_verified_replay_json(
            tampered,
            ref,
            expected_path=path,
            expected_schema_version="scene_mvp_replay_step_v1",
            expected_refine_count=1,
        )
    with pytest.raises(ValueError, match="size_bytes"):
        decode_verified_replay_json(
            data + b" ",
            ref,
            expected_path=path,
            expected_schema_version="scene_mvp_replay_step_v1",
            expected_refine_count=1,
        )
    with pytest.raises(ValueError, match="路径"):
        decode_verified_replay_json(
            data,
            {**ref, "path": "private/replay/steps/refine-002/record.json"},
            expected_path=path,
            expected_schema_version="scene_mvp_replay_step_v1",
            expected_refine_count=1,
        )
    with pytest.raises(ValueError, match="private/replay"):
        decode_verified_replay_json(
            data,
            ref,
            expected_path="final/manifest.json",
            expected_schema_version="scene_mvp_replay_step_v1",
            expected_refine_count=1,
        )
    with pytest.raises(ValueError, match="schema_version"):
        decode_verified_replay_json(
            data,
            ref,
            expected_path=path,
            expected_schema_version="scene_mvp_replay_patch_v1",
            expected_refine_count=1,
        )
    with pytest.raises(ValueError, match="refine_count"):
        decode_verified_replay_json(
            data,
            ref,
            expected_path=path,
            expected_schema_version="scene_mvp_replay_step_v1",
            expected_refine_count=2,
        )
    with pytest.raises(ValueError, match="object"):
        decode_verified_replay_json(
            data,
            None,
            expected_path=path,
            expected_schema_version="scene_mvp_replay_step_v1",
            expected_refine_count=1,
        )
    list_path = "private/replay/steps/refine-001/list.json"
    list_ref = _write_replay_json_once(run, list_path, [1, 2])
    with pytest.raises(ValueError, match="object"):
        decode_verified_replay_json(
            run.read_bytes(list_path),
            list_ref,
            expected_path=list_path,
            expected_schema_version="scene_mvp_replay_step_v1",
            expected_refine_count=1,
        )


def test_replay_write_rejects_path_traversal(tmp_path) -> None:
    run = LocalArtifactStore(tmp_path).start_run(_PROJECT, _RUN)
    payload = {"schema_version": "scene_mvp_replay_step_v1"}
    with pytest.raises(ValueError, match="相对路径"):
        _write_replay_json_once(run, "../escape.json", payload)
    with pytest.raises(ValueError, match="相对路径"):
        _write_replay_json_once(run, "/abs/escape.json", payload)
    assert not (tmp_path / _PROJECT / "escape.json").exists()


def test_replay_render_reuse_reverifies_existing_file(tmp_path) -> None:
    run = LocalArtifactStore(tmp_path).start_run(_PROJECT, _RUN)
    png = b"\x89PNG\r\n\x1a\nsynthetic"
    ref = _write_replay_render(run, png)
    assert ref["size_bytes"] == len(png)
    assert ref["content_type"] == "image/png"
    assert _write_replay_render(run, png) == ref
    blob = bytearray(run.read_bytes(ref["path"]))
    blob[0] ^= 0xFF
    run.path_for(ref["path"]).write_bytes(bytes(blob))
    with pytest.raises(RuntimeError, match="hash/size"):
        _write_replay_render(run, png)


@pytest.mark.anyio
async def test_tampered_patch_draft_fails_closed(tmp_path) -> None:
    state = _anchor_state()
    registry = MinRendererRegistry(_MaturityRenderer)  # type: ignore[arg-type]
    nodes = make_min_nodes(
        LocalArtifactStore(tmp_path), registry, _FakeGateway(_PATCH_JSON)
    )
    refined = await nodes["author_refine"](state)
    draft_path = _step_dir(tmp_path) / "patch.json"
    draft_path.write_bytes(draft_path.read_bytes().replace(b'"add"', b'"rep"', 1))
    with pytest.raises(ValueError, match="sha256"):
        await nodes["render_and_evaluate"]({**state, **refined})


@pytest.mark.anyio
async def test_tampered_step_dir_fails_closed(tmp_path) -> None:
    state = _anchor_state()
    registry = MinRendererRegistry(_FakeRenderer)  # type: ignore[arg-type]
    nodes = make_min_nodes(
        LocalArtifactStore(tmp_path), registry, _FakeGateway(_PATCH_JSON)
    )
    refined = await nodes["author_refine"](state)
    tampered_step = {
        **refined["pending_replay_step"],
        "step_dir": "private/replay/../../final",
    }
    with pytest.raises(RuntimeError, match="refine_count"):
        await nodes["render_and_evaluate"](
            {**state, **refined, "pending_replay_step": tampered_step}
        )


@pytest.mark.anyio
async def test_finalize_rejects_tampered_record(tmp_path) -> None:
    state = _anchor_state()
    update = await _author_and_branch(
        tmp_path, _MaturityRenderer, _FakeGateway(_PATCH_JSON), state
    )
    record_path = _step_dir(tmp_path) / "record.json"
    record_path.write_bytes(
        record_path.read_bytes().replace(b'"accepted":true', b'"accepted":false')
    )
    with pytest.raises(ValueError, match="size_bytes"):
        await _finalize(tmp_path, _finalize_state(state, update))


@pytest.mark.anyio
async def test_finalize_rejects_tampered_render(tmp_path) -> None:
    state = _anchor_state()
    update = await _author_and_branch(
        tmp_path, _MaturityRenderer, _FakeGateway(_PATCH_JSON), state
    )
    record = _read_json(_step_dir(tmp_path) / "record.json")
    render_path = _run_dir(tmp_path) / record["raw"]["render"]["path"]
    blob = bytearray(render_path.read_bytes())
    blob[-1] ^= 0xFF
    render_path.write_bytes(bytes(blob))
    with pytest.raises(RuntimeError, match="hash/size"):
        await _finalize(tmp_path, _finalize_state(state, update))


@pytest.mark.anyio
async def test_finalize_without_replay_steps_writes_empty_bundle(tmp_path) -> None:
    state = _anchor_state()
    final = await _finalize(tmp_path, _finalize_state(state, {}))
    assert final["status"] == "completed"
    bundle = _read_json(_run_dir(tmp_path) / "private" / "replay" / "bundle.json")
    assert bundle["steps"] == []
    assert bundle["step_count"] == 0
    assert bundle["identity"]["model_refs"]["requested"] == []
    assert bundle["identity"]["model_refs"]["actual"] == []
    assert (
        bundle["identity"]["model_refs"]["identity_source"]
        == "unavailable_no_refine_step"
    )
    manifest = _read_json(_run_dir(tmp_path) / "final" / "manifest.json")
    assert manifest["private_replay_bundle"]["step_count"] == 0


@pytest.mark.anyio
async def test_multi_step_bundle_aggregates_steps_and_model_refs(tmp_path) -> None:
    state = _anchor_state()
    registry = MinRendererRegistry(_MaturityRenderer)  # type: ignore[arg-type]
    nodes = make_min_nodes(
        LocalArtifactStore(tmp_path), registry, _FakeGateway(_PATCH_JSON, _PATCH_JSON)
    )
    first = await nodes["author_refine"](state)
    update1 = await nodes["render_and_evaluate"]({**state, **first})
    state2 = {**state, **first, **update1}
    second = await nodes["author_refine"](state2)
    assert second["refine_count"] == 2
    update2 = await nodes["render_and_evaluate"]({**state2, **second})
    assert len(update2["replay_step_refs"]) == 2

    final = await _finalize(tmp_path, _finalize_state(state2, {**second, **update2}))
    assert final["status"] == "completed"
    bundle = _read_json(_run_dir(tmp_path) / "private" / "replay" / "bundle.json")
    assert bundle["step_count"] == 2
    assert [step["refine_count"] for step in bundle["steps"]] == [1, 2]
    assert bundle["identity"]["model_refs"]["actual"] == ["fake:replay-author"]
    assert bundle["identity"]["model_refs"]["requested"] == [SHADER_GEN_MODEL_NAME]
    manifest = _read_json(_run_dir(tmp_path) / "final" / "manifest.json")
    assert manifest["private_replay_bundle"]["step_count"] == 2


@pytest.mark.anyio
async def test_repaired_step_records_source_and_output_prompt(tmp_path) -> None:
    state = _anchor_state()
    await _author_and_branch(
        tmp_path,
        _MaturityRenderer,
        _FakeGateway("not-a-json-patch", _PATCH_JSON),
        state,
    )
    record = _read_json(_step_dir(tmp_path) / "record.json")
    author = record["author"]
    assert author["repaired"] is True
    assert author["call_count"] == 2
    assert author["source_prompt"]["name"] == MIN_AUTHOR_REFINE_PROMPT.name
    assert author["output_prompt"]["name"] == MIN_AUTHOR_REPAIR_PROMPT.name
    draft = _read_json(_step_dir(tmp_path) / "patch.json")
    assert draft["author"]["output_prompt"]["name"] == MIN_AUTHOR_REPAIR_PROMPT.name


@pytest.mark.anyio
async def test_patch_apply_failed_has_consistent_author_stage(tmp_path) -> None:
    state = _anchor_state()
    update = await _author_and_branch(
        tmp_path, _FakeRenderer, _FakeGateway(_REMOVE_GHOST_JSON), state
    )
    record = _read_json(_step_dir(tmp_path) / "record.json")
    draft = _read_json(_step_dir(tmp_path) / "patch.json")
    assert record["status"] == "invalid"
    assert record["acceptance"]["rejected_reason"] == "patch_apply_failed"
    assert str(record["author"]["error_code"]).startswith("patch_apply_failed:")
    assert draft["author"]["error_code"] == record["author"]["error_code"]
    assert record["typed_patch"]["value"] == "ghost"
    assert update["current_best"] == state["current_best"]


@pytest.mark.anyio
async def test_progress_event_and_final_result_do_not_leak_replay(tmp_path) -> None:
    state = _anchor_state()
    registry = MinRendererRegistry(_MaturityRenderer)  # type: ignore[arg-type]
    nodes = make_min_nodes(
        LocalArtifactStore(tmp_path), registry, _FakeGateway(_PATCH_JSON)
    )
    refined = await nodes["author_refine"](state)
    update = await nodes["render_and_evaluate"]({**state, **refined})
    events = [
        _build_progress_event(
            node_name=node_name,
            update=node_update,
            budgets={"render_budget": 64},
            trace_tail=tuple(node_update.get("trace", ()))[-3:],
            elapsed_ms=1.0,
            duration_ms=1.0,
        )
        for node_name, node_update in (
            ("author_refine", refined),
            ("render_and_evaluate", update),
        )
    ]
    final = await _finalize(tmp_path, _finalize_state(state, {**refined, **update}))
    public_text = json.dumps(
        {"events": events, "final_result": final["final_result"]},
        ensure_ascii=False,
        default=str,
    )
    assert "private/replay" not in public_text
    assert "typed_patch" not in public_text
    assert "candidate_scene" not in public_text


@pytest.mark.anyio
async def test_repeated_finalize_refuses_bundle_overwrite(tmp_path) -> None:
    state = _anchor_state()
    update = await _author_and_branch(
        tmp_path, _MaturityRenderer, _FakeGateway(_PATCH_JSON), state
    )
    finalize_state = _finalize_state(state, update)
    final = await _finalize(tmp_path, finalize_state)
    assert final["status"] == "completed"
    # bundle.json 与 step 产物同为 write-once：重复 finalize fail-closed。
    with pytest.raises(RuntimeError, match="拒绝覆盖"):
        await _finalize(tmp_path, finalize_state)
