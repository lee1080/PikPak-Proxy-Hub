"use client";

import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";

/**
 * 在 https://mypikpak.com 控制台粘贴运行后，请刷新页面再登录。
 * token 多数来自 captcha/init（或 shield/captcha）的 JSON 响应，而不是 signin 成功响应。
 */
const PIKPAK_CAPTCHA_CLIP_SCRIPT = `(function(){
var TAG="[PikPak token 助手] ";
function copyToken(t){
  if(!t||typeof t!=="string")return;
  (navigator.clipboard&&navigator.clipboard.writeText?navigator.clipboard.writeText(t):Promise.reject())
    .then(function(){alert("captcha_token 已复制，回到号池管理页粘贴并保存。");console.log(TAG+"已复制，长度 "+t.length);})
    .catch(function(){window.prompt("请手动复制 captcha_token：",t);});
}
function pickTok(j){if(!j||typeof j!=="object")return null;return j.captcha_token||j.captchaToken||null;}
function tryJson(s){try{return JSON.parse(s);}catch(e){return null;}}
function sniffInitResponse(url,text){
  if(url.indexOf("captcha/init")===-1&&url.indexOf("shield/captcha")===-1)return;
  var j=tryJson(text);var t=pickTok(j);
  if(t){console.log(TAG+"从 init 响应抓到 token");copyToken(t);}
}
var xo=XMLHttpRequest.prototype.open,xs=XMLHttpRequest.prototype.send;
XMLHttpRequest.prototype.open=function(m,u){this._pkU=u;return xo.apply(this,arguments);};
XMLHttpRequest.prototype.send=function(body){
  var x=this;
  x.addEventListener("load",function(){
    var u=String(x.responseURL||x._pkU||"");
    if(body&&typeof body==="string"&&u.indexOf("signin")!==-1){var j=tryJson(body);var t=pickTok(j);if(t){console.log(TAG+"从 signin 请求体抓到 token");copyToken(t);}}
    sniffInitResponse(u,x.responseText||"");
  });
  return xs.apply(this,arguments);
};
var wf=window.fetch;
window.fetch=function(input,init){
  init=init||{};
  var url=typeof input==="string"?input:(input&&input.url)||"";
  if(input instanceof Request&&input.url.indexOf("signin")!==-1){
    input.clone().text().then(function(b){var j=tryJson(b);var t=pickTok(j);if(t){console.log(TAG+"从 signin(Request) 请求体抓到 token");copyToken(t);}}).catch(function(){});
  }
  if(init.body&&typeof init.body==="string"&&url.indexOf("signin")!==-1){
    var j2=tryJson(init.body);var t2=pickTok(j2);if(t2){console.log(TAG+"从 signin(fetch) 请求体抓到 token");copyToken(t2);}
  }
  return wf.apply(this,arguments).then(function(res){
    var u2=String(res.url||url);
    if(u2.indexOf("captcha/init")!==-1||u2.indexOf("shield/captcha")!==-1){
      res.clone().json().then(function(j){var t=pickTok(j);if(t){console.log(TAG+"从 init(fetch) 响应抓到 token");copyToken(t);}}).catch(function(){});
    }
    return res;
  });
};
console.log(TAG+"已挂载：请刷新本页再登录。若仍无弹窗，在 Network 里点开 captcha/init → Response 手动复制 captcha_token。");
alert("已启用抓取：请先刷新当前 PikPak 页面，再完成登录；token 通常来自 captcha/init 的响应。");
})();`;

type Account = {
  id: string;
  email: string;
  pool_type: string;
  status: string;
  quota_total: number;
  quota_used: number;
  quota_percent: number;
  daily_tasks_left: number;
  daily_tasks_left_unknown?: boolean;
  /** 网盘根目录下 PPHUB 文件夹 id，离线任务写入此目录 */
  hub_folder_id?: string | null;
};

type RuntimeConfig = {
  pikpak_proxy: string | null;
  pikpak_captcha_token: string | null;
  pikpak_captcha_sign_profile: string;
  pikpak_fixed_device_id: string | null;
  admin_log_retention_days?: number;
};

type Toast = {
  id: number;
  kind: "success" | "error" | "info";
  text: string;
};

type CleanupTarget = Pick<Account, "id" | "email" | "pool_type" | "status" | "quota_used" | "quota_total" | "quota_percent">;

