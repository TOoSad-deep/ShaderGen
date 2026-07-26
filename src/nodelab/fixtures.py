"""Node Lab 版本化 Fixture Registry."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any, Literal

from pydantic import Field, field_validator

from nodelab.models import (
    Identifier,
    NodeLabError,
    NodeLabModel,
    ensure_json_object,
)


class FixtureDefinition(NodeLabModel):
    """一个绑定节点、版本和期望结果的不可变 Fixture."""

    schema_version: Literal["node_lab_fixture_v1"] = "node_lab_fixture_v1"
    fixture_id: Identifier
    node_id: Identifier
    fixture_version: Identifier
    input_state: dict[str, Any] = Field(default_factory=dict)
    output_patch: dict[str, Any] = Field(default_factory=dict)
    expected_outcome: Literal["success", "rejected", "stopped"] = "success"
    next_action: str | None = None
    tags: list[Identifier] = Field(default_factory=list)

    @field_validator("input_state", "output_patch")
    @classmethod
    def validate_json_fields(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Fixture 必须是可冻结、可计算 hash 的 JSON object."""
        return ensure_json_object(value)

    @property
    def content_sha256(self) -> str:
        """计算不包含派生 hash 字段的稳定内容摘要."""
        payload = json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256(payload).hexdigest()


class FixtureRegistry:
    """按 fixture_id 显式查询并验证节点绑定."""

    def __init__(self, fixtures: list[FixtureDefinition] | None = None) -> None:
        """登记初始 Fixture 并拒绝重复 id."""
        self._by_id: dict[str, FixtureDefinition] = {}
        for fixture in fixtures or []:
            self.register(fixture)

    def register(self, fixture: FixtureDefinition) -> None:
        """登记一个不可变 Fixture."""
        if fixture.fixture_id in self._by_id:
            raise ValueError(f"Fixture id 重复：{fixture.fixture_id}。")
        self._by_id[fixture.fixture_id] = fixture

    def get(self, fixture_id: str, *, node_id: str) -> FixtureDefinition:
        """读取 Fixture 并防止跨节点误用."""
        try:
            fixture = self._by_id[fixture_id]
        except KeyError as exc:
            raise NodeLabError(
                "fixture_not_found",
                "未找到请求的 Node Lab Fixture。",
                stage="fixture_resolution",
                node_id=node_id,
                details={"fixture_id": fixture_id},
            ) from exc
        if fixture.node_id != node_id:
            raise NodeLabError(
                "fixture_node_mismatch",
                "Fixture 与目标节点不匹配。",
                stage="fixture_resolution",
                node_id=node_id,
                details={
                    "fixture_id": fixture_id,
                    "fixture_node_id": fixture.node_id,
                },
            )
        return fixture

    def list_for_node(self, node_id: str) -> tuple[FixtureDefinition, ...]:
        """按 id 排序返回一个节点的全部 Fixture."""
        return tuple(
            sorted(
                (
                    fixture
                    for fixture in self._by_id.values()
                    if fixture.node_id == node_id
                ),
                key=lambda fixture: fixture.fixture_id,
            )
        )
