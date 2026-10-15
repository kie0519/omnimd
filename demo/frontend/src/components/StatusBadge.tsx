import type { TaskStatus } from "../types";
import s from "./StatusBadge.module.css";

const LABEL: Record<TaskStatus, string> = {
  pending: "排队中",
  running: "转换中",
  done: "已完成",
  failed: "失败",
};

export default function StatusBadge({ status }: { status: TaskStatus }) {
  return (
    <span className={`${s.badge} ${s[status]}`}>
      <span className={s.dot} aria-hidden="true" />
      {LABEL[status]}
    </span>
  );
}
