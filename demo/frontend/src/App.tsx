/**
 * 编排层：唯一的「应用状态」持有者。
 *  - 列表轮询：有活跃任务 2s / 静默 5s；document.hidden 全暂停，恢复可见立即刷新
 *  - 详情：终态进程内缓存不重取；运行中同频 2s 补充轮询直至终态
 *  - 批量上传：App 级队列槽位感知提交（空态卸载不断流，429 回队随轮询重试）
 *  - URL hash 单一状态载体：#t=<task_id>&v=src
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { TaskDetail, TaskSummary } from "./types";
import {
  checkHealth,
  deleteTask,
  fetchFormats,
  fetchTaskDetail,
  fetchTasks,
  NetworkError,
  uploadTask,
} from "./api";
import { localNowIso, MAX_CONCURRENT_TASKS } from "./format";
import Sidebar from "./components/Sidebar";
import UploadZone from "./components/UploadZone";
import ResultPanel from "./components/ResultPanel";
import OfflineBanner from "./components/OfflineBanner";
import HelpPanel from "./components/HelpPanel";
import { IcMenu, IcHelp } from "./components/Icons";
import s from "./App.module.css";

type View = "rendered" | "source";

/** 批量上传的队列元素：产出组成在入队时定格（用户当时的选择） */
interface QueuedFile {
  file: File;
  output: string;
}



/** 视图模式本地记忆键（hash 缺省时的回退偏好） */
const VIEW_STORAGE_KEY = "omnimd.view";

