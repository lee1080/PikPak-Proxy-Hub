"use client";

import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";

export type Slice = { name: string; value: number; color: string };

export function PoolChart({ data }: { data: Slice[] }) {
  if (!data.length) {
    return <p className="text-white/40 text-sm py-12 text-center">暂无账号数据</p>;
  }

  return (
    <div className="glass-card p-6 h-[360px]">
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie data={data} dataKey="value" nameKey="name" cx="50%" cy="45%" outerRadius={100} innerRadius={56}>
            {data.map((e) => (
              <Cell key={e.name + e.color} fill={e.color} />
            ))}
          </Pie>
          <Tooltip
            formatter={(v: number) => `${v.toFixed(1)} %`}
            contentStyle={{ borderRadius: 8, border: "1px solid #fff2", background: "#0c1018" }}
            labelStyle={{ color: "#e8edf5" }}
          />
        </PieChart>
      </ResponsiveContainer>
      <div className="flex flex-wrap justify-center gap-x-6 gap-y-2 text-xs text-white/65">
        {data.map((d) => (
          <span key={d.name} className="inline-flex items-center gap-2">
            <span className="inline-block w-3 h-3 rounded-full" style={{ backgroundColor: d.color }} />
            {d.name}
          </span>
        ))}
      </div>
    </div>
  );
}
