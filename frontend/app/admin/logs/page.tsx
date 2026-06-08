"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";

type LogLine = { ts: string; level: string; logger: string; message: string };

type LogsApiResponse = {
  lines: LogLine[];
  source?: string;
  retention_days?: number;
  memory_buffered?: number;
  memory_max?: number;
  /** 旧字段兼容 */
  buffered?: number;
  max?: number;
};

function formatLocalTs(ts: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  const pad = (n: number, w = 2) => String(n).padStart(w, "0");
  const y = d.getFullYear();
  const m = pad(d.getMonth() + 1);
  const day = pad(d.getDate());
  const hh = pad(d.getHours());
  const mm = pad(d.getMinutes());
  const ss = pad(d.getSeconds());
  const ms = pad(d.getMilliseconds(), 3);
  return `${y}-${m}-${day} ${hh}:${mm}:${ss}.${ms}`;
}

function levelClass(lv: string): string {
  const u = lv.toUpperCase();
  if (u === "ERROR" || u === "CRITICAL") return "text-red-400";
  if (u === "WARNING") return "text-amber-400";
  if (u === "INFO") return "text-sky-300";
  return "text-white/55";
}

export default function AdminLogsPage() {
  const [lines, setLines] = useState<LogLine[]>([]);
  const [buffered, setBuffered] = useState(0);
  const [maxBuf, setMaxBuf] = useState(0);
  const [source, setSource] = useState<string>("");
  const [retentionDays, setRetentionDays] = useState(0);
  /** 与「保存天数」表单同步；自动刷新时仅在未编辑草稿时覆盖 */
  const [retentionDraft, setRetentionDraft] = useState(7);
  const retentionDraftTouchedRef = useRef(false);
  const [limit, setLimit] = useState(400);
  const [levelFilter, setLevelFilter] = useState("");
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [retentionMsg, setRetentionMsg] = useState<string | null>(null);
  const [retentionSaving, setRetentionSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get<LogsApiResponse>("/admin/logs", {
        params: {
          limit,
          ...(levelFilter.trim() ? { level: levelFilter.trim() } : {}),
        },
      });
      setLines(data.lines);
      const mb = data.memory_buffered ?? data.buffered ?? 0;
      const mm = data.memory_max ?? data.max ?? 0;
      setBuffered(mb);
      setMaxBuf(mm);
      setSource(data.source ?? "");
      const rd = data.retention_days ?? 0;
      setRetentionDays(rd);
      if (!retentionDraftTouchedRef.current) {
        setRetentionDraft(rd > 0 ? rd : 0);
      }
      setErr(null);
    } catch {
      setErr("加载失败，请确认已以管理员登录");
    }
  }, [limit, levelFilter]);

  async function applyRetentionDays() {
    setRetentionMsg(null);
    const n = Math.round(Number(retentionDraft));
    if (!Number.isFinite(n) || n < 0 || n > 3650) {
      setRetentionMsg("保存天数须为 0～3650 的整数（0 表示仅内存、不落库）");
      return;
    }
    setRetentionSaving(true);
    try {
      await api.patch("/admin/runtime-config", { admin_log_retention_days: n });
      retentionDraftTouchedRef.current = false;
      setRetentionMsg("已应用当前进程；超过天数的库内记录已尝试清理。重启服务后以 .env 的 ADMIN_LOG_RETENTION_DAYS 为准。");
      await load();
    } catch {
      setRetentionMsg("保存失败，请确认管理员权限或稍后重试");
    } finally {
      setRetentionSaving(false);
    }
  }

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!autoRefresh) return;
    const t = window.setInterval(() => void load(), 2500);
    return () => window.clearInterval(t);
  }, [autoRefresh, load]);

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-semibold text-white">运行日志</h1>
          <p className="text-xs text-white/45 mt-1">
            {source === "database" && retentionDays > 0 ? (
              <>
                列表来自数据库，仅保留最近 <span className="text-white/70">{retentionDays}</span> 天（默认 7 天，可下方自定义），更早的记录会自动删除
                · 内存环 {buffered} / {maxBuf} 条
              </>
            ) : (
              <>
                内存缓冲 {buffered} / {maxBuf} 条 · 当前为「仅内存」：保存天数为 0。改为 ≥1 并应用后开始落库；持久化默认保留 7 天，可在下方或环境变量{" "}
                <code className="text-white/55">ADMIN_LOG_RETENTION_DAYS</code> 配置
              </>
            )}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="text-xs text-white/50 whitespace-nowrap">
            条数
            <select
              className="ml-1 bg-white/10 border border-white/15 rounded px-2 py-1 text-white text-sm"
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
            >
              {[100, 200, 400, 800, 1500, 3000].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
          <input
            type="text"
            placeholder="级别过滤 INFO,WARNING"
            value={levelFilter}
            onChange={(e) => setLevelFilter(e.target.value)}
            className="bg-white/10 border border-white/15 rounded px-2 py-1 text-sm text-white placeholder:text-white/35 w-44"
          />
          <label className="flex items-center gap-1.5 text-xs text-white/70 cursor-pointer">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
            />
            自动刷新（2.5s）
          </label>
          <button
            type="button"
            onClick={() => void load()}
            className="text-sm bg-primary/90 hover:bg-primary text-white px-3 py-1.5 rounded-lg"
          >
            立即刷新
          </button>
        </div>
      </div>

      <div className="glass-card p-4 flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-end">
        <div className="flex flex-wrap items-end gap-2">
          <label className="text-xs text-white/60">
            日志保存天数
            <input
              type="number"
              min={0}
              max={3650}
              step={1}
              value={retentionDraft}
              onChange={(e) => {
                retentionDraftTouchedRef.current = true;
                setRetentionDraft(Number(e.target.value));
              }}
              className="ml-2 w-24 bg-white/10 border border-white/15 rounded px-2 py-1.5 text-sm text-white"
            />
          </label>
          <button
            type="button"
            disabled={retentionSaving}
            onClick={() => void applyRetentionDays()}
            className="text-sm bg-white/15 hover:bg-white/25 text-white px-3 py-1.5 rounded-lg disabled:opacity-50"
          >
            {retentionSaving ? "保存中…" : "应用保存天数"}
          </button>
        </div>
        <p className="text-xs text-white/45 flex-1 min-w-[220px]">
          默认 7 天；设为 0 则不再写入数据库（列表仅看内存环）。此处修改与号池「高级设置」一样仅作用于当前进程，重启后以{" "}
          <code className="text-white/50">.env</code> 为准。
        </p>
      </div>

      {retentionMsg && <p className="text-xs text-sky-300/90">{retentionMsg}</p>}
      {err && <p className="text-sm text-destructive">{err}</p>}

      <div className="glass-card overflow-hidden">
        <div className="max-h-[70vh] overflow-auto text-left">
          <table className="w-full text-xs font-mono border-collapse min-w-[640px]">
            <thead className="sticky top-0 bg-black/60 backdrop-blur border-b border-white/10 text-white/50">
              <tr>
                <th className="py-2 px-2 w-44 align-top text-left font-normal">时间</th>
                <th className="py-2 px-2 w-20 align-top text-left font-normal">级别</th>
                <th className="py-2 px-2 w-40 align-top text-left font-normal">Logger</th>
                <th className="py-2 px-2 align-top text-left font-normal">内容</th>
              </tr>
            </thead>
            <tbody className="text-white/85">
              {lines.length === 0 && (
                <tr>
                  <td colSpan={4} className="py-8 text-center text-white/40">
                    暂无日志（尚无 INFO 及以上记录，或当前筛选无匹配）
                  </td>
                </tr>
              )}
              {lines.map((row, i) => (
                <tr key={`${row.ts}-${i}`} className="border-b border-white/5 align-top hover:bg-white/[0.03]">
                  <td className="py-1.5 px-2 text-white/45 whitespace-nowrap" title={`UTC: ${row.ts}`}>
                    {formatLocalTs(row.ts)}
                  </td>
                  <td className={`py-1.5 px-2 whitespace-nowrap ${levelClass(row.level)}`}>{row.level}</td>
                  <td className="py-1.5 px-2 text-emerald-300/90 break-all">{row.logger}</td>
                  <td className="py-1.5 px-2 whitespace-pre-wrap break-words text-white/80">{row.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <p className="text-xs text-white/40">
        提示：检索可取关键字如 <code className="text-white/60">download_</code>、{" "}
        <code className="text-white/60">offline_</code>、<code className="text-white/60">reoffline</code> 等；浏览器内{" "}
        <kbd className="text-white/50">Ctrl+F</kbd> 可搜索本页。
      </p>
    </div>
  );
}
