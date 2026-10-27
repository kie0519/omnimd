/** fetch 封装：全部端点 + AbortController + 错误归一 */

import type {
  FormatsResponse,
  HealthResponse,
  TaskCreated,
  TaskDetail,
  TaskListResponse,
  UploadOptions,
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
  options: UploadOptions,
  signal?: AbortSignal
): Promise<TaskCreated> {
  const body = new FormData();
  body.append("file", file);
  if (options.images) body.append("images", options.images);
  body.append("transcribe", String(options.transcribe));
  body.append("describe", String(options.describe));
  if (options.prompt) body.append("prompt", options.prompt);
  if (options.system) body.append("system", options.system);
  return request(`/api/tasks`, { method: "POST", body, signal });
}

/** 提示词工作台的模板卡片（GET /api/prompt/templates） */
export interface PromptTemplate {
  name: string;
  label: string;
  blurb: string;
  prompt: string;
  system: string;   // 任务自带系统提示词（内置母本 / 任务文件 frontmatter）
}

export function fetchPromptTemplates(signal?: AbortSignal): Promise<{
  templates: PromptTemplate[];
  general_system: string;   // 内置通用骨架原文（「以骨架为起点」的数据源）
}> {
  return request(`/api/prompt/templates`, { signal });
}

/** 流式生成的收尾帧（delta 帧见 runPrompt 回调） */
export interface PromptRunDone {
  markdown: string;
  elapsed_ms: number;
  usage: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
  } | null;   // 端点未返回用量时为 null
}

/**
 * 对一张图流式生成（工作台动作：不进任务队列与历史）。
 * SSE 帧逐段回调 onDelta；done 帧解析后 resolve，error 帧拒绝。
 * 中止：传 AbortController 的 signal（生成按钮变停止）。
 */
export async function runPrompt(
  file: File,
  prompt: string,
  system: string,
  onDelta: (text: string) => void,
  signal?: AbortSignal
): Promise<PromptRunDone> {
  const body = new FormData();
  body.append("file", file);
  body.append("prompt", prompt);
  if (system) body.append("system", system);
  let resp: Response;
  try {
    resp = await fetch(`/api/prompt/run`, { method: "POST", body, signal });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new NetworkError("服务连接中断");
  }
  if (!resp.ok && resp.status >= 500)
    throw new NetworkError(`服务异常（${resp.status}）`);
  if (!resp.ok) {
    let detail = `请求失败（${resp.status}）`;
    try {
      const j = await resp.json();
      if (j?.detail) detail = j.detail;
    } catch {
      /* 保留缺省文案 */
    }
    throw new Error(detail);
  }
  // 逐帧解析 SSE：按空行分帧，取 data: 行的 JSON
  const reader = resp.body?.getReader();
  if (!reader) throw new NetworkError("服务连接中断");
  const decoder = new TextDecoder();
  let buffer = "";
  let done: PromptRunDone | null = null;
  for (;;) {
    const { value, done: finished } = await reader.read();
    if (finished) break;
    buffer += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      for (const line of frame.split("\n")) {
        if (!line.startsWith("data: ")) continue;
        let payload: any;
        try {
          payload = JSON.parse(line.slice(6));
        } catch {
          // 畸形帧跳过——按流中断收尾（done 缺失时下方统一报「生成中断」）
          continue;
        }
        if (payload.error !== undefined) throw new Error(String(payload.error));
        if (payload.done) {
          done = {
            markdown: payload.markdown,
            elapsed_ms: payload.elapsed_ms,
            usage: payload.usage ?? null,
          };
        } else if (typeof payload.delta === "string") {
          onDelta(payload.delta);
        }
      }
    }
  }
  if (!done) throw new Error("生成中断，请重试");
  return done;
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
