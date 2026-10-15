import { useEffect, useRef, useState } from "react";
import { groupFormats } from "../format";
import { IcUpload, IcFileDoc, IcSparkle, IcMarkdown } from "./Icons";
import s from "./UploadZone.module.css";

interface Props {
  /** 后端支持扩展名清单（App 启动时拉取，null 表示尚未就绪） */
  formats: string[] | null;
  /** App 上传队列仍有在途/排队文件（提交期间锁定热区防重复） */
  submitting: boolean;
  /** 批量上传入口：预检通过的文件交棒 App 槽位感知提交，output 为入队时定格的产出组成 */
  onAccepted: (files: File[], output: string) => void;
}

const MAX_MB = 50;

/** 单次批量上限：防误拖整个目录压垮队列，超出提示分批 */
const MAX_BATCH = 10;

/** 产出组成选择记忆键（下次上传沿用） */
const OUTPUT_STORAGE_KEY = "omnimd.output";

/** 产出组成的可选取值（与服务端 OUTPUT 一一对应：url=图片链接，text=转写文本，alt=链接描述） */
const OUTPUT_VALUES = ["url", "text", "alt"] as const;

/** 无记忆时的缺省选择（与服务端/CLI 缺省 url,text 一致——alt 产生额外调用成本，不默认开） */
const DEFAULT_PARTS = ["url", "text"];

/** 记忆上次选择；无记忆或记忆值非法时回到缺省（url,text） */
function loadOutput(): string[] {
  try {
    const raw = localStorage.getItem(OUTPUT_STORAGE_KEY);
    if (!raw) return [...DEFAULT_PARTS];
    const parts = raw.split(",").filter((p) => OUTPUT_VALUES.includes(p as never));
    return parts.length > 0 ? parts : [...DEFAULT_PARTS];
  } catch {
    return [...DEFAULT_PARTS];
  }
}

/** 当前选择下，一张图片在产出 Markdown 中的形态（实时预览）。
 * 示例值只示意结构：链接指向原图，alt 位放一句话描述，转写文本块跟在链接下方。 */
function outputPreview(parts: string[]): string {
  const hasUrl = parts.includes("url");
  const hasText = parts.includes("text");
  const alt = parts.includes("alt") ? "图片的一句话描述" : "图片";
  const link = `![${alt}](图片链接)`;
  const transcript = "图片的完整转写文本";
  if (hasUrl && hasText) return `${link}\n\n${transcript}`;
  if (hasUrl) return link;
  return transcript;
}

/** 三步流程的步骤图（与 Icons.tsx 同一套图标规范：细线、圆头、currentColor） */
function StepArt({ kind }: { kind: 0 | 1 | 2 }) {
  if (kind === 0) return <IcFileDoc size={26} />;
  if (kind === 1) return <IcSparkle size={26} />;
  return <IcMarkdown size={26} />;
}

