import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "PikPak Proxy Hub",
  description: "基于 PikPak 的资源中转加速平台",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" className="dark">
      <body className="min-h-screen grid-background antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
