import { useEffect, useRef } from "react";
import { groupFormats } from "../format";
import { IcX } from "./Icons";
import s from "./HelpPanel.module.css";

interface Props {
  open: boolean;
  onClose: () => void;
  /** 后端支持扩展名清单——格式列表的唯一来源，不在此复制第二份 */
  formats: string[] | null;
}

/**
 * 使用指南（只读）：怎么用 / 参数怎么配 / 限制与隐私。
 * 连接配置不在界面上编辑——连接配置只读服务端 .env；
 * 面板只解释配置在哪、有哪些维度。产出组成是上传时可选的运行参数，
 * 在上传区选择，不在此面板操作。
 */
export default function HelpPanel({ open, onClose, formats }: Props) {
  const panelRef = useRef<HTMLElement>(null);

  // 关闭时移出 Tab 序（面板滑出屏外但仍在 DOM 中承载过渡动画）
  useEffect(() => {
    if (panelRef.current) panelRef.current.inert = !open;
  }, [open]);

  return (
    <>
      <div
        className={`${s.backdrop} ${open ? s.backdropOpen : ""}`}
        onClick={onClose}
        aria-hidden="true"
      />
      <aside
        ref={panelRef}
        className={`${s.panel} ${open ? s.open : ""}`}
        aria-label="使用指南"
      >
        <header className={s.header}>
          <h2 className={s.title}>使用指南</h2>
          <button
            type="button"
            className={s.closeBtn}
            onClick={onClose}
            aria-label="关闭使用指南"
          >
            <IcX size={16} />
          </button>
        </header>

        <div className={s.body}>
          {/* ── 怎么用 ── */}
          <section className={s.section}>
            <h3 className={s.sectionTitle}>怎么用</h3>
            <ol className={s.flow}>
              <li>
                <b>上传文件</b>——点击或拖入上传区，创建转换任务。
              </li>
              <li>
                <b>等待转换</b>——大文件的转换可能需要几分钟，任务完成后自动展示结果。
              </li>
              <li>
                <b>查看与下载</b>——渲染视图阅读或源码核对，可下载 <code>.md</code>{" "}
                文件或复制全文。
              </li>
            </ol>
            <p className={s.note}>
              状态含义：<span className={s.badge}>排队中</span> 任务已受理，正在启动转换 ·{" "}
              <span className={s.badge}>转换中</span> 解析正文、转写图片、产出 Markdown ·{" "}
              <span className={s.badge}>已完成</span> 可查看与下载 ·{" "}
              <span className={s.badge}>失败</span> 查看错误原因
            </p>
          </section>

          {/* ── 参数怎么配 ── */}
          <section className={s.section}>
            <h3 className={s.sectionTitle}>参数怎么配</h3>
            <p className={s.note}>
              配置分两类：<b>连接配置</b>决定「用哪家的模型与存储」，
              只在服务端的 <code>.env</code> 中配置（<b>切换只改 .env，不改代码</b>
              ，改完重启服务生效），界面上不提供编辑；
              <b>产出组成</b>是运行参数，在上传区按次选择。
            </p>
            <div className={s.tableWrap}>
              <table className={s.table}>
                <thead>
                  <tr>
                    <th>维度</th>
                    <th>可选值</th>
                    <th>说明</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>解析引擎</td>
                    <td>
                      <code>markitdown</code> / <code>anydoc</code>
                    </td>
                    <td>非 PDF、非图片文件的解析器（PDF 固定走专用引擎，图片单独处理）</td>
                  </tr>
                  <tr>
                    <td>VLM 视觉模型</td>
                    <td>
                      <code>VLM_BASE_URL</code> 等凭据三项
                    </td>
                    <td>图片转写端点——任意 OpenAI 兼容端点直接接入（OpenAI / DeepSeek / 本地 Ollama 等）；<code>VLM_THINKING</code> 选思考控制方式</td>
                  </tr>
                  <tr>
                    <td>文件存储</td>
                    <td>
                      <code>s3</code>
                    </td>
                    <td>图片托管后端（S3 兼容对象存储：MinIO / OBS / OSS / COS 等），产出公开链接</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p className={s.note}>
              以上连接配置的模板与各项含义见 omnimd 仓库的{" "}
              <code>.env.example</code>（密钥等真实值只存在于服务端{" "}
              <code>.env</code>，缺失时服务可启动，首次转换会失败并显示配置错误）。
            </p>
            <p className={s.note}>
              <b>产出组成</b>（上传时可选）：
              <code>图片链接</code> 保留图片公开链接，
              <code>转写文本</code> 附加视觉模型的图片转写，
              <code>链接描述</code> 为链接生成一句话描述（依赖图片链接）；
              转写文本与链接描述需调用视觉模型，产生调用成本。
              至少选一项；缺省选中图片链接与转写文本。
              选择随上传记录保存，历史记录按各自的组成回看。
            </p>
          </section>

          {/* ── 限制与隐私 ── */}
          <section className={s.section}>
            <h3 className={s.sectionTitle}>限制与隐私</h3>
            <ul className={s.list}>
              <li>单文件 ≤ 50MB，同时最多 3 个转换任务；并发占满时新上传会被拒绝，请稍后重试。</li>
              <li>上传的原始文件为临时文件，转换完成后即删除。</li>
              <li>服务端只保留产出的 Markdown 与统计信息，不保留原始文件。</li>
              <li>删除历史记录会同时清除对应的转换结果。</li>
            </ul>
          </section>

          {/* ── 支持格式 ── */}
          <section className={s.section}>
            <h3 className={s.sectionTitle}>支持格式</h3>
            <p className={s.note}>
              {formats
                ? `当前服务端支持 ${formats.length} 种格式：`
                : "格式清单加载中…"}
            </p>
            {formats && (
              <div className={s.formatBoard}>
                {groupFormats(formats).map((g) => (
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
          </section>
        </div>
      </aside>
    </>
  );
}
