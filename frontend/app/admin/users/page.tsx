"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";

type Row = {
  id: string;
  username: string;
  email: string | null;
  level: string;
  is_active: boolean;
  created_at: string | null;
};

export default function AdminUsersPage() {
  const [rows, setRows] = useState<Row[]>([]);
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [msg, setMsg] = useState<string | null>(null);

  async function load() {
    const { data } = await api.get<{ users: Row[] }>("/admin/users");
    setRows(data.users);
    setSelected({});
  }

  useEffect(() => {
    void load().catch(() => {});
  }, []);

  async function setLevel(id: string, level: string) {
    await api.patch(`/admin/users/${id}`, { level });
    setMsg("等级已更新");
    await load();
  }

  function selectedIds() {
    return rows.filter((u) => selected[u.id]).map((u) => u.id);
  }

  async function deleteSelected() {
    const ids = selectedIds();
    if (!ids.length) {
      setMsg("请先选择用户");
      return;
    }
    if (!window.confirm(`确认删除选中的 ${ids.length} 个用户吗？`)) return;
    await api.post("/admin/users/batch-delete", { user_ids: ids });
    setMsg(`已删除 ${ids.length} 个用户`);
    await load();
  }

  async function resetPassword(id: string, username: string) {
    const value = window.prompt(`请输入用户 ${username} 的新密码（至少 6 位）`);
    if (!value) return;
    if (value.length < 6) {
      setMsg("新密码至少 6 位");
      return;
    }
    await api.post(`/admin/users/${id}/reset-password`, { new_password: value });
    setMsg(`用户 ${username} 密码已重置`);
  }

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold text-white">用户管理</h1>
      <div className="flex items-center gap-3">
        <button
          type="button"
          className="bg-destructive/90 hover:bg-destructive text-white text-sm rounded px-3 py-2"
          onClick={() => void deleteSelected()}
        >
          删除选中用户
        </button>
        {msg && <p className="text-sm text-accent">{msg}</p>}
      </div>

      <div className="glass-card overflow-x-auto">
        <table className="w-full text-sm text-left">
          <thead className="text-white/55 border-b border-white/10">
            <tr>
              <th className="py-3 px-4">
                <input
                  type="checkbox"
                  checked={rows.length > 0 && rows.every((u) => selected[u.id])}
                  onChange={(e) =>
                    setSelected(
                      Object.fromEntries(rows.map((u) => [u.id, e.target.checked])),
                    )
                  }
                />
              </th>
              <th className="py-3 px-4">用户</th>
              <th className="py-3 px-4">等级</th>
              <th className="py-3 px-4">调整</th>
            </tr>
          </thead>
          <tbody className="text-white/85">
            {rows.map((u) => (
              <tr key={u.id} className="border-b border-white/5">
                <td className="py-3 px-4">
                  <input
                    type="checkbox"
                    checked={!!selected[u.id]}
                    onChange={(e) =>
                      setSelected((prev) => ({
                        ...prev,
                        [u.id]: e.target.checked,
                      }))
                    }
                  />
                </td>
                <td className="py-3 px-4">
                  <div className="font-medium">{u.username}</div>
                  <div className="text-xs text-white/40">{u.email || "—"}</div>
                </td>
                <td className="py-3 px-4">{u.level}</td>
                <td className="py-3 px-4">
                  <div className="flex items-center gap-2">
                    <select
                      className="bg-white/5 border border-white/10 rounded px-2 py-1 text-xs"
                      value={u.level}
                      onChange={(e) => void setLevel(u.id, e.target.value)}
                    >
                      <option value="FREE">FREE</option>
                      <option value="VIP">VIP</option>
                      <option value="ADMIN">ADMIN</option>
                    </select>
                    <button
                      type="button"
                      className="text-xs text-primary hover:underline"
                      onClick={() => void resetPassword(u.id, u.username)}
                    >
                      重置密码
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
