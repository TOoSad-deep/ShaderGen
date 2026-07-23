from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pytest
from pydantic import BaseModel

from agent.app.messages.structured_multimodal import (
    canonical_json,
    labeled_image_parts,
    multimodal_human_message,
    text_part,
)


class _Mode(str, Enum):
    ACTIVE = "active"


@dataclass(frozen=True)
class _Payload:
    count: int
    mode: _Mode


class _Model(BaseModel):
    name: str


def test_canonical_json_is_stable_for_supported_structured_values() -> None:
    payload = {
        "z": (_Payload(count=2, mode=_Mode.ACTIVE),),
        "a": _Model(name="测试"),
    }

    assert canonical_json(payload) == (
        '{"a":{"name":"测试"},"z":[{"count":2,"mode":"active"}]}'
    )


def test_canonical_json_rejects_non_finite_values() -> None:
    with pytest.raises(ValueError):
        canonical_json({"loss": float("nan")})


def test_multimodal_parts_keep_label_image_and_payload_order() -> None:
    parts = [
        text_part("scene", {"version": 3}),
        *labeled_image_parts("target", b"png", "image/png"),
    ]

    message = multimodal_human_message(parts)

    assert message.content == parts
    assert parts[0]["text"].endswith('<scene>{"version":3}</scene>')
    assert parts[1] == {"type": "text", "text": "target："}
    assert parts[2]["type"] == "image_url"
