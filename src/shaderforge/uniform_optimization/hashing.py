"""Canonical identities for optimizer components and active sets."""

from __future__ import annotations

from collections.abc import Iterable

from shaderforge.program_spec import canonical_json, sha256_hex_text
from shaderforge.uniform_optimization.models import FlatTunableComponent


def component_identity_sha256(component: FlatTunableComponent) -> str:
    """Hash a scalar coordinate including its trusted bounds and lattice anchor."""
    return sha256_hex_text(canonical_json(component.to_dict()))


def active_components_sha256(components: Iterable[FlatTunableComponent]) -> str:
    """Hash ordered active coordinates without exposing them in a public summary."""
    return sha256_hex_text(canonical_json([item.to_dict() for item in components]))
