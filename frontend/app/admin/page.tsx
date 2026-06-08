"use client";

import axios from "axios";
import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { PoolChart, type Slice } from "@/components/pool-chart";

type Stats = {
  total_users: number;
  vip_users: number;
  free_pool: { count: number; active: number; avg_usage_percent: number };
  premium_pool: { count: number; active: number; avg_usage_percent: number };
  tasks_today: number;
  tasks_completed: number;
  tasks_failed: number;
};

export default function AdminDashboard() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function load() {
    try {
      const { data } = await api.get<Stats>("/admin/stats");
      setStats(data);
      setErr(null);
    } catch (ex: unknown) {
      if (axios.isAxiosError(ex) && ex.response?.status === 403) {
        setErr("没有管理员权限");
      } else {
        setErr("无法加载统计，请稍后重试");
      }
    }
  }

  useEffect(() => {
    void load();
    const t = window.setInterval(() => void load(), 60000);
    return () => window.clearInterval(t);
  }, []);

  const chartData = useMemo((): Slice[] => {
    if (!stats) return [];
    const f = stats.free_pool;
    const p = stats.premium_pool;
    return [
      {
        name: `免费池 平均用量 ${f.avg_usage_percent}%`,
        value: f.avg_usage_percent,
        color: "#3b82f6",
      },
      {
        name: `付费池 平均用量 ${p.avg_usage_percent}%`,
        value: Math.max(p.avg_usage_percent, 0.001),
        color: "#22c55e",
      },
    ];
  }, [stats]);

  return (
    <div className="space-y-8">
      <h1 className="text-xl font-semibold text-white">统计看板</h1>

      {err && <p className="text-destructive">{err}</p>}

      {stats && (
        <div className="grid md:grid-cols-3 gap-4">
          <div className="glass-card p-4">
            <p className="text-xs text-white/45">注册用户</p>
            <p className="text-2xl text-white mt-1">{stats.total_users}</p>
          </div>
          <div className="glass-card p-4">
            <p className="text-xs text-white/45">VIP 用户</p>
            <p className="text-2xl text-white mt-1">{stats.vip_users}</p>
          </div>
          <div className="glass-card p-4">
            <p className="text-xs text-white/45">今日任务</p>
            <p className="text-2xl text-white mt-1">{stats.tasks_today}</p>
          </div>
          <div className="glass-card p-4">
            <p className="text-xs text-white/45">累计完成 / 失败</p>
            <p className="text-2xl text-white mt-1">
              {stats.tasks_completed} / {stats.tasks_failed}
            </p>
          </div>
          <div className="glass-card p-4">
            <p className="text-xs text-white/45">免费号池账号</p>
            <p className="text-2xl text-white mt-1">
              {stats.free_pool.active}/{stats.free_pool.count} 激活
            </p>
          </div>
          <div className="glass-card p-4">
            <p className="text-xs text-white/45">付费号池账号</p>
            <p className="text-2xl text-white mt-1">
              {stats.premium_pool.active}/{stats.premium_pool.count} 激活
            </p>
          </div>
        </div>
      )}

      <div>
        <h2 className="text-sm font-medium text-white/70 mb-3">池容量使用率（示意）</h2>
        <PoolChart data={chartData} />
      </div>
    </div>
  );
}
