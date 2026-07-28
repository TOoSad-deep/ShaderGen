# Decisions

## Current decisions

1. Layered Direct is the only product execution path.
2. `ShaderProgramSpecV1` remains the canonical execution IR produced by the
   deterministic Layered compiler.
3. A parent run may create at most three fresh, isolated Direct attempts.
4. Layered authoring has no static uniform-count ceiling; the actual local
   WebGL1 renderer is the final capacity check.
5. Model output cannot provide hashes, identity, receipt or attestation fields.
6. Child details remain private; only the selected parent bundle is public.

Superseded decisions are retained only in `docs/archive/`.
