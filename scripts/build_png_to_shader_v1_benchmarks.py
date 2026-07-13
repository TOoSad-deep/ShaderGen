"""用标准库生成 PNG to Shader V1 的确定性 benchmark 图片."""

from __future__ import annotations

import math
import struct
import sys
import zlib
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "benchmarks/png_to_shader_v1/images"
WIDTH = 192
HEIGHT = 192
Color = tuple[float, float, float]


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _mix(a: Color, b: Color, amount: float) -> Color:
    t = _clamp(amount)
    return tuple(a[i] * (1.0 - t) + b[i] * t for i in range(3))  # type: ignore[return-value]


def _over(base: Color, layer: Color, alpha: float) -> Color:
    return _mix(base, layer, alpha)


def _smoothstep(edge0: float, edge1: float, value: float) -> float:
    if edge0 == edge1:
        return float(value >= edge1)
    t = _clamp((value - edge0) / (edge1 - edge0))
    return t * t * (3.0 - 2.0 * t)


def _circle_mask(x: float, y: float, cx: float, cy: float, radius: float) -> float:
    distance = math.hypot(x - cx, y - cy)
    return 1.0 - _smoothstep(radius - 0.006, radius + 0.006, distance)


def _ellipse_mask(
    x: float, y: float, cx: float, cy: float, rx: float, ry: float
) -> float:
    distance = math.hypot((x - cx) / rx, (y - cy) / ry)
    return 1.0 - _smoothstep(0.985, 1.015, distance)


def _gaussian(x: float, y: float, cx: float, cy: float, sx: float, sy: float) -> float:
    qx = (x - cx) / sx
    qy = (y - cy) / sy
    return math.exp(-0.5 * (qx * qx + qy * qy))


def _rounded_rect_mask(
    x: float,
    y: float,
    cx: float,
    cy: float,
    half_width: float,
    half_height: float,
    radius: float,
) -> float:
    qx = abs(x - cx) - (half_width - radius)
    qy = abs(y - cy) - (half_height - radius)
    outside = math.hypot(max(qx, 0.0), max(qy, 0.0))
    inside = min(max(qx, qy), 0.0)
    distance = outside + inside - radius
    return 1.0 - _smoothstep(-0.006, 0.006, distance)


def _solid_circle(x: float, y: float) -> Color:
    return _over((1.0, 1.0, 1.0), (0.94, 0.18, 0.32), _circle_mask(x, y, 0.5, 0.5, 0.32))


def _ellipse_gradient(x: float, y: float) -> Color:
    mask = _ellipse_mask(x, y, 0.5, 0.5, 0.35, 0.25)
    color = _mix((0.20, 0.40, 0.95), (0.65, 0.92, 1.0), (y - 0.25) / 0.5)
    return _over((0.98, 0.98, 1.0), color, mask)


def _shadow_disk(x: float, y: float) -> Color:
    color = (1.0, 1.0, 1.0)
    shadow = _gaussian(x, y, 0.52, 0.22, 0.25, 0.07) * 0.25
    color = _over(color, (0.20, 0.24, 0.34), shadow)
    mask = _circle_mask(x, y, 0.5, 0.53, 0.30)
    disk = _mix((0.22, 0.66, 0.88), (0.64, 0.90, 0.96), y)
    return _over(color, disk, mask)


def _rimmed_disk(x: float, y: float) -> Color:
    dx = x - 0.5
    dy = y - 0.5
    distance = math.hypot(dx, dy) / 0.33
    mask = 1.0 - _smoothstep(0.985, 1.015, distance)
    base = _mix((0.35, 0.12, 0.68), (0.78, 0.40, 0.94), 0.5 + 0.5 * y)
    rim = _smoothstep(0.78, 0.99, distance) * mask
    direction = _clamp(0.55 - 0.7 * dx + 0.5 * dy)
    base = _over(base, (0.96, 0.72, 1.0), rim * direction * 0.8)
    return _over((0.98, 0.97, 1.0), base, mask)


def _arc_highlight_orb(x: float, y: float) -> Color:
    dx = x - 0.5
    dy = y - 0.5
    radius = math.hypot(dx, dy)
    mask = _circle_mask(x, y, 0.5, 0.5, 0.34)
    base = _mix((0.08, 0.42, 0.72), (0.32, 0.82, 0.94), 0.5 + 0.8 * y)
    if radius > 1e-6:
        direction_dot = (-0.65 * dx + 0.76 * dy) / radius
    else:
        direction_dot = -1.0
    radial = math.exp(-0.5 * ((radius - 0.285) / 0.022) ** 2)
    angular = _smoothstep(0.66, 0.90, direction_dot)
    highlight = radial * angular * mask
    base = _over(base, (1.0, 1.0, 1.0), highlight * 0.95)
    return _over((0.96, 0.99, 1.0), base, mask)


def _color_lobes(x: float, y: float) -> Color:
    mask = _circle_mask(x, y, 0.5, 0.5, 0.34)
    base = _mix((0.46, 0.30, 0.88), (0.94, 0.42, 0.58), 0.5 + 0.7 * (y - x))
    warm = _gaussian(x, y, 0.31, 0.69, 0.18, 0.16)
    cool = _gaussian(x, y, 0.68, 0.31, 0.20, 0.17)
    base = _over(base, (1.0, 0.30, 0.42), warm * 0.65)
    base = _over(base, (0.20, 0.72, 1.0), cool * 0.55)
    return _over((0.99, 0.98, 1.0), base, mask)


