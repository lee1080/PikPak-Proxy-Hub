"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Navbar } from "@/components/navbar";
import { api } from "@/lib/api";
import { getStoredUser, persistAuth } from "@/lib/auth";

export default function ForceChangePage() {
  const router = useRouter();
  const [currentPassword, setCurrentPassword] = useState("admin123");
  const [newPassword, setNewPassword] = useState("");
  const [newUsername, setNewUsername] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    const u = getStoredUser();
    if (!u || u.level !== "ADMIN") router.replace("/");
  }, [router]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setOk(null);
    setPending(true);
    try {
      const { data } = await api.post("/auth/admin/force-change", {
        current_password: currentPassword,
        new_password: newPassword,
        new_username: newUsername || undefined,
      });

      const token = window.localStorage.getItem("access_token");
      const refresh = window.localStorage.getItem("refresh_token");
      if (token && refresh) {
        persistAuth({
          access_token: token,
          refresh_token: refresh,
          user: data,
        });
      }

      setOk("修改成功，正在进入管理后台…");
      router.replace("/admin");
      router.refresh();
    } catch (ex: unknown) {
      const msg =
        typeof ex === "object" &&
        ex &&
        "response" in ex &&
        typeof (ex as { response?: { data?: { detail?: string } } }).response?.data?.detail ===
          "string"
          ? (ex as { response: { data: { detail: string } } }).response.data.detail
          : "修改失败";
      setErr(msg);
    } finally {
      setPending(false);
    }
  }

  return (
    <div>
      <Navbar />
      <main className="max-w-md mx-auto px-4 py-20">
        <div className="glass-card p-8">
          <h1 className="text-xl font-semibold text-white mb-2">首次登录：修改管理员密码</h1>
          <p className="text-sm text-white/50 mb-6">
            默认账号密码为 <span className="text-white">admin / admin123</span>，登录后必须修改密码；
            账号（用户名）可选修改。
          </p>

          <form onSubmit={submit} className="space-y-4">
            <div>
              <label className="block text-xs text-white/55 mb-1">当前密码</label>
              <input
                required
                type="password"
                className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-white"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
              />
            </div>
            <div>
              <label className="block text-xs text-white/55 mb-1">新密码（至少 6 位）</label>
              <input
                required
                type="password"
                className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-white"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
              />
            </div>
            <div>
              <label className="block text-xs text-white/55 mb-1">新用户名（可选）</label>
              <input
                className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-white"
                value={newUsername}
                onChange={(e) => setNewUsername(e.target.value)}
                placeholder="不填则保持 admin"
              />
            </div>

            {err && <p className="text-sm text-destructive">{err}</p>}
            {ok && <p className="text-sm text-accent">{ok}</p>}

            <button
              type="submit"
              disabled={pending}
              className="w-full bg-primary hover:bg-primary-hover text-white py-3 rounded-lg font-medium disabled:opacity-50"
            >
              {pending ? "处理中…" : "确认修改"}
            </button>
          </form>
        </div>
      </main>
    </div>
  );
}

