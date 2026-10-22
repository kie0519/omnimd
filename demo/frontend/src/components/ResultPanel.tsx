import { useEffect, useRef, useState } from "react";
import type { TaskDetail } from "../types";
import { formatElapsed, formatNumber, formatSize } from "../format";
import StatusBadge from "./StatusBadge";
import MarkdownView from "./MarkdownView";
import { FileIcon, IcDownload, IcCopy, IcCheck, IcEye, IcCode } from "./Icons";
import s from "./ResultPanel.module.css";

interface Props {
  task: TaskDetail | null;
  loading: boolean;
  view: "rendered" | "source";
  onViewChange: (v: "rendered" | "source") => void;
}

/** 转换中实时计时 */
function useLiveSeconds(active: boolean, createdAt: string): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [active]);
  if (!active) return 0;
  const created = new Date(createdAt).getTime();
  return Math.max(0, Math.round((now - created) / 1000));
}

export default function ResultPanel({ task, loading, view, onViewChange }: Props) {
  const contentRef = useRef<HTMLDivElement>(null);
  // 同一任务从 running/pending 到 done 时向屏幕阅读器播报「转换完成」
  const prevRef = useRef<{ id: string; status: string } | null>(null);
  const [announce, setAnnounce] = useState(false);

  useEffect(() => {
    if (!task) {
      prevRef.current = null;
      return;
    }
    const p = prevRef.current;
    prevRef.current = { id: task.task_id, status: task.status };
    if (
      p &&
      p.id === task.task_id &&
      (p.status === "running" || p.status === "pending") &&
      task.status === "done"
    ) {
      setAnnounce(true);
      const t = setTimeout(() => setAnnounce(false), 3000);
      return () => clearTimeout(t);
    }
  }, [task?.task_id, task?.status]);

  // 视图切换保持滚动位置：切换前记录，下一帧恢复
  function handleViewChange(v: "rendered" | "source") {
    const el = contentRef.current;
    const top = el?.scrollTop ?? 0;
    onViewChange(v);
    requestAnimationFrame(() => {
      if (el) el.scrollTop = top;
    });
  }

  if (!task) {
    return (
      <div className={s.placeholderWrap}>
        {loading ? (
          <p className={s.placeholder}>加载中…</p>
        ) : (
          <p className={s.placeholder}>从历史记录中选择，或新建一个转换</p>
        )}
      </div>
    );
  }

  const running = task.status === "pending" || task.status === "running";

  return (
    <div className={s.wrap}>
      {/* 屏幕阅读器播报：转换完成 */}
      <span role="status" aria-live="polite" className={s.srOnly}>
        {announce ? "转换完成" : ""}
      </span>
      <header className={s.header}>
        <span className={s.icon} aria-hidden="true">
          <FileIcon name={task.filename} size={18} />
        </span>
        <h1 className={s.title} title={task.filename}>{task.filename}</h1>
        {outputLabel(task.output).map((label) => (
          <span key={label} className={s.compTag}>{label}</span>
        ))}
        <StatusBadge status={task.status} />
        {task.status === "done" && <MetadataBand task={task} />}
      </header>

      {/* 内容区：三态；源码视图换容器级双向滚动（滚动条贴视口边缘） */}
      <div
        className={`${s.content} ${task.status === "done" && task.markdown !== null && view === "source" ? s.contentSrc : ""}`}
        ref={contentRef}
      >
        {running && <RunningState status={task.status} createdAt={task.created_at} />}
        {task.status === "failed" && (
          <div className={s.errorCard} role="alert">
            <h2 className={s.errorTitle}>转换失败</h2>
            <pre className={s.errorBody}>{task.error ?? "（无错误信息）"}</pre>
          </div>
        )}
        {task.status === "done" && task.markdown !== null && (
          <>
            {view === "rendered"
              ? <MarkdownView markdown={task.markdown} />
              : <pre className={s.source}>{task.markdown}</pre>}
          </>
        )}
      </div>

      {task.status === "done" && task.markdown !== null && (
        <footer className={s.footer}>
          <div
            className={s.segment}
            role="radiogroup"
            aria-label="视图模式"
          >
            <span
              className={`${s.segIndicator} ${view === "source" ? s.segIndicatorRight : ""}`}
              aria-hidden="true"
            />
            <button
              type="button"
              role="radio"
              aria-checked={view === "rendered"}
              className={`${s.segBtn} ${view === "rendered" ? s.segActive : ""}`}
              onClick={() => handleViewChange("rendered")}
            >
              <IcEye size={14} />
              渲染
            </button>
            <button
              type="button"
              role="radio"
              aria-checked={view === "source"}
              className={`${s.segBtn} ${view === "source" ? s.segActive : ""}`}
              onClick={() => handleViewChange("source")}
            >
              <IcCode size={14} />
              源码
            </button>
          </div>
          <div className={s.actions}>
            <CopyButton text={task.markdown} />
            <DownloadButton task={task} />
          </div>
        </footer>
      )}
    </div>
  );
}

