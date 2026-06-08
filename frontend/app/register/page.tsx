"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Navbar } from "@/components/navbar";
import { api } from "@/lib/api";
import { getApiErrorMessage } from "@/lib/error";

export default function RegisterPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [email, setEmail] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setPending(true);
    try {
      await api.post("/auth/register", { username, password, email: email || undefined });
      router.push("/login");
    } catch (ex: unknown) {
      setErr(getApiErrorMessage(ex, "注册失败"));
    } finally {
      setPending(false);
    }
  }

  return (
    <div>
      <Navbar />
      <main className="max-w-md mx-auto px-4 py-20">
        <div className="glass-card p-8">
          <h1 className="text-xl font-semibold text-white mb-6">注册</h1>
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
              <label className="block text-xs text-white/55 mb-1">邮箱（可选）</label>
              <input
                type="email"
                className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-white"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
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
              {pending ? "…" : "创建账号"}
            </button>
          </form>
          <p className="text-sm text-white/45 mt-4 text-center">
            <Link href="/login" className="text-primary hover:underline">
              已有账号？
            </Link>
          </p>
        </div>
      </main>
    </div>
  );
}
