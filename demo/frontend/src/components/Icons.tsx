/** 全站图标集：统一 18×18 视窗、1.4px 圆头描边、currentColor。
 *  所有组件统一从此取用图标，不各自手绘。
 */
import type { ReactNode } from "react";

interface IconProps {
  size?: number;
  className?: string;
}

function Svg({ size = 18, className, children }: IconProps & { children: ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 18 18"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.4}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

/** 上传：托盘 + 上箭头 */
export function IcUpload({ size, className }: IconProps) {
  return (
    <Svg size={size} className={className}>
      <path d="M9 12.5V3.5" />
      <path d="M5.5 7 9 3.5 12.5 7" />
      <path d="M3 15h12" />
    </Svg>
  );
}

/** 眼睛：渲染视图（查看产出形态） */
export function IcEye({ size, className }: IconProps) {
  return (
    <Svg size={size} className={className}>
      <path d="M2.25 9s2.7-4.75 6.75-4.75S15.75 9 15.75 9 13.05 13.75 9 13.75 2.25 9 2.25 9Z" />
      <circle cx="9" cy="9" r="2.2" />
    </Svg>
  );
}

/** 代码括号：源码视图（逐字对照产出） */
export function IcCode({ size, className }: IconProps) {
  return (
    <Svg size={size} className={className}>
      <path d="M6.5 4.5 3 9l3.5 4.5" />
      <path d="M11.5 4.5 15 9l-3.5 4.5" />
    </Svg>
  );
}

/** 下载：托盘 + 下箭头 */
export function IcDownload({ size, className }: IconProps) {
  return (
    <Svg size={size} className={className}>
      <path d="M9 3.5v9" />
      <path d="M5.5 9 9 12.5 12.5 9" />
      <path d="M3 15h12" />
    </Svg>
  );
}

/** 转写：四角星（视觉模型加工） */
export function IcSparkle({ size, className }: IconProps) {
  return (
    <Svg size={size} className={className}>
      <path d="M9 2.8l1.9 4.3 4.3 1.9-4.3 1.9L9 15.2 7.1 10.9 2.8 9l4.3-1.9z" />
    </Svg>
  );
}

/** Markdown 产出：圆角框 + M 形下落箭头 */
export function IcMarkdown({ size, className }: IconProps) {
  return (
    <Svg size={size} className={className}>
      <rect x="2" y="4.5" width="14" height="9" rx="2" />
      <path d="M5 11.4V7.8l2 2 2-2v3.6" />
      <path d="M11.4 7.8v3.6h1.9" />
    </Svg>
  );
}

/** 关闭 / 清除 */
export function IcX({ size, className }: IconProps) {
  return (
    <Svg size={size} className={className}>
      <path d="M4.5 4.5l9 9" />
      <path d="M13.5 4.5l-9 9" />
    </Svg>
  );
}

/** 抽屉菜单 */
export function IcMenu({ size, className }: IconProps) {
  return (
    <Svg size={size} className={className}>
      <path d="M3 5h12" />
      <path d="M3 9h12" />
      <path d="M3 13h12" />
    </Svg>
  );
}

/** 复制：前后两个叠层方框 */
export function IcCopy({ size, className }: IconProps) {
  return (
    <Svg size={size} className={className}>
      <rect x="6.2" y="6.2" width="9.3" height="9.3" rx="1.6" />
      <path d="M11.8 6.2V4A1.6 1.6 0 0 0 10.2 2.4H4A1.6 1.6 0 0 0 2.4 4v6.2A1.6 1.6 0 0 0 4 11.8h2.2" />
    </Svg>
  );
}

/** 对勾（反馈态） */
export function IcCheck({ size, className }: IconProps) {
  return (
    <Svg size={size} className={className}>
      <path d="M3.5 9.5l4 4 7-9" />
    </Svg>
  );
}

/** 使用指南：问号 */
export function IcHelp({ size, className }: IconProps) {
  return (
    <Svg size={size} className={className}>
      <circle cx="9" cy="9" r="6.5" />
      <path d="M6.9 6.9a2.1 2.1 0 1 1 2.9 2c-.6.3-.8.8-.8 1.4" />
      <path d="M9 12.7h.01" />
    </Svg>
  );
}

/* ---- 文件类型图标 ---- */

function IcFileDoc({ size, className }: IconProps) {
  return (
    <Svg size={size} className={className}>
      <path d="M4 2h7l3 3v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1z" />
      <path d="M10.5 2v3.5H14" />
      <path d="M5.5 9h7" />
      <path d="M5.5 12h5" />
    </Svg>
  );
}

/** 文档文件 */
export { IcFileDoc };

function IcFileImage({ size, className }: IconProps) {
  return (
    <Svg size={size} className={className}>
      <rect x="2.5" y="3" width="13" height="12" rx="1.5" />
      <circle cx="6.3" cy="6.8" r="1.2" />
      <path d="M2.5 12.5l3.6-3.6 3 3 2.4-2.4 4 4" />
    </Svg>
  );
}

function IcFileTable({ size, className }: IconProps) {
  return (
    <Svg size={size} className={className}>
      <rect x="2.5" y="3" width="13" height="12" rx="1.5" />
      <path d="M2.5 7.5h13" />
      <path d="M2.5 11h13" />
      <path d="M7.5 3v12" />
    </Svg>
  );
}

function IcFilePresentation({ size, className }: IconProps) {
  return (
    <Svg size={size} className={className}>
      <rect x="2" y="3" width="14" height="9.5" rx="1.5" />
      <path d="M6.5 15.5 9 12.5l2.5 3" />
      <path d="M5.5 10.5V7l2.2 2.2L10 6.8v3.7" />
    </Svg>
  );
}

/** 按扩展名取文件图标（演示不维护类型映射全集，四档足够） */
export function FileIcon({ name, size, className }: IconProps & { name: string }) {
  const dot = name.lastIndexOf(".");
  const ext = dot > 0 ? name.slice(dot).toLowerCase() : "";
  if ([".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".svg", ".ico"].includes(ext)) {
    return <IcFileImage size={size} className={className} />;
  }
  if ([".xlsx", ".xlsm", ".xlsb", ".xls", ".csv", ".ods"].includes(ext)) {
    return <IcFileTable size={size} className={className} />;
  }
  if ([".ppt", ".pptx", ".pptm", ".ppsx", ".ppsm", ".odp"].includes(ext)) {
    return <IcFilePresentation size={size} className={className} />;
  }
  return <IcFileDoc size={size} className={className} />;
}
