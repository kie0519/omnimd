import { useEffect, useRef, useState } from "react";
import {
  fetchPromptTemplates,
  runPrompt,
  type PromptTemplate,
} from "../api";
import { IcX } from "./Icons";
import MarkdownView from "./MarkdownView";
import s from "./PromptStudio.module.css";

interface Props {
  open: boolean;
  /** 主界面当前生效的提示词（打开时带入编辑区） */
  initialPrompt: string;
  /** 主界面当前生效的系统提示词（空串 = 通用骨架） */
  initialSystem: string;
  /** 关闭（弃置编辑内容；Esc 与右上 X） */
  onClose: () => void;
  /** 应用到本次上传：prompt 空串 = 清除自定义（system 随 prompt 一并清除） */
  onApply: (prompt: string, system: string) => void;
}

const MAX_PROMPT_CHARS = 10000; // 与后端 MAX_PROMPT_CHARS 同值（改值需两侧同步）

type OutputState =
  | { kind: "empty" }
  | { kind: "streaming"; text: string }
  | { kind: "done"; markdown: string; elapsedMs: number; tokens: number | null }
  | { kind: "error"; message: string };

/**
 * 提示词工作台（二级模态）：左栏输入（提示词 + 图片选择与预览 + 生成），
 * 右栏输出（流式逐字滚出 → Markdown 渲染；空态是模板卡片矩阵）。
 *
 * 双编辑文件（IDE 心智）：提示词与系统提示词共用同一编辑区——头部 ⚙ 图标
 * 就地切换编辑对象（不是弹窗）；system 空 = 内置通用骨架（图标灰），
 * 自定义后高亮；「以通用骨架为起点」让垫底内容透明可改。
 * 模板卡片完整回填：正文进提示词、任务 system 进系统提示词——卡片即任务文件全貌。
 * 流式中生成按钮变停止；Esc 三级（lightbox → 全屏 → 关闭）。
 */
