"use client";

import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Loader2, Send } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

export function LinkSubmitBox() {
  const [url, setUrl] = useState("");
  const [torrentFile, setTorrentFile] = useState<File | null>(null);
  const queryClient = useQueryClient();
  const [localErr, setLocalErr] = useState<string | null>(null);
  const [authed, setAuthed] = useState(false);

  useEffect(() => {
    setAuthed(!!window.localStorage.getItem("access_token"));
  }, []);

  const submit = useMutation({
    mutationFn: async (u: string) => {
      const { data } = await api.post("/tasks", { url: u });
      return data;
    },
    onSuccess: () => {
      setUrl("");
      setLocalErr(null);
      void queryClient.invalidateQueries({ queryKey: ["tasks"] });
    },
  });

  const submitTorrent = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append("file", file);
      const { data } = await api.post("/tasks/upload-torrent", form, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      return data;
    },
    onSuccess: () => {
      setTorrentFile(null);
      setLocalErr(null);
      void queryClient.invalidateQueries({ queryKey: ["tasks"] });
    },
  });

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="glass-card p-6 max-w-2xl mx-auto"
    >
      <div className="flex gap-3">
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="粘贴磁力链接、HTTP 地址或社交媒体链接..."
          className="flex-1 bg-white/5 border border-white/10 rounded-lg px-4 py-3 text-white placeholder:text-white/30 focus:outline-none focus:ring-2 focus:ring-ring/40"
        />
        <button
          type="button"
          onClick={() => {
            setLocalErr(null);
            if (!authed) {
              setLocalErr("请先登录后再提交任务");
              return;
            }
            submit.mutate(url);
          }}
          disabled={!url || submit.isPending || submitTorrent.isPending}
          className="bg-primary hover:bg-primary-hover text-primary-foreground px-6 py-3 rounded-lg flex items-center gap-2 disabled:opacity-50 transition-colors"
        >
          {submit.isPending ? (
            <Loader2 className="animate-spin shrink-0" size={18} />
          ) : (
            <Send size={18} />
          )}
          提交
        </button>
      </div>
      <div className="flex items-center gap-3 mt-3">
        <input
          type="file"
          accept=".torrent,application/x-bittorrent"
          onChange={(e) => setTorrentFile(e.target.files?.[0] ?? null)}
          className="text-xs text-white/70 file:mr-3 file:rounded file:border-0 file:bg-white/10 file:px-3 file:py-2 file:text-white hover:file:bg-white/20"
        />
        <button
          type="button"
          onClick={() => {
            setLocalErr(null);
            if (!authed) {
              setLocalErr("请先登录后再提交任务");
              return;
            }
            if (!torrentFile) {
              setLocalErr("请先选择 .torrent 文件");
              return;
            }
            submitTorrent.mutate(torrentFile);
          }}
          disabled={!torrentFile || submit.isPending || submitTorrent.isPending}
          className="bg-white/10 hover:bg-white/20 text-white px-4 py-2 rounded-lg text-sm disabled:opacity-50"
        >
          {submitTorrent.isPending ? "上传中..." : "上传种子"}
        </button>
      </div>
      {localErr && <p className="text-destructive text-sm mt-2">{localErr}</p>}
      {(submit.isError || submitTorrent.isError) && (
        <p className="text-destructive text-sm mt-2">
          {(() => {
            const err = (submit.error || submitTorrent.error) as { response?: { data?: unknown } };
            const d = err?.response?.data;
            if (typeof d === "object" && d && "detail" in d) {
              const det = (d as { detail: unknown }).detail;
              if (typeof det === "string") return det;
              if (typeof det === "object" && det && "message" in det) return String((det as { message: unknown }).message);
            }
            return "提交失败";
          })()}
        </p>
      )}
    </motion.div>
  );
}
