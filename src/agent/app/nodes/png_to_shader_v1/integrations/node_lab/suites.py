"""PNG-to-Shader V1 的固定 Node Lab suite 注册表."""

from __future__ import annotations

from pathlib import Path

from nodelab.suites import SuiteRegistry

ROOT = Path(__file__).resolve().parents[7]
SUITE_ROOT = ROOT / "benchmarks/node_lab/png_to_shader_v1"


def build_png_to_shader_v1_suite_registry() -> SuiteRegistry:
    """构造只接受仓库内固定 manifest 的 V1 allowlist."""
    return SuiteRegistry(
        {
            "node_lab_ai_off_v1": SUITE_ROOT / "manifest.yaml",
            "node_lab_scenario_ai_off_v1": SUITE_ROOT / "scenario-manifest.yaml",
            "node_lab_renderer_warm_ai_off_v1": SUITE_ROOT
            / "renderer-warm-manifest.yaml",
        }
    )


__all__ = ["build_png_to_shader_v1_suite_registry"]
