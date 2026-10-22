import { useEffect, useRef, useState } from "react";
import { groupFormats, MAX_UPLOAD_MB } from "../format";
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


/** 单次批量上限：防误拖整个目录压垮队列，超出提示分批 */
const MAX_BATCH = 10;

/** 产出组成选择记忆键（下次上传沿用） */
const OUTPUT_STORAGE_KEY = "omnimd.output";

/** 图片呈现部件（互斥单选：url=公开链接 / base64=内嵌） */
const PRESENTATION_VALUES = ["url", "base64"] as const;

/** 图片内容部件（依赖呈现：ocr=转写 / alt=描述，可多选） */
const CONTENT_VALUES = ["ocr", "alt"] as const;

type Selection = { presentation: string | null; content: string[] };

/** 记忆上次选择；无记忆或记忆值非法时回到缺省（纯解析，与服务端/CLI 缺省 body 一致）。
 *  依赖关系一并校验：无呈现部件时内容清空（localStorage 手改可能产生 body,ocr 之类的矛盾态，直接提交会被服务端 400） */
function loadOutput(): Selection {
  try {
    const raw = localStorage.getItem(OUTPUT_STORAGE_KEY);
    if (!raw) return { presentation: null, content: [] };
    const parts = raw.split(",").filter(Boolean);
    const presentation = PRESENTATION_VALUES.find((p) => parts.includes(p)) ?? null;
    const content = presentation
      ? CONTENT_VALUES.filter((c) => parts.includes(c))
      : [];
    return { presentation, content };
  } catch {
    return { presentation: null, content: [] };
  }
}

/** 提交时组装为规范序 CSV（body 锚点 + 呈现 + 内容按固定序），与服务端 parse_output 归一化同序 */
function buildOutput(sel: Selection): string {
  const parts = ["body"];
  if (sel.presentation) parts.push(sel.presentation);
  parts.push(...CONTENT_VALUES.filter((c) => sel.content.includes(c)));
  return parts.join(",");
}

/** 当前选择下，一张图片在产出 Markdown 中的形态（实时预览）。
 * 示例值只示意结构：链接指向原图或 data URI，alt 位放一句话描述，转写文本块跟在下方；纯解析时图片不进产出。 */
function outputPreview(sel: Selection): string {
  if (!sel.presentation) return "（仅正文，图片不处理）";
  const alt = sel.content.includes("alt") ? "一句话描述" : "图片";
  const ref = sel.presentation === "url" ? "图片链接" : "data:image/png;base64,…";
  let block = `![${alt}](${ref})`;
  if (sel.content.includes("ocr")) block += "\n图片的完整转写文本";
  return block;
}

