/** 与后端 models.py 对应的接口类型（契约的编译期镜像） */

export type TaskStatus = "pending" | "running" | "done" | "failed";

export interface TaskSummary {
  task_id: string;
  filename: string;
  status: TaskStatus;
  /** 转换选项（图片保留方式与图片任务的规范串，如 link+transcribe，上传时选定） */
  options: string;
  image_count: number | null;
  char_count: number | null;
  elapsed_ms: number | null;
  cache_hit: boolean | null;
  created_at: string;
}

/** 上传时选定的转换选项（图片保留方式 + 图片任务，与库参数同名同义） */
export interface UploadOptions {
  images: "link" | "embed" | null;
  transcribe: boolean;
  describe: boolean;
  prompt: string;   // 自定义提示词（空串 = 未使用）
  system: string;   // 自定义系统提示词（空串 = 内置通用骨架）
}

export interface TaskDetail extends TaskSummary {
  size_bytes: number;
  markdown: string | null;
  error: string | null;
  /** 自定义提示词原文（上传时填写的即时图片任务） */
  prompt: string;
  /** 自定义系统提示词（空 = 内置通用骨架） */
  system: string;
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
  /** 支持扩展名全量清单（含点号、已排序） */
  formats: string[];
}
