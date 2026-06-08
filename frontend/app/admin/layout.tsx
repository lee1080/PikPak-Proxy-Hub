"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ReactNode, useEffect } from "react";
import { Navbar } from "@/components/navbar";
import { getStoredUser } from "@/lib/auth";

const NAV = [
  { href: "/admin", label: "统计看板" },
  { href: "/admin/accounts", label: "号池管理" },
  { href: "/admin/tasks", label: "任务列表" },
  { href: "/admin/users", label: "用户管理" },
  { href: "/admin/logs", label: "运行日志" },
];

export default function AdminLayout({ children }: { children: ReactNode }) {
  const router = useRouter();
  const path = usePathname();

  useEffect(() => {
    const u = getStoredUser();
    if (!u || u.level !== "ADMIN") {
      router.replace("/");
      return;
    }
    if (u.must_change_password) {
      router.replace("/admin/force-change");
    }
  }, [router]);

  return (
    <div>
      <Navbar />
      <div className="max-w-6xl mx-auto px-4 py-10 flex gap-10">
        <aside className="w-52 shrink-0 space-y-1">
          <p className="text-xs text-white/35 uppercase tracking-wide mb-3">控制台</p>
          {NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={`block rounded-lg px-3 py-2 text-sm transition-colors ${
                path === item.href ? "bg-primary/20 text-primary" : "text-white/70 hover:bg-white/5"
              }`}
            >
              {item.label}
            </Link>
          ))}
        </aside>
        <div className="flex-1 min-w-0">{children}</div>
      </div>
    </div>
  );
}
