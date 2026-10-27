import { useEffect, useRef, useState } from "react";
import { groupFormats, MAX_UPLOAD_MB } from "../format";
import type { UploadOptions } from "../types";
import { IcUpload, IcFileDoc, IcSparkle, IcMarkdown } from "./Icons";
import PromptStudio from "./PromptStudio";
import s from "./UploadZone.module.css";

interface Props {
  /** 后端支持扩展名清单（App 启动时拉取，null 表示尚未就绪） */
  formats: string[] | null;
  /** App 上传队列仍有在途/排队文件（提交期间锁定热区防重复） */
  submitting: boolean;
  /** 批量上传入口：预检通过的文件交棒 App 槽位感知提交，options 为入队时定格的转换选项 */
  onAccepted: (files: File[], options: UploadOptions) => void;
}


/** 单次批量上限：防误拖整个目录压垮队列，超出提示分批 */
const MAX_BATCH = 10;

/** 转换选项记忆键（下次上传沿用），值形如 link+transcribe+describe */
const OPTIONS_STORAGE_KEY = "omnimd.options";
/** 自定义提示词与系统提示词文本单独记忆（选项串只存 prompt 标记不存全文） */
const PROMPT_STORAGE_KEY = "omnimd.prompt";
const SYSTEM_STORAGE_KEY = "omnimd.system";

type Selection = {
  images: "link" | "embed" | null;
  transcribe: boolean;
  describe: boolean;
  prompt: string;
  system: string;
};

const DEFAULT_SELECTION: Selection = {
  images: null,
  transcribe: false,
  describe: false,
  prompt: "",
  system: "",
};

/** 记忆上次选择；无记忆或记忆值非法时回到缺省（与服务端/CLI 缺省一致：不传呈现与任务，
 *  缺省由输入类型决定——文字文件→正文，图片文件→完整转写）。
 *  依赖关系一并校验：无呈现时描述清空（localStorage 手改可能产生矛盾态，直接提交会被服务端 400） */
function loadOptions(): Selection {
  try {
    const raw = localStorage.getItem(OPTIONS_STORAGE_KEY);
    if (!raw) return DEFAULT_SELECTION;
    const parts = raw.split("+").filter(Boolean);
    const images = parts.includes("link")
      ? "link"
      : parts.includes("embed")
        ? "embed"
        : null;
    let prompt = "";
    let system = "";
    try {
      prompt = localStorage.getItem(PROMPT_STORAGE_KEY) ?? "";
      system = prompt ? localStorage.getItem(SYSTEM_STORAGE_KEY) ?? "" : "";
    } catch {
      /* 忽略：仅丢失提示词记忆 */
    }
    return {
      images,
      transcribe: parts.includes("transcribe"),
      describe: images ? parts.includes("describe") : false,
      prompt,
      system,
    };
  } catch {
    return DEFAULT_SELECTION;
  }
}

/** 当前选择 → 提交选项（images / transcribe / describe / prompt / system） */
function buildOptions(sel: Selection): UploadOptions {
  return {
    images: sel.images,
    transcribe: sel.transcribe,
    describe: sel.describe,
    prompt: sel.prompt.trim(),
    system: sel.system.trim(),
  };
}

/** 选择记忆串（与后端 options 列同构） */
function optionsKey(sel: Selection): string {
  return [
    sel.images ?? "drop",
    ...(sel.transcribe ? ["transcribe"] : []),
    ...(sel.describe ? ["describe"] : []),
    ...(sel.prompt.trim() ? ["prompt"] : []),
  ].join("+");
}

/** 当前选择下，一张图片在产出 Markdown 中的形态（实时预览）。
 *  示例值只示意结构：链接指向原图或 data URI，描述位放一句话描述，转写文本块跟在下方或独立成段。 */
