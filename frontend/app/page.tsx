"use client";

import { useEffect, useState } from "react";
import { Navbar } from "@/components/navbar";
import { LinkSubmitBox } from "@/components/link-submit-box";
import { TaskList } from "@/components/task-list";

export default function Page() {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const authed = mounted && typeof window !== "undefined" && !!window.localStorage.getItem("access_token");

  return (
    <div>
      <Navbar />
      <main className="max-w-6xl mx-auto px-4 py-16 space-y-6">
        <div className="text-center mb-12">
          <h1 className="text-3xl md:text-4xl font-bold text-white tracking-tight mb-4">
            资源离线 · 一键取回
          </h1>
          {!authed && (
            <p className="text-white/55 text-sm max-w-lg mx-auto">
              请先 <a href="/register" className="text-primary hover:underline">注册</a> 并{" "}
              <a href="/login" className="text-primary hover:underline">登录</a>
              ，然后提交磁力或 HTTP 链接。
            </p>
          )}
        </div>

        <LinkSubmitBox />
        <TaskList authed={authed} />
      </main>
    </div>
  );
}
