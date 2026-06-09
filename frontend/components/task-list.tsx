"use client";

import { motion } from "framer-motion";
import { Clock, Loader2, CheckCircle, XCircle, Download } from "lucide-react";
import { useState } from "react";
import axios from "axios";
import { formatBytes } from "@/lib/utils";
import { useCleanupTasks, useDeleteTask, useTasks, type TaskRow } from "@/hooks/use-tasks";
import { api } from "@/lib/api";

function formatTaskError(msg?: string | null): string {
  const raw = msg?.trim();
  if (!raw) return "失败（未返回具体原因）";
  try {
    const parsed = JSON.parse(raw) as {
      error?: string;
      error_description?: string;
    };
    if (parsed.error === "file_space_not_enough") {
      return parsed.error_description || "云存储空间不足，请使用付费号池";
    }
    if (parsed.error_description) return parsed.error_description;
  } catch {
    // 非 JSON，原样展示
  }
  return raw;
}

const STATUS: Record<string, { Icon: typeof Clock; color: string; label: string }> = {
  PENDING: { Icon: Clock, color: "text-warning", label: "排队中" },
  SUBMITTED: { Icon: Clock, color: "text-warning", label: "已提交" },
  DOWNLOADING: { Icon: Loader2, color: "text-primary", label: "离线中" },
  COMPLETED: { Icon: CheckCircle, color: "text-accent", label: "已完成" },
  FAILED: { Icon: XCircle, color: "text-destructive", label: "失败" },
  EXPIRED: { Icon: Clock, color: "text-muted", label: "已过期" },
};

type DownloadJson = {
  url?: string;
  file_id?: string;
  files?: { file_id?: string; name?: string; size?: number; episode_key?: string | null }[];
  items?: { file_id?: string; name?: string; size?: number; episode_key?: string | null }[];
};

type PlayUrlResponse = {
  url: string;
  file_id: string;
  from_cache?: boolean;
  expires_at?: number | null;
};

type ListedFile = {
  file_id?: string;
  name: string;
  size?: number;
  episode_key?: string | null;
};

function parseFileList(json: DownloadJson | null): ListedFile[] {
  const base =
    Array.isArray(json?.items) && json.items.length > 0
      ? json.items
      : Array.isArray(json?.files)
        ? json.files
        : [];
  const listed = base
    .map((f) => ({
      file_id: f?.file_id ? String(f.file_id) : undefined,
      name: String(f?.name || "文件"),
      size: typeof f?.size === "number" ? f.size : undefined,
      episode_key: typeof f?.episode_key === "string" ? f.episode_key : null,
    }))
    .filter((f) => !!f.file_id || !!f.name);
  if (listed.length > 0) return listed;
  if (json?.file_id || json?.url) {
    return [{ file_id: json.file_id, name: "主文件", size: undefined, episode_key: null }];
  }
  return [];
}

async function copyText(text: string): Promise<void> {
  if (!text || typeof window === "undefined") return;
  try {
    await window.navigator.clipboard.writeText(text);
  } catch {
    window.prompt("复制失败，请手动复制以下链接：", text);
  }
}

