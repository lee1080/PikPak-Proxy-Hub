export function getApiErrorMessage(ex: unknown, fallback = "请求失败"): string {
  if (typeof ex !== "object" || !ex) return fallback;

  const maybe = ex as {
    message?: string;
    response?: { data?: unknown; status?: number };
  };

  const data = maybe.response?.data;
  if (typeof data === "object" && data) {
    if ("detail" in data) {
      const detail = (data as { detail: unknown }).detail;
      if (typeof detail === "string") return detail;
      if (typeof detail === "object" && detail) {
        if ("message" in detail && typeof (detail as { message?: unknown }).message === "string") {
          return (detail as { message: string }).message;
        }
      }
      return JSON.stringify(detail);
    }
    if ("message" in data && typeof (data as { message?: unknown }).message === "string") {
      return (data as { message: string }).message;
    }
  }

  if (!maybe.response) {
    return "无法连接后端服务，请确认后端已启动并可访问 127.0.0.1:8000";
  }

  return maybe.message || fallback;
}

