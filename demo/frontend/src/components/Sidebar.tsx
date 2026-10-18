import { useEffect, useRef } from "react";
import type { TaskSummary } from "../types";
import TaskItem from "./TaskItem";
import { IcHelp, IcX } from "./Icons";
import s from "./Sidebar.module.css";

interface Props {
  tasks: TaskSummary[];
  /** 搜索命中的真实总数（可能大于列表展示的 50 条） */
  total: number;
  /** 首次列表拉取是否完成：未完成时不显示空态（避免「记录丢了」误读闪现） */
  loaded: boolean;
  selectedId: string | null;
  /** 受控搜索文本（防抖由上层处理），空串表示无过滤 */
  value: string;
  onValueChange: (text: string) => void;
  onSelect: (taskId: string) => void;
  onDelete: (taskId: string) => Promise<boolean>;
  onNew: () => void;
  /** 打开使用指南（帮助面板，App 持有开合状态） */
  onHelp: () => void;
  open: boolean;
  onClose: () => void;
  /** 全局 Ctrl/⌘+K 聚焦信号：每次递增触发一次聚焦 */
  focusSignal: number;
}

/** 按创建时间分组：今天 / 昨天 / 本周 / 更早 */
function groupOf(createdAt: string): string {
  const d = new Date(createdAt);
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const diffDays = (startOfToday.getTime() - d.getTime()) / 86_400_000;
  if (diffDays < 1) return "今天";
  if (diffDays < 2) return "昨天";
  if (diffDays < 7) return "本周";
  return "更早";
}

export default function Sidebar({
  tasks,
  loaded,
  selectedId,
  value,
  onValueChange,
  onSelect,
  onDelete,
  onNew,
  onHelp,
  open,
  onClose,
  focusSignal,
  total,
}: Props) {
  const searchRef = useRef<HTMLInputElement>(null);
  const sidebarRef = useRef<HTMLElement>(null);

  // 抽屉打开时焦点圈入（窄屏）；桌面 open 恒为 false 不触发
  useEffect(() => {
    if (open) searchRef.current?.focus();
  }, [open]);

  // 全局快捷键信号：Ctrl/⌘+K → 聚焦搜索框（窄屏时 App 已先开抽屉）
  useEffect(() => {
    if (focusSignal > 0) searchRef.current?.focus();
  }, [focusSignal]);

  // 窄屏关闭态移出 Tab 序（滑出屏外但仍在 DOM 中承载过渡动画）；
  // 桌面态（≥ 900px）侧栏常驻不受 open 影响。跨断点拉宽时重算——
  // 否则窄屏关抽屉后拉宽，侧栏保持 inert 且无法再打开抽屉
  useEffect(() => {
    const apply = () => {
      if (sidebarRef.current)
        sidebarRef.current.inert = !open && window.innerWidth < 900;
    };
    apply();
    window.addEventListener("resize", apply);
    return () => window.removeEventListener("resize", apply);
  }, [open]);

  const groups: { label: string; items: TaskSummary[] }[] = [];
  for (const t of tasks) {
    const label = groupOf(t.created_at);
    const g = groups.find((x) => x.label === label);
    if (g) g.items.push(t);
    else groups.push({ label, items: [t] });
  }

  return (
    <>
      <div
        className={`${s.backdrop} ${open ? s.backdropOpen : ""}`}
        onClick={onClose}
        aria-hidden="true"
      />
      <aside ref={sidebarRef} className={`${s.sidebar} ${open ? s.open : ""}`} aria-label="历史记录">
        <div className={s.brand}>
          {/* 产品 LOGO（public/logo.svg，与浏览器图标同源）；装饰图——
              品牌名由相邻 wordmark 文本提供 */}
          <img className={s.mark} src="/logo.svg" width={26} height={26} alt="" aria-hidden="true" />
          <span className={s.wordmark}>omnimd</span>
          <span className={s.tagline}>任意文件 → Markdown</span>
          <button
            type="button"
            className={s.helpBtn}
            onClick={onHelp}
            aria-label="使用指南"
            title="使用指南"
          >
            <IcHelp size={16} />
          </button>
        </div>

        <div className={s.searchWrap}>
          <input
            ref={searchRef}
            className={s.search}
            type="search"
            value={value}
            placeholder="搜索文件名…"
            aria-label="搜索历史记录"
            aria-keyshortcuts="Control+K Meta+K"
            onChange={(e) => onValueChange(e.target.value)}
          />
          {value !== "" && (
            <button
              type="button"
              className={s.clearBtn}
              onClick={() => {
                onValueChange("");
                searchRef.current?.focus();
              }}
              aria-label="清除搜索"
            >
              <IcX size={12} />
            </button>
          )}
        </div>

        <div className={s.list}>
          {tasks.length === 0 && loaded && (
            <div className={s.empty}>
              <p className={s.emptyTitle}>
                {value ? `没有匹配「${value}」的记录` : "还没有转换记录"}
              </p>
              {value ? (
                <p className={s.emptyHint}>
                  换个关键词试试，或
                  <button
                    type="button"
                    className={s.emptyAction}
                    onClick={() => onValueChange("")}
                  >
                    清除搜索
                  </button>
                </p>
              ) : (
                <p className={s.emptyHint}>转换过的文件会记录在这里。</p>
              )}
            </div>
          )}
          {value !== "" && tasks.length > 0 && (
            <p className={s.resultCount}>
              {total} 条结果
              {total > tasks.length && `（显示前 ${tasks.length} 条）`}
            </p>
          )}
          {groups.map((g) => (
            <section key={g.label}>
              <h2 className={s.group}>{g.label}</h2>
              <ul className={s.groupItems}>
                {g.items.map((t) => (
                  <TaskItem
                    key={t.task_id}
                    task={t}
                    selected={t.task_id === selectedId}
                    onSelect={onSelect}
                    onDelete={onDelete}
                  />
                ))}
              </ul>
            </section>
          ))}
        </div>

        <div className={s.footer}>
          <button type="button" className={s.newBtn} onClick={onNew}>
            ＋ 新建转换
          </button>
        </div>
      </aside>
    </>
  );
}
