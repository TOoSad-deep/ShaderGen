"""Validate the live documentation and current-only source boundary."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []


def _require(condition: bool, message: str) -> None:
    if not condition:
        ERRORS.append(message)


def _main() -> int:
    for path in (
        "README.md",
        "PROGRESS.md",
        "docs/ARCHITECTURE.md",
        "docs/FEATURES.md",
        "backend/README.md",
        "frontend/README.md",
    ):
        _require((ROOT / path).is_file(), f"missing live document: {path}")

    features = (ROOT / "docs/FEATURES.md").read_text(encoding="utf-8")
    _require("F09" in features and "`active`" in features, "F09 must remain active")
    _require(
        "LayeredShaderSpecV1" in features and "ShaderProgramSpecV1" in features,
        "F09 must describe the current Layered execution path",
    )

    removed = (
        "src/agent/app/graphs/png_to_shader_min_graph.py",
        "src/agent/app/services/png_to_shader_min.py",
        "src/agent/app/services/layerplan_glsl_shadow.py",
        "src/shaderforge/dsl/document.py",
        "src/nodelab/runner.py",
        "backend/app/core/engine_policy.py",
        "backend/app/services/production_shadow.py",
    )
    for path in removed:
        _require(not (ROOT / path).is_file(), f"legacy live file remains: {path}")

    current = (
        "src/agent/app/contracts/layer_plan.py",
        "src/agent/app/contracts/layerplan_glsl_direct.py",
        "src/agent/app/graphs/layerplan_glsl_direct.py",
        "src/agent/app/graphs/layerplan_glsl_direct_studio.py",
        "src/agent/app/nodes/layered_direct/authors.py",
        "src/agent/app/services/layerplan_glsl_direct.py",
        "src/agent/app/states/layerplan_glsl_direct.py",
        "src/shaderforge/layered_spec/compiler.py",
        "src/shaderforge/program_spec/models.py",
        "backend/app/services/engine_rollout.py",
        "backend/app/services/engine_rollout_graph.py",
    )
    for path in current:
        _require((ROOT / path).is_file(), f"current pipeline file missing: {path}")

    langgraph_config = (ROOT / "langgraph.json").read_text(encoding="utf-8")
    _require(
        "layerplan_glsl_direct_studio.py:graph" in langgraph_config,
        "safe LayerPlan Direct Studio adapter must be registered",
    )

    app_source = (ROOT / "frontend/src/App.tsx").read_text(encoding="utf-8")
    _require('href="/lab"' not in app_source, "removed Node Lab link remains")
    _require("场景 JSON" not in app_source, "removed scene response copy remains")

    package_json = (ROOT / "frontend/package.json").read_text(encoding="utf-8")
    _require('"test:unit"' not in package_json, "empty frontend test gate remains")

    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    _require(
        "run_memory_postgres_test.py" not in makefile,
        "deleted memory test script remains in Makefile",
    )

    plan_prompt = (
        ROOT / "src/agent/app/prompts/layerplan_visual_analysis_v1.yaml"
    ).read_text(encoding="utf-8")
    _require("shadow 实验" not in plan_prompt, "current LayerPlan prompt says shadow")

    if ERRORS:
        sys.stdout.write("docs-check failed:\n")
        for error in ERRORS:
            sys.stdout.write(f"- {error}\n")
        return 1
    sys.stdout.write("docs-check passed\n")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
