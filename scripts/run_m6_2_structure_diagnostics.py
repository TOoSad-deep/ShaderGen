"""只读生成 F09 M6.2 结构能力诊断报告."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from shaderforge.benchmark.m6_2_diagnostics import (
    build_m6_2_structure_diagnostic_report,
)
from shaderforge.benchmark.v2_dataset import load_v2_dataset_manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite-output",
        type=Path,
        required=True,
        help="包含 report.json 与 blind-review/ 的既有 M5 run 目录。",
    )
    parser.add_argument(
        "--artifact-root",
        type=Path,
        required=True,
        help="既有 PNG-to-Shader run Artifact 根目录。",
    )
    parser.add_argument(
        "--dataset-manifest",
        type=Path,
        required=True,
        help="包含 topology/instance/hole/required-layer 标签的 V2 Manifest。",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="新的诊断报告路径；不得指向既有 suite-output 内部。",
    )
    return parser


def main() -> int:
    """校验全部输入锚点并独占创建新的诊断报告."""
    args = _parser().parse_args()
    suite_output = args.suite_output.resolve()
    artifact_root = args.artifact_root.resolve()
    output = args.output.resolve()
    if output.is_relative_to(suite_output):
        raise ValueError("M6.2 诊断报告不得写入或覆盖既有 M5 run。")
    if output.is_relative_to(artifact_root):
        raise ValueError("M6.2 诊断报告不得写入或覆盖既有 run Artifact。")
    dataset = load_v2_dataset_manifest(args.dataset_manifest)
    report = build_m6_2_structure_diagnostic_report(
        suite_root=suite_output,
        artifact_root=artifact_root,
        dataset=dataset,
    )
    document = (
        json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as stream:
        stream.write(document)
    print(  # noqa: T201
        "M6.2 structure diagnostic: "
        f"cases={report.case_count} "
        f"initial_preferred={report.initial_preferred_count} "
        f"capability_unsupported={report.capability_unsupported_count} "
        "initial_preferred_capability_unsupported="
        f"{report.initial_preferred_capability_unsupported_count} "
        f"report_hash={report.report_hash} output={output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
