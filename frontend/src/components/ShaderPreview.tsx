import { useEffect, useRef, useState } from "react";

const VERTEX_SHADER = `
attribute vec2 a_position;
varying vec2 v_uv;

void main() {
  v_uv = a_position * 0.5 + 0.5;
  gl_Position = vec4(a_position, 0.0, 1.0);
}
`;

export interface ClientCompatibilityReport {
  status: "compatible" | "different" | "error";
  rmse?: number;
  message: string;
}

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

async function compareRenderBlobs(
  clientBlob: Blob,
  serverRenderUrl: string,
): Promise<ClientCompatibilityReport> {
  const response = await fetch(serverRenderUrl, { cache: "no-store" });
  if (!response.ok) throw new Error("无法读取服务端最终渲染图。");

  const [clientImage, serverImage] = await Promise.all([
    createImageBitmap(clientBlob),
    createImageBitmap(await response.blob()),
  ]);
  try {
    if (clientImage.width !== serverImage.width || clientImage.height !== serverImage.height) {
      return {
        status: "different",
        message: `客户端与服务端尺寸不一致：${clientImage.width}×${clientImage.height} / ${serverImage.width}×${serverImage.height}`,
      };
    }

    const canvas = document.createElement("canvas");
    canvas.width = clientImage.width;
    canvas.height = clientImage.height;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    if (!context) throw new Error("无法创建兼容性复核 Canvas。");

    context.drawImage(clientImage, 0, 0);
    const clientPixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.drawImage(serverImage, 0, 0);
    const serverPixels = context.getImageData(0, 0, canvas.width, canvas.height).data;

    let squaredError = 0;
    for (let index = 0; index < clientPixels.length; index += 4) {
      for (let channel = 0; channel < 3; channel += 1) {
        const delta = clientPixels[index + channel] - serverPixels[index + channel];
        squaredError += delta * delta;
      }
    }
    const rmse = Math.sqrt(squaredError / (canvas.width * canvas.height * 3)) / 255;
    const compatible = rmse <= 0.02;
    return {
      status: compatible ? "compatible" : "different",
      rmse,
      message: compatible
        ? `客户端与服务端渲染一致（RMSE ${rmse.toFixed(4)}）`
        : `客户端与服务端渲染差异超出容差（RMSE ${rmse.toFixed(4)}）`,
    };
  } finally {
    clientImage.close();
    serverImage.close();
  }
}

interface ShaderPreviewProps {
  imageUrl: string | null;
  glsl: string;
  staticMode?: boolean;
  renderWidth?: number | null;
  renderHeight?: number | null;
  serverRenderUrl?: string | null;
  onRendered?: (image: Blob) => void;
  onCompatibility?: (report: ClientCompatibilityReport) => void;
}

export function ShaderPreview({
  imageUrl,
  glsl,
  staticMode = false,
  renderWidth = null,
  renderHeight = null,
  serverRenderUrl = null,
  onRendered,
  onCompatibility,
}: ShaderPreviewProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!imageUrl || !glsl || !canvasRef.current) return;

    let cancelled = false;
    let frame = 0;
    let program: WebGLProgram | null = null;
    let buffer: WebGLBuffer | null = null;
    let texture: WebGLTexture | null = null;
    let gl: WebGLRenderingContext | null = null;

    const image = new Image();
    image.onload = () => {
      if (cancelled || !canvasRef.current) return;

      const canvas = canvasRef.current;
      canvas.width = staticMode && renderWidth ? renderWidth : image.naturalWidth;
      canvas.height = staticMode && renderHeight ? renderHeight : image.naturalHeight;
      gl = canvas.getContext("webgl", {
        alpha: false,
        antialias: false,
        depth: false,
        premultipliedAlpha: false,
        preserveDrawingBuffer: true,
        stencil: false,
      });
      if (!gl) {
        const message = "当前浏览器不支持 WebGL1。";
        setError(message);
        onCompatibility?.({ status: "error", message });
        return;
      }

      try {
        program = createProgram(gl, glsl);
        const position = gl.getAttribLocation(program, "a_position");
        const resolution = gl.getUniformLocation(program, "u_resolution");
        const time = gl.getUniformLocation(program, "u_time");
        const imageUniform = gl.getUniformLocation(program, "u_image");

        buffer = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
        gl.bufferData(
          gl.ARRAY_BUFFER,
          new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]),
          gl.STATIC_DRAW,
        );

        if (!staticMode) {
          texture = gl.createTexture();
          gl.activeTexture(gl.TEXTURE0);
          gl.bindTexture(gl.TEXTURE_2D, texture);
          gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
          gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
          gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
          gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
          gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, image);
        }

        const startedAt = performance.now();
        let reported = false;
        const render = () => {
          if (cancelled || !gl || !program) return;

          gl.viewport(0, 0, canvas.width, canvas.height);
          gl.useProgram(program);
          gl.enableVertexAttribArray(position);
          gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
          gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
          gl.uniform2f(resolution, canvas.width, canvas.height);
          gl.uniform1f(time, staticMode ? 0 : (performance.now() - startedAt) / 1000);
          if (!staticMode) gl.uniform1i(imageUniform, 0);
          gl.drawArrays(gl.TRIANGLES, 0, 6);
          gl.finish();

          if (!reported) {
            reported = true;
            canvas.toBlob((blob) => {
              if (!blob || cancelled) return;
              onRendered?.(blob);
              if (staticMode && serverRenderUrl && onCompatibility) {
                void compareRenderBlobs(blob, serverRenderUrl)
                  .then((report) => {
                    if (!cancelled) onCompatibility(report);
                  })
                  .catch((reason: unknown) => {
                    if (!cancelled) {
                      onCompatibility({
                        status: "error",
                        message: reason instanceof Error ? reason.message : "兼容性复核失败。",
                      });
                    }
                  });
              }
            }, "image/png");
          }
          if (!staticMode) frame = requestAnimationFrame(render);
        };

        setError("");
        render();
      } catch (reason) {
        const message = reason instanceof Error ? reason.message : "WebGL 渲染失败。";
        setError(message);
        onCompatibility?.({ status: "error", message });
      }
    };
    image.onerror = () => setError("图片加载失败。");
    image.src = imageUrl;

    return () => {
      cancelled = true;
      cancelAnimationFrame(frame);
      if (gl) {
        if (texture) gl.deleteTexture(texture);
        if (buffer) gl.deleteBuffer(buffer);
        if (program) gl.deleteProgram(program);
      }
    };
  }, [
    imageUrl,
    glsl,
    staticMode,
    renderWidth,
    renderHeight,
    serverRenderUrl,
    onRendered,
    onCompatibility,
  ]);

  return (
    <div className="preview">
      <canvas ref={canvasRef} />
      {error ? <p className="error">客户端编译失败：{error}</p> : null}
    </div>
  );
}
