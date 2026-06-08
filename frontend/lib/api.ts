import axios from "axios";

import { clearAuth } from "./auth";

const raw = process.env.NEXT_PUBLIC_API_URL || "/api/v1";

export const api = axios.create({
  baseURL: raw,
});

function isAuthPublic401Request(config: unknown): boolean {
  const c = config as { url?: string };
  const path = (c.url || "").toString();
  return (
    path.includes("/auth/login") ||
    path.includes("/auth/register") ||
    path.includes("/auth/refresh")
  );
}

api.interceptors.request.use((config) => {
  if (typeof window !== "undefined") {
    const token = window.localStorage.getItem("access_token");
    if (token) config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (res) => res,
  (error) => {
    const status = error.response?.status;
    if (status !== 401 || typeof window === "undefined") {
      return Promise.reject(error);
    }
    if (isAuthPublic401Request(error.config)) {
      return Promise.reject(error);
    }
    clearAuth();
    const path = window.location.pathname;
    if (path.startsWith("/login") || path.startsWith("/register")) {
      return Promise.reject(error);
    }
    const next = encodeURIComponent(`${path}${window.location.search}`);
    window.location.assign(`/login?next=${next}&reason=expired`);
    return Promise.reject(error);
  },
);
