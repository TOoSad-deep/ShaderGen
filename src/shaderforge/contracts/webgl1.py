"""WebGL1 无贴图 Fragment Shader 的通用运行契约."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class RenderContract:
    """WebGL1 无贴图 Fragment Shader 运行契约."""

    contract_id: str
    glsl_version: str
    precision: str
    varying_name: str
    required_uniforms: tuple[tuple[str, str], ...]
    fragment_output: str
    uv_origin: str
    texture_sampling_allowed: bool
    animation_enabled: bool
    max_long_side: int
    required_declarations: tuple[str, ...]
    forbidden_tokens: tuple[str, ...]

    def __post_init__(self) -> None:
        """拒绝无法安全执行的契约定义."""
        if not self.contract_id.strip():
            raise ValueError("contract_id 不能为空。")
        if self.max_long_side <= 0:
            raise ValueError("max_long_side 必须大于 0。")
        if self.uv_origin not in {"bottom_left", "top_left"}:
            raise ValueError("uv_origin 只能是 bottom_left 或 top_left。")
        if len({name for name, _ in self.required_uniforms}) != len(
            self.required_uniforms
        ):
            raise ValueError("required_uniforms 不能包含重复名称。")

    def to_dict(self) -> dict[str, Any]:
        """返回适合 Prompt、日志和 manifest 的普通字典."""
        return asdict(self)


WEBGL1_STATIC_NO_TEXTURE_V1 = RenderContract(
    contract_id="webgl1_static_no_texture_v1",
    glsl_version="GLSL_ES_100",
    precision="mediump",
    varying_name="v_uv",
    required_uniforms=(
        ("u_image", "sampler2D"),
        ("u_resolution", "vec2"),
        ("u_time", "float"),
    ),
    fragment_output="gl_FragColor",
    uv_origin="bottom_left",
    texture_sampling_allowed=False,
    animation_enabled=False,
    max_long_side=1024,
    required_declarations=(
        "precision mediump float;",
        "varying vec2 v_uv;",
        "uniform sampler2D u_image;",
        "uniform vec2 u_resolution;",
        "uniform float u_time;",
        "void main()",
    ),
    forbidden_tokens=(
        "#version",
        "texture2D",
        "textureCube",
        "texture(",
        "texelFetch",
        "mainImage",
    ),
)

__all__ = ["WEBGL1_STATIC_NO_TEXTURE_V1", "RenderContract"]
