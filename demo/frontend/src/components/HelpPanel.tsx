import { useEffect, useRef } from "react";
import { groupFormats, MAX_CONCURRENT_TASKS, MAX_UPLOAD_MB } from "../format";
import { IcX } from "./Icons";
import s from "./HelpPanel.module.css";

interface Props {
  open: boolean;
  onClose: () => void;
  /** 后端支持扩展名清单（来自 /api/formats；null 表示尚未就绪） */
  formats: string[] | null;
}

/**
 * 使用指南（只读）：怎么用 / 参数怎么配 / 限制与隐私。
 * 面板只解释配置在哪、有哪些配置项，不提供连接配置编辑；
 * 产出组成是上传时可选的运行参数，在上传区选择，不在此面板操作。
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

          <section className={s.section}>
            <h3 className={s.sectionTitle}>参数怎么配</h3>
            <p className={s.note}>
              配置分两类：<b>连接配置</b>决定「用哪家的模型与存储」，
              只在服务端的 <code>.env</code> 中配置（<b>切换只改 .env，不改代码</b>
              ，改完重启服务，新配置才会用上），界面上不提供编辑；
              <b>产出组成</b>是运行参数，在上传区按次选择。
            </p>
            <div className={s.tableWrap}>
              <table className={s.table}>
                <thead>
                  <tr>
                    <th>配置项</th>
                    <th>可选值</th>
                    <th>说明</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>解析引擎</td>
                    <td>
                      {/* 取值以库的引擎注册表（omnimd/engines）为准 */}
                      <code>markitdown</code> / <code>anydoc</code>
                    </td>
                    <td>负责把文档读成文字（PDF 固定走专用引擎，图片单独处理）</td>
                  </tr>
                  <tr>
                    <td>视觉模型</td>
                    <td>任意 OpenAI 兼容服务</td>
                    <td>负责看图转写（OpenAI / 智谱 GLM / 本地 Ollama 等），密钥填 <code>VLM_API_KEY</code> / <code>VLM_BASE_URL</code> / <code>VLM_MODEL</code> 三项；<code>VLM_THINKING</code> 选思考方式</td>
                  </tr>
                  <tr>
                    <td>文件存储</td>
                    <td>
                      <code>s3</code>
                    </td>
                    <td>给「链接」存放图片的地方（S3 兼容对象存储：MinIO / OBS / OSS / COS 等）</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p className={s.note}>
              连接配置怎么填见 omnimd 仓库的{" "}
              <code>.env.example</code>（密钥只写在服务端{" "}
              <code>.env</code> 里；只提取正文时不需要配置任何密钥，
              选了转写或链接而密钥缺失时，转换会提示缺什么）。
            </p>
            <p className={s.note}>
              <b>产出组成</b>（上传时可选）：
              <code>链接</code> 给图片一个可点开的网址，随时查看原图；
              <code>内嵌</code> 把图片直接放进 Markdown，单文件自带全部内容，离线也能看；
              <code>转写</code> 用视觉模型把图片内容转成文字，可搜索、可引用；
              <code>描述</code> 一句话说明图片是什么。
              链接和内嵌二选一；要转写或描述，要先选链接或内嵌其中一个（这两项会调用视觉模型，产生模型费用）。
              都不选就只提取正文——不调用模型、不用配置存储。
              选择会记住，下次上传自动沿用；每条历史记录按上传时的选择展示。
            </p>
          </section>

          <section className={s.section}>
            <h3 className={s.sectionTitle}>限制与隐私</h3>
            <ul className={s.list}>
              <li>单文件 ≤ {MAX_UPLOAD_MB}MB，同时最多 {MAX_CONCURRENT_TASKS} 个转换任务；占满时新上传自动排队，依次提交。</li>
              <li>上传的原始文件为临时文件，转换完成后即删除。</li>
              <li>服务端只保留产出的 Markdown 与统计信息，不保留原始文件。</li>
              <li>删除历史记录会同时清除对应的转换结果。</li>
            </ul>
          </section>

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
