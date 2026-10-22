/** fetch 封装：全部端点 + AbortController + 错误归一 */

import type {
  FormatsResponse,
  HealthResponse,
  TaskCreated,
  TaskDetail,
  TaskListResponse,
} from "./types";

const BASE = "";

/** 网络层失败（离线/断连）的统一信号；HTTP 4xx/5xx 由调用方按 detail 处理 */
export class NetworkError extends Error {}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(BASE + path, init);
  } catch (e) {
    // 主动中止不是故障，原样上抛（调用方以 AbortError 识别并静默丢弃）
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new NetworkError("服务连接中断");
  }
  if (!resp.ok && resp.status >= 500) {
    throw new NetworkError(`服务异常（${resp.status}）`);
  }
  if (!resp.ok) {
    let detail = `请求失败（${resp.status}）`;
    try {
      const body = await resp.json();
      if (body?.detail) detail = body.detail;
    } catch {
      /* 保留缺省文案 */
    }
    const err = new Error(detail) as Error & { status: number };
    err.status = resp.status;
    throw err;
  }
  return resp.json() as Promise<T>;
}

export function fetchTasks(
  q: string,
  limit = 50,
  signal?: AbortSignal
): Promise<TaskListResponse> {
  const params = new URLSearchParams({ q, limit: String(limit) });
  return request(`/api/tasks?${params}`, { signal });
}

export function fetchTaskDetail(
  taskId: string,
  signal?: AbortSignal
): Promise<TaskDetail> {
  return request(`/api/tasks/${taskId}`, { signal });
}

export function uploadTask(
  file: File,
  output: string,
  signal?: AbortSignal
): Promise<TaskCreated> {
  const body = new FormData();
  body.append("file", file);
  body.append("output", output);
  return request(`/api/tasks`, { method: "POST", body, signal });
}

export async function deleteTask(
  taskId: string,
  signal?: AbortSignal
): Promise<void> {
  // DELETE 204 无响应体，不解析 JSON；5xx 归一 NetworkError
  let resp: Response;
  try {
    resp = await fetch(`${BASE}/api/tasks/${taskId}`, { method: "DELETE", signal });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new NetworkError("服务连接中断");
  }
  if (resp.status >= 500) {
    throw new NetworkError(`服务异常（${resp.status}）`);
  }
  if (!resp.ok) {
    throw new Error(`删除失败（${resp.status}）`);
  }
}

export function checkHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return request(`/api/health`, { signal });
}

export function fetchFormats(signal?: AbortSignal): Promise<FormatsResponse> {
  return request(`/api/formats`, { signal });
}