def _rounded_rect_glow(x: float, y: float) -> Color:
    color = (0.035, 0.045, 0.085)
    glow = _gaussian(x, y, 0.5, 0.5, 0.33, 0.22)
    color = _over(color, (0.10, 0.78, 0.94), glow * 0.45)
    mask = _rounded_rect_mask(x, y, 0.5, 0.5, 0.30, 0.20, 0.07)
    body = _mix((0.05, 0.28, 0.54), (0.15, 0.88, 0.92), 0.35 + 0.65 * y)
    return _over(color, body, mask)


def _neon_ring(x: float, y: float) -> Color:
    radius = math.hypot(x - 0.5, y - 0.5)
    glow = math.exp(-0.5 * ((radius - 0.31) / 0.055) ** 2)
    core = math.exp(-0.5 * ((radius - 0.31) / 0.012) ** 2)
    color = _over((0.015, 0.012, 0.05), (0.36, 0.04, 0.72), glow * 0.72)
    return _over(color, (0.35, 0.95, 1.0), core)


def _dual_disks(x: float, y: float) -> Color:
    color = (0.98, 0.98, 1.0)
    left = _circle_mask(x, y, 0.38, 0.5, 0.22)
    right = _circle_mask(x, y, 0.62, 0.5, 0.22)
    color = _over(color, (0.98, 0.30, 0.42), left * 0.88)
    return _over(color, (0.18, 0.52, 0.98), right * 0.82)


def _pink_gel(x: float, y: float) -> Color:
    color = (1.0, 1.0, 1.0)
    shadow = _gaussian(x, y, 0.53, 0.12, 0.30, 0.09)
    color = _over(color, (1.0, 0.58, 0.76), shadow * 0.22)

    dx = x - 0.5
    dy = y - 0.51
    radius = math.hypot(dx, dy)
    mask = 1.0 - _smoothstep(0.394, 0.406, radius)
    base = _mix((1.0, 0.16, 0.40), (1.0, 0.82, 0.90), _clamp(0.45 - 0.65 * dy + 0.18 * dx))
    haze = _gaussian(x, y, 0.53, 0.42, 0.24, 0.20)
    deep = _gaussian(x, y, 0.29, 0.66, 0.18, 0.20)
    base = _over(base, (1.0, 0.94, 0.96), haze * 0.50)
    base = _over(base, (0.92, 0.02, 0.24), deep * 0.34)

    normalized_radius = radius / 0.40
    rim = _smoothstep(0.80, 0.99, normalized_radius) * mask
    rim_direction = _clamp(0.58 - 0.45 * dx + 0.55 * dy)
    base = _over(base, (1.0, 0.38, 0.58), rim * rim_direction * 0.58)

    if radius > 1e-6:
        upper_left_dot = (-0.68 * dx + 0.73 * dy) / radius
        lower_right_dot = (0.70 * dx - 0.71 * dy) / radius
    else:
        upper_left_dot = lower_right_dot = -1.0
    radial = math.exp(-0.5 * ((radius - 0.345) / 0.020) ** 2)
    upper_left = radial * _smoothstep(0.70, 0.94, upper_left_dot)
    lower_right = radial * _smoothstep(0.76, 0.95, lower_right_dot)
    base = _over(base, (1.0, 0.96, 0.99), upper_left * 0.95 * mask)
    base = _over(base, (1.0, 1.0, 1.0), lower_right * 0.88 * mask)
    return _over(color, base, mask)


CASES: dict[str, Callable[[float, float], Color]] = {
    "solid_circle": _solid_circle,
    "ellipse_gradient": _ellipse_gradient,
    "shadow_disk": _shadow_disk,
    "rimmed_disk": _rimmed_disk,
    "arc_highlight_orb": _arc_highlight_orb,
    "color_lobes": _color_lobes,
    "rounded_rect_glow": _rounded_rect_glow,
    "neon_ring": _neon_ring,
    "dual_disks": _dual_disks,
    "pink_gel": _pink_gel,
}


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    payload = kind + data
    return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload))


def _write_png(path: Path, render: Callable[[float, float], Color]) -> None:
    rows = bytearray()
    for row in range(HEIGHT):
        rows.append(0)
        y = 1.0 - (row + 0.5) / HEIGHT
        for column in range(WIDTH):
            x = (column + 0.5) / WIDTH
            color = render(x, y)
            rows.extend(round(_clamp(channel) * 255.0) for channel in color)
            rows.append(255)

    signature = b"\x89PNG\r\n\x1a\n"
    header = struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 6, 0, 0, 0)
    content = signature
    content += _png_chunk(b"IHDR", header)
    content += _png_chunk(b"IDAT", zlib.compress(bytes(rows), level=9))
    content += _png_chunk(b"IEND", b"")
    path.write_bytes(content)


def main() -> None:
    """重新生成全部 V1 benchmark PNG."""
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for case_id, render in CASES.items():
        _write_png(OUTPUT / f"{case_id}.png", render)
    sys.stdout.write(f"generated {len(CASES)} benchmark PNGs in {OUTPUT}\n")


if __name__ == "__main__":
    main()
