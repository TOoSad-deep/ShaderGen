from pathlib import Path

from backend.app.services.engine_rollout_runtime import build_engine_rollout_runtime
from shaderforge.store import LocalArtifactStore


def test_runtime_is_direct_only_and_uses_isolated_stores(tmp_path: Path) -> None:
    public = LocalArtifactStore(tmp_path / "public")
    runtime = build_engine_rollout_runtime(
        public_store=public,
        private_attempt_root=tmp_path / "private",
    )
    assert runtime.artifacts.public_store.base_root == public.base_root
    assert runtime.artifacts.private_attempt_store.restrictive_permissions is True
    assert runtime.coordinator._direct_attempt_limit == 3