function optionsPreview(sel: Selection): string {
  const promptLine = sel.prompt.trim() ? "按你的提示词生成的文本" : "";
  if (!sel.images) {
    if (sel.transcribe)
      return "图片的完整转写文本" +
        (promptLine ? `\n${promptLine}` : "\n（不留图，图片原位变文字）");
    if (promptLine) return `${promptLine}\n（不留图，按你的提示词生成文字）`;
    return "（不选时：文档只提取正文，图片自动完整转写）";
  }
  const alt = sel.describe ? "一句话描述" : "图片";
  const ref = sel.images === "link" ? "图片链接" : "data:image/png;base64,…";
  let block = `![${alt}](${ref})`;
  if (sel.transcribe) block += "\n图片的完整转写文本";
  if (promptLine) block += `\n${promptLine}`;
  return block;
}

/** 三步流程的步骤图 */
function StepArt({ kind }: { kind: 0 | 1 | 2 }) {
  if (kind === 0) return <IcFileDoc size={26} />;
  if (kind === 1) return <IcSparkle size={26} />;
  return <IcMarkdown size={26} />;
}

/** 首访主舞台：上传热区 + 转换选项 + 价值主张 + 三步流程 + 格式矩阵 */
export default function UploadZone({ formats, submitting, onAccepted }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [studioOpen, setStudioOpen] = useState(false);
  const [sel, setSel] = useState<Selection>(loadOptions);
  const isDefault =
    !sel.images && !sel.transcribe && !sel.describe && !sel.prompt.trim();

  // 选择记忆（缺省同样落盘——下次上传沿用；提示词全文单独存）
  useEffect(() => {
    try {
      localStorage.setItem(OPTIONS_STORAGE_KEY, optionsKey(sel));
      localStorage.setItem(PROMPT_STORAGE_KEY, sel.prompt);
      localStorage.setItem(SYSTEM_STORAGE_KEY, sel.system);
    } catch {
      /* 存储不可用：忽略，仅丢失偏好 */
    }
  }, [sel]);

  /** 呈现三选单选（drop / link / embed） */
  function setPresentation(images: Selection["images"]) {
    setSel((cur) => ({
      ...cur,
      images,
      describe: images ? cur.describe : false,
    }));
  }

  /** 任务开关：转写独立可用；描述是图片的文字说明，需先选呈现 */
  function toggleTask(task: "transcribe" | "describe") {
    setSel((cur) => ({
      ...cur,
      [task]: !cur[task],
    }));
  }

  /** 单文件预检：返回拒绝原因，null 表示通过 */
  function checkFile(file: File): string | null {
    if (file.size > MAX_UPLOAD_MB * 1024 * 1024)
      return `${file.name}：超出大小上限（≤ ${MAX_UPLOAD_MB}MB）`;
    // 扩展名与后端同口径：取最后一个点之后；无点、隐藏文件、点在末尾均按「无扩展名」报（后端另有校验，预检省一次无谓上传）
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
    if (accepted.length > 0) onAccepted(accepted, buildOptions(sel));
  }

  const groups = groupFormats(formats);

  return (
    <div className={s.wrap}>
      {/* 价值主张：一句话说清 omnimd 是什么 */}
      <div className={s.pitch}>
        <h1 className={s.pitchTitle}>任意文件 → Markdown</h1>
        <p className={s.pitchSub}>
          图片不丢、内容可检索——图片怎么保留、要不要转成文字，由你选择。
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
        {/* 三步流程线：上传 → 自动转换 → 组合产出 */}
        <ol className={s.steps}>
          {[
            { art: 0, title: "上传文件", desc: "文档、表格、演示或图片" },
            { art: 1, title: "自动转换", desc: "提取正文，图片按需保存与转写" },
            { art: 2, title: "组合产出", desc: "图片按你选的方式写进同一份 Markdown" },
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

        {/* 转换选项：上传时选定的运行参数（呈现三选 + 任务开关） */}
        <aside className={s.options}>
          <div className={s.optionsHeader}>
            <h2 className={s.optionsTitle}>图片怎么处理</h2>
            <span className={s.headerRight}>
              {isDefault && (
                <span
                  className={s.pureParseHint}
                  title="不选任何选项：文档只提取正文，图片自动完整转写"
                >
                  💡 默认
                </span>
              )}
              <button
                type="button"
                className={`${s.customIcon} ${sel.prompt.trim() ? s.customIconOn : ""}`}
                aria-haspopup="dialog"
                onClick={() => setStudioOpen(true)}
                title={
                  sel.prompt.trim()
                    ? `自定义分析已设置：${sel.prompt.trim()}（点击修改或清除）`
                    : "自定义图片分析——写提示词、选图生成看效果"
                }
              >
                ✎{sel.prompt.trim() ? " ●" : ""}
              </button>
            </span>
          </div>
          <div className={s.partRows}>
            <div className={s.partRow}>
              <button
                type="button"
                className={`${s.partBtn} ${sel.images === "link" ? s.partBtnOn : ""}`}
                aria-pressed={sel.images === "link"}
                onClick={() => setPresentation(sel.images === "link" ? null : "link")}
                title="给图片一个可点开的网址，随时查看原图；需要配置图片存储。再点一次取消（不保留图片）"
              >
                <span className={s.partDot} aria-hidden="true" />
                链接
              </button>
              <button
                type="button"
                className={`${s.partBtn} ${sel.images === "embed" ? s.partBtnOn : ""}`}
                aria-pressed={sel.images === "embed"}
                onClick={() => setPresentation(sel.images === "embed" ? null : "embed")}
                title="把图片直接放进 Markdown，离线也能看。再点一次取消（不保留图片）"
              >
                <span className={s.partDot} aria-hidden="true" />
                内嵌
              </button>
            </div>
            <div className={s.partRow}>
              <button
                type="button"
                className={`${s.partBtn} ${sel.transcribe ? s.partBtnOn : ""}`}
                aria-pressed={sel.transcribe}
                onClick={() => toggleTask("transcribe")}
                title="用视觉模型把图片内容转成完整文字（产生模型费用）"
              >
                <span className={s.partDot} aria-hidden="true" />
                转写
              </button>

              <button
                type="button"
                className={`${s.partBtn} ${sel.describe ? s.partBtnOn : ""} ${!sel.images ? s.partBtnLocked : ""}`}
                aria-pressed={sel.describe}
                aria-disabled={!sel.images}
                onClick={() => {
                  if (sel.images) toggleTask("describe");
                }}
                title={
                  sel.images
                    ? "一句话说明图片是什么，作为图片的文字说明"
                    : "描述写在图片的文字说明里，要先选链接或内嵌"
                }
              >
                <span className={s.partDot} aria-hidden="true" />
                描述
              </button>

              {/* 任务轴的开放端：内置两动词之外的任意意图，进工作台撰写与生成。
                  入口两态：未设置 / 已设置（加亮点）；摘要在悬停提示与工作台内，
                  清除在工作台内 */}
            </div>
          </div>
          <div className={s.preview} aria-live="polite">
            <span className={s.previewLabel}>转换结果里的图片</span>
            <pre className={s.previewBody}>{optionsPreview(sel)}</pre>
          </div>
          <p className={s.optionsHint}>
            链接、内嵌都不选就是不保留图片（默认）；转写可以单独开（不留图也能只要文字），
            描述要先选链接或内嵌。转写、描述、自定义提示词会调用视觉模型（产生模型费用）。
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

      {/* 提示词工作台：二级模态，左写右看流式生成；应用后写回 sel.prompt */}
      <PromptStudio
        open={studioOpen}
        initialPrompt={sel.prompt}
        initialSystem={sel.system}
        onClose={() => setStudioOpen(false)}
        onApply={(prompt, system) => {
          setSel((cur) => ({ ...cur, prompt, system }));
          setStudioOpen(false);
        }}
      />
    </div>
  );
}
