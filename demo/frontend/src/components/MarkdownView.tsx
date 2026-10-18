import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import s from "./MarkdownView.module.css";

/** react-markdown 封装：GFM + 图片懒加载可点开 + 表格横向滚动 + 代码块复制；
 *  排版质量是本组件的重点 */

/** 代码块：外壳带复制按钮（pre 覆写，children 已是渲染好的 code 元素） */
function CodeBlock({ children }: { children: React.ReactNode }) {
  const [copied, setCopied] = useState(false);
  function handleCopy(e: React.MouseEvent<HTMLButtonElement>) {
    const pre = e.currentTarget.closest("pre");
    const text = pre?.querySelector("code")?.textContent ?? "";
    void navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2400);
    }).catch(() => {
      // 剪贴板被拒（非安全上下文等）：保持未复制态，不产生未处理拒绝
    });
  }
  return (
    <pre className={s.pre}>
      {children}
      <button type="button" className={s.codeCopy} onClick={handleCopy} aria-label="复制代码">
        {copied ? "已复制" : "复制"}
      </button>
    </pre>
  );
}

export default function MarkdownView({ markdown }: { markdown: string }) {
  return (
    <div className={s.doc}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          img: ({ alt, src }) => (
            <span className={s.imgWrap}>
              {/* 图片来自对象存储公开 URL 或内嵌 data URI，点开看原图 */}
              <a href={src} target="_blank" rel="noreferrer" title="在新窗口打开原图">
                <img src={src} alt={alt ?? ""} loading="lazy" />
              </a>
            </span>
          ),
          pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
          table: ({ children }) => <div className={s.tableWrap}><table>{children}</table></div>,
        }}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  );
}
