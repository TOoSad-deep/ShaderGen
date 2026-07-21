"""只读重放 F09 M6.2 measurement seed admission 选择点."""

from __future__ import annotations

import argparse
import json
from hashlib import sha256
from pathlib import Path

from shaderforge.benchmark.m6_2_diagnostics import M6_2StructureDiagnosticReport
from shaderforge.benchmark.m6_2_selector_replay import (
    build_m6_2_selector_replay_report,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite-output",
        type=Path,
        required=True,
        help="既有 M5 suite run 目录。",
    )
    parser.add_argument(
        "--artifact-root",
        type=Path,
        required=True,
        help="既有 PNG-to-Shader run Artifact 根目录。",
    )
    parser.add_argument(
        "--diagnostic-report",
        type=Path,
        required=True,
        help="已冻结的 capability-v2 diagnostic report。",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="新的 replay 报告路径；不得写入任何只读输入根。",
    )
    return parser


def main() -> int:
    """严格读取输入并独占创建 counterfactual replay 报告."""
    args = _parser().parse_args()
    suite_output = args.suite_output.resolve()
    artifact_root = args.artifact_root.resolve()
    diagnostic_path = args.diagnostic_report.resolve()
    output = args.output.resolve()
    forbidden_roots = (suite_output, artifact_root, diagnostic_path.parent)
    if any(output.is_relative_to(root) for root in forbidden_roots):
        raise ValueError("M6.2 replay 不得写入或覆盖任何只读输入根。")
    diagnostic_bytes = diagnostic_path.read_bytes()
    diagnostic = M6_2StructureDiagnosticReport.model_validate_json(
        diagnostic_bytes,
        strict=True,
    )
    report = build_m6_2_selector_replay_report(
        suite_root=suite_output,
        artifact_root=artifact_root,
        diagnostic=diagnostic,
        diagnostic_document_sha256=sha256(diagnostic_bytes).hexdigest(),
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
        "M6.2 seed admission replay: "
        f"cases={report.case_count} "
        f"baseline_accepted={report.baseline_accepted_count} "
        f"admission_rejected={report.admission_rejected_count} "
        "initial_preferred_unsupported_rejected="
        f"{report.initial_preferred_unsupported_rejected_count} "
        f"supported_admitted={report.supported_admitted_count} "
        f"report_hash={report.report_hash} output={output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