function parseHash(): { t: string | null; v: View | null } {
  const params = new URLSearchParams(location.hash.replace(/^#/, ""));
  return {
    t: params.get("t"),
    v: params.has("v") ? (params.get("v") === "src" ? "source" : "rendered") : null,
  };
}

/** 状态 → hash（replaceState：文档工具不污染历史栈） */
function writeHash(t: string | null, v: View) {
  const next = t ? (v === "source" ? `#t=${t}&v=src` : `#t=${t}`) : "";
  if (location.hash !== next) {
    history.replaceState(null, "", next || location.pathname);
  }
}

export default function App() {
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  /** 首次列表拉取完成标志：侧栏空态在完成前不显示（防「记录丢了」误读） */
  const [listLoaded, setListLoaded] = useState(false);
  /** 搜索命中的真实总数（列表只取前 50，计数不能拿截断长度充数） */
  const [total, setTotal] = useState(0);
  // 初始选中直接从 hash 取：避免挂载效果顺序造成的 URL 瞬时清写
  const [selectedId, setSelectedId] = useState<string | null>(() => parseHash().t);
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  // 视图偏好：hash 显式值 > localStorage 记忆 > rendered
  const [view, setView] = useState<View>(() => {
    if (parseHash().v) return parseHash().v as View;
    try {
      return localStorage.getItem(VIEW_STORAGE_KEY) === "source" ? "source" : "rendered";
    } catch {
      return "rendered";
    }
  });
  const [searchText, setSearchText] = useState("");
  const [query, setQuery] = useState("");
  const [offline, setOffline] = useState(false);
  const [formats, setFormats] = useState<string[] | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [searchFocusSignal, setSearchFocusSignal] = useState(0);
  const [visible, setVisible] = useState(() => !document.hidden);

  /** 终态详情缓存：done/failed 不再重取 */
  const cache = useRef(new Map<string, TaskDetail>());
  const queryRef = useRef("");
  /** 首次加载后是否已自动选中（仅一次，避免与「新建转换」清空选中冲突） */
  const didAutoSelect = useRef(false);
  /** 列表请求序号：慢响应晚到时丢弃过期数据，旧结果不覆盖新结果 */
  const listSeq = useRef(0);

  const refreshList = useCallback(async () => {
    const seq = ++listSeq.current;
    try {
      const data = await fetchTasks(queryRef.current, 50);
      if (seq !== listSeq.current) return;   // 已有更新请求，本次作废
      setTasks(data.items);
      setTotal(data.total);
      setListLoaded(true);
      setOffline(false);
    } catch (e) {
      if (e instanceof NetworkError && seq === listSeq.current) setOffline(true);
    }
  }, []);

  // 启动自检：探活确认后端在线；能力数据（支持格式清单）单独拉取
  useEffect(() => {
    checkHealth()
      .then(() => setOffline(false))
      .catch((e) => {
        if (e instanceof NetworkError) setOffline(true);
      });
    fetchFormats()
      .then((f) => {
        setFormats(f.formats);
        setOffline(false);
      })
      .catch((e) => {
        if (e instanceof NetworkError) setOffline(true);
      });
  }, []);

  // 初始加载 + 查询词变化时刷新列表
  useEffect(() => {
    void refreshList();
  }, [query, refreshList]);

  // 首次列表非空后默认选中：最新一条 done（无 done 则最新一条）
  useEffect(() => {
    if (didAutoSelect.current || tasks.length === 0 || selectedId !== null) return;
    didAutoSelect.current = true;
    const target = tasks.find((t) => t.status === "done") ?? tasks[0];
    setSelectedId(target.task_id);
  }, [tasks, selectedId]);

  // 搜索防抖 300ms
  useEffect(() => {
    const id = setTimeout(() => {
      queryRef.current = searchText;
      setQuery(searchText);
    }, 300);
    return () => clearTimeout(id);
  }, [searchText]);

  // 页面隐藏暂停全部轮询；恢复可见立即刷新一次
  useEffect(() => {
    const onVis = () => {
      const v = !document.hidden;
      setVisible(v);
      if (v) void refreshList();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, [refreshList]);

  // 列表轮询：活跃 2s / 静默 5s（活跃与搜索中保持 2s 保证结果及时，静默降频省请求）
  const hasActive = tasks.some(
    (t) => t.status === "pending" || t.status === "running"
  );
  useEffect(() => {
    if (!visible) return;
    const fast = hasActive || query !== "";
    const id = setInterval(() => void refreshList(), fast ? 2000 : 5000);
    return () => clearInterval(id);
  }, [visible, hasActive, query, refreshList]);

  // hash → state（初始加载与手动改地址/前进后退；未显式带 v 时不动视图记忆）
  useEffect(() => {
    const sync = () => {
      const { t, v } = parseHash();
      setSelectedId(t);
      if (v !== null) setView(v);
    };
    sync();
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);

  // state → hash；视图偏好同步写入 localStorage（下次打开沿用）
  useEffect(() => {
    writeHash(selectedId, view);
    try {
      localStorage.setItem(VIEW_STORAGE_KEY, view);
    } catch {
      /* 隐私模式等存储不可用：忽略，仅丢失偏好 */
    }
  }, [selectedId, view]);

  // 选中即取详情：终态命中缓存；未命中发请求（可中止）。
  // 依赖 offline：断网期间失败的详情请求在网络恢复后自动重拉
  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      setDetailLoading(false);
      return;
    }
    const cached = cache.current.get(selectedId);
    if (cached && (cached.status === "done" || cached.status === "failed")) {
      setDetail(cached);
      setDetailLoading(false);
      return;
    }
    // 清掉上一个任务的内容：拉取期间显示 loading，不残留旧详情
    setDetail(null);
    const controller = new AbortController();
    setDetailLoading(true);
    fetchTaskDetail(selectedId, controller.signal)
      .then((d) => {
        cache.current.set(selectedId, d);
        setDetail(d);
        setOffline(false);
      })
      .catch((e) => {
        if (e instanceof NetworkError) {
          setOffline(true);
        } else if (e.name !== "AbortError") {
          // 404 等请求失败：选中态一并清掉（记录已不存在，不能停留在
          // 「已选中但无内容」的中间态），并从本地列表移除该行
          const failedId = selectedId;
          setSelectedId(null);
          setDetail(null);
          setTasks((ts) => ts.filter((t) => t.task_id !== failedId));
          cache.current.delete(failedId);
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setDetailLoading(false);
      });
    return () => controller.abort();
  }, [selectedId, offline]);

  // 运行中详情同频 2s 补充轮询，直至终态写入缓存；页面隐藏时暂停。
  // 切换选中时 abort 在途轮询：旧选中的迟到响应不覆盖新选中的详情
  useEffect(() => {
    if (!selectedId || !detail || !visible) return;
    if (detail.status !== "pending" && detail.status !== "running") return;
    const controller = new AbortController();
    let seq = 0;
    const id = setInterval(async () => {
      const current = ++seq;
      try {
        const d = await fetchTaskDetail(selectedId, controller.signal);
        if (current !== seq) return;   // 已有更新一轮，本次作废
        cache.current.set(selectedId, d);
        setDetail(d);
      } catch (e) {
        if (e instanceof NetworkError) {
          setOffline(true);
        } else if (!(e instanceof DOMException && e.name === "AbortError")) {
          // 404 等：任务已被删除（如另一端操作），停止轮询并同步清理
          // 选中态与列表行——与首次取详情的清理路径同一处理
          const failedId = selectedId;
          setSelectedId(null);
          setDetail(null);
          setTasks((ts) => ts.filter((t) => t.task_id !== failedId));
          cache.current.delete(failedId);
        }
      }
    }, 2000);
    return () => {
      controller.abort();
      clearInterval(id);
    };
  }, [selectedId, detail, visible]);

  // Esc 关抽屉与帮助面板
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setDrawerOpen(false);
        setHelpOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Ctrl/⌘+K 聚焦搜索（窄屏顺带打开抽屉，桌面直接聚焦）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        if (window.innerWidth < 900) setDrawerOpen(true);
        setSearchFocusSignal((n) => n + 1);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const handleSelect = useCallback((taskId: string) => {
    setSelectedId(taskId);
    setDrawerOpen(false);
  }, []);

  const handleViewChange = useCallback((v: View) => setView(v), []);

  const handleNew = useCallback(() => {
    setSelectedId(null);
    setDrawerOpen(false);
  }, []);

  // 上传成功：立即置顶插入本地列表（不等列表回包），随后轮询校准
  const handleCreated = useCallback(
    (taskId: string, filename: string, output: string) => {
      const optimistic: TaskSummary = {
        task_id: taskId,
        filename,
        status: "pending",
        output,
        image_count: null,
        char_count: null,
        elapsed_ms: null,
        cache_hit: null,
        created_at: localNowIso(),
      };
      // 搜索过滤进行中不乐观置顶——服务端按关键词过滤会把不匹配的新任务
      // 从列表剔除，置顶反而造成列表与详情短暂分裂；由下轮轮询自然收录
      if (!queryRef.current)
        setTasks((ts) => [optimistic, ...ts.filter((t) => t.task_id !== taskId)]);
      // 批量上传只选中首个任务：其余不抢焦点（用户可能已在看某个结果）
      setSelectedId((cur) => cur ?? taskId);
      setDrawerOpen(false);
      void refreshList();
    },
    [refreshList]
  );

  // 批量上传队列：App 级持有（首个任务创建后空态即卸载，队列不能住在
  // UploadZone 里）。槽位感知提交——按列表活跃数 + 在途请求数计算空位，
  // 逐个发出；列表轮询每轮重估，任务完成腾出的空位随即补发。
  const uploadQueue = useRef<QueuedFile[]>([]);
  const inFlight = useRef(0);
  const [submitting, setSubmitting] = useState(false);
  const [uploadRejections, setUploadRejections] = useState<string[]>([]);

  const flushQueue = useCallback(() => {
    const active = tasks.filter(
      (t) => t.status === "pending" || t.status === "running"
    ).length;
    let slots = MAX_CONCURRENT_TASKS - active - inFlight.current;
    while (slots > 0 && uploadQueue.current.length > 0) {
      slots -= 1;
      const item = uploadQueue.current.shift()!;
      inFlight.current += 1;
      void uploadTask(item.file, item.output)
        .then(({ task_id, filename }) =>
          handleCreated(task_id, filename, item.output)
        )
        .catch((e) => {
          // 网络故障与 429（并发已满）回队首，随下轮轮询重试；
          // 其余失败（后端拒格式/大小等）放弃该文件并告知用户，
          // 不允许文件悄悄消失
          if (e instanceof NetworkError) setOffline(true);
          if (e instanceof NetworkError || (e as { status?: number }).status === 429) {
            uploadQueue.current.unshift(item);
          } else {
            const reason = e instanceof Error ? e.message : "上传失败";
            setUploadRejections((rs) => [...rs, `${item.file.name}：${reason}`]);
          }
        })
        .finally(() => {
          inFlight.current -= 1;
          // 请求结束即收口：被放弃的文件不触发列表变化，submitting
          // 不能等下一轮轮询重估才收回
          setSubmitting(uploadQueue.current.length > 0 || inFlight.current > 0);
        });
    }
    setSubmitting(uploadQueue.current.length > 0 || inFlight.current > 0);
  }, [tasks, handleCreated]);

  useEffect(() => {
    flushQueue();
  }, [flushQueue]);

  // 上传入口：校验通过的文件入队（连续多批追加同一队列），立即尝试占位提交
  const handleAccepted = useCallback(
    (files: File[], output: string) => {
      uploadQueue.current.push(...files.map((file) => ({ file, output })));
      flushQueue();
    },
    [flushQueue]
  );

  // 删除成功：本地移除 + 缓存失效，返回 true；失败返回 false，
  // 由 TaskItem 还原动画状态（记录保持可见、可再次删除）
  const handleDelete = useCallback(
    async (taskId: string): Promise<boolean> => {
      try {
        await deleteTask(taskId);
      } catch (e) {
        if (e instanceof NetworkError) setOffline(true);   // 与列表/详情同款离线语义
        return false;
      }
      cache.current.delete(taskId);
      setTasks((ts) => ts.filter((t) => t.task_id !== taskId));
      if (selectedId === taskId) {
        setSelectedId(null);
        setDetail(null);
      }
      return true;
    },
    [selectedId]
  );

  return (
    <div className={s.shell}>
      <Sidebar
        tasks={tasks}
        total={total}
        loaded={listLoaded}
        selectedId={selectedId}
        value={searchText}
        onValueChange={setSearchText}
        onSelect={handleSelect}
        onDelete={handleDelete}
        onNew={handleNew}
        onHelp={() => setHelpOpen(true)}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        focusSignal={searchFocusSignal}
      />
      <HelpPanel
        open={helpOpen}
        onClose={() => setHelpOpen(false)}
        formats={formats}
      />
      <main className={s.main}>
        <OfflineBanner show={offline} />
        {/* 窄屏顶栏：抽屉入口 + 品牌标 + 使用指南（与左栏同一套图标规范） */}
        <div className={s.topbar}>
          <button
            type="button"
            className={s.menuBtn}
            onClick={() => setDrawerOpen(true)}
            aria-label="打开历史记录"
          >
            <IcMenu size={18} />
          </button>
          {/* 产品 LOGO（与侧边栏品牌区、浏览器图标同源） */}
          <img className={s.topMark} src="/logo.svg" width={20} height={20} alt="" aria-hidden="true" />
          <span className={s.topTitle}>omnimd</span>
          <button
            type="button"
            className={s.topHelpBtn}
            onClick={() => setHelpOpen(true)}
            aria-label="使用指南"
          >
            <IcHelp size={18} />
          </button>
        </div>
        <div className={s.work}>
          {uploadRejections.length > 0 && (
            <div className={s.uploadErrors} role="alert">
              <span>以下文件未被接受：</span>
              {uploadRejections.map((r) => (
                <span key={r} className={s.uploadErrorItem}>{r}</span>
              ))}
              <button
                type="button"
                className={s.uploadErrorDismiss}
                onClick={() => setUploadRejections([])}
              >
                知道了
              </button>
            </div>
          )}
          {selectedId === null ? (
            <UploadZone
              formats={formats}
              submitting={submitting}
              onAccepted={handleAccepted}
            />
          ) : (
            <ResultPanel
              task={detail}
              loading={detailLoading}
              view={view}
              onViewChange={handleViewChange}
            />
          )}
        </div>
      </main>
    </div>
  );
}
