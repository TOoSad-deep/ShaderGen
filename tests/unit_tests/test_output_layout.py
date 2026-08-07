from pathlib import Path

import pytest

from shaderforge.store.output_layout import (
    private_attempt_relative_path,
    public_run_relative_path,
    safe_png_name_slug,
    validate_output_date,
)


def test_safe_png_name_slug_keeps_chinese_and_discards_extension() -> None:
    assert safe_png_name_slug("玻璃 图标.v2.png") == "玻璃-图标-v2"


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        (None, "unnamed-png"),
        ("", "unnamed-png"),
        ("   .png", "unnamed-png"),
        ("___", "unnamed-png"),
        (".png", "unnamed-png"),
    ],
)
def test_safe_png_name_slug_uses_fallback_for_empty_names(
    filename: str | None,
    expected: str,
) -> None:
    assert safe_png_name_slug(filename) == expected


@pytest.mark.parametrize(
    "filename",
    ["bad\n.png"],
)
def test_safe_png_name_slug_rejects_controls(
    filename: str,
) -> None:
    with pytest.raises(ValueError, match="非法路径或控制字符"):
        safe_png_name_slug(filename)


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("../target.png", "target"),
        ("dir/file.png", "file"),
        ("dir\\file.png", "file"),
    ],
)
def test_safe_png_name_slug_uses_leaf_for_untrusted_client_paths(
    filename: str,
    expected: str,
) -> None:
    assert safe_png_name_slug(filename) == expected


def test_safe_png_name_slug_rejects_unsafe_fallback() -> None:
    with pytest.raises(ValueError, match="fallback"):
        safe_png_name_slug(None, fallback="../escape")


@pytest.mark.parametrize("value", ["2026-08-07", "2024-02-29"])
def test_validate_output_date_accepts_real_strict_dates(value: str) -> None:
    assert validate_output_date(value) == value


@pytest.mark.parametrize("value", ["2026-2-07", "2026-02-30", "2026/08/07", "today"])
def test_validate_output_date_rejects_invalid_dates(value: str) -> None:
    with pytest.raises(ValueError, match="output_date"):
        validate_output_date(value)


def test_public_and_private_output_layouts_are_relative_and_hierarchical() -> None:
    assert public_run_relative_path(
        "粉色气泡.png", "2026-08-07", "parent-1"
    ) == Path("粉色气泡/2026-08-07/parent-1")
    assert private_attempt_relative_path(
        "粉色气泡.png", "2026-08-07", "parent-1", "attempt-2"
    ) == Path("粉色气泡/2026-08-07/parent-1/attempt-2")


@pytest.mark.parametrize(
    ("factory", "args"),
    [
        (public_run_relative_path, ("image.png", "2026-08-07", "../run")),
        (
            private_attempt_relative_path,
            ("image.png", "2026-08-07", "parent", "attempt/id"),
        ),
    ],
)
def test_output_layout_rejects_unsafe_identifiers(
    factory: object,
    args: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError, match="非法字符"):
        factory(*args)  # type: ignore[operator]