/** 首访主舞台：上传热区 + 产出组成选择 + 价值主张 + 三步流程 + 格式矩阵 */
export default function UploadZone({ formats, submitting, onAccepted }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [parts, setParts] = useState<string[]>(loadOutput);

  // 选择记忆：两枚开关自由组合，但至少保留一个（全关无意义）
  useEffect(() => {
    if (parts.length === 0) return; // 渲染期不落盘空集，toggle 里已防
    try {
      localStorage.setItem(OUTPUT_STORAGE_KEY, parts.join(","));
    } catch {
      /* 存储不可用：忽略，仅丢失偏好 */
    }
  }, [parts]);

  function togglePart(part: string) {
    setParts((cur) => {
      if (cur.includes(part)) {
        // 关 url 时连带关 alt（alt 依赖 url）；至少保留一项
        let next = cur.filter((p) => p !== part);
        if (part === "url") next = next.filter((p) => p !== "alt");
        return next.length > 0 ? next : cur;
      }
      // 开 alt 时确保 url 在（依赖关系）；保持与服务端一致的规范序
      const target = new Set(cur);
      if (part === "alt") target.add("url");
      target.add(part);
      return OUTPUT_VALUES.filter((p) => target.has(p));
    });
  }

  /** 单文件预检：返回拒绝原因，null 表示通过 */
  function checkFile(file: File): string | null {
    if (file.size > MAX_MB * 1024 * 1024)
      return `${file.name}：超出大小上限（≤ ${MAX_MB}MB）`;
    // 扩展名与后端同口径：取最后一个点之后；无点、隐藏文件、点在末尾
    // 均按「无扩展名」报（后端仍兜底校验，预检只是省一次无谓上传）
    const dot = file.name.lastIndexOf(".");
    const ext =
      dot > 0 && dot < file.name.length - 1
        ? file.name.slice(dot).toLowerCase()
        : "";
    if (formats && !formats.includes(ext)) {
      return ext
        ? `${file.name}：不支持的格式 ${ext}`
        : `${file.name}：没有扩展名，无法识别格式`;
    }
    return null;
  }

  /** 批量收集：逐个预检，被拒项合并展示，通过项整批交棒 App 排队提交 */
  function collect(fileList: FileList) {
    setError(null);
    const files = Array.from(fileList);
    if (files.length === 0) return;
    if (files.length > MAX_BATCH) {
      setError(`一次最多提交 ${MAX_BATCH} 个文件，请分批上传。`);
      return;
    }
    const rejected: string[] = [];
    const accepted: File[] = [];
    for (const file of files) {
      const reason = checkFile(file);
      if (reason) rejected.push(reason);
      else accepted.push(file);
    }
    if (rejected.length > 0) setError(`已跳过：\n${rejected.join("\n")}`);
    if (accepted.length > 0) onAccepted(accepted, parts.join(","));
  }

  const groups = groupFormats(formats);

  return (
    <div className={s.wrap}>
      {/* 价值主张：一句话说清 omnimd 是什么 */}
      <div className={s.pitch}>
        <h1 className={s.pitchTitle}>任意文件 → Markdown</h1>
        <p className={s.pitchSub}>
          图片不丢、内容可检索——正文、图片链接、图片转写文本，一次转换三合一产出。
        </p>
      </div>

      {/* 上传热区。提交锁定用 aria-disabled 而非 disabled：disabled 元素
          不派发拖拽事件，onDrop 不执行 preventDefault，拖入的文件会被
          浏览器当作页面打开（导航劫持）——事件照常拦截，动作层值守卫 */}
      <button
        type="button"
        className={`${s.zone} ${dragOver ? s.dragOver : ""} ${submitting ? s.zoneBusy : ""}`}
        onClick={() => {
          if (!submitting) inputRef.current?.click();
        }}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          if (!submitting && e.dataTransfer.files.length > 0)
            collect(e.dataTransfer.files);
        }}
        aria-disabled={submitting}
        aria-label="上传文件"
      >
        <span className={s.zoneIcon}>
          <IcUpload size={30} />
        </span>
        <span className={s.zoneTitle}>
          {submitting ? "提交中…" : "把文件拖到这里，或点击选择"}
        </span>
        <span className={s.zoneHint}>
          {formats
            ? `支持 ${formats.length} 种格式 · 可一次多选 · 单文件 ≤ ${MAX_MB}MB`
            : `PDF · Word · PPT · Excel · 图片 等 · 可一次多选 · 单文件 ≤ ${MAX_MB}MB`}
        </span>
        <span className={s.zoneHint}>大文件的转换可能需要几分钟</span>
      </button>

      {error && (
        <div className={s.error} role="alert">
          {error}
        </div>
      )}

      {/* 主舞台分区：左列认识产品（三步流程 + 格式矩阵），右列动手配置 */}
      <div className={s.stage}>
        {/* 三步流程线：上传 → 管线转换 → 三合一产出 */}
        <ol className={s.steps}>
          {[
            { art: 0, title: "上传文件", desc: "文档、表格、演示或图片" },
            { art: 1, title: "管线转换", desc: "解析正文，图片托管为公开链接" },
            { art: 2, title: "三合一产出", desc: "正文 + 图片 + 转写文本写入 Markdown" },
          ].map((step, i) => (
            <li key={step.title} className={s.step}>
              {i > 0 && (
                <span className={s.stepArrow} aria-hidden="true">
                  →
                </span>
              )}
              <div className={s.stepCard}>
                <span className={s.stepIcon}>
                  <StepArt kind={step.art as 0 | 1 | 2} />
                </span>
                <span className={s.stepTitle}>{step.title}</span>
                <span className={s.stepDesc}>{step.desc}</span>
              </div>
            </li>
          ))}
        </ol>

        {/* 产出组成：上传时选定的唯一运行参数 */}
        <aside className={s.options}>
          <h2 className={s.optionsTitle}>产出组成</h2>
          <div className={s.partBtns}>
            {OUTPUT_VALUES.map((part) => (
              <button
                key={part}
                type="button"
                className={`${s.partBtn} ${parts.includes(part) ? s.partBtnOn : ""}`}
                aria-pressed={parts.includes(part)}
                onClick={() => togglePart(part)}
              >
                <span className={s.partDot} aria-hidden="true" />
                {part === "url" ? "图片链接" : part === "text" ? "转写文本" : "链接描述"}
              </button>
            ))}
          </div>
          <div className={s.preview} aria-live="polite">
            <span className={s.previewLabel}>图片在产出中的形态</span>
            <pre className={s.previewBody}>{outputPreview(parts)}</pre>
          </div>
          <p className={s.optionsHint}>
            转写文本与链接描述需调用视觉模型，产生调用成本。至少选一项；
            链接描述依赖图片链接，会自动同时选中；选择会记住，随本次上传生效。
          </p>
        </aside>

        {/* 格式矩阵：分组展示，清单唯一来源是 /api/formats */}
        {groups.length > 0 && (
          <div className={s.formatBoard}>
            {groups.map((g) => (
              <div key={g.label} className={s.formatGroup}>
                <span className={s.formatLabel}>{g.label}</span>
                <div className={s.formatChips}>
                  {g.exts.map((ext) => (
                    <span key={ext} className={s.formatChip}>
                      {ext.slice(1)}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <input
        ref={inputRef}
        type="file"
        hidden
        multiple
        accept={formats ? formats.join(",") : undefined}
        onChange={(e) => {
          if (e.target.files && e.target.files.length > 0) collect(e.target.files);
          e.target.value = ""; // 同文件可重复选择
        }}
      />
    </div>
  );
}
