"""固定 fake LLM + 真实 Chromium 的 shadow suite 全链验收."""

from __future__ import annotations

import json
import os
from datetime import date
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pytest
import yaml
from PIL import Image

from agent.app.services.layerplan_glsl_shadow_suite import (
    current_direct_glsl_implementation_identity,
    load_shadow_suite_gate,
    load_shadow_suite_manifest,
    run_shadow_suite,
    verify_shadow_suite_report,
)
from shaderforge.program_spec import canonical_json
from shaderforge.rendering import PlaywrightWebGL1Renderer
from tests.integration_tests.test_layerplan_glsl_shadow_full_chain import (
    CANVAS,
    _FakeGateway,
)


def _png(gray: int) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (CANVAS, CANVAS), (gray, gray, gray)).save(buffer, "PNG")
    return buffer.getvalue()


def _write_protocol(root: Path) -> tuple[Path, Path]:
    images = root / "images"
    images.mkdir(parents=True)
    samples = []
    instruction = "match"
    for sample_id, gray in (("gray_a", 128), ("gray_b", 144)):
        image = _png(gray)
        (images / f"{sample_id}.png").write_bytes(image)
        samples.append(
            {
                "sample_id": sample_id,
                "reference_path": f"images/{sample_id}.png",
                "reference_sha256": sha256(image).hexdigest(),
                "reference_content_type": "image/png",
                "instruction": instruction,
                "instruction_sha256": sha256(instruction.encode()).hexdigest(),
            }
        )
    manifest_path = root / "manifest.yaml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "layerplan_glsl_shadow_manifest_v2",
                "experiment_id": "layerplan_glsl_shadow_ab_v1",
                "run_classification": "independent_experiment",
                "report_schema_version": "layerplan_glsl_shadow_ab_report_v1",
                "frozen_at": date(2026, 7, 27),
                "rounds": 2,
                "arm_order_schedule": ["AB", "BA"],
                "config": {
                    "direct_author_llm_budget": 2,
                    "compile_budget_per_arm": 2,
                    "draw_budget_per_arm": 2,
                    "refine_budget_per_arm": 0,
                    "plan_llm_budget": 1,
                    "canvas_width": CANVAS,
                    "canvas_height": CANVAS,
                },
                "samples": samples,
                "implementation_identity": (
                    current_direct_glsl_implementation_identity()
                ),
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    manifest = load_shadow_suite_manifest(manifest_path)
    gate_path = root / "gate.yaml"
    gate_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "layerplan_glsl_shadow_gate_v2",
                "experiment_id": "layerplan_glsl_shadow_ab_v1",
                "run_classification": "independent_experiment",
                "report_schema_version": "layerplan_glsl_shadow_ab_report_v1",
                "metric_version": "min_scene_composite_v3",
                "frozen_at": date(2026, 7, 27),
                "manifest_sha256": manifest.manifest_sha256,
                "implementation_identity_sha256": (
                    manifest.implementation_identity_sha256
                ),
                "config_fingerprints": {
                    "AB": manifest.config_fingerprint_for_order("AB"),
                    "BA": manifest.config_fingerprint_for_order("BA"),
                },
                "primary_endpoint": {
                    "metric": "current_best_loss",
                    "comparison": "paired_per_sample_per_round",
                    "improvement_margin": 0.005,
                    "min_improved_sample_ratio": 0.75,
                },
                "order_effect": {"rule": "consistent_direction_required"},
                "inconclusive_policy": {
                    "counting": "inconclusive_counts_against_arm_b",
                    "max_inconclusive_sample_ratio": 0.25,
                },
                "human_review": {
                    "required": True,
                    "min_arm_b_preference_rate": 0.5,
                    "tie_policy": "ties_not_counted_as_b_win",
                },
                "durability_requirement": "durable_required_for_promotion",
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return manifest_path, gate_path


@pytest.mark.anyio
async def test_shadow_suite_runs_cross_balanced_real_renderer_chain(
    tmp_path: Path,
) -> None:
    manifest_path, gate_path = _write_protocol(tmp_path / "protocol")
    manifest = load_shadow_suite_manifest(manifest_path)
    gate = load_shadow_suite_gate(gate_path, manifest=manifest)
    output_root = tmp_path / "evidence"

    async with PlaywrightWebGL1Renderer() as renderer:
        suite_dir = await run_shadow_suite(
            gateway=_FakeGateway(),
            renderer=renderer,
            manifest=manifest,
            gate=gate,
            output_root=output_root,
        )

    report = verify_shadow_suite_report(
        suite_dir, manifest=manifest, gate=gate
    )
    assert len(report["runs"]) == 4
    assert report["schedule"]["arm_orders"] == ["AB", "BA"]
    assert report["aggregate"]["automatic_gate"]["outcome"] == "not_supported"
    assert report["aggregate"]["promotion_decision"] == (
        "no_go_automatic_gate_failed"
    )
    assert len(list(output_root.glob("shadow-*"))) == 5

    tampered = json.loads(
        (suite_dir / "suite_report.json").read_text(encoding="utf-8")
    )
    tampered.pop("suite_report_sha256")
    tampered["runs"][0]["order_label"] = "BA"
    tampered["suite_report_sha256"] = sha256(
        canonical_json(tampered).encode("utf-8")
    ).hexdigest()
    tampered_body = dict(tampered)
    tampered_body.pop("suite_report_sha256")
    tampered_id = "shadow-suite-" + sha256(
        canonical_json(tampered_body).encode("utf-8")
    ).hexdigest()[:12]
    tampered_dir = output_root / tampered_id
    tampered_dir.mkdir(mode=0o700)
    tampered_report = tampered_dir / "suite_report.json"
    tampered_report.write_text(canonical_json(tampered) + "\n", encoding="utf-8")
    os.chmod(tampered_report, 0o600)
    with pytest.raises(ValueError, match="order_label 与冻结 schedule"):
        verify_shadow_suite_report(tampered_dir, manifest=manifest, gate=gate)

    referenced = output_root / report["runs"][0]["run_id"] / "report.json"
    referenced.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        verify_shadow_suite_report(suite_dir, manifest=manifest, gate=gate)
