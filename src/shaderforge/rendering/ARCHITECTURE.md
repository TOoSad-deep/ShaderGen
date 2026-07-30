# Rendering

`PlaywrightWebGL1Renderer` validates the fixed WebGL1 contract, prepares and
links a program, uploads typed uniforms, draws pixels and returns a signed
execution receipt. Actual local compile/link/draw is the final capacity check.

Prepared draws additionally set the compiler-owned `u_sg_role_mask_mode` on
every call. It is not a public uniform binding: `diagnostic_mode=0.0` is the
beauty default, while trusted callers can request compiler-defined diagnostic
mask modes without changing `uniform_schema` or `uniform_values`. Non-zero
diagnostic modes reject `receipt_spec_sha256`, so mask pixels can never receive
a Beauty Render execution receipt.
