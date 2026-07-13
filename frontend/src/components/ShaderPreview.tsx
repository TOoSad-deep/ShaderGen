import { useEffect, useRef, useState } from "react";

const VERTEX_SHADER = `
attribute vec2 a_position;
varying vec2 v_uv;

void main() {
  v_uv = a_position * 0.5 + 0.5;
  gl_Position = vec4(a_position, 0.0, 1.0);
}
`;

function compileShader(gl: WebGLRenderingContext, type: number, source: string) {
  const shader = gl.createShader(type);
  if (!shader) throw new Error("无法创建 shader。");

  gl.shaderSource(shader, source);
  gl.compileShader(shader);

  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    const message = gl.getShaderInfoLog(shader) ?? "shader 编译失败。";
    gl.deleteShader(shader);
    throw new Error(message);
  }

  return shader;
}

function createProgram(gl: WebGLRenderingContext, fragmentSource: string) {
  const vertexShader = compileShader(gl, gl.VERTEX_SHADER, VERTEX_SHADER);
  const fragmentShader = compileShader(gl, gl.FRAGMENT_SHADER, fragmentSource);
  const program = gl.createProgram();
  if (!program) throw new Error("无法创建 WebGL program。");

  gl.attachShader(program, vertexShader);
  gl.attachShader(program, fragmentShader);
  gl.linkProgram(program);

  gl.deleteShader(vertexShader);
  gl.deleteShader(fragmentShader);

  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    const message = gl.getProgramInfoLog(program) ?? "program 链接失败。";
    gl.deleteProgram(program);
    throw new Error(message);
  }

  return program;
}

interface ShaderPreviewProps {
  imageUrl: string | null;
  glsl: string;
  onRendered?: (image: Blob) => void;
}

export function ShaderPreview({ imageUrl, glsl, onRendered }: ShaderPreviewProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!imageUrl || !glsl || !canvasRef.current) return;

    let cancelled = false;
    let frame = 0;
    let reported = false;

    const image = new Image();
    image.onload = () => {
      if (cancelled || !canvasRef.current) return;

      const canvas = canvasRef.current;
      canvas.width = image.naturalWidth;
      canvas.height = image.naturalHeight;

      const gl = canvas.getContext("webgl");
      if (!gl) {
        setError("当前浏览器不支持 WebGL。");
        return;
      }

      try {
        const program = createProgram(gl, glsl);
        const position = gl.getAttribLocation(program, "a_position");
        const resolution = gl.getUniformLocation(program, "u_resolution");
        const time = gl.getUniformLocation(program, "u_time");
        const imageUniform = gl.getUniformLocation(program, "u_image");

        const buffer = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
        gl.bufferData(
          gl.ARRAY_BUFFER,
          new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]),
          gl.STATIC_DRAW,
        );

        const texture = gl.createTexture();
        gl.activeTexture(gl.TEXTURE0);
        gl.bindTexture(gl.TEXTURE_2D, texture);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, image);

        const startedAt = performance.now();
        const render = () => {
          if (cancelled) return;

          gl.viewport(0, 0, canvas.width, canvas.height);
          gl.useProgram(program);
          gl.enableVertexAttribArray(position);
          gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
          gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
          gl.uniform2f(resolution, canvas.width, canvas.height);
          gl.uniform1f(time, (performance.now() - startedAt) / 1000);
          gl.uniform1i(imageUniform, 0);
          gl.drawArrays(gl.TRIANGLES, 0, 6);
          if (!reported && onRendered) {
            reported = true;
            canvas.toBlob((blob) => {
              if (blob && !cancelled) onRendered(blob);
            }, "image/png");
          }
          frame = requestAnimationFrame(render);
        };

        setError("");
        render();
      } catch (err) {
        setError(err instanceof Error ? err.message : "WebGL 渲染失败。");
      }
    };
    image.onerror = () => setError("图片加载失败。");
    image.src = imageUrl;

    return () => {
      cancelled = true;
      cancelAnimationFrame(frame);
    };
  }, [imageUrl, glsl, onRendered]);

  return (
    <div className="preview">
      <canvas ref={canvasRef} />
      {error ? <p className="error">{error}</p> : null}
    </div>
  );
}
