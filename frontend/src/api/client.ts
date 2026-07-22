const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8088";

export interface ParsedApiError {
  status: number;
  message: string;
  fields: Record<string, unknown>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

export function resolveApiUrl(pathOrUrl: string, baseUrl = API_BASE_URL): string {
  return new URL(pathOrUrl, `${baseUrl}/`).toString();
}

export function apiFetch(
  pathOrUrl: string,
  init?: RequestInit,
  baseUrl = API_BASE_URL,
): Promise<Response> {
  return fetch(resolveApiUrl(pathOrUrl, baseUrl), init);
}

export async function parseApiError(
  response: Response,
  fallback: string,
): Promise<ParsedApiError> {
  let fields: Record<string, unknown> = {};
  let message = fallback;
  try {
    const body: unknown = JSON.parse(await response.text());
    if (isRecord(body)) {
      const detail = body.detail;
      const nestedFields = isRecord(detail)
        ? detail
        : isRecord(body.error)
          ? body.error
          : {};
      fields = { ...body, ...nestedFields };
      message =
        readString(nestedFields.message) ??
        readString(body.message) ??
        readString(detail) ??
        fallback;
    } else {
      message = readString(body) ?? fallback;
    }
  } catch {
    // 非 JSON 响应只显示稳定 fallback，避免把 HTML 或供应商原文暴露到页面。
  }
  return { status: response.status, message, fields };
}
