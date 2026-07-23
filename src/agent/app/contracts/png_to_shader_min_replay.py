"""scene_mvp 私有 Patch replay bundle v1 的契约常量与稳定 hash 工具。.

所有 replay 内容只写入 run 目录 `private/replay/` 子树，永不进入 trace、
进度事件、DB 摘要或公开 Artifact 白名单；公开 manifest 只持有
`build_bundle_summary()` 产出的 hash 级摘要。
"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

REPLAY_BUNDLE_SCHEMA_VERSION = "scene_mvp_replay_bundle_v1"
REPLAY_STEP_SCHEMA_VERSION = "scene_mvp_replay_step_v1"
REPLAY_PATCH_SCHEMA_VERSION = "scene_mvp_replay_patch_v1"
REPLAY_ROOT = "private/replay"
REPLAY_STEPS_DIR = f"{REPLAY_ROOT}/steps"
REPLAY_RENDERS_DIR = f"{REPLAY_ROOT}/renders"
REPLAY_BUNDLE_PATH = f"{REPLAY_ROOT}/bundle.json"
REPLAY_DURABILITY_STATUS = "local_ignored"


def replay_step_dir_name(refine_count: int) -> str:
    """返回 zero-padded 的稳定 step 目录名，拒绝非法序号."""
    if isinstance(refine_count, bool) or not isinstance(refine_count, int):
        raise ValueError("refine_count 必须是整数。")
    if refine_count < 1 or refine_count > 999:
        raise ValueError("refine_count 超出可编码范围。")
    return f"refine-{refine_count:03d}"


def canonical_json_sha256(value: Any) -> str:
    """对 JSON 可序列化值生成排版无关的稳定 SHA-256."""
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


def bytes_sha256(data: bytes) -> str:
    """返回二进制内容的 SHA-256."""
    return sha256(data).hexdigest()


def decode_verified_replay_json(
    data: bytes,
    ref: Any,
    *,
    expected_path: str,
    expected_schema_version: str,
    expected_refine_count: int,
) -> dict[str, Any]:
    """按引用 fail-closed 解码私有 replay JSON.

    精确校验：引用必须是携带 path/sha256/size_bytes 的 object，路径必须等于
    调用方按 refine_count 推导的 ``private/replay/`` 内预期路径（拒绝路径
    注入与跨 step 错位），内容的 size 与 SHA-256 必须匹配引用，解码结果
    必须是 schema_version 与 refine_count 均符合预期的 JSON object。
    """
    if not expected_path.startswith(f"{REPLAY_ROOT}/"):
        raise ValueError(f"replay 预期路径必须位于 {REPLAY_ROOT}/ 内。")
    if not isinstance(ref, dict):
        raise ValueError("replay 引用必须是 object。")
    if ref.get("path") != expected_path:
        raise ValueError(
            f"replay 引用路径与预期不符：{ref.get('path')!r} != {expected_path!r}"
        )
    size = ref.get("size_bytes")
    if isinstance(size, bool) or not isinstance(size, int) or size != len(data):
        raise ValueError("replay 内容 size_bytes 与引用不符。")
    if ref.get("sha256") != bytes_sha256(data):
        raise ValueError("replay 内容 sha256 与引用不符。")
    payload = json.loads(data.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("replay JSON 必须是 object。")
    if payload.get("schema_version") != expected_schema_version:
        raise ValueError(
            "replay schema_version 不符："
            f"{payload.get('schema_version')!r} != {expected_schema_version!r}"
        )
    if payload.get("refine_count") != expected_refine_count:
        raise ValueError("replay refine_count 与预期 step 不符。")
    return payload


def build_bundle_summary(
    *,
    bundle_sha256: str,
    size_bytes: int,
    step_count: int,
) -> dict[str, Any]:
    """生成允许进入公开 manifest/账本的安全摘要，不含路径或内容."""
    return {
        "schema_version": REPLAY_BUNDLE_SCHEMA_VERSION,
        "sha256": bundle_sha256,
        "size_bytes": size_bytes,
        "step_count": step_count,
        "durability_status": REPLAY_DURABILITY_STATUS,
    }


__all__ = [
    "REPLAY_BUNDLE_PATH",
    "REPLAY_BUNDLE_SCHEMA_VERSION",
    "REPLAY_DURABILITY_STATUS",
    "REPLAY_PATCH_SCHEMA_VERSION",
    "REPLAY_RENDERS_DIR",
    "REPLAY_ROOT",
    "REPLAY_STEPS_DIR",
    "REPLAY_STEP_SCHEMA_VERSION",
    "build_bundle_summary",
    "bytes_sha256",
    "canonical_json_sha256",
    "decode_verified_replay_json",
    "replay_step_dir_name",
]
