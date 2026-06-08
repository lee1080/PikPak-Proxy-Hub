"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";

type Row = {
  job_id: string;
  user_email: string | null;
  account_email: string | null;
  pool_type: string | null;
  file_name: string | null;
  file_size: number;
  status: string;
  progress: number;
  created_at: string | null;
  completed_at: string | null;
};

export default function AdminTasksPage() {
  const [rows, setRows] = useState<Row[]>([]);
  const [total, setTotal] = useState<number>(0);
  const [err, setErr] = useState<string | null>(null);

  const [status, setStatus] = useState<string>("ALL");
  const [poolType, setPoolType] = useState<string>("ALL");
  const [email, setEmail] = useState<string>("");
  const [orderBy, setOrderBy] = useState<"created_at_desc" | "created_at_asc">("created_at_desc");

  async function load() {
    setErr(null);
    try {
      const params = {
        status: status === "ALL" ? undefined : status,
        pool_type: poolType === "ALL" ? undefined : poolType,
        email: email.trim() ? email.trim() : undefined,
        order_by: orderBy,
      };
      const { data } = await api.get<{
        tasks: Row[];
        total: number;
      }>("/admin/tasks", { params });
      setRows(data.tasks);
      setTotal(data.total);
    } catch (ex: unknown) {
      const d =
        ex && typeof ex === "object" && "response" in ex
          ? (ex as { response?: { data?: { detail?: string | unknown } } }).response?.data?.detail
          : undefined;
      setErr(typeof d === "string" ? d : "加载任务失败");
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold text-white">任务列表</h1>

      <div className="glass-card p-6 space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <div className="text-xs text-white/45 mb-1">状态</div>
            <select
              value={status}
              onChange={(e) => setStatus(e.target.value)}
              className="w-full bg-white/5 border border-white/10 rounded px-3 py-2 text-white text-sm"
            >
              <option value="ALL">全部</option>
              <option value="PENDING">PENDING</option>
              <option value="SUBMITTED">SUBMITTED</option>
              <option value="DOWNLOADING">DOWNLOADING</option>
              <option value="COMPLETED">COMPLETED</option>
              <option value="FAILED">FAILED</option>
              <option value="EXPIRED">EXPIRED</option>
            </select>
          </div>

          <div>
            <div className="text-xs text-white/45 mb-1">号池类型</div>
            <select
              value={poolType}
              onChange={(e) => setPoolType(e.target.value)}
              className="w-full bg-white/5 border border-white/10 rounded px-3 py-2 text-white text-sm"
            >
              <option value="ALL">全部</option>
              <option value="FREE">FREE</option>
              <option value="PREMIUM">PREMIUM</option>
            </select>
          </div>

          <div className="sm:col-span-2">
            <div className="text-xs text-white/45 mb-1">用户邮箱（模糊匹配）</div>
            <input
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="例如：xxx@xxx.com（可留空）"
              className="w-full bg-white/5 border border-white/10 rounded px-3 py-2 text-white text-sm"
            />
          </div>

          <div className="sm:col-span-2">
            <div className="text-xs text-white/45 mb-1">排序</div>
            <select
              value={orderBy}
              onChange={(e) => setOrderBy(e.target.value as "created_at_desc" | "created_at_asc")}
              className="w-full bg-white/5 border border-white/10 rounded px-3 py-2 text-white text-sm"
            >
              <option value="created_at_desc">创建时间 新到旧</option>
              <option value="created_at_asc">创建时间 旧到新</option>
            </select>
          </div>
        </div>

        <div className="flex gap-3 flex-wrap pt-2">
          <button
            type="button"
            onClick={() => void load()}
            className="bg-primary px-6 py-2 rounded-lg text-sm text-white hover:bg-primary-hover"
          >
            查询
          </button>
          <button
            type="button"
            onClick={() => {
              setStatus("ALL");
              setPoolType("ALL");
              setEmail("");
              setOrderBy("created_at_desc");
              void load();
            }}
            className="bg-white/10 px-4 py-2 rounded-lg text-sm text-white hover:bg-white/20"
          >
            重置
          </button>
        </div>
      </div>

      {err && <p className="text-destructive">{err}</p>}

      <div className="glass-card overflow-x-auto w-full">
        <div className="px-4 py-3 text-xs text-white/60">
          共 {total} 条
        </div>
        <table className="w-full text-sm text-left border-collapse">
          <thead className="text-white/55 border-b border-white/10">
            <tr>
              <th className="py-3 px-4">任务</th>
              <th className="py-3 px-4">用户</th>
              <th className="py-3 px-4">账号/池</th>
              <th className="py-3 px-4">状态</th>
              <th className="py-3 px-4">进度</th>
              <th className="py-3 px-4">文件</th>
              <th className="py-3 px-4">创建</th>
              <th className="py-3 px-4">完成</th>
            </tr>
          </thead>
          <tbody className="text-white/85">
            {rows.map((t) => {
              const fileMb = t.file_size ? Math.round(t.file_size / 1024 / 1024) : 0;
              return (
                <tr key={t.job_id} className="border-b border-white/5">
                  <td className="py-3 px-4 font-mono text-xs">
                    {t.job_id.slice(0, 8)}…
                  </td>
                  <td className="py-3 px-4">
                    <div className="font-medium">{t.user_email || "—"}</div>
                  </td>
                  <td className="py-3 px-4">
                    <div className="font-medium">{t.account_email || "—"}</div>
                    <div className="text-xs text-white/40">{t.pool_type || "—"}</div>
                  </td>
                  <td className="py-3 px-4">{t.status}</td>
                  <td className="py-3 px-4">{t.progress}%</td>
                  <td className="py-3 px-4">
                    <div className="max-w-[240px] truncate">{t.file_name || "—"}</div>
                    <div className="text-xs text-white/40">{fileMb} MB</div>
                  </td>
                  <td className="py-3 px-4">{t.created_at || "—"}</td>
                  <td className="py-3 px-4">{t.completed_at || "—"}</td>
                </tr>
              );
            })}
            {!rows.length && (
              <tr>
                <td colSpan={8} className="py-8 px-4 text-center text-white/50">
                  暂无任务
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