/** 三步流程的步骤图 */
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
  const [sel, setSel] = useState<Selection>(loadOutput);
  const pureParse = !sel.presentation && sel.content.length === 0;

  // 选择记忆（全不选即纯解析，同样落盘——下次上传沿用）
  useEffect(() => {
    try {
      localStorage.setItem(OUTPUT_STORAGE_KEY, buildOutput(sel));
    } catch {
      /* 存储不可用：忽略，仅丢失偏好 */
    }
  }, [sel]);

  /** 呈现互斥单选：再点当前项即取消（回到未选）；关呈现连带清内容（依赖） */
  function togglePresentation(part: string) {
    setSel((cur) =>
      cur.presentation === part
        ? { presentation: null, content: [] }
        : { presentation: part, content: cur.content },
    );
  }

  /** 内容多选：仅在已选呈现时可用（依赖关系） */
  function toggleContent(part: string) {
    setSel((cur) => ({
      presentation: cur.presentation,
      content: cur.content.includes(part)
        ? cur.content.filter((c) => c !== part)
        : [...cur.content, part],
    }));
  }

  /** 单文件预检：返回拒绝原因，null 表示通过 */
  function checkFile(file: File): string | null {
    if (file.size > MAX_UPLOAD_MB * 1024 * 1024)
      return `${file.name}：超出大小上限（≤ ${MAX_UPLOAD_MB}MB）`;
    // 扩展名与后端同口径：取最后一个点之后；无点、隐藏文件、点在末尾均按「无扩展名」报（后端仍兜底校验，预检只是省一次无谓上传）
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
    if (accepted.length > 0) onAccepted(accepted, buildOutput(sel));
  }

  const groups = groupFormats(formats);

  return (
    <div className={s.wrap}>
      {/* 价值主张：一句话说清 omnimd 是什么 */}
      <div className={s.pitch}>
        <h1 className={s.pitchTitle}>任意文件 → Markdown</h1>
        <p className={s.pitchSub}>
          图片不丢、内容可检索——正文、图片链接、图片转写文本，按需组合产出。
        </p>
      </div>

      {/* 上传热区。提交锁定用 aria-disabled 而非 disabled：disabled 元素不派发拖拽事件，
          onDrop 不执行 preventDefault，拖入的文件会被浏览器当作页面打开（导航劫持）——事件照常拦截，动作层值守卫 */}
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
            ? `支持 ${formats.length} 种格式 · 可一次多选 · 单文件 ≤ ${MAX_UPLOAD_MB}MB`
            : `PDF · Word · PPT · Excel · 图片等 · 可一次多选 · 单文件 ≤ ${MAX_UPLOAD_MB}MB`}
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
        {/* 三步流程线：上传 → 自动转换 → 按需组合产出 */}
        <ol className={s.steps}>
          {[
            { art: 0, title: "上传文件", desc: "文档、表格、演示或图片" },
            { art: 1, title: "自动转换", desc: "提取正文，图片按需保存与转写" },
            { art: 2, title: "组合产出", desc: "正文 + 链接/内嵌 + 转写写入 Markdown" },
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

        {/* 产出组成：上传时选定的唯一运行参数（全不选即纯解析） */}
        <aside className={s.options}>
          <div className={s.optionsHeader}>
            <h2 className={s.optionsTitle}>产出组成</h2>
            {pureParse && (
              <span
                className={s.pureParseHint}
                title="只提取正文，不处理图片，不调用模型"
              >
                💡 纯解析
              </span>
            )}
          </div>
          <div className={s.partRows}>
            <div className={s.partRow}>
              <button
                type="button"
                className={`${s.partBtn} ${sel.presentation === "url" ? s.partBtnOn : ""}`}
                aria-pressed={sel.presentation === "url"}
                onClick={() => togglePresentation("url")}
                title="给图片一个可点开的网址，随时查看原图（与内嵌二选一）"
              >
                <span className={s.partDot} aria-hidden="true" />
                链接
              </button>
              <button
                type="button"
                className={`${s.partBtn} ${sel.presentation === "base64" ? s.partBtnOn : ""}`}
                aria-pressed={sel.presentation === "base64"}
                onClick={() => togglePresentation("base64")}
                title="把图片直接放进 Markdown，离线也能看（与链接二选一）"
              >
                <span className={s.partDot} aria-hidden="true" />
                内嵌
              </button>
            </div>
            <div className={s.partRow}>
              <button
                type="button"
                className={`${s.partBtn} ${sel.content.includes("ocr") ? s.partBtnOn : ""} ${!sel.presentation ? s.partBtnLocked : ""}`}
                aria-pressed={sel.content.includes("ocr")}
                aria-disabled={!sel.presentation}
                onClick={() => {
                  if (sel.presentation) toggleContent("ocr");
                }}
                title={
                  sel.presentation
                    ? "用视觉模型把图片内容转成完整文字（产生模型费用）"
                    : "请先选择链接或内嵌"
                }
              >
                <span className={s.partDot} aria-hidden="true" />
                转写
              </button>
              <button
                type="button"
                className={`${s.partBtn} ${sel.content.includes("alt") ? s.partBtnOn : ""} ${!sel.presentation ? s.partBtnLocked : ""}`}
                aria-pressed={sel.content.includes("alt")}
                aria-disabled={!sel.presentation}
                onClick={() => {
                  if (sel.presentation) toggleContent("alt");
                }}
                title={
                  sel.presentation
                    ? "一句话说明图片是什么"
                    : "请先选择链接或内嵌"
                }
              >
                <span className={s.partDot} aria-hidden="true" />
                描述
              </button>
            </div>
          </div>
          <div className={s.preview} aria-live="polite">
            <span className={s.previewLabel}>图片在产出中的形态</span>
            <pre className={s.previewBody}>{outputPreview(sel)}</pre>
          </div>
          <p className={s.optionsHint}>
            链接和内嵌二选一；转写、描述要先选链接或内嵌其中一个，会调用视觉模型（产生模型费用）。
            都不选就只提取正文，不调用模型。
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
