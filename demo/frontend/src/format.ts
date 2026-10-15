/** 展示格式化：千分位 / 耗时人性化 / 文件大小 / 格式分组 */

export function formatNumber(n: number | null): string {
  if (n === null) return "—";
  return n.toLocaleString("zh-CN");
}

/** 4320 → "4.3s"；43200 → "43s"；72000 → "1m 12s" */
export function formatElapsed(ms: number | null): string {
  if (ms === null) return "—";
  if (ms < 10_000) return `${(ms / 1000).toFixed(1)}s`;
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** 格式看板的展示分组。支持的完整格式清单以 /api/formats 返回为准，
 *  这里只负责归类摆放。 */
const FORMAT_CATEGORIES: { label: string; exts: string[] }[] = [
  { label: "文档", exts: [".pdf", ".docx", ".docm", ".doc", ".odt", ".rtf", ".epub", ".html", ".htm", ".json", ".xml", ".zip", ".txt", ".md", ".rst", ".ipynb"] },
  { label: "表格", exts: [".xlsx", ".xlsm", ".xlsb", ".xls", ".csv", ".ods"] },
  { label: "演示", exts: [".ppt", ".pptx", ".pptm", ".ppsx", ".ppsm", ".odp"] },
  { label: "图片", exts: [".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".svg", ".ico"] },
];

/** 后端清单 → 展示分组（未知扩展名兜底进「其他」，清单演进不丢项） */
export function groupFormats(formats: string[] | null): { label: string; exts: string[] }[] {
  if (!formats) return [];
  const known = new Set(FORMAT_CATEGORIES.flatMap((c) => c.exts));
  const groups = FORMAT_CATEGORIES.map((c) => ({
    label: c.label,
    exts: c.exts.filter((e) => formats.includes(e)),
  })).filter((c) => c.exts.length > 0);
  const rest = formats.filter((e) => !known.has(e));
  if (rest.length > 0) groups.push({ label: "其他", exts: rest });
  return groups;
}

/** 当前 UTC 时间，格式与后端 created_at 一致（ISO 8601 带 Z 后缀） */
export function localNowIso(): string {
  return new Date().toISOString().slice(0, 19) + "Z";
}
