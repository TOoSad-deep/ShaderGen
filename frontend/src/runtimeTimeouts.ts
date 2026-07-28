import { parse } from "yaml";

import timeoutYaml from "../../src/shaderforge/config/runtime_timeouts.yaml?raw";

const QUALITY_PRESETS = ["fast", "balanced", "high", "manual"] as const;

type QualityPreset = (typeof QUALITY_PRESETS)[number];

interface RuntimeTimeouts {
  generationRequestSeconds: Record<QualityPreset, number>;
  progressRequestSeconds: number;
  progressObservationGraceSeconds: number;
}

function record(value: unknown, field: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${field} 必须是 object`);
  }
  return value as Record<string, unknown>;
}

function exactKeys(
  value: Record<string, unknown>,
  expected: readonly string[],
  field: string,
) {
  const actual = Object.keys(value).sort();
  const wanted = [...expected].sort();
  if (actual.join("\0") !== wanted.join("\0")) {
    throw new Error(`${field} 字段必须恰好为 ${wanted.join(", ")}`);
  }
}

function positiveSeconds(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) {
    throw new Error(`${field} 必须是正有限数`);
  }
  return value;
}

function loadFrontendTimeouts(): RuntimeTimeouts {
  const root = record(parse(timeoutYaml), "root");
  const frontend = record(root.frontend, "frontend");
  exactKeys(
    frontend,
    [
      "generation_request_seconds",
      "progress_request_seconds",
      "progress_observation_grace_seconds",
    ],
    "frontend",
  );
  const generation = record(
    frontend.generation_request_seconds,
    "frontend.generation_request_seconds",
  );
  exactKeys(
    generation,
    QUALITY_PRESETS,
    "frontend.generation_request_seconds",
  );
  const generationRequestSeconds = Object.fromEntries(
    QUALITY_PRESETS.map((preset) => [
      preset,
      positiveSeconds(
        generation[preset],
        `frontend.generation_request_seconds.${preset}`,
      ),
    ]),
  ) as Record<QualityPreset, number>;
  return {
    generationRequestSeconds,
    progressRequestSeconds: positiveSeconds(
      frontend.progress_request_seconds,
      "frontend.progress_request_seconds",
    ),
    progressObservationGraceSeconds: positiveSeconds(
      frontend.progress_observation_grace_seconds,
      "frontend.progress_observation_grace_seconds",
    ),
  };
}

export const RUNTIME_TIMEOUTS = Object.freeze(loadFrontendTimeouts());
