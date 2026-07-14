precision mediump float;

varying vec2 v_uv;
uniform sampler2D u_image;
uniform vec2 u_resolution;
uniform float u_time;

float gaussian(float value, float width) {
    float scaled = value / width;
    return exp(-scaled * scaled);
}

void main() {
    float shortSide = min(u_resolution.x, u_resolution.y);
    vec2 p = (gl_FragCoord.xy - 0.5 * u_resolution) / shortSide;

    vec2 center = vec2(0.0, 0.0396);
    float radius = 0.420;
    vec2 q = p - center;
    float radialDistance = length(q);
    vec2 n = q / max(radialDistance, 0.00001);
    vec2 discPosition = q / radius;
    float normalizedRadius = radialDistance / radius;

    float aa = 1.25 / shortSide;
    float discMask = 1.0 - smoothstep(
        radius - aa,
        radius + aa,
        radialDistance
    );

    vec3 color = vec3(0.9961);

    vec2 shadowOffset = vec2(0.024, -0.055);
    float shadowDistance = length(q - shadowOffset) - radius * 0.975;
    float shadowBlur = gaussian(max(shadowDistance, 0.0), 0.075);
    float shadowDirection = smoothstep(
        -0.75,
        0.75,
        dot(n, normalize(vec2(0.68, -0.74)))
    );
    float outsideDisc = 1.0 - smoothstep(
        radius - aa * 2.0,
        radius + aa * 2.0,
        radialDistance
    );
    float shadowAlpha = shadowBlur * (0.035 + 0.145 * shadowDirection);
    shadowAlpha *= 1.0 - outsideDisc;
    color = mix(color, vec3(0.945, 0.535, 0.685), shadowAlpha);

    float gradientPosition = 0.48 + 0.15 * discPosition.x - 0.45 * discPosition.y;
    float gradientMix = smoothstep(-0.06, 1.06, gradientPosition);
    vec3 hotPink = vec3(0.9804, 0.1333, 0.3451);
    vec3 palePink = vec3(1.0000, 0.9490, 0.9765);
    vec3 body = mix(hotPink, palePink, gradientMix);

    vec2 bloomPoint = discPosition - vec2(0.30, -0.42);
    float lowerBloom = exp(-1.55 * dot(bloomPoint, bloomPoint));
    body = mix(body, vec3(1.0, 0.982, 0.990), 0.04 * lowerBloom);

    float darkSide = smoothstep(
        -0.35,
        0.82,
        dot(n, normalize(vec2(-0.72, 0.69)))
    );
    float lightSide = smoothstep(
        -0.55,
        0.80,
        dot(n, normalize(vec2(0.68, -0.74)))
    );
    vec3 rimColor = mix(
        vec3(0.957, 0.620, 0.735),
        vec3(0.855, 0.050, 0.235),
        darkSide
    );
    rimColor = mix(rimColor, vec3(0.986, 0.690, 0.810), 0.42 * lightSide);

    float outerRim = gaussian(normalizedRadius - 0.991, 0.012);
    float innerRim = gaussian(normalizedRadius - 0.958, 0.031);
    body = mix(body, rimColor, 0.76 * outerRim);
    body = mix(body, rimColor, 0.15 * innerRim * (0.30 + 0.70 * darkSide));

    float topLip = gaussian(normalizedRadius - 0.976, 0.010);
    topLip *= smoothstep(
        0.10,
        0.90,
        dot(n, normalize(vec2(-0.10, 0.995)))
    );
    body = mix(body, vec3(1.0, 0.91, 0.95), 0.40 * topLip);

    vec2 upperLightDirection = normalize(vec2(-0.55, 0.835));
    float upperLightAngle = dot(n, upperLightDirection);
    float upperGlow = gaussian(normalizedRadius - 0.900, 0.070);
    upperGlow *= smoothstep(0.82, 0.97, upperLightAngle);
    float upperCore = gaussian(normalizedRadius - 0.910, 0.035);
    upperCore *= smoothstep(0.90, 0.990, upperLightAngle);
    body = mix(body, vec3(1.0, 0.91, 0.96), 0.48 * upperGlow);
    body = mix(body, vec3(1.0), 0.98 * upperCore);

    vec2 lowerLightDirection = normalize(vec2(0.66, -0.75));
    float lowerLightAngle = dot(n, lowerLightDirection);
    float lowerGlow = gaussian(normalizedRadius - 0.835, 0.095);
    lowerGlow *= smoothstep(0.48, 0.92, lowerLightAngle);
    float lowerCore = gaussian(normalizedRadius - 0.855, 0.036);
    lowerCore *= smoothstep(0.58, 0.96, lowerLightAngle);
    body = mix(body, vec3(1.0, 0.965, 0.982), 0.58 * lowerGlow);
    body = mix(body, vec3(1.0), 0.86 * lowerCore);

    color = mix(color, body, discMask);
    gl_FragColor = vec4(color, 1.0);
}
