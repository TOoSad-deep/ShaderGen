# Rendering

`PlaywrightWebGL1Renderer` validates the fixed WebGL1 contract, prepares and
links a program, uploads typed uniforms, draws pixels and returns a signed
execution receipt. Actual local compile/link/draw is the final capacity check.
