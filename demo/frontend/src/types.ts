/** 与后端 models.py 对应的接口类型（契约的编译期镜像） */

export type TaskStatus = "pending" | "running" | "done" | "failed";

export interface TaskSummary {
  task_id: string;
  filename: string;
  status: TaskStatus;
  /** 产出组成（body,url,base64,ocr,alt 规范序 CSV，上传时选定） */
  output: string;
  image_count: number | null;
  char_count: number | null;
  elapsed_ms: number | null;
  cache_hit: boolean | null;
  created_at: string;
}

export interface TaskDetail extends TaskSummary {
  size_bytes: number;
  markdown: string | null;
  error: string | null;
}

export interface TaskListResponse {
  items: TaskSummary[];
  total: number;
}

export interface TaskCreated {
  task_id: string;
  filename: string;
  status: string;
}

export interface HealthResponse {
  status: string;
}

export interface FormatsResponse {
  /** 支持扩展名全量清单（含点号、已排序），上传预检与格式提示共用 */
  formats: string[];
}
