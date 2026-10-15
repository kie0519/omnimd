import s from "./OfflineBanner.module.css";

export default function OfflineBanner({ show }: { show: boolean }) {
  return (
    <div
      className={`${s.banner} ${show ? s.show : ""}`}
      role="status"
      aria-live="polite"
    >
      {show ? "服务连接中断，正在重试…" : ""}
    </div>
  );
}
