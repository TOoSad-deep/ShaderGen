precision mediump float;
varying vec2 v_uv;
uniform sampler2D u_image;
uniform vec2 u_resolution;
uniform float u_time;

void main() {
    gl_FragColor = texture2D(u_image, v_uv);
}
