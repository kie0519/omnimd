import { useEffect, useState } from "react";
import type { TaskSummary } from "../types";
import { formatElapsed, formatNumber } from "../format";
import StatusBadge from "./StatusBadge";
import { IcX } from "./Icons";
import s from "./TaskItem.module.css";

interface Props {
  task: TaskSummary;
  selected: boolean;
  onSelect: (taskId: string) => void;
  onDelete: (taskId: string) => Promise<boolean>;
}

/** running 行的实时计时：秒级跳动，等宽数字防抖动 */
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

export default function TaskItem({ task, selected, onSelect, onDelete }: Props) {
  // 仅 running 启动计时器（pending 只显示「排队中」，不空转定时器）
  const liveSec = useLiveSeconds(task.status === "running", task.created_at);
  const [confirming, setConfirming] = useState(false);
  const [leaving, setLeaving] = useState(false);

  // 确认态 3s 未操作自动还原
  useEffect(() => {
    if (!confirming) return;
    const id = setTimeout(() => setConfirming(false), 3000);
    return () => clearTimeout(id);
  }, [confirming]);

  const meta =
    task.status === "pending" ? "排队中" :
    task.status === "running" ? `转换中 · ${liveSec}s` :
    task.status === "done"
      ? [
          task.image_count !== null && `${task.image_count} 图`,
          task.char_count !== null && `${formatNumber(task.char_count)} 字`,
          task.elapsed_ms !== null && formatElapsed(task.elapsed_ms),
          task.cache_hit && "⚡ 之前转过，直接用上次结果",
        ].filter(Boolean).join(" · ")
      : "失败 · 点击查看";

  // 确认删除：先播退出动画，动画结束后真正删除；
  // 服务端删除失败时回滚 leaving 态（记录保持可见、可再次删除）
  async function handleDeleteClick() {
    if (leaving) return;
    if (!confirming) {
      setConfirming(true);
      return;
    }
    setLeaving(true);
    setTimeout(async () => {
      const removed = await onDelete(task.task_id);
      if (!removed) setLeaving(false);
    }, 150);
  }

  return (
    <li className={`${s.item} ${leaving ? s.leaving : ""}`}>
      <button
        type="button"
        className={`${s.row} ${selected ? s.selected : ""}`}
        onClick={() => onSelect(task.task_id)}
        aria-current={selected || undefined}
      >
        <StatusBadge status={task.status} />
        <span className={s.name} title={task.filename}>
          {task.filename}
        </span>
        <span className={`${s.meta} ${task.cache_hit ? s.cached : ""}`}>{meta}</span>
      </button>
      <button
        type="button"
        className={`${s.delete} ${confirming && !leaving ? s.confirming : ""}`}
        onClick={handleDeleteClick}
        onBlur={() => setConfirming(false)}
        onKeyDown={(e) => {
          if (e.key === "Escape") setConfirming(false);
        }}
        aria-label={confirming ? "确认删除" : "删除该记录"}
        title={confirming ? "确认删除" : "删除"}
      >
        {confirming && !leaving ? "确认删除" : <IcX size={12} />}
      </button>
    </li>
  );
}
