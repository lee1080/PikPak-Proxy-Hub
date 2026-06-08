"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { LogOut } from "lucide-react";
import { clearAuth, getStoredUser, type StoredUser } from "@/lib/auth";

export function Navbar() {
  const router = useRouter();
  const [mounted, setMounted] = useState(false);
  const [user, setUser] = useState<StoredUser | null>(null);

  useEffect(() => {
    setUser(getStoredUser());
    setMounted(true);
  }, []);

  return (
    <header className="border-b border-white/10 bg-black/40 backdrop-blur-md sticky top-0 z-40">
      <div className="max-w-5xl mx-auto px-4 h-14 flex items-center justify-between">
        <Link href="/" className="text-lg font-semibold text-white hover:text-primary transition-colors">
          PikPak Proxy Hub
        </Link>
        <nav className="flex gap-6 text-sm items-center text-white/80">
          {!mounted || !user ? (
            <>
              <Link href="/login" className="hover:text-white">
                登录
              </Link>
              <Link href="/register" className="hover:text-white">
                注册
              </Link>
            </>
          ) : (
            <>
              {user.level === "ADMIN" && (
                <Link href="/admin" className="hover:text-primary">
                  管理后台
                </Link>
              )}
              <span className="text-white/40">{user.username}</span>
              <button
                type="button"
                className="flex items-center gap-1 hover:text-white"
                onClick={() => {
                  clearAuth();
                  setUser(null);
                  router.refresh();
                  router.push("/");
                }}
              >
                <LogOut size={16} /> 退出
              </button>
            </>
          )}
        </nav>
      </div>
    </header>
  );
}
