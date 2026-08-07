# Artifact store

`LocalArtifactStore` provides path-safe run registration, restrictive private
permissions and atomic public final-bundle publication with recursive hash
verification.

Default callers keep the `project_id/run_id` layout. Current public parents use
`png_name/date/run_id`, while Direct private attempts use
`png_name/date/parent_run_id/attempt_id`. Every custom relative root must remain
inside its isolated artifact store and end with the validated run identifier. The
run-id index records that location in schema v2, while resolution remains compatible
with legacy v1 `project_id/run_id` entries. `claim_run` exclusively reserves a new
private run directory plus index and rejects either an existing directory or index;
failed claims remove only newly-created empty directories.