function DownloadLink({ task }: { task: TaskRow }) {
  const [opening, setOpening] = useState(false);
  const [copying, setCopying] = useState(false);
  const [m3uing, setM3uing] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [moreFiles, setMoreFiles] = useState<ListedFile[]>([]);

  function shouldInvalidateFromError(ex: unknown): boolean {
    if (!axios.isAxiosError(ex)) return false;
    const status = ex.response?.status;
    return status === 403 || status === 404 || status === 410;
  }

  async function invalidateLinkCache(): Promise<void> {
    await api.post(`/tasks/${task.job_id}/invalidate-link-cache`, undefined);
  }

  async function withInvalidateRetry<T>(fn: () => Promise<T>): Promise<T> {
    try {
      return await fn();
    } catch (ex) {
      if (!shouldInvalidateFromError(ex)) throw ex;
      try {
        await invalidateLinkCache();
      } catch {
        // ignore
      }
      return await fn();
    }
  }

  /** 与 LiteEmby 播放一致：GET /tasks/{id}/play-url?file_index=N */
  async function fetchPlayUrl(fileIndex: number): Promise<string | null> {
    return await withInvalidateRetry(async () => {
      const { data } = await api.get<PlayUrlResponse>(`/tasks/${task.job_id}/play-url`, {
        params: { file_index: fileIndex },
      });
      const url = data?.url?.trim();
      return url && url.startsWith("http") ? url : null;
    });
  }

  /** 仅拉文件清单（元数据），不取直链 */
  async function fetchFileList(): Promise<ListedFile[]> {
    return await withInvalidateRetry(async () => {
      const { data } = await api.get<DownloadJson>(`/download/${task.job_id}`, {
        params: { format: "json" },
      });
      return parseFileList(data || null);
    });
  }

  async function handleDownload() {
    if (opening) return;
    try {
      setOpening(true);
      setMoreFiles([]);
      const listed = await fetchFileList();
      setMoreFiles(listed);
      if (listed.length > 1) setExpanded(true);
      const url = await fetchPlayUrl(0);
      if (!url || typeof window === "undefined") return;
      window.open(url, "_blank", "noopener,noreferrer");
    } finally {
      setOpening(false);
    }
  }

  async function handleToggleFiles() {
    const next = !expanded;
    setExpanded(next);
    if (!next) return;
    if (moreFiles.length > 0) return;
    try {
      setOpening(true);
      setMoreFiles(await fetchFileList());
    } finally {
      setOpening(false);
    }
  }

  async function handleCopyLink() {
    if (copying) return;
    try {
      setCopying(true);
      const listed = moreFiles.length > 0 ? moreFiles : await fetchFileList();
      if (listed.length > 1) setMoreFiles(listed);
      const count = Math.max(1, listed.length);
      const urls: string[] = [];
      for (let i = 0; i < count; i++) {
        const url = await fetchPlayUrl(i);
        if (url) urls.push(url);
      }
      if (urls.length === 0) return;
      await copyText(urls.join("\n"));
    } finally {
      setCopying(false);
    }
  }

  async function handleDownloadM3U() {
    if (m3uing) return;
    try {
      setM3uing(true);
      const { data } = await withInvalidateRetry(async () => {
        return await api.get<string>(`/download/${task.job_id}`, {
          params: { format: "m3u" },
          responseType: "text",
        });
      });
      const text = typeof data === "string" ? data : "";
      if (!text.trim() || typeof window === "undefined") return;
      const blob = new Blob([text], { type: "application/x-mpegURL;charset=utf-8" });
      const href = URL.createObjectURL(blob);
      const a = document.createElement("a");
      const base = (task.file_name || `task-${task.job_id}`).replace(/[^\w.\-\u4e00-\u9fa5]+/g, "_");
      a.href = href;
      a.download = `${base}.m3u`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(href);
    } finally {
      setM3uing(false);
    }
  }

  return (
    <div className="inline-flex flex-col items-start gap-1">
      <div className="inline-flex items-center gap-2">
        <button
          type="button"
          onClick={handleDownload}
          disabled={opening}
          className="bg-accent/20 text-accent hover:bg-accent/30 px-4 py-2 rounded-lg inline-flex items-center gap-2 transition-colors whitespace-nowrap"
        >
          <Download size={16} /> {opening ? "生成中..." : "取回"}
        </button>
        <button
          type="button"
          onClick={handleCopyLink}
          disabled={copying}
          className="bg-white/10 text-white/80 hover:bg-white/15 px-3 py-2 rounded-lg inline-flex items-center transition-colors whitespace-nowrap text-xs"
        >
          {copying ? "复制中..." : "复制链接"}
        </button>
        <button
          type="button"
          onClick={() => void handleToggleFiles()}
          disabled={opening}
          className="bg-white/10 text-white/80 hover:bg-white/15 px-3 py-2 rounded-lg inline-flex items-center transition-colors whitespace-nowrap text-xs"
        >
          {expanded ? "收起文件" : "选择文件"}
        </button>
        <button
          type="button"
          onClick={() => void handleDownloadM3U()}
          disabled={m3uing}
          className="bg-white/10 text-white/80 hover:bg-white/15 px-3 py-2 rounded-lg inline-flex items-center transition-colors whitespace-nowrap text-xs"
        >
          {m3uing ? "生成M3U..." : "下载M3U"}
        </button>
      </div>
      {expanded && moreFiles.length > 0 && (
        <div className="text-xs text-white/60 max-w-md">
          <span className="text-white/45">
            文件列表（已自动过滤广告/小文件；按集数/末尾数字/A-Z 排序）：
          </span>
          <div className="mt-1 max-h-56 overflow-y-auto pr-1 flex flex-col gap-1">
            {moreFiles.map((f, i) => (
              <div key={`${i}-${f.name}`} className="flex items-center gap-2">
                <button
                  type="button"
                  className="text-accent hover:underline truncate max-w-[320px] text-left"
                  title={f.name}
                  onClick={async () => {
                    const url = await fetchPlayUrl(i);
                    if (!url) return;
                    window.open(url, "_blank", "noopener,noreferrer");
                  }}
                >
                  {f.episode_key ? `[${f.episode_key}] ` : ""}
                  {f.name}
                  {typeof f.size === "number" ? ` (${formatBytes(f.size)})` : ""}
                </button>
                <button
                  type="button"
                  className="text-white/70 hover:text-white underline decoration-white/20 hover:decoration-white/40"
                  onClick={async () => {
                    const url = await fetchPlayUrl(i);
                    if (!url) return;
                    await copyText(url);
                  }}
                >
                  复制
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
      {expanded && moreFiles.length === 0 && (
        <div className="text-xs text-white/55">
          {opening ? "拉取文件清单中…" : "暂无可用文件清单（可能是单文件任务或仍在生成直链）。"}
        </div>
      )}
    </div>
  );
}

export function TaskList({ authed }: { authed: boolean }) {
  const { data = [], isLoading, isFetching } = useTasks(authed);
  const deleteTask = useDeleteTask();
  const cleanupTasks = useCleanupTasks();

  if (!authed) return null;

  return (
    <div className="max-w-2xl mx-auto mt-10 space-y-3">
      <div className="flex items-center justify-between text-white/70 text-sm">
        <span>我的任务</span>
        <div className="flex items-center gap-3">
          <button
            type="button"
            className="text-xs text-white/55 hover:text-white disabled:text-white/25 transition-colors"
            disabled={cleanupTasks.isPending}
            onClick={() => cleanupTasks.mutate()}
          >
            {cleanupTasks.isPending ? "清理中..." : "清除已过期"}
          </button>
          {isFetching && !isLoading && <span className="text-xs text-white/40">刷新中...</span>}
        </div>
      </div>
      {isLoading && <p className="text-white/50 text-center py-12">加载中...</p>}
      {!isLoading &&
        data.map((task) => {
          const cfg = STATUS[task.status] || STATUS.PENDING;
          const Icon = cfg.Icon;
          return (
            <motion.div
              key={task.job_id}
              layout
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              className="glass-card p-4 flex items-center gap-4"
            >
              <Icon
                className={`${cfg.color} shrink-0 ${task.status === "DOWNLOADING" ? "animate-spin" : ""}`}
                size={24}
              />
              <div className="flex-1 min-w-0">
                <p className="text-white font-medium truncate">{task.file_name || cfg.label}</p>
                <div className="flex items-center gap-2 mt-1 text-xs text-white/45">
                  <span>{formatBytes(task.file_size)}</span>
                  <span className="text-white/30">·</span>
                  <span>{cfg.label}</span>
                  {task.status === "DOWNLOADING" && (
                    <span className="text-primary">{task.progress}%</span>
                  )}
                </div>
                {(task.status === "FAILED" || task.status === "EXPIRED") && (
                  <p className="mt-1 text-xs text-destructive/90 line-clamp-2">
                    {formatTaskError(task.error_message)}
                  </p>
                )}
                {task.status === "DOWNLOADING" && (
                  <div className="mt-2 h-1.5 bg-white/10 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-primary transition-all"
                      style={{ width: `${Math.min(100, task.progress)}%` }}
                    />
                  </div>
                )}
              </div>
              {task.status === "COMPLETED" && task.download_url && <DownloadLink task={task} />}
              <button
                type="button"
                className="text-xs text-white/45 hover:text-destructive disabled:text-white/20 transition-colors whitespace-nowrap"
                disabled={deleteTask.isPending}
                onClick={() => deleteTask.mutate(task.job_id)}
              >
                {deleteTask.isPending ? "删除中..." : "删除"}
              </button>
            </motion.div>
          );
        })}
      {!isLoading && data.length === 0 && (
        <p className="text-center text-white/40 py-12">暂无任务</p>
      )}
    </div>
  );
}