/** 产出组成形态标签：非缺省（纯解析 body）且非全形态（body,url,ocr,alt）时显示组合概要。
 *  output 是后端规范序 CSV；纯解析是缺省态不标（图片数为 0 已可区分），全形态不标（完整产出无需标注） */
function outputLabel(output: string): string[] {
  const parts = output.split(",");
  if (parts.length <= 2) {
    // body 单独（缺省）或 body+单一呈现部件
    if (parts.includes("url")) return ["仅链接"];
    if (parts.includes("base64")) return ["仅内嵌"];
    return [];
  }
  if (parts.includes("ocr") && parts.includes("alt")) return [];  // 全形态（含内容双选）
  const presentation = parts.includes("url") ? "链接" : "内嵌";
  const content = parts.includes("ocr") ? "转写" : "描述";
  return [`${presentation}+${content}`];
}

/** 完成态统计 */
function MetadataBand({ task }: { task: TaskDetail }) {
  return (
    <span className={s.meta}>
      <span className={s.metaStat}>
        <span className={`${s.metaValue} numeric`}>{formatNumber(task.image_count)}</span>
        <span className={s.metaLabel}>图片</span>
      </span>
      <span className={s.metaStat}>
        <span className={`${s.metaValue} numeric`}>{formatNumber(task.char_count)}</span>
        <span className={s.metaLabel}>字符</span>
      </span>
      <span className={s.metaStat}>
        <span className={s.metaValue}>{formatElapsed(task.elapsed_ms)}</span>
        <span className={s.metaLabel}>耗时</span>
      </span>
      <span className={s.metaStat}>
        <span className={s.metaValue}>{formatSize(task.size_bytes)}</span>
        <span className={s.metaLabel}>大小</span>
      </span>
      {task.cache_hit && <span className={s.cacheBadge}>⚡ 之前转过，直接复用</span>}
    </span>
  );
}

/**
 * 转换中：骨架屏占位，避免内容区空白。
 * 阶段文案只描述真实管线（解析 → 转写 → 产出），不虚构进度百分比。
 */
function RunningState({ status, createdAt }: { status: string; createdAt: string }) {
  return (
    <div className={s.running}>
      <div className={s.runningHead}>
        <span className={s.runningDot} aria-hidden="true" />
        <p className={s.runningText}>
          {status === "pending" ? "排队中" : (
            <>转换中 · <LiveSeconds createdAt={createdAt} />s</>
          )}
        </p>
        <p className={s.runningHint}>
          {status === "pending"
            ? "任务已受理，正在启动转换"
            : "解析正文 · 转写图片 · 产出 Markdown"}
        </p>
      </div>
      <div className={s.skeleton} aria-hidden="true">
        <span className={s.skLine} style={{ width: "42%" }} />
        <span className={s.skLine} style={{ width: "88%" }} />
        <span className={s.skLine} style={{ width: "76%" }} />
        <span className={s.skBlock} />
        <span className={s.skLine} style={{ width: "94%" }} />
        <span className={s.skLine} style={{ width: "58%" }} />
      </div>
    </div>
  );
}

/** 实时计时（tabular-nums 防抖动） */
function LiveSeconds({ createdAt }: { createdAt: string }) {
  const sec = useLiveSeconds(true, createdAt);
  return <span className="numeric">{sec}</span>;
}

function CopyButton({ text }: { text: string }) {
  const [state, setState] = useState<"idle" | "ok" | "fail">("idle");
  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(text);
      setState("ok");
    } catch {
      setState("fail");
    }
    setTimeout(() => setState("idle"), 2400);
  }
  return (
    <button
      type="button"
      className={`${s.action} ${state === "ok" ? s.actionOk : ""}`}
      onClick={() => void handleCopy()}
    >
      {state === "ok" ? <IcCheck size={14} /> : <IcCopy size={14} />}
      {state === "ok" ? "已复制" : state === "fail" ? "复制失败" : "复制 Markdown"}
    </button>
  );
}

function DownloadButton({ task }: { task: TaskDetail }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className={s.action}
      onClick={() => {
        const blob = new Blob([task.markdown ?? ""], {
          type: "text/markdown;charset=utf-8",
        });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = task.filename.replace(/\.[^.]+$/, "") + ".md";
        a.click();
        URL.revokeObjectURL(url);
        setDone(true);
        setTimeout(() => setDone(false), 2400);
      }}
    >
      {done ? <IcCheck size={14} /> : <IcDownload size={14} />}
      {done ? "已下载" : "下载 .md"}
    </button>
  );
}