export default function AdminAccountsPage() {
  const [rows, setRows] = useState<Account[]>([]);
  const [poolFilter, setPoolFilter] = useState<"ALL" | "FREE" | "PREMIUM">("ALL");
  const [statusFilter, setStatusFilter] = useState<"ALL" | "ACTIVE" | "INACTIVE">("ALL");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [pool, setPool] = useState<"FREE" | "PREMIUM">("FREE");
  const [msg, setMsg] = useState<string | null>(null);
  const [proxy, setProxy] = useState("");
  const [captchaToken, setCaptchaToken] = useState("");
  const [fixedDeviceId, setFixedDeviceId] = useState("");
  const [signProfile, setSignProfile] = useState<"android" | "web">("android");
  const [runtimeMsg, setRuntimeMsg] = useState<string | null>(null);
  const [helperMsg, setHelperMsg] = useState<string | null>(null);
  const [diagMsg, setDiagMsg] = useState<string | null>(null);
  const [readyToImport, setReadyToImport] = useState(false);
  const [isGeneratingToken, setIsGeneratingToken] = useState(false);
  const [isImporting, setIsImporting] = useState(false);
  const [isCleaningId, setIsCleaningId] = useState<string | null>(null);
  const [isSyncingId, setIsSyncingId] = useState<string | null>(null);
  const [isVerifyingAll, setIsVerifyingAll] = useState(false);
  const [isResettingDaily, setIsResettingDaily] = useState(false);
  const [savingDailyId, setSavingDailyId] = useState<string | null>(null);
  const [dailyDraft, setDailyDraft] = useState<Record<string, string>>({});
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [cleanupTarget, setCleanupTarget] = useState<CleanupTarget | null>(null);
  const [cleanupConfirmText, setCleanupConfirmText] = useState("");

  const pushToast = useCallback((kind: Toast["kind"], text: string, timeoutMs = 3500) => {
    const id = Date.now() + Math.floor(Math.random() * 1000);
    setToasts((prev) => [...prev, { id, kind, text }]);
    window.setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, timeoutMs);
  }, []);

  const load = useCallback(async () => {
    const { data } = await api.get<{ accounts: Account[] }>("/admin/accounts", {
      params: {
        ...(poolFilter === "ALL" ? {} : { pool_type: poolFilter }),
        ...(statusFilter === "ALL" ? {} : { status: statusFilter }),
      },
    });
    const accounts = (data.accounts || []).map((a) => ({ ...a, daily_tasks_left_unknown: false }));
    setRows(accounts);
    setDailyDraft(
      Object.fromEntries(
        accounts.map((a) => [
          a.id,
          a.pool_type === "PREMIUM" && a.daily_tasks_left === -1 ? "-1" : String(a.daily_tasks_left),
        ]),
      ),
    );
    const rc = await api.get<RuntimeConfig>("/admin/runtime-config");
    setProxy(rc.data.pikpak_proxy || "");
    setCaptchaToken(rc.data.pikpak_captcha_token || "");
    setFixedDeviceId(rc.data.pikpak_fixed_device_id || "");
    const sp = rc.data.pikpak_captcha_sign_profile === "web" ? "web" : "android";
    setSignProfile(sp);
  }, [poolFilter, statusFilter]);

  useEffect(() => {
    void load().catch(() => {});
  }, [load]);

  async function add(ev: React.FormEvent) {
    ev.preventDefault();
    if (isImporting) return;
    setMsg(null);
    setIsImporting(true);
    pushToast("info", "已提交登录入库请求，正在处理，请勿重复点击。", 2200);
    try {
      await api.post("/admin/accounts", { email, password, pool_type: pool });
      setEmail("");
      setPassword("");
      setReadyToImport(false);
      setMsg("账号已添加");
      pushToast("success", "账号已添加");
      await load();
    } catch (ex: unknown) {
      const d =
        ex && typeof ex === "object" && "response" in ex
          ? (ex as { response?: { data?: { detail?: string | unknown } } }).response?.data?.detail
          : undefined;
      setMsg(typeof d === "string" ? d : "添加失败");
      pushToast("error", typeof d === "string" ? d : "添加失败");
    } finally {
      setIsImporting(false);
    }
  }

  async function remove(id: string) {
    await api.delete(`/admin/accounts/${id}`);
    await load();
  }

  async function verifyAllAccounts() {
    if (isVerifyingAll || isCleaningId || isSyncingId) return;
    setIsVerifyingAll(true);
    setMsg(null);
    pushToast("info", "正在依次验证账号，请勿关闭页面…", 4000);
    try {
      const { data } = await api.post<{
        ok: boolean;
        total: number;
        ok_count: number;
        relogin_ok_count: number;
        deactivated_count: number;
        results: { email: string; outcome: string; message: string }[];
      }>("/admin/accounts/verify-all", undefined, {
        params: {
          ...(poolFilter === "ALL" ? {} : { pool_type: poolFilter }),
          ...(statusFilter === "ALL" ? {} : { status: statusFilter }),
        },
        timeout: 600_000,
      });
      const summary = `验证完成：共 ${data.total} 个，有效 ${data.ok_count}，重新登录恢复 ${data.relogin_ok_count}，已停用 ${data.deactivated_count}`;
      setMsg(summary);
      pushToast(data.deactivated_count === 0 ? "success" : "error", summary, 9000);
      if (data.deactivated_count > 0) {
        const bad = (data.results || [])
          .filter((r) => r.outcome === "deactivated")
          .slice(0, 3)
          .map((r) => r.email)
          .join("、");
        if (bad) {
          pushToast("error", `已停用：${bad}${data.deactivated_count > 3 ? " 等" : ""}`, 10000);
        }
      }
      await load();
    } catch (ex: unknown) {
      const d =
        ex && typeof ex === "object" && "response" in ex
          ? (ex as { response?: { data?: { detail?: string | unknown } } }).response?.data?.detail
          : undefined;
      const text = typeof d === "string" ? d : "批量验证失败";
      setMsg(text);
      pushToast("error", text, 8000);
    } finally {
      setIsVerifyingAll(false);
    }
  }

  async function resetAllFreeDailyLimits() {
    if (isResettingDaily || isVerifyingAll || !!isCleaningId || !!isSyncingId) return;
    if (!window.confirm(`将所有 FREE 账号的剩次数重置为配置默认值？`)) return;
    setIsResettingDaily(true);
    pushToast("info", "正在重置 FREE 池今日次数…", 2200);
    try {
      const { data } = await api.post<{
        ok: boolean;
        updated_count: number;
        daily_limit: number;
        reset_date: string;
        message: string;
      }>("/admin/accounts/reset-daily-limits");
      pushToast("success", data.message || "已重置", 6000);
      await load();
    } catch (ex: unknown) {
      const d =
        ex && typeof ex === "object" && "response" in ex
          ? (ex as { response?: { data?: { detail?: string | unknown } } }).response?.data?.detail
          : undefined;
      pushToast("error", typeof d === "string" ? d : "重置失败", 6000);
    } finally {
      setIsResettingDaily(false);
    }
  }

  async function saveDailyTasksLeft(acc: Account) {
    if (savingDailyId || isResettingDaily) return;
    const raw = (dailyDraft[acc.id] ?? "").trim();
    if (!raw) {
      pushToast("error", "请输入剩次数");
      return;
    }
    const n = Number(raw);
    if (!Number.isFinite(n) || !Number.isInteger(n)) {
      pushToast("error", "剩次数须为整数");
      return;
    }
    setSavingDailyId(acc.id);
    try {
      const { data } = await api.patch<{
        ok: boolean;
        daily_tasks_left: number;
        message: string;
      }>(`/admin/accounts/${acc.id}/daily-tasks-left`, undefined, {
        params: { daily_tasks_left: n },
      });
      pushToast("success", data.message || "已保存");
      setRows((prev) =>
        prev.map((r) =>
          r.id === acc.id
            ? { ...r, daily_tasks_left: data.daily_tasks_left, daily_tasks_left_unknown: false }
            : r,
        ),
      );
      setDailyDraft((prev) => ({
        ...prev,
        [acc.id]:
          acc.pool_type === "PREMIUM" && data.daily_tasks_left === -1
            ? "-1"
            : String(data.daily_tasks_left),
      }));
    } catch (ex: unknown) {
      const d =
        ex && typeof ex === "object" && "response" in ex
          ? (ex as { response?: { data?: { detail?: string | unknown } } }).response?.data?.detail
          : undefined;
      pushToast("error", typeof d === "string" ? d : "设置失败", 6000);
    } finally {
      setSavingDailyId(null);
    }
  }

  async function toggleAccountStatus(acc: Account) {
    if (isCleaningId || isSyncingId || isVerifyingAll) return;
    const next = (acc.status || "").toUpperCase() === "ACTIVE" ? "INACTIVE" : "ACTIVE";
    pushToast("info", `${next === "ACTIVE" ? "正在启用" : "正在停用"}「${acc.email}」…`, 2200);
    try {
      await api.patch(`/admin/accounts/${acc.id}/status`, undefined, { params: { status: next } });
      pushToast("success", `${next === "ACTIVE" ? "已启用" : "已停用"}：${acc.email}`);
      await load();
    } catch (ex: unknown) {
      const d =
        ex && typeof ex === "object" && "response" in ex
          ? (ex as { response?: { data?: { detail?: string | unknown } } }).response?.data?.detail
          : undefined;
      pushToast("error", typeof d === "string" ? d : "操作失败", 6000);
    }
  }

  async function syncFromPikpak(acc: Account) {
    if (isSyncingId || isCleaningId || isVerifyingAll) return;
    setIsSyncingId(acc.id);
    pushToast("info", `正在从 PikPak 同步「${acc.email}」…`, 2600);
    try {
      const { data } = await api.post<{
        ok: boolean;
        message: string;
        quota_total: number;
        quota_used: number;
        daily_tasks_left: number;
        offline_remaining_from_pikpak: number | null;
        updated_daily_tasks_from_pikpak: boolean;
      }>(`/admin/accounts/${acc.id}/sync-from-pikpak`);
      const text =
        typeof data?.message === "string"
          ? data.message
          : "同步完成";
      pushToast("success", text, 8000);
      setRows((prev) =>
        prev.map((r) => {
          if (r.id !== acc.id) return r;
          const quota_total = Number(data.quota_total || r.quota_total);
          const quota_used = Number(data.quota_used || r.quota_used);
          const quota_percent =
            quota_total > 0 ? Math.round((quota_used / quota_total) * 10000) / 100 : r.quota_percent;
          const hasOfficialRemaining = data.offline_remaining_from_pikpak !== null;
          const updatedDaily = !!data.updated_daily_tasks_from_pikpak && hasOfficialRemaining;
          return {
            ...r,
            quota_total,
            quota_used,
            quota_percent,
            daily_tasks_left: updatedDaily ? Number(data.daily_tasks_left) : r.daily_tasks_left,
            daily_tasks_left_unknown: r.pool_type === "FREE" && !updatedDaily,
          };
        }),
      );
    } catch (ex: unknown) {
      const d =
        ex && typeof ex === "object" && "response" in ex
          ? (ex as { response?: { data?: { detail?: string | unknown } } }).response?.data?.detail
          : undefined;
      pushToast("error", typeof d === "string" ? d : "同步失败", 6000);
    } finally {
      setIsSyncingId(null);
    }
  }

  async function cleanupDrive(acc: Account) {
    if (isCleaningId || isSyncingId || isVerifyingAll) return;
    setCleanupConfirmText("");
    setCleanupTarget({
      id: acc.id,
      email: acc.email,
      pool_type: acc.pool_type,
      status: acc.status,
      quota_used: acc.quota_used,
      quota_total: acc.quota_total,
      quota_percent: acc.quota_percent,
    });
  }

  async function confirmCleanupDrive() {
    if (!cleanupTarget) return;
    if (cleanupConfirmText.trim() !== "清空") {
      pushToast("error", "请输入“清空”后再继续");
      return;
    }
    setMsg(null);
    setIsCleaningId(cleanupTarget.id);
    pushToast("info", `已提交清空请求（${cleanupTarget.email}），处理中，请勿重复点击。`, 2600);
    try {
      const { data } = await api.post<{ ok: boolean; message: string; deleted_files: number }>(
        `/admin/accounts/${cleanupTarget.id}/cleanup-drive`,
      );
      const text = typeof data?.message === "string" ? data.message : "清空网盘已完成";
      setMsg(text);
      pushToast(data?.ok ? "success" : "error", text, 5500);
      await load();
    } catch (ex: unknown) {
      const d =
        ex && typeof ex === "object" && "response" in ex
          ? (ex as { response?: { data?: { detail?: string | unknown } } }).response?.data?.detail
          : undefined;
      setMsg(typeof d === "string" ? d : "清空网盘失败");
      pushToast("error", typeof d === "string" ? d : "清空网盘失败", 5000);
    } finally {
      setCleanupTarget(null);
      setCleanupConfirmText("");
      setIsCleaningId(null);
    }
  }

  async function saveRuntimeConfig(ev: React.FormEvent) {
    ev.preventDefault();
    setRuntimeMsg(null);
    try {
      await api.patch("/admin/runtime-config", {
        pikpak_proxy: proxy,
        pikpak_captcha_token: captchaToken,
        pikpak_captcha_sign_profile: signProfile,
        pikpak_fixed_device_id: fixedDeviceId,
      });
      setRuntimeMsg("代理与验证码 Token 已保存（当前运行实例已生效）");
      pushToast("success", "高级设置已保存");
    } catch (ex: unknown) {
      const d =
        ex && typeof ex === "object" && "response" in ex
          ? (ex as { response?: { data?: { detail?: string | unknown } } }).response?.data?.detail
          : undefined;
      setRuntimeMsg(typeof d === "string" ? d : "保存失败");
      pushToast("error", typeof d === "string" ? d : "保存失败");
    }
  }

  async function testProxy() {
    setDiagMsg(null);
    try {
      const { data } = await api.post<{ ok: boolean; status_code?: number; message: string }>(
        "/admin/runtime-config/test-proxy",
      );
      setDiagMsg(`${data.ok ? "✅" : "❌"} ${data.message}`);
      pushToast(data.ok ? "success" : "error", data.message);
    } catch (ex: unknown) {
      const d =
        ex && typeof ex === "object" && "response" in ex
          ? (ex as { response?: { data?: { detail?: string | unknown } } }).response?.data?.detail
          : undefined;
      setDiagMsg(typeof d === "string" ? d : "代理测试失败");
      pushToast("error", typeof d === "string" ? d : "代理测试失败");
    }
  }

  async function copyCaptchaHelperScript() {
    setHelperMsg(null);
    try {
      await navigator.clipboard.writeText(PIKPAK_CAPTCHA_CLIP_SCRIPT);
      setHelperMsg("脚本已复制。请打开 PikPak 官网，在控制台粘贴运行后再登录。");
      pushToast("success", "脚本已复制到剪贴板");
    } catch {
      setHelperMsg("复制失败：请手动全选下方脚本复制（或换 Chrome）。");
      pushToast("error", "复制失败：请手动复制脚本");
    }
  }

  async function generateCaptchaToken() {
    if (isGeneratingToken) return;
    setDiagMsg(null);
    setMsg(null);
    if (!email || !password) {
      setDiagMsg("请先填写账号和密码");
      return;
    }
    setIsGeneratingToken(true);
    pushToast("info", "已提交 token 生成请求，正在处理，请勿重复点击。", 2200);
    try {
      const { data } = await api.post<{
        ok: boolean;
        message: string;
        captcha_token?: string;
        device_id?: string;
      }>("/admin/runtime-config/generate-captcha-token", {
        email,
        password,
      });
      if (data.captcha_token) {
        setCaptchaToken(data.captcha_token);
      }
      if (data.device_id) {
        setFixedDeviceId(data.device_id);
      }
      setDiagMsg(`${data.ok ? "✅" : "❌"} ${data.message}`);
      setReadyToImport(Boolean(data.ok));
      pushToast(data.ok ? "success" : "error", data.message, data.ok ? 2800 : 5000);
    } catch (ex: unknown) {
      const d =
        ex && typeof ex === "object" && "response" in ex
          ? (ex as { response?: { data?: { detail?: string | unknown } } }).response?.data?.detail
          : undefined;
      setDiagMsg(typeof d === "string" ? d : "生成 captcha_token 失败");
      setReadyToImport(false);
      pushToast("error", typeof d === "string" ? d : "生成 captcha_token 失败", 5000);
    } finally {
      setIsGeneratingToken(false);
    }
  }

  return (
    <div className="space-y-8">
      <h1 className="text-xl font-semibold text-white">号池管理</h1>

      <div className="w-full max-w-5xl space-y-8">
        <form onSubmit={add} className="glass-card p-6 space-y-3 w-full">
          <p className="text-sm text-white/70 mb-2">只填账号密码即可：先生成 token，再点击登录入库。</p>
          <div className="grid gap-3 sm:grid-cols-2">
            <input
              placeholder="PikPak 账号（邮箱）"
              className="w-full bg-white/5 border border-white/10 rounded px-3 py-2 text-white text-sm"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
            <input
              type="password"
              placeholder="密码"
              className="w-full bg-white/5 border border-white/10 rounded px-3 py-2 text-white text-sm"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
            <select
              value={pool}
              onChange={(e) => setPool(e.target.value as "FREE" | "PREMIUM")}
              className="w-full bg-white/5 border border-white/10 rounded px-3 py-2 text-white text-sm"
            >
              <option value="FREE">FREE（免费池）</option>
              <option value="PREMIUM">PREMIUM（付费池）</option>
            </select>
            <div className="hidden sm:block" />
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <button
              type="button"
              onClick={() => void generateCaptchaToken()}
              disabled={isGeneratingToken}
              className="w-full bg-white/10 px-4 py-2 rounded-lg text-sm text-white hover:bg-white/20 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isGeneratingToken ? "生成中..." : "一键生成 captcha_token"}
            </button>
            <button
              type="submit"
              disabled={isImporting}
              className={`w-full px-6 py-2 rounded-lg text-sm text-white transition-all ${
                readyToImport
                  ? "bg-accent hover:bg-accent/90 ring-2 ring-accent/60 animate-pulse"
                  : "bg-primary hover:bg-primary-hover"
              } disabled:opacity-50 disabled:cursor-not-allowed`}
            >
              {isImporting ? "入库中..." : "PikPak 登录入库"}
            </button>
          </div>

          {readyToImport && (
            <p className="text-sm text-accent">下一步：请点击“PikPak 登录入库”完成账号入库。</p>
          )}
        </form>

        <form onSubmit={saveRuntimeConfig} className="glass-card p-6 space-y-3 w-full">
          <details className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-white/75">
            <summary className="cursor-pointer text-white/90 select-none">高级设置（代理、签名策略、Token 覆盖）</summary>
            <div className="space-y-3 mt-3">
              <input
                placeholder="PIKPAK_PROXY（例：http://127.0.0.1:7890 或 socks5://127.0.0.1:7891）"
                className="w-full bg-white/5 border border-white/10 rounded px-3 py-2 text-white text-sm"
                value={proxy}
                onChange={(e) => setProxy(e.target.value)}
              />
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                <label className="text-sm text-white/70 shrink-0">Captcha 签名策略</label>
                <select
                  value={signProfile}
                  onChange={(e) => setSignProfile(e.target.value as "android" | "web")}
                  className="flex-1 bg-white/5 border border-white/10 rounded px-3 py-2 text-white text-sm"
                >
                  <option value="android">android（默认）</option>
                  <option value="web">web（网页端）</option>
                </select>
              </div>
              <input
                placeholder="PIKPAK_CAPTCHA_TOKEN（可选，手动覆盖）"
                className="w-full bg-white/5 border border-white/10 rounded px-3 py-2 text-white text-sm"
                value={captchaToken}
                onChange={(e) => setCaptchaToken(e.target.value)}
              />
              <input
                placeholder="PIKPAK_FIXED_DEVICE_ID（可选，16-64 位）"
                className="w-full bg-white/5 border border-white/10 rounded px-3 py-2 text-white text-sm"
                value={fixedDeviceId}
                onChange={(e) => setFixedDeviceId(e.target.value)}
              />
              <div className="flex gap-3 flex-wrap">
                <button
                  type="submit"
                  className="bg-primary px-6 py-2 rounded-lg text-sm text-white hover:bg-primary-hover"
                >
                  保存高级设置
                </button>
                <button
                  type="button"
                  onClick={() => void testProxy()}
                  className="bg-white/10 px-4 py-2 rounded-lg text-sm text-white hover:bg-white/20"
                >
                  代理连通测试
                </button>
              </div>
            </div>
          </details>

          <details className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-white/75">
            <summary className="cursor-pointer text-white/90 select-none">从官网登录抓取 captcha_token（备用）</summary>
            <ol className="list-decimal pl-5 mt-3 space-y-2 text-white/70">
              <li>点击下方「复制控制台脚本」。</li>
              <li>
                新标签打开{" "}
                <a
                  href="https://mypikpak.com/"
                  target="_blank"
                  rel="noreferrer"
                  className="text-accent underline hover:no-underline"
                >
                  mypikpak.com
                </a>
                ，按 F12 → Console，粘贴脚本回车。
              </li>
              <li>刷新页面（F5）后再登录，抓到 token 会自动弹窗复制。</li>
            </ol>
            <div className="flex flex-wrap gap-2 mt-3">
              <button
                type="button"
                onClick={() => void copyCaptchaHelperScript()}
                className="bg-white/15 px-4 py-2 rounded-lg text-sm text-white hover:bg-white/25"
              >
                复制控制台脚本
              </button>
            </div>
          </details>
        </form>

        <div className="glass-card overflow-x-auto w-full">
          <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-white/10">
            <div className="text-sm text-white/70">池筛选</div>
            <div className="flex items-center gap-2 flex-wrap justify-end">
              <select
                value={poolFilter}
                onChange={(e) => setPoolFilter(e.target.value as "ALL" | "FREE" | "PREMIUM")}
                className="bg-white/5 border border-white/10 rounded px-3 py-2 text-white text-sm"
              >
                <option value="ALL">全部池</option>
                <option value="FREE">FREE（免费池）</option>
                <option value="PREMIUM">PREMIUM（付费池）</option>
              </select>
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value as "ALL" | "ACTIVE" | "INACTIVE")}
                className="bg-white/5 border border-white/10 rounded px-3 py-2 text-white text-sm"
              >
                <option value="ALL">全部状态</option>
                <option value="ACTIVE">仅启用</option>
                <option value="INACTIVE">仅停用</option>
              </select>
              <button
                type="button"
                disabled={isResettingDaily || isVerifyingAll || !!isCleaningId || !!isSyncingId}
                onClick={() => void resetAllFreeDailyLimits()}
                className="bg-white/10 hover:bg-white/20 px-4 py-2 rounded-lg text-sm text-white disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
                title="将所有 FREE 账号剩次数恢复为 Hub 配置默认值（默认 3），并重新启用"
              >
                {isResettingDaily ? "重置中…" : "重置 FREE 今日次数"}
              </button>
              <button
                type="button"
                disabled={isVerifyingAll || !!isCleaningId || !!isSyncingId || rows.length === 0}
                onClick={() => void verifyAllAccounts()}
                className="bg-primary hover:bg-primary-hover px-4 py-2 rounded-lg text-sm text-white disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
                title="按当前池/状态筛选依次验证；失效账号自动重新登录，仍失败则自动停用"
              >
                {isVerifyingAll ? "验证中…" : "一键验证账号"}
              </button>
            </div>
          </div>
          <table className="w-full text-sm text-left border-collapse">
          <thead className="text-white/55 border-b border-white/10">
            <tr>
              <th className="py-3 px-4">邮箱</th>
              <th className="py-3 px-4">池</th>
              <th className="py-3 px-4">状态</th>
              <th className="py-3 px-4">用量</th>
              <th
                className="py-3 px-4"
                title="FREE 池每日离线配额，Hub 按北京时间 0 点自动重置；可手动修改。PREMIUM 填 -1 表示无限。"
              >
                剩次数
              </th>
              <th className="py-3 px-4" title="根目录文件夹 PPHUB：离线任务写入此处；清空网盘仅清理该文件夹">
                PPHUB
              </th>
              <th className="py-3 px-4" />
            </tr>
          </thead>
          <tbody className="text-white/85">
            {rows.map((a) => (
              <tr key={a.id} className="border-b border-white/5">
                <td className="py-3 px-4">{a.email}</td>
                <td className="py-3 px-4">{a.pool_type}</td>
                <td className="py-3 px-4">{a.status}</td>
                <td className="py-3 px-4">
                  {Math.round(a.quota_used / 1024 / 1024)} / {Math.round(a.quota_total / 1024 / 1024)} MB (
                  {a.quota_percent}%)
                </td>
                <td className="py-3 px-4">
                  {a.pool_type === "FREE" && a.daily_tasks_left_unknown ? (
                    <span className="text-white/60 text-xs">未知</span>
                  ) : (
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <input
                        type="number"
                        className="w-14 bg-white/5 border border-white/10 rounded px-2 py-1 text-white text-xs"
                        value={dailyDraft[a.id] ?? String(a.daily_tasks_left)}
                        onChange={(e) =>
                          setDailyDraft((prev) => ({ ...prev, [a.id]: e.target.value }))
                        }
                        title={a.pool_type === "PREMIUM" ? "PREMIUM：-1=无限" : "FREE：0 表示今日已用完"}
                        disabled={!!savingDailyId || isResettingDaily}
                      />
                      <button
                        type="button"
                        className="text-xs text-accent hover:underline disabled:opacity-50"
                        disabled={!!savingDailyId || isResettingDaily}
                        onClick={() => void saveDailyTasksLeft(a)}
                      >
                        {savingDailyId === a.id ? "…" : "设置"}
                      </button>
                      {a.pool_type === "PREMIUM" && a.daily_tasks_left === -1 && (
                        <span className="text-white/45 text-xs">∞</span>
                      )}
                    </div>
                  )}
                </td>
                <td className="py-3 px-4 text-white/70">{a.hub_folder_id ? "已就绪" : "—"}</td>
                <td className="py-3 px-4 text-right">
                  <div className="flex items-center justify-end gap-3 flex-wrap">
                    <button
                      type="button"
                      disabled={!!isCleaningId || !!isSyncingId || isVerifyingAll}
                      className="text-xs text-white/70 hover:text-white underline decoration-white/20 hover:decoration-white/40 disabled:opacity-50 disabled:no-underline"
                      onClick={() => void toggleAccountStatus(a)}
                      title={a.status === "ACTIVE" ? "停用后不会再被分配新任务" : "启用后可继续分配新任务"}
                    >
                      {a.status === "ACTIVE" ? "停用" : "启用"}
                    </button>
                    <button
                      type="button"
                      disabled={!!isCleaningId || !!isSyncingId || isVerifyingAll}
                      className="text-accent hover:underline text-xs disabled:opacity-50 disabled:no-underline"
                      onClick={() => void syncFromPikpak(a)}
                    >
                      {isSyncingId === a.id ? "同步中…" : "同步 PikPak"}
                    </button>
                    <button
                      type="button"
                      disabled={!!isCleaningId || !!isSyncingId || isVerifyingAll}
                      className="text-destructive hover:underline text-xs disabled:opacity-50 disabled:no-underline"
                      onClick={() => void cleanupDrive(a)}
                    >
                      {isCleaningId === a.id ? "清理中..." : "清空网盘"}
                    </button>
                    <button
                      type="button"
                      className="text-destructive hover:underline text-xs"
                      onClick={() => void remove(a.id)}
                    >
                      删除
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
          </table>
        </div>
      </div>

      {cleanupTarget && (
        <div className="fixed inset-0 z-50 bg-black/45 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="w-full max-w-lg rounded-xl border border-white/15 bg-slate-900/95 p-5 text-white shadow-2xl">
            <h3 className="text-base font-semibold">二次确认：清空网盘</h3>
            <p className="mt-2 text-sm text-white/75">
              账号：{cleanupTarget.email} / 池：{cleanupTarget.pool_type} / 状态：{cleanupTarget.status}
            </p>
            <p className="mt-1 text-sm text-white/75">
              用量：{Math.round(cleanupTarget.quota_used / 1024 / 1024)} /{" "}
              {Math.round(cleanupTarget.quota_total / 1024 / 1024)} MB（约 {cleanupTarget.quota_percent}%）
            </p>
            <p className="mt-3 text-sm text-white/80">
              仅清空网盘根目录下「PPHUB」文件夹内的文件；您在其它目录中的文件不会被删除。
            </p>
            <p className="mt-2 text-sm text-amber-300">此操作不可恢复。请输入“清空”后确认执行。</p>
            <input
              value={cleanupConfirmText}
              onChange={(e) => setCleanupConfirmText(e.target.value)}
              placeholder="请输入：清空"
              className="mt-3 w-full bg-white/5 border border-white/20 rounded px-3 py-2 text-sm text-white"
              autoFocus
            />
            <div className="mt-4 flex items-center justify-end gap-2">
              <button
                type="button"
                className="px-3 py-2 text-sm rounded bg-white/10 hover:bg-white/20"
                onClick={() => {
                  setCleanupTarget(null);
                  setCleanupConfirmText("");
                  pushToast("info", "已取消清空操作");
                }}
              >
                取消
              </button>
              <button
                type="button"
                disabled={isCleaningId === cleanupTarget.id}
                className="px-3 py-2 text-sm rounded bg-destructive/80 hover:bg-destructive disabled:opacity-50"
                onClick={() => void confirmCleanupDrive()}
              >
                {isCleaningId === cleanupTarget.id ? "清理中..." : "确认清空"}
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="fixed right-4 bottom-4 z-50 flex flex-col gap-2 max-w-sm w-[calc(100vw-2rem)]">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`rounded-lg border px-3 py-2 shadow-lg backdrop-blur ${
              t.kind === "success"
                ? "bg-emerald-500/15 border-emerald-400/40 text-emerald-100"
                : t.kind === "error"
                  ? "bg-red-500/15 border-red-400/40 text-red-100"
                  : "bg-white/10 border-white/20 text-white"
            }`}
          >
            <div className="flex items-start gap-2">
              <div className="text-sm leading-5 flex-1 break-words">{t.text}</div>
              <button
                type="button"
                className="text-xs opacity-80 hover:opacity-100"
                onClick={() => setToasts((prev) => prev.filter((x) => x.id !== t.id))}
              >
                关闭
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
