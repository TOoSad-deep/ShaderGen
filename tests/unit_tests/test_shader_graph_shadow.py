from types import SimpleNamespace

import pytest

from agent.app.nodes.png_to_shader_min.shader_graph_shadow import (
    ShaderGraphShadowRunner,
    adapt_min_scene_to_shader_graph,
)
from shaderforge.scene import (
    Canvas,
    Feature,
    MinScene,
    Primitive,
    RadialColorField,
    SceneObject,
    SolidColorField,
)


def _scene(*features: Feature, radial: bool = False) -> MinScene:
    color_field = (
        RadialColorField(
            model="radial",
            inner=(1.0, 0.8, 0.9),
            outer=(0.8, 0.2, 0.4),
        )
        if radial
        else SolidColorField(model="solid", color=(0.9, 0.3, 0.5))
    )
    return MinScene(
        canvas=Canvas(width=64, height=64, background=(1.0, 1.0, 1.0)),
        object=SceneObject(
            primitive=Primitive(
                type="ellipse",
                center=(0.1, -0.1),
                axes=(0.55, 0.4),
            ),
            color_field=color_field,
            features=features,
        ),
    )


def test_adapter_maps_supported_min_scene_to_one_layer_graph() -> None:
    graph = adapt_min_scene_to_shader_graph(
        _scene(
            Feature(
                id="shadow",
                type="shadow",
                center=(0.12, -0.18),
                axes=(0.5, 0.2),
                color=(0.1, 0.1, 0.1),
                intensity=0.4,
            ),
            Feature(
                id="rim",
                type="rim",
                axes=(0.5, 0.1),
                color=(1.0, 1.0, 1.0),
                intensity=0.8,
            ),
            radial=True,
        )
    )

    assert graph.schema_version == "shader_graph_v1"
    assert len(graph.layers) == 2
    shadow_layer, layer = graph.layers
    assert shadow_layer.id == "legacy_shadow_0"
    assert shadow_layer.shape.kind == "ellipse"
    assert shadow_layer.fill.kind == "solid"
    assert [effect.kind for effect in shadow_layer.effects] == ["glow"]
    assert layer.id == "legacy_body"
    assert layer.shape.kind == "ellipse"
    assert layer.fill.kind == "radial"
    assert [effect.kind for effect in layer.effects] == ["rim"]


@pytest.mark.anyio
async def test_shadow_reports_unsupported_without_starting_renderer() -> None:
    calls = 0

    def factory():
        nonlocal calls
        calls += 1
        raise AssertionError("unsupported scene 不应启动 Renderer")

    runner = ShaderGraphShadowRunner(factory)  # type: ignore[arg-type]
    result = await runner.run(
        _scene(
            Feature(
                id="arc",
                type="polar_arc",
                axes=(0.4, 0.1),
            )
        )
    )

    assert result.summary["status"] == "unsupported"
    assert result.summary["unsupported_features"] == ["polar_arc"]
    assert result.fragment_source is None
    assert result.image_bytes is None
    assert calls == 0


class _FakePrepared:
    width = 64
    height = 64

    def __init__(self) -> None:
        self.close_calls = 0
        self.render_calls = 0

    async def render_uniforms(self, _values, *, capture_png=False):
        self.render_calls += 1
        assert capture_png is True
        return SimpleNamespace(
            success=True,
            image_bytes=b"shadow-png",
            duration_ms=1.25,
        )

    async def close(self) -> None:
        self.close_calls += 1


class _FakeRenderer:
    def __init__(self) -> None:
        self.prepared = _FakePrepared()
        self.prepare_calls = 0
        self.close_calls = 0

    async def prepare(self, source, width, height, uniform_schema):
        assert "void main()" in source
        assert (width, height) == (64, 64)
        assert uniform_schema == {}
        self.prepare_calls += 1
        return self.prepared

    async def close(self) -> None:
        self.close_calls += 1


@pytest.mark.anyio
async def test_shadow_compiles_renders_and_closes_resources() -> None:
    renderer = _FakeRenderer()
    runner = ShaderGraphShadowRunner(lambda: renderer)  # type: ignore[arg-type]

    result = await runner.run(_scene())

    assert result.summary["status"] == "rendered"
    assert result.summary["renderer_path"] == ("compiled_graph_program_cache_v1")
    assert result.summary["layer_count"] == 1
    assert result.summary["primitive_count"] == 1
    assert result.summary["compile_count"] == 1
    assert result.summary["cache_hit_count"] == 0
    assert result.summary["cache_size"] == 1
    assert result.summary["shader_graph"]["layers"][0]["id"] == "legacy_body"
    assert result.image_bytes == b"shadow-png"
    assert result.fragment_source is not None
    assert renderer.prepare_calls == 1
    assert renderer.prepared.render_calls == 1
    assert renderer.prepared.close_calls == 1
    assert renderer.close_calls == 1