export default function PromptStudio({
  open, initialPrompt, initialSystem, onClose, onApply,
}: Props) {
  const [text, setText] = useState(initialPrompt);
  const [systemText, setSystemText] = useState(initialSystem);
  const [editingSystem, setEditingSystem] = useState(false);
  const [generalSystem, setGeneralSystem] = useState("");
  const [image, setImage] = useState<File | null>(null);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [lightbox, setLightbox] = useState(false);
  const [running, setRunning] = useState(false);
  const [output, setOutput] = useState<OutputState>({ kind: "empty" });
  const [ranWith, setRanWith] = useState<{
    prompt: string; system: string; imageName: string;
  } | null>(null);
  const [fullscreen, setFullscreen] = useState(false);
  const [templates, setTemplates] = useState<PromptTemplate[] | null>(null);
  const [templatesFailed, setTemplatesFailed] = useState(false);
  const [sourceView, setSourceView] = useState(false);
  const editRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const outRef = useRef<HTMLDivElement>(null);

  // 打开时带入当前提示词、重置运行态、聚焦编辑区并拉取模板卡片
  useEffect(() => {
    if (!open) return;
    setText(initialPrompt);
    setSystemText(initialSystem);
    setEditingSystem(false);
    setOutput({ kind: "empty" });
    setRanWith(null);
    setLightbox(false);
    setSourceView(false);
    requestAnimationFrame(() => editRef.current?.focus());
    const controller = new AbortController();
    fetchPromptTemplates(controller.signal)
      .then((d) => {
        setTemplates(d.templates);
        setGeneralSystem(d.general_system);
        setTemplatesFailed(false);
      })
      .catch(() => {
        setTemplates(null);
        setTemplatesFailed(true);
      });
    return () => controller.abort();
  }, [open, initialPrompt, initialSystem]);

  // 原图 objectURL 生命周期
  useEffect(() => {
    const url = image ? URL.createObjectURL(image) : null;
    setImageUrl(url);
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [image]);

  // 流式输出自动滚底（跟随最新增量）
  useEffect(() => {
    if (output.kind === "streaming" && outRef.current) {
      outRef.current.scrollTop = outRef.current.scrollHeight;
    }
  }, [output]);

  // Esc 三级：lightbox 优先收起 → 全屏先退 → 普通态关闭
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        if (lightbox) setLightbox(false);
        else if (fullscreen) setFullscreen(false);
        else onClose();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [open, fullscreen, lightbox, onClose]);

  // 关闭即中止在途生成
  useEffect(() => {
    if (!open) abortRef.current?.abort();
  }, [open]);

  if (!open) return null;

  const trimmed = text.trim();
  const systemCustom = systemText.trim().length > 0;
  const activeText = editingSystem ? systemText : text;
  const dirty =
    ranWith !== null &&
    (trimmed !== ranWith.prompt ||
      systemText.trim() !== ranWith.system ||
      image?.name !== ranWith.imageName);
  const canGenerate = !running && trimmed.length > 0 && image !== null;

  function handleGenerate() {
    if (!canGenerate || !image) return;
    const controller = new AbortController();
    abortRef.current = controller;
    setRunning(true);
    setOutput({ kind: "streaming", text: "" });
    let acc = "";
    runPrompt(
      image,
      trimmed,
      systemText.trim(),
      (delta) => {
        acc += delta;
        setOutput({ kind: "streaming", text: acc });
      },
      controller.signal
    )
      .then((done) => {
        setOutput({
          kind: "done", markdown: done.markdown,
          elapsedMs: done.elapsed_ms,
          tokens: done.usage ? done.usage.total_tokens : null,
        });
        setRanWith({
          prompt: trimmed, system: systemText.trim(), imageName: image.name,
        });
      })
      .catch((e) => {
        if (e instanceof DOMException && e.name === "AbortError") {
          setOutput({ kind: "empty" });   // 用户停止：回到空态
          return;
        }
        setOutput({
          kind: "error",
          message: e instanceof Error ? e.message : "生成失败",
        });
      })
      .finally(() => setRunning(false));
  }

  return (
    <>
      <div className={s.backdrop} onClick={onClose} aria-hidden="true" />
      <div
        className={`${s.panel} ${fullscreen ? s.fullscreen : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label="自定义图片分析"
      >
        <header className={s.header}>
          <h2 className={s.title}>✎ 自定义图片分析</h2>
          <div className={s.headerActions}>
            <button
              type="button"
              className={trimmed ? s.applyBtn : s.clearTopBtn}
              disabled={!trimmed && !initialPrompt.trim()}
              onClick={() => onApply(trimmed, systemCustom ? systemText.trim() : "")}
              title={
                trimmed
                  ? "把当前提示词与系统提示词用于本次上传"
                  : "清空提示词并移除自定义分析"
              }
            >
              {trimmed ? "应用到本次上传" : "清除自定义"}
            </button>
            <button
              type="button"
              className={s.headerBtn}
              onClick={() => setFullscreen((f) => !f)}
            >
              {fullscreen ? "退出全屏" : "全屏"}
            </button>
            <button type="button" className={s.headerBtn} onClick={onClose} aria-label="关闭">
              <IcX size={14} />
            </button>
          </div>
        </header>

        <div className={s.body}>
          {/* 左栏：输入——提示词（含 system 就地切换）与图片 */}
          <section className={s.left} aria-label="撰写与图片">
            <div className={s.imageBlock}>
              {image ? (
                <>
                  <button
                    type="button"
                    className={s.imageThumb}
                    onClick={() => setLightbox(true)}
                    title="点击放大查看"
                  >
                    <img src={imageUrl ?? undefined} alt="待生成图片" />
                  </button>
                  <div className={s.imageRow}>
                    <span className={s.imageName} title={image.name}>{image.name}</span>
                    <button type="button" className={s.imageOp} onClick={() => fileRef.current?.click()}>
                      换一张
                    </button>
                    <button type="button" className={s.imageOp} onClick={() => setImage(null)}>
                      移除
                    </button>
                  </div>
                </>
              ) : (
                <button
                  type="button"
                  className={s.imagePick}
                  onClick={() => fileRef.current?.click()}
                >
                  选择一张图片用于生成
                </button>
              )}
              <input
                ref={fileRef}
                type="file"
                hidden
                accept="image/*"
                onChange={(e) => {
                  if (e.target.files?.[0]) setImage(e.target.files[0]);
                  e.target.value = "";
                }}
              />
            </div>
            {editingSystem ? (
              /* system 编辑上下文：同一编辑区切换对象（IDE 双文件心智） */
              <div className={s.sysBar}>
                <button
                  type="button"
                  className={s.sysBack}
                  onClick={() => setEditingSystem(false)}
                >
                  ← 返回提示词
                </button>
                <span className={s.sysTitle}>系统提示词</span>
                <button
                  type="button"
                  className={s.sysOp}
                  onClick={() => setSystemText(generalSystem)}
                  title="把内置通用骨架填入编辑区作为起点，可再修改"
                >
                  以通用骨架为起点
                </button>
                <button
                  type="button"
                  className={s.sysOp}
                  onClick={() => setSystemText("")}
                  title="清空系统提示词，回到内置通用骨架"
                >
                  清除自定义
                </button>
              </div>
            ) : null}
            <div className={s.fieldHead}>
              <span className={s.fieldLabel}>
                {editingSystem ? "系统提示词（不填 = 内置通用骨架）" : "提示词"}
              </span>
              <span className={s.headOps}>
                <span
                  className={`${s.count} ${activeText.length > MAX_PROMPT_CHARS ? s.countOver : ""}`}
                >
                  {activeText.length} / {MAX_PROMPT_CHARS}
                </span>
                {!editingSystem && (
                  <button
                    type="button"
                    className={`${s.sysToggle} ${systemCustom ? s.sysToggleOn : ""}`}
                    onClick={() => setEditingSystem(true)}
                    aria-pressed={systemCustom}
                    title={
                      systemCustom
                        ? "系统提示词已自定义（点击查看与修改）"
                        : "系统提示词：当前使用内置通用骨架（点击自定义）"
                    }
                  >
                    ⚙{systemCustom ? " ●" : ""}
                  </button>
                )}
              </span>
            </div>
            <textarea
              ref={editRef}
              className={s.editor}
              value={activeText}
              onChange={(e) =>
                editingSystem ? setSystemText(e.target.value) : setText(e.target.value)
              }
              placeholder={
                editingSystem
                  ? "系统提示词可选——缺省用内置通用骨架（点上方「以通用骨架为起点」查看与修改）。领域方法论（角色设定、输出纪律）写在这里，任务本体写在提示词里。"
                  : "想让模型对图片做什么？写下提示词……\n左侧写提示词，右侧看结果——改一版，生成一次"
              }
              spellCheck={false}
            />
            <button
              type="button"
              className={running ? s.stopBtn : s.runBtn}
              disabled={!running && !canGenerate}
              onClick={() => {
                if (running) abortRef.current?.abort();
                else handleGenerate();
              }}
              title={
                running
                  ? "停止当前生成"
                  : image
                    ? "用当前提示词生成（调用视觉模型，逐字输出）"
                    : "先选择一张图片"
              }
            >
              {running ? "■ 停止" : "▶ 生成"}
            </button>
          </section>

          {/* 右栏：输出——流式滚出 → 渲染/源码 */}
          <section className={s.right} aria-label="生成结果">
            <div className={s.rightHead}>
              <div className={s.seg} role="radiogroup" aria-label="输出视图">
                <button
                  type="button"
                  role="radio"
                  aria-checked={!sourceView}
                  className={`${s.segBtn} ${!sourceView ? s.segOn : ""}`}
                  onClick={() => setSourceView(false)}
                >
                  阅读
                </button>
                <button
                  type="button"
                  role="radio"
                  aria-checked={sourceView}
                  className={`${s.segBtn} ${sourceView ? s.segOn : ""}`}
                  onClick={() => setSourceView(true)}
                >
                  源码
                </button>
              </div>
              {output.kind === "done" && (
                <span className={s.meta}>
                  {(output.elapsedMs / 1000).toFixed(1)}s
                  {output.tokens !== null
                    ? ` · ${(output.tokens / 1000).toFixed(1)}k tokens`
                    : ""}
                </span>
              )}
            </div>

            {dirty && output.kind !== "empty" && (
              <div className={s.dirty} role="status">
                提示词已修改，重新生成查看最新结果
              </div>
            )}

            <div className={s.output} ref={outRef}>
              {output.kind === "empty" ? (
                <div className={s.welcome}>
                  <p className={s.welcomeTitle}>用一句提示词，让模型按你的意图处理图片</p>
                  {templates ? (
                    <div className={s.cards}>
                      {templates.map((tpl) => (
                        <button
                          key={tpl.name}
                          type="button"
                          className={s.card}
                          onClick={() => {
                            setText(tpl.prompt);
                            setSystemText(tpl.system);
                            setEditingSystem(false);
                            editRef.current?.focus();
                          }}
                          title="点击把这段提示词与它的系统提示词填入左侧（可再修改）"
                        >
                          <span className={s.cardLabel}>{tpl.label}</span>
                          <span className={s.cardBlurb}>{tpl.blurb}</span>
                        </button>
                      ))}
                    </div>
                  ) : templatesFailed ? (
                    <p className={s.welcomeSub}>模板加载失败，关掉重开试试</p>
                  ) : (
                    <p className={s.welcomeSub}>模板加载中…</p>
                  )}
                  <p className={s.welcomeSub}>点一个开始——提示词会填入左侧，选张图生成看看</p>
                </div>
              ) : output.kind === "streaming" ? (
                <pre className={s.stream}>{output.text}</pre>
              ) : output.kind === "error" ? (
                <div className={s.error} role="alert">
                  生成失败：{output.message}
                </div>
              ) : sourceView ? (
                <pre className={s.stream}>{output.markdown}</pre>
              ) : (
                <MarkdownView markdown={output.markdown} />
              )}
            </div>
          </section>
        </div>
      </div>

      {/* lightbox：全屏朦胧遮罩 + 居中大图（点遮罩 / Esc 关闭） */}
      {lightbox && imageUrl && (
        <div className={s.lightbox} onClick={() => setLightbox(false)} role="dialog" aria-label="图片预览">
          <img src={imageUrl} alt="放大查看" className={s.lightboxImg} />
        </div>
      )}
    </>
  );
}
