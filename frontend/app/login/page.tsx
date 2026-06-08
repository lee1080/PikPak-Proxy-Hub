"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Navbar } from "@/components/navbar";
import { api } from "@/lib/api";
import { persistAuth } from "@/lib/auth";
import { getApiErrorMessage } from "@/lib/error";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [sessionExpired, setSessionExpired] = useState(false);

  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    setSessionExpired(q.get("reason") === "expired");
  }, []);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setPending(true);
    try {
      const { data } = await api.post("/auth/login", { username, password });
      persistAuth({
        access_token: data.access_token,
        refresh_token: data.refresh_token,
        user: data.user,
      });
      router.push("/");
      router.refresh();
    } catch (ex: unknown) {
      setErr(getApiErrorMessage(ex, "登录失败"));
    } finally {
      setPending(false);
    }
  }

  return (
    <div>
      <Navbar />
      <main className="max-w-md mx-auto px-4 py-20">
        <div className="glass-card p-8">
          <h1 className="text-xl font-semibold text-white mb-6">登录</h1>
          {sessionExpired && (
            <p className="text-sm text-amber-400/90 mb-4">登录已过期，请重新登录</p>
          )}
          <form onSubmit={onSubmit} className="space-y-4">
            <div>
              <label className="block text-xs text-white/55 mb-1">用户名</label>
              <input
                required
                className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-white"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
            </div>
            <div>
              <label className="block text-xs text-white/55 mb-1">密码</label>
              <input
                required
                type="password"
                className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-white"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>
            {err && <p className="text-sm text-destructive">{err}</p>}
            <button
              type="submit"
              disabled={pending}
              className="w-full bg-primary hover:bg-primary-hover text-white py-3 rounded-lg font-medium disabled:opacity-50"
            >
              {pending ? "…" : "登录"}
            </button>
          </form>
          <p className="text-sm text-white/45 mt-4 text-center">
            还没有账号？
            <Link href="/register" className="text-primary ml-1 hover:underline">
              注册
            </Link>
          </p>
        </div>
      </main>
    </div>
  );
}
