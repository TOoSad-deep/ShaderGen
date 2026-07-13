precision mediump float;

varying vec2 v_uv;
uniform sampler2D u_image;
uniform vec2 u_resolution;
uniform float u_time;

float gaussian(vec2 point, vec2 center, vec2 sigma) {
  vec2 q = (point - center) / sigma;
  return exp(-0.5 * dot(q, q));
}

vec2 axisCoordinates(vec2 point, vec2 center, vec2 axis) {
  vec2 delta = point - center;
  return vec2(dot(delta, axis), dot(delta, vec2(-axis.y, axis.x)));
}

void main() {
  vec2 referenceSize = vec2(505.0, 527.0);
  vec2 uv = vec2(v_uv.x, 1.0 - v_uv.y);
  vec2 pixel = uv * referenceSize;

  vec2 center = vec2(251.5, 243.3);
  vec2 radius = vec2(213.5, 211.5);
  vec2 ellipse = (pixel - center) / radius;
  float radial = length(ellipse);
  vec2 safeResolution = max(u_resolution, vec2(1.0));
  float referencePixel = 0.5 * (
    referenceSize.x / safeResolution.x +
    referenceSize.y / safeResolution.y
  );
  float aa = 1.2 * referencePixel / min(radius.x, radius.y);
  float bodyMask = 1.0 - smoothstep(1.0 - aa, 1.0 + aa, radial);

  vec3 background = vec3(0.996);
  float shadow =
    0.20 * gaussian(pixel, vec2(455.0, 290.0), vec2(29.0, 80.0)) +
    0.22 * gaussian(pixel, vec2(290.0, 462.0), vec2(90.0, 32.0));
  vec3 color = mix(
    background,
    vec3(0.96, 0.32, 0.55),
    clamp(shadow * (1.0 - bodyMask), 0.0, 0.32)
  );

  float gradient = clamp(
    0.58 + 0.18 * ellipse.x + 0.35 * ellipse.y,
    0.0,
    1.0
  );
  vec3 deepPink = vec3(0.97, 0.02, 0.22);
  vec3 hotPink = vec3(1.0, 0.47, 0.65);
  vec3 palePink = vec3(1.0, 0.96, 0.98);
  vec3 body = mix(deepPink, hotPink, smoothstep(0.05, 0.68, gradient));
  body = mix(body, palePink, smoothstep(0.48, 1.02, gradient));

  float darkLobe = gaussian(pixel, vec2(78.0, 178.0), vec2(92.0, 132.0));
  float rightLobe = gaussian(pixel, vec2(405.0, 250.0), vec2(175.0, 175.0));
  float milkLobe = gaussian(pixel, vec2(252.0, 423.0), vec2(178.0, 78.0));
  body = mix(body, vec3(0.92, 0.02, 0.19), 0.22 * darkLobe);
  body = mix(body, vec3(1.0, 0.60, 0.75), 0.20 * rightLobe);
  body = mix(body, vec3(1.0, 0.975, 0.987), 0.68 * milkLobe);
  float innerHaze =
    gaussian(pixel, vec2(270.0, 330.0), vec2(165.0, 135.0)) *
    (1.0 - smoothstep(0.70, 0.96, radial));
  body = mix(body, vec3(1.0, 0.80, 0.70), 0.09 * innerHaze);

  float softRim = smoothstep(0.79, 0.995, radial);
  float outerStroke = smoothstep(0.958, 0.993, radial);
  float edgeLight = clamp(
    0.55 + 0.25 * ellipse.x + 0.40 * ellipse.y,
    0.0,
    1.0
  );
  vec3 edgeColor = mix(
    vec3(0.91, 0.02, 0.21),
    vec3(1.0, 0.63, 0.77),
    edgeLight
  );
  body = mix(body, edgeColor, clamp(0.24 * softRim + 0.58 * outerStroke, 0.0, 0.78));
  float innerSheen =
    smoothstep(0.84, 0.89, radial) *
    (1.0 - smoothstep(0.92, 0.965, radial));
  body = mix(
    body,
    vec3(1.0, 0.86, 0.94),
    clamp(0.20 * innerSheen * (0.30 + 0.70 * edgeLight), 0.0, 0.20)
  );

  vec2 highlightAxis = vec2(0.819, -0.574);
  vec2 left = axisCoordinates(pixel, vec2(153.0, 87.0), highlightAxis);
  vec2 leftGlowDistance = left / vec2(70.0, 32.0);
  vec2 leftCoreDistance = left / vec2(48.0, 15.0);
  float leftGlowRadial = (radial - 0.88) / 0.065;
  float leftCoreRadial = (radial - 0.885) / 0.027;
  float leftGlow =
    exp(-0.5 * dot(leftGlowDistance, leftGlowDistance)) *
    exp(-0.5 * leftGlowRadial * leftGlowRadial);
  float leftCore =
    exp(-0.5 * dot(leftCoreDistance, leftCoreDistance)) *
    exp(-0.5 * leftCoreRadial * leftCoreRadial);
  body = mix(body, vec3(1.0, 0.72, 0.92), 0.42 * leftGlow);
  body = mix(body, vec3(1.0), clamp(0.94 * leftCore, 0.0, 0.96));

  vec2 right = axisCoordinates(pixel, vec2(368.0, 382.0), highlightAxis);
  vec2 rightGlowDistance = right / vec2(88.0, 43.0);
  vec2 rightCoreDistance = right / vec2(65.0, 20.0);
  float rightGlowRadial = (radial - 0.86) / 0.075;
  float rightCoreRadial = (radial - 0.855) / 0.026;
  float rightGlow =
    exp(-0.5 * dot(rightGlowDistance, rightGlowDistance)) *
    exp(-0.5 * rightGlowRadial * rightGlowRadial);
  float rightCore =
    exp(-0.5 * dot(rightCoreDistance, rightCoreDistance)) *
    exp(-0.5 * rightCoreRadial * rightCoreRadial);
  body = mix(body, vec3(1.0, 0.93, 0.97), 0.48 * rightGlow);
  body = mix(body, vec3(1.0), clamp(0.99 * rightCore, 0.0, 0.995));

  float topRimRadial = (radial - 0.985) / 0.018;
  float topRim =
    gaussian(pixel, vec2(252.0, 45.0), vec2(145.0, 25.0)) *
    exp(-0.5 * topRimRadial * topRimRadial);
  body = mix(body, vec3(1.0, 0.68, 0.80), 0.42 * topRim);

  color = mix(color, body, bodyMask);
  gl_FragColor = vec4(clamp(color, 0.0, 1.0), 1.0);
}
